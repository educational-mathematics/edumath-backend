from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from app.schemas.user import UserCreate, UserLogin, UserOut
from app.schemas.auth import Token
from app.models.user import User
from app.security import get_password_hash, verify_password, create_access_token
from app.deps import get_db
from app.services.email import send_email_code
from app.models.email_code import EmailCode, CodePurpose
from datetime import datetime, timedelta, timezone
import secrets

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/register", response_model=UserOut, status_code=201)
def register(payload: UserCreate, db: Session = Depends(get_db)):

    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="Email ya registrado")

    vak_scores_dict = payload.vak_scores.model_dump() if payload.vak_scores else None

    user = User(
        email=payload.email,
        name=payload.name,
        password=get_password_hash(payload.password),   # guarda HASH
        first_login_done=payload.first_login_done,
        vak_style=payload.vak_style,
        vak_scores=vak_scores_dict,
        test_answered_by=payload.test_answered_by,
        test_date=payload.test_date,
        email_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    
    # Enviar código de verificación (120s)
    code = f"{secrets.randbelow(10**6):06d}"
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=120)
    db.add(EmailCode(email=user.email, code=code, purpose=CodePurpose.register, expires_at=expires_at))
    db.commit()
    send_email_code(to_email=user.email, code=code, purpose="Verificación de registro")

    return user

@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(),
            db: Session = Depends(get_db)):
    email = form_data.username
    password = form_data.password

    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Email o contraseña incorrectos")
    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, 
                            detail="Verifica tu correo para iniciar sesión")
    
    return {"access_token": create_access_token(subject=user.email),
            "token_type": "bearer"}