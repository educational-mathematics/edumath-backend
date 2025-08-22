from pydantic import BaseModel, EmailStr
from typing import Optional, Literal
from datetime import datetime

class VakScores(BaseModel):
    visual: int
    auditivo: int
    kinestesico: int

class UserBase(BaseModel):
    email: EmailStr
    name: str
    first_login_done: bool = False
    vak_style: Optional[Literal['visual','auditivo','kinestesico']] = None
    vak_scores: Optional[VakScores] = None
    test_answered_by: Optional[Literal['alumno','representante']] = None
    test_date: Optional[datetime] = None

class UserCreate(UserBase):
    password: str

class UserOut(UserBase):
    id: int

    class Config:
        from_attributes = True  # pydantic v2

class UserLogin(BaseModel):
    email: EmailStr
    password: str