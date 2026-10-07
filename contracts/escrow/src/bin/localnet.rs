//! pactmesh-localnet: a tiny host-side JSON-RPC emulator that executes the
//! *same* escrow processor as the on-chain program, so the Python client and
//! the full agent flow can be tested end to end without a Solana validator.
//!
//! It verifies Ed25519 transaction signatures, charges a 5000-lamport fee,
//! emulates the System Program CPIs used by the escrow (create_account,
//! transfer) and the Memo program, enforces lamport conservation and
//! read-only accounts, and serves the RPC subset the client uses.
//!
//! It is NOT a validator: no BPF execution, no compute limits, no blockhash
//! expiry, no rent collection. Final testing must happen on Devnet.

#![allow(deprecated)]

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::sync::Mutex;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use base64::{engine::general_purpose::STANDARD as B64, Engine};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde_json::{json, Value};
use solana_program::{
    account_info::AccountInfo, clock::Clock, entrypoint::ProgramResult, instruction::Instruction,
    program_error::ProgramError, program_stubs, pubkey::Pubkey, rent::Rent, system_program,
};

const FEE: u64 = 5_000;
const MEMO: &str = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr";
const SLOT_MS: u128 = 400;

static LOGS: Mutex<Vec<String>> = Mutex::new(Vec::new());
static PROGRAM: Mutex<Option<Pubkey>> = Mutex::new(None);

struct Stubs;

impl program_stubs::SyscallStubs for Stubs {
    fn sol_log(&self, m: &str) {
        LOGS.lock().unwrap().push(format!("Program log: {m}"));
    }

    fn sol_invoke_signed(&self, ix: &Instruction, infos: &[AccountInfo], seeds: &[&[&[u8]]]) -> ProgramResult {
        if ix.program_id != system_program::id() {
            return Err(ProgramError::IncorrectProgramId);
        }
        let pid = PROGRAM.lock().unwrap().expect("program");
        let pdas: Vec<Pubkey> = seeds.iter().filter_map(|s| Pubkey::create_program_address(s, &pid).ok()).collect();
        let find = |k: &Pubkey| infos.iter().find(|a| a.key == k).ok_or(ProgramError::NotEnoughAccountKeys);
        let from = find(&ix.accounts[0].pubkey)?;
        let to = find(&ix.accounts[1].pubkey)?;
        let signed = |a: &AccountInfo| a.is_signer || pdas.contains(a.key);
        if !signed(from) {
            return Err(ProgramError::MissingRequiredSignature);
        }
        if *from.owner != system_program::id() {
            return Err(ProgramError::IllegalOwner);
        }
        let tag = u32::from_le_bytes(ix.data[0..4].try_into().unwrap());
        let lamports = u64::from_le_bytes(ix.data[4..12].try_into().unwrap());
        if from.lamports() < lamports {
            return Err(ProgramError::InsufficientFunds);
        }
        match tag {
            0 => {
                if !signed(to) || to.lamports() > 0 || !to.data_is_empty() {
                    return Err(ProgramError::AccountAlreadyInitialized);
                }
                let space = u64::from_le_bytes(ix.data[12..20].try_into().unwrap()) as usize;
                let owner = Pubkey::new_from_array(ix.data[20..52].try_into().unwrap());
                *to.data.borrow_mut() = Box::leak(vec![0u8; space].into_boxed_slice());
                to.assign(&owner);
            }
            2 => {}
            _ => return Err(ProgramError::InvalidInstructionData),
        }
        **from.try_borrow_mut_lamports()? -= lamports;
        **to.try_borrow_mut_lamports()? += lamports;
        LOGS.lock().unwrap().push("Program 11111111111111111111111111111111 success".into());
        Ok(())
    }

    fn sol_get_clock_sysvar(&self, var_addr: *mut u8) -> u64 {
        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs() as i64;
        unsafe { std::ptr::write(var_addr as *mut Clock, Clock { unix_timestamp: now, ..Clock::default() }) };
        0
    }

    fn sol_get_rent_sysvar(&self, var_addr: *mut u8) -> u64 {
        unsafe { std::ptr::write(var_addr as *mut Rent, Rent::default()) };
        0
    }
}

#[derive(Clone)]
struct Acct {
    lamports: u64,
    data: Vec<u8>,
    owner: Pubkey,
}

struct TxRec {
    slot: u64,
    err: Value,
    logs: Vec<String>,
}

struct Chain {
    program_id: Pubkey,
    accounts: HashMap<Pubkey, Acct>,
    txs: HashMap<String, TxRec>,
    start: Instant,
}

struct Parsed {
    sigs: Vec<[u8; 64]>,
    message: Vec<u8>,
    nsig: usize,
    ro_signed: usize,
    ro_unsigned: usize,
    keys: Vec<Pubkey>,
    ixs: Vec<(usize, Vec<usize>, Vec<u8>)>,
}

