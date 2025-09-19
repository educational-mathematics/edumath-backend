from pydantic import BaseModel, EmailStr, ConfigDict
from typing import Optional, Literal
from datetime import datetime

class VakScores(BaseModel):
    visual: int
    auditivo: int
    kinestesico: int

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str
    first_login_done: bool = False
    vak_style: Optional[Literal['visual','auditivo','kinestesico']] = None
    vak_scores: Optional[VakScores] = None
    test_answered_by: Optional[Literal['alumno','representante']] = None
    test_date: Optional[datetime] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserOut(BaseModel):
    id: int
    email: EmailStr
    name: str
    first_login_done: bool
    vak_style: Optional[Literal['visual','auditivo','kinestesico']] = None
    vak_scores: Optional[VakScores] = None
    test_answered_by: Optional[Literal['alumno','representante']] = None
    test_date: Optional[datetime] = None
    email_verified: bool
    avatar_url: Optional[str] = None
    # IMPORTANTE para devolver ORM:
    model_config = ConfigDict(from_attributes=True)
    
class UserUpdate(BaseModel):
    name: Optional[str] = None
    first_login_done: Optional[bool] = None
    vak_style: Optional[Literal['visual','auditivo','kinestesico']] = None
    vak_scores: Optional[VakScores] = None
    test_answered_by: Optional[Literal['alumno','representante']] = None
    test_date: Optional[datetime] = None
    avatar_url: Optional[str] = None