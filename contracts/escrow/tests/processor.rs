//! Runs the real processor on the host. Solana syscalls are replaced by stubs
//! that emulate the System Program (create_account, transfer), the Clock and
//! the Rent sysvars, so every instruction path is exercised without a validator.

#![allow(deprecated)]

use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Mutex, Once};

use cripito_escrow::instruction::EscrowInstruction;
use cripito_escrow::processor::{escrow_address, process};
use cripito_escrow::state::{Escrow, State, LEN};
use solana_program::{
    account_info::AccountInfo, clock::Clock, entrypoint::ProgramResult, instruction::Instruction,
    program_error::ProgramError, program_stubs, pubkey::Pubkey, rent::Rent, system_program,
};

static NOW: AtomicI64 = AtomicI64::new(1_000);
static LOCK: Mutex<()> = Mutex::new(());
static INIT: Once = Once::new();

struct Stubs;

impl program_stubs::SyscallStubs for Stubs {
    fn sol_log(&self, _m: &str) {}

    fn sol_invoke_signed(&self, ix: &Instruction, infos: &[AccountInfo], seeds: &[&[&[u8]]]) -> ProgramResult {
        assert_eq!(ix.program_id, system_program::id());
        let find = |k: &Pubkey| infos.iter().find(|a| a.key == k).expect("account passed to CPI");
        let tag = u32::from_le_bytes(ix.data[0..4].try_into().unwrap());
        let from = find(&ix.accounts[0].pubkey);
        let to = find(&ix.accounts[1].pubkey);
        // Every account the system program debits must have signed (directly or via PDA seeds).
        let pda_signed = seeds.iter().any(|s| {
            Pubkey::create_program_address(s, &cripito_program_id()).map(|p| p == *to.key).unwrap_or(false)
        });
        assert!(from.is_signer, "funding account must sign");
        let lamports = u64::from_le_bytes(ix.data[4..12].try_into().unwrap());
        match tag {
            0 => {
                // CreateAccount { lamports, space, owner }
                assert!(pda_signed, "new PDA must be signed by seeds");
                let space = u64::from_le_bytes(ix.data[12..20].try_into().unwrap()) as usize;
                let owner = Pubkey::new_from_array(ix.data[20..52].try_into().unwrap());
                assert_eq!(to.lamports(), 0);
                **from.try_borrow_mut_lamports()? -= lamports;
                **to.try_borrow_mut_lamports()? += lamports;
                *to.data.borrow_mut() = Box::leak(vec![0u8; space].into_boxed_slice());
                #[allow(deprecated)]
                to.assign(&owner);
            }
            2 => {
                // Transfer { lamports }
                if from.lamports() < lamports {
                    return Err(ProgramError::InsufficientFunds);
                }
                **from.try_borrow_mut_lamports()? -= lamports;
                **to.try_borrow_mut_lamports()? += lamports;
            }
            other => panic!("unexpected system instruction {other}"),
        }
        Ok(())
    }

    fn sol_get_clock_sysvar(&self, var_addr: *mut u8) -> u64 {
        let c = Clock { unix_timestamp: NOW.load(Ordering::SeqCst), ..Clock::default() };
        unsafe { std::ptr::write(var_addr as *mut Clock, c) };
        0
    }

    fn sol_get_rent_sysvar(&self, var_addr: *mut u8) -> u64 {
        unsafe { std::ptr::write(var_addr as *mut Rent, Rent::default()) };
        0
    }
}

fn cripito_program_id() -> Pubkey {
    Pubkey::new_from_array([7u8; 32])
}

fn leak<T>(v: T) -> &'static mut T {
    Box::leak(Box::new(v))
}

fn account(key: Pubkey, lamports: u64, owner: Pubkey, signer: bool, data: usize) -> AccountInfo<'static> {
    AccountInfo::new(
        leak(key),
        signer,
        true,
        leak(lamports),
        Box::leak(vec![0u8; data].into_boxed_slice()),
        leak(owner),
        false,
        0,
    )
}

struct Env {
    pid: Pubkey,
    payer: AccountInfo<'static>,
    payee: AccountInfo<'static>,
    escrow: AccountInfo<'static>,
    sys: AccountInfo<'static>,
    hash: [u8; 32],
}

fn env(seed: u8) -> Env {
    INIT.call_once(|| {
        program_stubs::set_syscall_stubs(Box::new(Stubs));
    });
    let pid = cripito_program_id();
    let hash = [seed; 32];
    let (pda, _) = escrow_address(&pid, &hash);
    Env {
        pid,
        payer: account(Pubkey::new_unique(), 10_000_000_000, system_program::id(), true, 0),
        payee: account(Pubkey::new_unique(), 0, system_program::id(), false, 0),
        escrow: account(pda, 0, system_program::id(), false, 0),
        sys: account(system_program::id(), 1, Pubkey::default(), false, 0),
        hash,
    }
}

