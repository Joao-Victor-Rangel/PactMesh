//! Instruction encoding: 1-byte tag followed by fixed little-endian fields.
//!
//! | tag | name    | data                                                     | accounts                                   |
//! |-----|---------|----------------------------------------------------------|--------------------------------------------|
//! | 0   | Create  | agreement_hash[32] payee[32] amount u64 deadline i64     | payer (s,w), escrow PDA (w), system program |
//! | 1   | Fund    | amount u64                                               | payer (s,w), escrow PDA (w), system program |
//! | 2   | Release | -                                                        | payer (s), escrow PDA (w), payee (w)        |
//! | 3   | Refund  | -                                                        | payer (s,w), escrow PDA (w)                 |
//! | 4   | Dispute | -                                                        | payer or payee (s), escrow PDA (w)          |

use crate::state::EscrowError;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum EscrowInstruction {
    Create { agreement_hash: [u8; 32], payee: [u8; 32], amount: u64, deadline: i64 },
    Fund { amount: u64 },
    Release,
    Refund,
    Dispute,
}

impl EscrowInstruction {
    pub fn unpack(data: &[u8]) -> Result<Self, EscrowError> {
        let (&tag, rest) = data.split_first().ok_or(EscrowError::InvalidInstruction)?;
        let bad = EscrowError::InvalidInstruction;
        Ok(match tag {
            0 => {
                if rest.len() != 80 {
                    return Err(bad);
                }
                EscrowInstruction::Create {
                    agreement_hash: rest[0..32].try_into().unwrap(),
                    payee: rest[32..64].try_into().unwrap(),
                    amount: u64::from_le_bytes(rest[64..72].try_into().unwrap()),
                    deadline: i64::from_le_bytes(rest[72..80].try_into().unwrap()),
                }
            }
            1 => {
                if rest.len() != 8 {
                    return Err(bad);
                }
                EscrowInstruction::Fund { amount: u64::from_le_bytes(rest.try_into().unwrap()) }
            }
            2 if rest.is_empty() => EscrowInstruction::Release,
            3 if rest.is_empty() => EscrowInstruction::Refund,
            4 if rest.is_empty() => EscrowInstruction::Dispute,
            _ => return Err(bad),
        })
    }

    pub fn pack(&self) -> Vec<u8> {
        match self {
            EscrowInstruction::Create { agreement_hash, payee, amount, deadline } => {
                let mut v = vec![0u8];
                v.extend_from_slice(agreement_hash);
                v.extend_from_slice(payee);
                v.extend_from_slice(&amount.to_le_bytes());
                v.extend_from_slice(&deadline.to_le_bytes());
                v
            }
            EscrowInstruction::Fund { amount } => {
                let mut v = vec![1u8];
                v.extend_from_slice(&amount.to_le_bytes());
                v
            }
            EscrowInstruction::Release => vec![2],
            EscrowInstruction::Refund => vec![3],
            EscrowInstruction::Dispute => vec![4],
        }
    }
}
