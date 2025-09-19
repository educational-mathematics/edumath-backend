from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.user import User as UserModel
from app.schemas.user import UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])

@router.get("/me", response_model=UserOut)
def read_me(current_user: UserModel = Depends(get_current_user)):
    return current_user

@router.get("/", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user)):
    # TODO: validar admin si quieres restringir
    return db.query(UserModel).all()

@router.put("/me", response_model=UserOut)
def update_me(payload: UserUpdate, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user)):
    if payload.name is not None:
        current_user.name = payload.name
    if payload.first_login_done is not None:
        current_user.first_login_done = payload.first_login_done
    if payload.vak_style is not None:
        current_user.vak_style = payload.vak_style
    if payload.vak_scores is not None:
        current_user.vak_scores = payload.vak_scores.model_dump() if hasattr(payload.vak_scores, "model_dump") else payload.vak_scores
    if payload.test_answered_by is not None:
        current_user.test_answered_by = payload.test_answered_by
    if payload.test_date is not None:
        current_user.test_date = payload.test_date
    if payload.avatar_url is not None:
        current_user.avatar_url = payload.avatar_url

    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user
