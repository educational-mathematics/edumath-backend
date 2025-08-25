from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
import secrets

from app.deps import get_db
from app.models.email_code import EmailCode, CodePurpose
from app.models.user import User
from app.schemas.verification import SendCodeIn, VerifyCodeIn, SimpleMsg
from app.services.email import send_email_code

router = APIRouter(prefix="/verification", tags=["verification"])

def _active_code(db: Session, email: str, purpose: CodePurpose):
    now = datetime.now(timezone.utc)
    return db.query(EmailCode).filter(
        EmailCode.email == email,
        EmailCode.purpose == purpose,
        EmailCode.consumed == False,
        EmailCode.expires_at > now
    ).first()

@router.post("/send", response_model=SimpleMsg)
def send_code(payload: SendCodeIn, db: Session = Depends(get_db)):
    purpose = CodePurpose(payload.purpose)

    # Validaciones básicas
    if purpose == CodePurpose.register:
        u = db.query(User).filter(User.email == payload.email).first()
        if not u:
            raise HTTPException(status_code=404, detail="Regístrate primero para verificar tu correo")
        if u.email_verified:
            return {"message": "El correo ya está verificado"}

    code_row = _active_code(db, payload.email, purpose)
    # Generar siempre un nuevo código (invalidamos los activos previos)
    if code_row:
        code_row.consumed = True
        db.commit()

    code = f"{secrets.randbelow(10**6):06d}"
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=120)

    db.add(EmailCode(email=payload.email, code=code, purpose=purpose, expires_at=expires_at))
    db.commit()

    send_email_code(to_email=payload.email, code=code, purpose=("registro" if purpose==CodePurpose.register else "recuperación de contraseña"))
    return {"message": "Código enviado"}

@router.post("/verify", response_model=SimpleMsg)
def verify_code(payload: VerifyCodeIn, db: Session = Depends(get_db)):
    purpose = CodePurpose(payload.purpose)
    now = datetime.now(timezone.utc)
    row = db.query(EmailCode).filter(
        EmailCode.email == payload.email,
        EmailCode.purpose == purpose,
        EmailCode.consumed == False,
        EmailCode.expires_at > now
    ).order_by(EmailCode.created_at.desc()).first()

    if not row or row.code != payload.code:
        raise HTTPException(status_code=400, detail="Código inválido o expirado")

    row.consumed = True
    db.commit()

    if purpose == CodePurpose.register:
        user = db.query(User).filter(User.email == payload.email).first()
        if user:
            user.email_verified = True
            db.commit()

    return {"message": "Verificación exitosa"}
