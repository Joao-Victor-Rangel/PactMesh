//! Regenerates fixtures.json: `cargo run --example fixture > fixtures.json`
use cripito_escrow::instruction::EscrowInstruction;
use cripito_escrow::processor::escrow_address;
use solana_program::pubkey::Pubkey;

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn main() {
    let program_id = [0x5cu8; 32];
    let mut agreement_hash = [0u8; 32];
    for (i, b) in agreement_hash.iter_mut().enumerate() {
        *b = (i as u8).wrapping_mul(37).wrapping_add(11);
    }
    let payee = [0x22u8; 32];
    let (amount, deadline) = (78_000_000u64, 1_791_381_420i64);
    let (pda, bump) = escrow_address(&Pubkey::new_from_array(program_id), &agreement_hash);
    let data = EscrowInstruction::Create { agreement_hash, payee, amount, deadline }.pack();
    println!("{{");
    println!("  \"program_id_hex\": \"{}\",", hex(&program_id));
    println!("  \"agreement_hash\": \"{}\",", hex(&agreement_hash));
    println!("  \"payee_hex\": \"{}\",", hex(&payee));
    println!("  \"amount\": {amount},");
    println!("  \"deadline\": {deadline},");
    println!("  \"escrow_pda_hex\": \"{}\",", hex(&pda.to_bytes()));
    println!("  \"escrow_pda_base58\": \"{pda}\",");
    println!("  \"bump\": {bump},");
    println!("  \"create_data_hex\": \"{}\"", hex(&data));
    println!("}}");
}
