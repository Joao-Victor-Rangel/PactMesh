# PactMesh on Solana

Two Solana pieces live in this repository:

| Piece | Where | Tested how |
|---|---|---|
| Escrow program (native Rust, no Anchor) | `contracts/escrow` | 8 Rust tests run the real processor with stubbed syscalls; mutation-checked |
| Python client (`SolanaEscrowClient`, `SolanaMemoAnchor`) | `pactmesh/ledger/solana.py` | PDA and instruction bytes cross-checked against the Rust code (`fixtures.json`); end-to-end agent flows against `pactmesh-localnet` |
| `pactmesh-localnet` | `contracts/escrow/src/bin/localnet.rs` | JSON-RPC emulator that executes the program's processor on the host |

**Done:** `cargo build-sbf` (Agave 4.3.0, 72 KB program), `solana program deploy` to a local
`solana-test-validator`, and the full demo on it: escrow create, fund and release plus a Memo anchor, all as
real transactions, with the evidence package verifying against the chain. **Not yet done:** Devnet, which only
needs a funded Devnet keypair (steps below).

## Program summary

- Escrow account = PDA with seeds `["pactmesh-escrow", agreement_hash]`, holding native SOL (lamports).
- States `CREATED -> FUNDED -> RELEASED | REFUNDED | DISPUTED`; RELEASED/REFUNDED are terminal.
- Only the payer funds (exact amount), releases (only to the agreed payee) and refunds (only after the deadline).
- Payer or payee can open a dispute, which freezes the funds (arbitration is future work).
- Error codes (`Custom(n)`): 1 wrong authority, 2 bad state, 3 payee mismatch, 4 amount mismatch,
  5 deadline not reached, 6 terminal, 7 invalid PDA, 8 invalid instruction, 9 already exists,
  10 invalid account data, 11 bad amount.

## Offline: real program logic, no network

```bash
cargo test --manifest-path contracts/escrow/Cargo.toml          # program tests
cargo build --manifest-path contracts/escrow/Cargo.toml --features localnet --bin pactmesh-localnet
pytest tests/test_solana_localnet.py                            # agents + Rust escrow end to end
python -m pactmesh demo --chain localnet                         # narrated demo on the Rust escrow
```

`pactmesh-localnet` is not a validator (no BPF, no compute limits, no blockhash expiry, no rent
collection). It exists so that the Python client and the program logic can be tested together.

## Real validator on your machine (no faucet, no network)

`solana-test-validator` ships with the Solana CLI and runs the compiled program exactly as a cluster does.
On Windows, run these in WSL (Ubuntu); the agents pay themselves from the local faucet.

```bash
solana-test-validator --reset --quiet &
solana-keygen new --no-bip39-passphrase -o deployer.json && solana -u localhost -k deployer.json airdrop 10
cd contracts/escrow && cargo build-sbf && cd ../..
solana -u localhost -k deployer.json program deploy contracts/escrow/target/deploy/pactmesh_escrow.so
# prints: Program Id: <PROGRAM_ID>
python -m pactmesh demo --chain solana --rpc http://127.0.0.1:8899 --program-id <PROGRAM_ID>
```

The narration labels this run `local solana-test-validator`: real program execution, not a public cluster.

## Devnet from zero (you do not need a wallet app)

1. Install the Solana CLI (Agave) and the Rust SBF toolchain:
   ```bash
   sh -c "$(curl -sSfL https://release.anza.xyz/stable/install)"
   solana config set --url devnet
   ```
2. Create a Devnet-only keypair and get free test SOL (deploying needs about 1 to 2 SOL):
   ```bash
   python -m pactmesh solana-keygen --out devnet.json --airdrop
   # if rate-limited: paste the printed address at https://faucet.solana.com (you can request more than once)
   ```
3. Build and deploy the program:
   ```bash
   cd contracts/escrow
   cargo build-sbf
   solana program deploy target/deploy/pactmesh_escrow.so --keypair ../../devnet.json
   # prints: Program Id: <PROGRAM_ID>
   cd ../..
   ```
4. Run the narrated demo on Devnet. Airdrops are rate-limited there, and a payee account must be
   rent-exempt to receive a payment at all, so `--funder` tops up each agent with 0.01 SOL from your
   Devnet keypair (about 0.03 SOL per run):
   ```bash
   python -m pactmesh demo --chain solana --program-id <PROGRAM_ID> --funder devnet.json
   ```
   Or run the agents yourself (amounts are lamports; the buyer pays rent of about 0.0017 SOL per escrow):
   ```bash
   python -m pactmesh relay --port 8701
   python -m pactmesh supplier --name alpha --price 90 --min-price 78 --chain solana --program-id <PROGRAM_ID>
   python -m pactmesh buyer --chain solana --program-id <PROGRAM_ID> --keypair devnet.json \
       --policy examples/policy-solana.json
   ```
   Open the printed dashboard URL and create a task. Suppliers only need SOL to anchor their own
   evidence; without it they log a warning and continue.
5. Anchor an evidence root from the simulated demo, if you only want a quick on-chain artifact:
   ```bash
   python -m pactmesh demo
   python -m pactmesh solana-anchor .pactmesh-demo/evidence.json --keypair devnet.json
   ```

Never commit `devnet.json` (it is git-ignored). Devnet SOL has no value, but the habit matters.
