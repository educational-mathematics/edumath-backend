from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from datetime import datetime, timezone

from app.deps import get_db
from app.models.user import User
from app.models.email_code import EmailCode, CodePurpose
from app.security import get_password_hash

router = APIRouter(prefix="/auth", tags=["auth"])

class ForgotIn(BaseModel):
    email: EmailStr

class ResetIn(BaseModel):
    email: EmailStr
    code: str
    new_password: str

@router.post("/forgot", status_code=200)
def forgot_password(payload: ForgotIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user:
        # No reveles si existe o no
        return {"message": "Si el correo existe, se enviará un código"}
    # Reutiliza /verification/send en el frontend, pero dejamos compatibilidad:
    from app.routers.verification import send_code, SendCodeIn  # evitar duplicar lógica
    send_code(SendCodeIn(email=payload.email, purpose="reset_password"), db)  # type: ignore
    return {"message": "Si el correo existe, se enviará un código"}

@router.post("/reset", status_code=200)
def reset_password(payload: ResetIn, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    row = db.query(EmailCode).filter(
        EmailCode.email == payload.email,
        EmailCode.purpose == CodePurpose.reset_password,
        EmailCode.consumed == False,
        EmailCode.expires_at > now
    ).order_by(EmailCode.created_at.desc()).first()

    if not row or row.code != payload.code:
        raise HTTPException(status_code=400, detail="Código inválido o expirado")

    user = db.query(User).filter(User.email == payload.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    user.password = get_password_hash(payload.new_password)
    row.consumed = True
    db.commit()
    return {"message": "Contraseña actualizada"}