fn compact(b: &[u8], pos: &mut usize) -> Result<usize, String> {
    let mut n = 0usize;
    for shift in 0..3 {
        let byte = *b.get(*pos).ok_or("truncated")?;
        *pos += 1;
        n |= ((byte & 0x7f) as usize) << (7 * shift);
        if byte & 0x80 == 0 {
            return Ok(n);
        }
    }
    Err("bad compact-u16".into())
}

fn take<'a>(b: &'a [u8], pos: &mut usize, n: usize) -> Result<&'a [u8], String> {
    let s = b.get(*pos..*pos + n).ok_or("truncated")?;
    *pos += n;
    Ok(s)
}

fn parse(raw: &[u8]) -> Result<Parsed, String> {
    let mut p = 0;
    let nsigs = compact(raw, &mut p)?;
    let mut sigs = vec![];
    for _ in 0..nsigs {
        sigs.push(take(raw, &mut p, 64)?.try_into().unwrap());
    }
    let message = raw[p..].to_vec();
    let m = &message;
    let mut q = 0;
    let h = take(m, &mut q, 3)?;
    let (nsig, ro_signed, ro_unsigned) = (h[0] as usize, h[1] as usize, h[2] as usize);
    if h[0] & 0x80 != 0 {
        return Err("versioned transactions not supported".into());
    }
    let nkeys = compact(m, &mut q)?;
    let mut keys = vec![];
    for _ in 0..nkeys {
        keys.push(Pubkey::new_from_array(take(m, &mut q, 32)?.try_into().unwrap()));
    }
    take(m, &mut q, 32)?; // recent blockhash (not validated by this emulator)
    let nix = compact(m, &mut q)?;
    let mut ixs = vec![];
    for _ in 0..nix {
        let prog = take(m, &mut q, 1)?[0] as usize;
        let na = compact(m, &mut q)?;
        let accs = take(m, &mut q, na)?.iter().map(|&i| i as usize).collect::<Vec<_>>();
        let nd = compact(m, &mut q)?;
        let data = take(m, &mut q, nd)?.to_vec();
        if prog >= keys.len() || accs.iter().any(|&i| i >= keys.len()) {
            return Err("account index out of range".into());
        }
        ixs.push((prog, accs, data));
    }
    if sigs.len() != nsig || nsig == 0 {
        return Err("signature count mismatch".into());
    }
    Ok(Parsed { sigs, message, nsig, ro_signed, ro_unsigned, keys, ixs })
}

impl Chain {
    fn slot(&self) -> u64 {
        (self.start.elapsed().as_millis() / SLOT_MS) as u64
    }

    fn acct(&self, k: &Pubkey) -> Acct {
        self.accounts.get(k).cloned().unwrap_or(Acct { lamports: 0, data: vec![], owner: system_program::id() })
    }

    /// Executes against a copy; returns (new account states, logs) or (error, logs).
    fn execute(&self, t: &Parsed) -> (Result<Vec<Acct>, Value>, Vec<String>) {
        LOGS.lock().unwrap().clear();
        let n = t.keys.len();
        let writable = |i: usize| {
            if i < t.nsig {
                i < t.nsig - t.ro_signed
            } else {
                i < n - t.ro_unsigned
            }
        };
        let mut states: Vec<Acct> = t.keys.iter().map(|k| self.acct(k)).collect();
        if states[0].lamports < FEE {
            return (Err(json!("InsufficientFundsForFee")), vec![]);
        }
        states[0].lamports -= FEE;
        let before_total: u128 = states.iter().map(|a| a.lamports as u128).sum();
        let mut lamports: Vec<u64> = states.iter().map(|a| a.lamports).collect();
        let mut owners: Vec<Pubkey> = states.iter().map(|a| a.owner).collect();
        let datas: Vec<&'static mut [u8]> = states.iter().map(|a| &mut *Box::leak(a.data.clone().into_boxed_slice())).collect();
        let infos: Vec<AccountInfo> = t
            .keys
            .iter()
            .zip(lamports.iter_mut())
            .zip(datas)
            .zip(owners.iter_mut())
            .enumerate()
            .map(|(i, (((k, l), d), o))| AccountInfo::new(k, i < t.nsig, writable(i), l, d, o, false, 0))
            .collect();
        let memo: Pubkey = MEMO.parse().unwrap();
        for (ix_i, (prog, accs, data)) in t.ixs.iter().enumerate() {
            let pid = t.keys[*prog];
            LOGS.lock().unwrap().push(format!("Program {pid} invoke [1]"));
            let res: ProgramResult = if pid == memo {
                match std::str::from_utf8(data) {
                    Ok(s) => {
                        LOGS.lock().unwrap().push(format!("Program log: Memo (len {}): {:?}", s.len(), s));
                        Ok(())
                    }
                    Err(_) => Err(ProgramError::InvalidInstructionData),
                }
            } else if pid == self.program_id {
                *PROGRAM.lock().unwrap() = Some(pid);
                let sub: Vec<AccountInfo> = accs.iter().map(|&i| infos[i].clone()).collect();
                pactmesh_escrow::processor::process(&pid, &sub, data)
            } else {
                Err(ProgramError::IncorrectProgramId)
            };
            if let Err(e) = res {
                let logs = LOGS.lock().unwrap().clone();
                let detail = match e {
                    ProgramError::Custom(c) => json!({"Custom": c}),
                    other => json!(format!("{other:?}")),
                };
                return (Err(json!({"InstructionError": [ix_i, detail]})), logs);
            }
            LOGS.lock().unwrap().push(format!("Program {pid} success"));
        }
        // Runtime invariants: lamports conserved, read-only accounts untouched.
        let after: Vec<Acct> = infos
            .iter()
            .map(|a| Acct { lamports: a.lamports(), data: a.data.borrow().to_vec(), owner: *a.owner })
            .collect();
        let after_total: u128 = after.iter().map(|a| a.lamports as u128).sum();
        let logs = LOGS.lock().unwrap().clone();
        if after_total != before_total {
            return (Err(json!("UnbalancedTransaction")), logs);
        }
        for (i, (a, b)) in states.iter().zip(after.iter()).enumerate() {
            let changed = a.data != b.data || a.owner != b.owner || a.lamports != b.lamports;
            if !writable(i) && changed {
                return (Err(json!({"ReadonlyDataModified": i})), logs);
            }
        }
        (Ok(after), logs)
    }

