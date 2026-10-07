# Open source and zero cost ("0800")

Everything needed to build, test and demo Cripito is open source and free to use. Nothing requires a
paid API, a paid account, real money, or hosting: the demo runs on one laptop.

| Component | License | Cost | Verified how |
|---|---|---|---|
| Cripito (protocol, runtime, escrow program, dashboard, tests) | MIT | free | `LICENSE` |
| Python dependencies (PyNaCl, cffi, pycparser; pytest for tests) | Apache-2.0 / MIT-0 / BSD / MIT | free | `scripts/license_inventory.py` |
| Rust crates of the escrow program and localnet (199) | MIT / Apache-2.0 / BSD family | free | same script, `docs/LICENSES.md` |
| Solana Devnet + faucet | network test tokens with no value | free | Devnet SOL comes from the faucet |
| Solana CLI / Agave toolchain (deploy) | Apache-2.0 | free | upstream repository |
| Local AI: Ollama (MIT), llama.cpp (MIT), vLLM (Apache-2.0) | open source | free, runs on your machine | upstream repositories |
| Laya model weights | Apache-2.0 according to its model card | free | **check the card for the exact revision you use** |
| Nym `nym-client` | Apache-2.0 (workspace license in `nymtech/nym`) | software free | checked in `Cargo.toml` upstream |
| Nym network usage | — | **not verified** | the build environment could not reach Nym; check whether the network you join requires bandwidth credentials |
| GitHub repository + Actions CI | — | free for public repositories | `.github/workflows/ci.yml` |

## Rules this repository follows

- `python scripts/license_inventory.py` fails if any dependency has a license outside the permissive
  allow-list (no copyleft, no proprietary, no unknown). CI runs it on every push.
- The decision engine defaults to deterministic rules and only talks to models **you run locally**.
  The `openai-compat` engine works with any compatible server; no paid API is needed or configured.
- Settlement runs on the simulated ledger, `cripito-localnet` or Solana **Devnet**. Mainnet is not used.
- No dataset leaves the test environment: the demo uses a seeded synthetic dataset.

## What would cost money (not used)

Solana mainnet fees, hosted AI APIs, cloud hosting, and possibly Nym network credentials (see above). The
architecture allows them; the project does not depend on any of them.
