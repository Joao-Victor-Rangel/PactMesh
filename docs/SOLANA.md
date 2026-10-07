# Cripito on Solana

Two Solana pieces live in this repository:

| Piece | Where | Tested how |
|---|---|---|
| Escrow program (native Rust, no Anchor) | `contracts/escrow` | 8 Rust tests run the real processor with stubbed syscalls; mutation-checked |
| Python client (`SolanaEscrowClient`, `SolanaMemoAnchor`) | `cripito/ledger/solana.py` | PDA and instruction bytes cross-checked against the Rust code (`fixtures.json`); end-to-end agent flows against `cripito-localnet` |
| `cripito-localnet` | `contracts/escrow/src/bin/localnet.rs` | JSON-RPC emulator that executes the program's processor on the host |

**Not yet done:** compiling to SBF (`cargo build-sbf`) and deploying to Devnet. The build environment had no
access to the Solana toolchain or Devnet RPC. The steps below are the ones to run on your machine. If
any of them fails, keep the error message: it is the next thing to fix.

## Program summary

- Escrow account = PDA with seeds `["cripito-escrow", agreement_hash]`, holding native SOL (lamports).
- States `CREATED -> FUNDED -> RELEASED | REFUNDED | DISPUTED`; RELEASED/REFUNDED are terminal.
- Only the payer funds (exact amount), releases (only to the agreed payee) and refunds (only after the deadline).
- Payer or payee can open a dispute, which freezes the funds (arbitration is future work).
- Error codes (`Custom(n)`): 1 wrong authority, 2 bad state, 3 payee mismatch, 4 amount mismatch,
  5 deadline not reached, 6 terminal, 7 invalid PDA, 8 invalid instruction, 9 already exists,
  10 invalid account data, 11 bad amount.

## Offline: real program logic, no network

```bash
cargo test --manifest-path contracts/escrow/Cargo.toml          # program tests
cargo build --manifest-path contracts/escrow/Cargo.toml --features localnet --bin cripito-localnet
pytest tests/test_solana_localnet.py                            # agents + Rust escrow end to end
python -m cripito demo --chain localnet                         # narrated demo on the Rust escrow
```

`cripito-localnet` is not a validator (no BPF, no compute limits, no blockhash expiry, no rent
collection). It exists so that the Python client and the program logic can be tested together.

## Devnet from zero (you do not need a wallet app)

1. Install the Solana CLI (Agave) and the Rust SBF toolchain:
   ```bash
   sh -c "$(curl -sSfL https://release.anza.xyz/stable/install)"
   solana config set --url devnet
   ```
2. Create a Devnet-only keypair and get free test SOL (deploying needs about 1 to 2 SOL):
   ```bash
   python -m cripito solana-keygen --out devnet.json --airdrop
   # if rate-limited: paste the printed address at https://faucet.solana.com (you can request more than once)
   ```
3. Build and deploy the program:
   ```bash
   cd contracts/escrow
   cargo build-sbf
   solana program deploy target/deploy/cripito_escrow.so --keypair ../../devnet.json
   # prints: Program Id: <PROGRAM_ID>
   cd ../..
   ```
4. Run the agents on Devnet (amounts are lamports; the buyer pays rent of about 0.0017 SOL per escrow):
   ```bash
   python -m cripito relay --port 8701
   python -m cripito supplier --name alpha --price 90 --min-price 78 --chain solana --program-id <PROGRAM_ID>
   python -m cripito buyer --chain solana --program-id <PROGRAM_ID> --keypair devnet.json \
       --policy examples/policy-solana.json
   ```
   Open the printed dashboard URL and create a task. Suppliers only need SOL to anchor their own
   evidence; without it they log a warning and continue.
5. Anchor an evidence root from the simulated demo, if you only want a quick on-chain artifact:
   ```bash
   python -m cripito demo
   python -m cripito solana-anchor .cripito-demo/evidence.json --keypair devnet.json
   ```

Never commit `devnet.json` (it is git-ignored). Devnet SOL has no value, but the habit matters.