    fn send(&mut self, b64: &str) -> Result<Value, Value> {
        let raw = B64.decode(b64).map_err(|e| rpc_err(-32602, &format!("bad base64: {e}"), Value::Null))?;
        let t = parse(&raw).map_err(|e| rpc_err(-32602, &e, Value::Null))?;
        for (i, sig) in t.sigs.iter().enumerate() {
            let vk = VerifyingKey::from_bytes(&t.keys[i].to_bytes()).map_err(|_| rpc_err(-32003, "bad signer key", Value::Null))?;
            vk.verify(&t.message, &Signature::from_bytes(sig))
                .map_err(|_| rpc_err(-32003, "Transaction signature verification failure", Value::Null))?;
        }
        let sig_str = bs58(&t.sigs[0]);
        if self.txs.contains_key(&sig_str) {
            return Err(rpc_err(-32002, "This transaction has already been processed", Value::Null));
        }
        // Preflight: a failing transaction is rejected and never lands (like sendTransaction with preflight).
        let (res, logs) = self.execute(&t);
        match res {
            Err(err) => {
                let msg = match err.pointer("/InstructionError/1/Custom").and_then(Value::as_u64) {
                    Some(c) => format!("Transaction simulation failed: Error processing Instruction 0: custom program error: 0x{c:x}"),
                    None => format!("Transaction simulation failed: {err}"),
                };
                Err(rpc_err(-32002, &msg, json!({"err": err, "logs": logs})))
            }
            Ok(after) => {
                for (k, a) in t.keys.iter().zip(after) {
                    self.accounts.insert(*k, a);
                }
                let slot = self.slot();
                self.txs.insert(sig_str.clone(), TxRec { slot, err: Value::Null, logs });
                Ok(json!(sig_str))
            }
        }
    }

