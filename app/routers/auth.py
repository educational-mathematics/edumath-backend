from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from app.schemas.user import UserCreate, UserLogin, UserOut
from app.schemas.auth import Token
from app.models.user import User
from app.security import get_password_hash, verify_password, create_access_token
from app.deps import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/register", response_model=UserOut, status_code=201)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    # Verifica email único
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="Email ya registrado")

    # Pydantic v2: usa model_dump() para convertir submodelos
    vak_scores_dict = payload.vak_scores.model_dump() if payload.vak_scores else None

    user = User(
        email=payload.email,
        name=payload.name,
        password=get_password_hash(payload.password),   # guarda HASH, no el texto plano
        first_login_done=payload.first_login_done,
        vak_style=payload.vak_style,
        vak_scores=vak_scores_dict,
        test_answered_by=payload.test_answered_by,
        test_date=payload.test_date,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(),
            db: Session = Depends(get_db)):
    email = form_data.username      # usamos el email en el campo username
    password = form_data.password

    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Email o contraseña incorrectos")
    return {"access_token": create_access_token(subject=user.email),
            "token_type": "bearer"}