impl Env {
    fn run(&self, signer: &AccountInfo<'static>, ix: EscrowInstruction, extra: &[&AccountInfo<'static>]) -> ProgramResult {
        let mut accs = vec![signer.clone(), self.escrow.clone()];
        accs.extend(extra.iter().map(|a| (*a).clone()));
        process(&self.pid, &accs, &ix.pack())
    }
    fn create(&self, amount: u64, deadline: i64) -> ProgramResult {
        let ix = EscrowInstruction::Create { agreement_hash: self.hash, payee: self.payee.key.to_bytes(), amount, deadline };
        self.run(&self.payer, ix, &[&self.sys])
    }
    fn fund(&self, amount: u64) -> ProgramResult {
        self.run(&self.payer, EscrowInstruction::Fund { amount }, &[&self.sys])
    }
    fn state(&self) -> Escrow {
        Escrow::unpack(&self.escrow.data.borrow()).unwrap()
    }
}

fn custom(code: u32) -> ProgramResult {
    Err(ProgramError::Custom(code))
}

const AMOUNT: u64 = 78_000_000;

#[test]
fn happy_path_create_fund_release() {
    let _g = LOCK.lock().unwrap();
    let e = env(1);
    let start = e.payer.lamports();
    e.create(AMOUNT, 5_000).unwrap();
    let rent = Rent::default().minimum_balance(LEN);
    assert_eq!(e.state().state, State::Created);
    assert_eq!(e.escrow.owner, &e.pid);
    e.fund(AMOUNT).unwrap();
    assert_eq!(e.state().state, State::Funded);
    assert_eq!(e.escrow.lamports(), rent + AMOUNT);
    e.run(&e.payer, EscrowInstruction::Release, &[&e.payee]).unwrap();
    assert_eq!(e.state().state, State::Released);
    assert_eq!(e.payee.lamports(), AMOUNT);
    assert_eq!(e.payer.lamports(), start - rent - AMOUNT);
    // terminal: no double release, no refund after release
    assert_eq!(e.run(&e.payer, EscrowInstruction::Release, &[&e.payee]), custom(6));
    NOW.store(10_000, Ordering::SeqCst);
    assert_eq!(e.run(&e.payer, EscrowInstruction::Refund, &[]), custom(6));
    NOW.store(1_000, Ordering::SeqCst);
}

#[test]
fn create_twice_and_bad_pda_rejected() {
    let _g = LOCK.lock().unwrap();
    let e = env(2);
    e.create(AMOUNT, 5_000).unwrap();
    assert_eq!(e.create(AMOUNT, 5_000), custom(9));
    let other = env(3);
    let ix = EscrowInstruction::Create { agreement_hash: [99; 32], payee: other.payee.key.to_bytes(), amount: AMOUNT, deadline: 1 };
    assert_eq!(other.run(&other.payer, ix, &[&other.sys]), custom(7));
}

#[test]
fn wrong_authority_and_amount() {
    let _g = LOCK.lock().unwrap();
    let e = env(4);
    e.create(AMOUNT, 5_000).unwrap();
    let mallory = account(Pubkey::new_unique(), 10_000_000_000, system_program::id(), true, 0);
    assert_eq!(e.run(&mallory, EscrowInstruction::Fund { amount: AMOUNT }, &[&e.sys]), custom(1));
    assert_eq!(e.fund(AMOUNT - 1), custom(4));
    e.fund(AMOUNT).unwrap();
    assert_eq!(e.fund(AMOUNT), custom(2));
    // payee cannot release to itself; release to a different account is refused
    let payee_signer = AccountInfo { is_signer: true, ..e.payee.clone() };
    assert_eq!(e.run(&payee_signer, EscrowInstruction::Release, &[&e.payee]), custom(1));
    assert_eq!(e.run(&e.payer, EscrowInstruction::Release, &[&mallory]), custom(3));
    // unsigned payer is refused before anything else
    let unsigned = AccountInfo { is_signer: false, ..e.payer.clone() };
    assert_eq!(e.run(&unsigned, EscrowInstruction::Release, &[&e.payee]), custom(1));
}

#[test]
fn refund_only_after_deadline() {
    let _g = LOCK.lock().unwrap();
    let e = env(5);
    e.create(AMOUNT, 5_000).unwrap();
    e.fund(AMOUNT).unwrap();
    let before = e.payer.lamports();
    assert_eq!(e.run(&e.payer, EscrowInstruction::Refund, &[]), custom(5));
    NOW.store(5_000, Ordering::SeqCst);
    e.run(&e.payer, EscrowInstruction::Refund, &[]).unwrap();
    NOW.store(1_000, Ordering::SeqCst);
    assert_eq!(e.state().state, State::Refunded);
    assert_eq!(e.payer.lamports(), before + AMOUNT);
    assert_eq!(e.run(&e.payer, EscrowInstruction::Release, &[&e.payee]), custom(6));
}

