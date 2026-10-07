//! PactMesh test escrow program (Solana, native, no Anchor).
//!
//! Holds native SOL (Devnet) in a PDA derived from the agreement hash:
//! seeds = ["pactmesh-escrow", agreement_hash]. States: CREATED -> FUNDED ->
//! RELEASED | REFUNDED | DISPUTED. RELEASED and REFUNDED are terminal.
//! Only the payer can fund, release (to the exact payee) and refund (after
//! the deadline); payer or payee can open a dispute, which freezes funds.
//!
//! Prototype for test networks only. Not audited.

pub mod instruction;
pub mod processor;
pub mod state;

#[cfg(not(feature = "no-entrypoint"))]
mod entrypoint {
    use solana_program::{account_info::AccountInfo, entrypoint, entrypoint::ProgramResult, pubkey::Pubkey};

    entrypoint!(process_instruction);

    fn process_instruction(program_id: &Pubkey, accounts: &[AccountInfo], data: &[u8]) -> ProgramResult {
        crate::processor::process(program_id, accounts, data)
    }
}
