from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.user import User as UserModel
from app.schemas.user import UserOut

router = APIRouter(prefix="/users", tags=["users"])

@router.get("/me", response_model=UserOut)
def read_me(current_user: UserModel = Depends(get_current_user)):
    return current_user

@router.get("/", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user)):
    # TODO: validar admin si quieres restringir
    return db.query(UserModel).all()
