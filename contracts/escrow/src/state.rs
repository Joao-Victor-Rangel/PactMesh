//! Escrow account layout and the pure state machine (no Solana runtime needed).

pub const SEED: &[u8] = b"pactmesh-escrow";
pub const VERSION: u8 = 1;
/// version u8 | state u8 | bump u8 | payer 32 | payee 32 | amount u64 LE | agreement_hash 32 | deadline i64 LE
pub const LEN: usize = 1 + 1 + 1 + 32 + 32 + 8 + 32 + 8;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum State {
    Created = 0,
    Funded = 1,
    Released = 2,
    Refunded = 3,
    Disputed = 4,
}

impl State {
    pub fn from_u8(v: u8) -> Option<State> {
        Some(match v {
            0 => State::Created,
            1 => State::Funded,
            2 => State::Released,
            3 => State::Refunded,
            4 => State::Disputed,
            _ => return None,
        })
    }
    pub fn is_terminal(self) -> bool {
        matches!(self, State::Released | State::Refunded)
    }
}

/// Error codes are stable: they appear as `Custom(n)` on chain.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u32)]
pub enum EscrowError {
    WrongAuthority = 1,
    BadState = 2,
    PayeeMismatch = 3,
    AmountMismatch = 4,
    DeadlineNotReached = 5,
    Terminal = 6,
    InvalidPda = 7,
    InvalidInstruction = 8,
    AlreadyExists = 9,
    InvalidAccountData = 10,
    BadAmount = 11,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Escrow {
    pub state: State,
    pub bump: u8,
    pub payer: [u8; 32],
    pub payee: [u8; 32],
    pub amount: u64,
    pub agreement_hash: [u8; 32],
    pub deadline: i64,
}

impl Escrow {
    pub fn pack(&self, dst: &mut [u8]) -> Result<(), EscrowError> {
        if dst.len() < LEN {
            return Err(EscrowError::InvalidAccountData);
        }
        dst[0] = VERSION;
        dst[1] = self.state as u8;
        dst[2] = self.bump;
        dst[3..35].copy_from_slice(&self.payer);
        dst[35..67].copy_from_slice(&self.payee);
        dst[67..75].copy_from_slice(&self.amount.to_le_bytes());
        dst[75..107].copy_from_slice(&self.agreement_hash);
        dst[107..115].copy_from_slice(&self.deadline.to_le_bytes());
        Ok(())
    }

    pub fn unpack(src: &[u8]) -> Result<Escrow, EscrowError> {
        if src.len() < LEN || src[0] != VERSION {
            return Err(EscrowError::InvalidAccountData);
        }
        let a32 = |r: core::ops::Range<usize>| -> [u8; 32] { src[r].try_into().unwrap() };
        Ok(Escrow {
            state: State::from_u8(src[1]).ok_or(EscrowError::InvalidAccountData)?,
            bump: src[2],
            payer: a32(3..35),
            payee: a32(35..67),
            amount: u64::from_le_bytes(src[67..75].try_into().unwrap()),
            agreement_hash: a32(75..107),
            deadline: i64::from_le_bytes(src[107..115].try_into().unwrap()),
        })
    }
}

/// Lamport movement implied by a transition.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Move {
    None,
    PayerToEscrow(u64),
    EscrowToPayee(u64),
    EscrowToPayer(u64),
}

#[derive(Clone, Copy, Debug)]
pub enum Action {
    Fund { amount: u64 },
    Release { payee: [u8; 32] },
    Refund { now: i64 },
    Dispute,
}

/// The whole escrow policy in one pure function. Terminal states never move.
pub fn apply(e: &Escrow, signer: &[u8; 32], action: Action) -> Result<(State, Move), EscrowError> {
    if e.state.is_terminal() {
        return Err(EscrowError::Terminal);
    }
    match action {
        Action::Fund { amount } => {
            if signer != &e.payer {
                return Err(EscrowError::WrongAuthority);
            }
            if e.state != State::Created {
                return Err(EscrowError::BadState);
            }
            if amount != e.amount {
                return Err(EscrowError::AmountMismatch);
            }
            Ok((State::Funded, Move::PayerToEscrow(amount)))
        }
        Action::Release { payee } => {
            if signer != &e.payer {
                return Err(EscrowError::WrongAuthority);
            }
            if e.state != State::Funded {
                return Err(EscrowError::BadState);
            }
            if payee != e.payee {
                return Err(EscrowError::PayeeMismatch);
            }
            Ok((State::Released, Move::EscrowToPayee(e.amount)))
        }
        Action::Refund { now } => {
            if signer != &e.payer {
                return Err(EscrowError::WrongAuthority);
            }
            if now < e.deadline {
                return Err(EscrowError::DeadlineNotReached);
            }
            match e.state {
                State::Created => Ok((State::Refunded, Move::None)),
                State::Funded => Ok((State::Refunded, Move::EscrowToPayer(e.amount))),
                _ => Err(EscrowError::BadState),
            }
        }
        Action::Dispute => {
            if signer != &e.payer && signer != &e.payee {
                return Err(EscrowError::WrongAuthority);
            }
            if e.state != State::Funded {
                return Err(EscrowError::BadState);
            }
            Ok((State::Disputed, Move::None))
        }
    }
}
