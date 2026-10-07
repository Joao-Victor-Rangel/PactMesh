//! Account handling. All decisions are delegated to `state::apply`.

#[allow(deprecated)]
use solana_program::{
    account_info::{next_account_info, AccountInfo},
    clock::Clock,
    entrypoint::ProgramResult,
    msg,
    program::{invoke, invoke_signed},
    program_error::ProgramError,
    pubkey::Pubkey,
    rent::Rent,
    system_instruction, system_program,
    sysvar::Sysvar,
};

use crate::instruction::EscrowInstruction;
use crate::state::{apply, Action, Escrow, EscrowError, Move, State, LEN, SEED};

impl From<EscrowError> for ProgramError {
    fn from(e: EscrowError) -> Self {
        ProgramError::Custom(e as u32)
    }
}

pub fn escrow_address(program_id: &Pubkey, agreement_hash: &[u8; 32]) -> (Pubkey, u8) {
    Pubkey::find_program_address(&[SEED, agreement_hash], program_id)
}

fn load(program_id: &Pubkey, escrow: &AccountInfo) -> Result<Escrow, ProgramError> {
    if escrow.owner != program_id {
        return Err(EscrowError::InvalidAccountData.into());
    }
    let e = Escrow::unpack(&escrow.try_borrow_data()?)?;
    let expected = Pubkey::create_program_address(&[SEED, &e.agreement_hash, &[e.bump]], program_id)
        .map_err(|_| EscrowError::InvalidPda)?;
    if expected != *escrow.key {
        return Err(EscrowError::InvalidPda.into());
    }
    Ok(e)
}

fn store(escrow: &AccountInfo, e: &Escrow) -> ProgramResult {
    e.pack(&mut escrow.try_borrow_mut_data()?)?;
    Ok(())
}

fn move_from_escrow(escrow: &AccountInfo, to: &AccountInfo, amount: u64) -> ProgramResult {
    // The escrow PDA is owned by this program, so it can debit it directly.
    let from = escrow.lamports().checked_sub(amount).ok_or(ProgramError::InsufficientFunds)?;
    let dest = to.lamports().checked_add(amount).ok_or(ProgramError::ArithmeticOverflow)?;
    **escrow.try_borrow_mut_lamports()? = from;
    **to.try_borrow_mut_lamports()? = dest;
    Ok(())
}

pub fn process(program_id: &Pubkey, accounts: &[AccountInfo], data: &[u8]) -> ProgramResult {
    let ix = EscrowInstruction::unpack(data)?;
    let it = &mut accounts.iter();
    let signer = next_account_info(it)?;
    if !signer.is_signer {
        return Err(EscrowError::WrongAuthority.into());
    }
    let escrow = next_account_info(it)?;

    match ix {
        EscrowInstruction::Create { agreement_hash, payee, amount, deadline } => {
            let sys = next_account_info(it)?;
            if *sys.key != system_program::id() {
                return Err(ProgramError::IncorrectProgramId);
            }
            if amount == 0 {
                return Err(EscrowError::BadAmount.into());
            }
            let (pda, bump) = escrow_address(program_id, &agreement_hash);
            if pda != *escrow.key {
                return Err(EscrowError::InvalidPda.into());
            }
            if escrow.lamports() > 0 || !escrow.data_is_empty() {
                return Err(EscrowError::AlreadyExists.into());
            }
            let lamports = Rent::get()?.minimum_balance(LEN);
            invoke_signed(
                &system_instruction::create_account(signer.key, escrow.key, lamports, LEN as u64, program_id),
                &[signer.clone(), escrow.clone(), sys.clone()],
                &[&[SEED, &agreement_hash, &[bump]]],
            )?;
            let e = Escrow {
                state: State::Created,
                bump,
                payer: signer.key.to_bytes(),
                payee,
                amount,
                agreement_hash,
                deadline,
            };
            store(escrow, &e)?;
            msg!("cripito-escrow: CREATED");
        }
        EscrowInstruction::Fund { amount } => {
            let sys = next_account_info(it)?;
            let mut e = load(program_id, escrow)?;
            let (st, mv) = apply(&e, &signer.key.to_bytes(), Action::Fund { amount })?;
            if let Move::PayerToEscrow(a) = mv {
                invoke(
                    &system_instruction::transfer(signer.key, escrow.key, a),
                    &[signer.clone(), escrow.clone(), sys.clone()],
                )?;
            }
            e.state = st;
            store(escrow, &e)?;
            msg!("cripito-escrow: FUNDED");
        }
        EscrowInstruction::Release => {
            let payee = next_account_info(it)?;
            let mut e = load(program_id, escrow)?;
            let (st, mv) = apply(&e, &signer.key.to_bytes(), Action::Release { payee: payee.key.to_bytes() })?;
            if let Move::EscrowToPayee(a) = mv {
                move_from_escrow(escrow, payee, a)?;
            }
            e.state = st;
            store(escrow, &e)?;
            msg!("cripito-escrow: RELEASED");
        }
        EscrowInstruction::Refund => {
            let mut e = load(program_id, escrow)?;
            let now = Clock::get()?.unix_timestamp;
            let (st, mv) = apply(&e, &signer.key.to_bytes(), Action::Refund { now })?;
            if let Move::EscrowToPayer(a) = mv {
                move_from_escrow(escrow, signer, a)?;
            }
            e.state = st;
            store(escrow, &e)?;
            msg!("cripito-escrow: REFUNDED");
        }
        EscrowInstruction::Dispute => {
            let mut e = load(program_id, escrow)?;
            let (st, _) = apply(&e, &signer.key.to_bytes(), Action::Dispute)?;
            e.state = st;
            store(escrow, &e)?;
            msg!("cripito-escrow: DISPUTED");
        }
    }
    Ok(())
}