    fn handle(&mut self, method: &str, params: &Value) -> Result<Value, Value> {
        let slot = self.slot();
        let ctx = json!({"slot": slot});
        let p0 = params.get(0).cloned().unwrap_or(Value::Null);
        let key = |v: &Value| -> Result<Pubkey, Value> {
            v.as_str().and_then(|s| s.parse().ok()).ok_or_else(|| rpc_err(-32602, "Invalid param: pubkey", Value::Null))
        };
        Ok(match method {
            "getHealth" => json!("ok"),
            "getSlot" => json!(slot),
            "getLatestBlockhash" => json!({"context": ctx, "value": {"blockhash": bs58(&[(slot % 251) as u8 + 1; 32]), "lastValidBlockHeight": slot + 150}}),
            "getBalance" => json!({"context": ctx, "value": self.acct(&key(&p0)?).lamports}),
            "requestAirdrop" => {
                let k = key(&p0)?;
                let amt = params.get(1).and_then(Value::as_u64).unwrap_or(1_000_000_000);
                self.accounts.entry(k).or_insert(Acct { lamports: 0, data: vec![], owner: system_program::id() }).lamports += amt;
                let sig = bs58(&{
                    let mut b = [0u8; 64];
                    b[..8].copy_from_slice(&(self.txs.len() as u64 + 1).to_le_bytes());
                    b[8..40].copy_from_slice(&k.to_bytes());
                    b
                });
                self.txs.insert(sig.clone(), TxRec { slot, err: Value::Null, logs: vec!["airdrop".into()] });
                json!(sig)
            }
            "getAccountInfo" => {
                let k = key(&p0)?;
                match self.accounts.get(&k) {
                    Some(a) if a.lamports > 0 => json!({"context": ctx, "value": {
                        "lamports": a.lamports, "owner": a.owner.to_string(), "executable": false, "rentEpoch": 0,
                        "space": a.data.len(), "data": [B64.encode(&a.data), "base64"]}}),
                    _ => json!({"context": ctx, "value": null}),
                }
            }
            "sendTransaction" => self.send(p0.as_str().unwrap_or(""))?,
            "getSignatureStatuses" => {
                let sigs = p0.as_array().cloned().unwrap_or_default();
                let vals: Vec<Value> = sigs
                    .iter()
                    .map(|s| match self.txs.get(s.as_str().unwrap_or("")) {
                        None => Value::Null,
                        Some(t) => {
                            let age = slot.saturating_sub(t.slot);
                            let level = if age >= 4 { "finalized" } else if age >= 1 { "confirmed" } else { "processed" };
                            json!({"slot": t.slot, "confirmations": if age >= 4 { Value::Null } else { json!(age) },
                                   "err": t.err, "status": {"Ok": null}, "confirmationStatus": level})
                        }
                    })
                    .collect();
                json!({"context": ctx, "value": vals})
            }
            "getTransaction" => match self.txs.get(p0.as_str().unwrap_or("")) {
                None => Value::Null,
                Some(t) => json!({"slot": t.slot, "meta": {"err": t.err, "logMessages": t.logs, "fee": FEE}}),
            },
            other => return Err(rpc_err(-32601, &format!("Method not found: {other}"), Value::Null)),
        })
    }
}

fn rpc_err(code: i64, message: &str, data: Value) -> Value {
    json!({"code": code, "message": message, "data": data})
}

fn bs58(b: &[u8]) -> String {
    const A: &[u8] = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
    let mut digits: Vec<u8> = vec![];
    for &byte in b {
        let mut carry = byte as u32;
        for d in digits.iter_mut() {
            carry += (*d as u32) << 8;
            *d = (carry % 58) as u8;
            carry /= 58;
        }
        while carry > 0 {
            digits.push((carry % 58) as u8);
            carry /= 58;
        }
    }
    let zeros = b.iter().take_while(|&&x| x == 0).count();
    std::iter::repeat('1').take(zeros).chain(digits.iter().rev().map(|&d| A[d as usize] as char)).collect()
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let arg = |name: &str, default: &str| {
        args.iter().position(|a| a == name).and_then(|i| args.get(i + 1).cloned()).unwrap_or(default.to_string())
    };
    let port = arg("--port", "8899");
    let default_id = Pubkey::new_from_array([0x5c; 32]).to_string();
    let program_id: Pubkey = arg("--program-id", &default_id).parse().expect("program id");
    program_stubs::set_syscall_stubs(Box::new(Stubs));
    let mut chain = Chain { program_id, accounts: HashMap::new(), txs: HashMap::new(), start: Instant::now() };
    let listener = TcpListener::bind(format!("127.0.0.1:{port}")).expect("bind");
    println!("[localnet] escrow program {program_id} on http://127.0.0.1:{port} (host emulator, not a validator)");
    for stream in listener.incoming() {
        let Ok(mut stream) = stream else { continue };
        let mut reader = BufReader::new(stream.try_clone().unwrap());
        let mut len = 0usize;
        loop {
            let mut line = String::new();
            if reader.read_line(&mut line).unwrap_or(0) == 0 {
                break;
            }
            let l = line.to_ascii_lowercase();
            if let Some(v) = l.strip_prefix("content-length:") {
                len = v.trim().parse().unwrap_or(0);
            }
            if line == "\r\n" {
                break;
            }
        }
        let mut body = vec![0u8; len.min(1 << 20)];
        if reader.read_exact(&mut body).is_err() {
            continue;
        }
        let req: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
        let id = req.get("id").cloned().unwrap_or(json!(1));
        let method = req.get("method").and_then(Value::as_str).unwrap_or("");
        let params = req.get("params").cloned().unwrap_or(json!([]));
        let resp = match chain.handle(method, &params) {
            Ok(r) => json!({"jsonrpc": "2.0", "id": id, "result": r}),
            Err(e) => json!({"jsonrpc": "2.0", "id": id, "error": e}),
        };
        let out = resp.to_string();
        let _ = write!(stream, "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}", out.len(), out);
    }
}