#[test]
fn dispute_freezes_funds() {
    let _g = LOCK.lock().unwrap();
    let e = env(6);
    e.create(AMOUNT, 5_000).unwrap();
    e.fund(AMOUNT).unwrap();
    let payee_signer = AccountInfo { is_signer: true, ..e.payee.clone() };
    e.run(&payee_signer, EscrowInstruction::Dispute, &[]).unwrap();
    assert_eq!(e.state().state, State::Disputed);
    assert_eq!(e.run(&e.payer, EscrowInstruction::Release, &[&e.payee]), custom(2));
    NOW.store(9_000, Ordering::SeqCst);
    assert_eq!(e.run(&e.payer, EscrowInstruction::Refund, &[]), custom(2));
    NOW.store(1_000, Ordering::SeqCst);
}

#[test]
fn foreign_account_is_not_an_escrow() {
    let _g = LOCK.lock().unwrap();
    let e = env(8);
    e.create(AMOUNT, 5_000).unwrap();
    // Same bytes but owned by someone else: must be rejected.
    let fake = account(*e.escrow.key, 1, Pubkey::new_unique(), false, LEN);
    fake.data.borrow_mut().copy_from_slice(&e.escrow.data.borrow());
    let accs = vec![e.payer.clone(), fake, e.sys.clone()];
    assert_eq!(process(&e.pid, &accs, &EscrowInstruction::Fund { amount: AMOUNT }.pack()), custom(10));
}

#[test]
fn instruction_roundtrip_and_garbage() {
    let ix = EscrowInstruction::Create { agreement_hash: [1; 32], payee: [2; 32], amount: 3, deadline: -4 };
    assert_eq!(EscrowInstruction::unpack(&ix.pack()).unwrap(), ix);
    assert!(EscrowInstruction::unpack(&[]).is_err());
    assert!(EscrowInstruction::unpack(&[9]).is_err());
    assert!(EscrowInstruction::unpack(&[2, 0]).is_err());
}

/// Cross-language fixture: the Python client must derive the same PDA and
/// produce the same instruction bytes (see tests/test_solana_escrow.py).
#[test]
fn cross_language_fixture() {
    let fixture: serde_like::Fixture = serde_like::load();
    let pid = Pubkey::new_from_array(fixture.program_id);
    let (pda, bump) = escrow_address(&pid, &fixture.agreement_hash);
    assert_eq!(pda.to_bytes(), fixture.escrow_pda, "PDA mismatch");
    assert_eq!(bump, fixture.bump);
    let ix = EscrowInstruction::Create {
        agreement_hash: fixture.agreement_hash,
        payee: fixture.payee,
        amount: fixture.amount,
        deadline: fixture.deadline,
    };
    assert_eq!(ix.pack(), fixture.create_data);
}

/// Minimal hand parser for the fixture file (keeps the crate dependency-free).
mod serde_like {
    pub struct Fixture {
        pub program_id: [u8; 32],
        pub agreement_hash: [u8; 32],
        pub payee: [u8; 32],
        pub amount: u64,
        pub deadline: i64,
        pub escrow_pda: [u8; 32],
        pub bump: u8,
        pub create_data: Vec<u8>,
    }

    fn field<'a>(s: &'a str, k: &str) -> &'a str {
        let i = s.find(&format!("\"{k}\"")).unwrap_or_else(|| panic!("missing {k}"));
        let rest = &s[i + k.len() + 2..];
        let rest = rest[rest.find(':').unwrap() + 1..].trim_start();
        let end = rest.find([',', '}', '\n']).unwrap();
        rest[..end].trim().trim_matches('"')
    }

    fn hex(s: &str) -> Vec<u8> {
        (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()
    }

    fn h32(s: &str) -> [u8; 32] {
        hex(s).try_into().unwrap()
    }

    pub fn load() -> Fixture {
        let s = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures.json")).unwrap();
        Fixture {
            program_id: h32(field(&s, "program_id_hex")),
            agreement_hash: h32(field(&s, "agreement_hash")),
            payee: h32(field(&s, "payee_hex")),
            amount: field(&s, "amount").parse().unwrap(),
            deadline: field(&s, "deadline").parse().unwrap(),
            escrow_pda: h32(field(&s, "escrow_pda_hex")),
            bump: field(&s, "bump").parse().unwrap(),
            create_data: hex(field(&s, "create_data_hex")),
        }
    }
}
