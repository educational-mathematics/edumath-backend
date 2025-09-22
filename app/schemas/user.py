from pydantic import BaseModel, EmailStr, ConfigDict, field_validator
from typing import Optional, Literal, List
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
    points: int = 0
    alias: Optional[str] = None
    badges: Optional[List[str]] = None
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
    
class AliasIn(BaseModel):
    alias: str

    @field_validator('alias')
    @classmethod
    def validate_alias(cls, v: str) -> str:
        v = v.strip()
        if not (3 <= len(v) <= 32):
            raise ValueError("El alias debe tener entre 3 y 32 caracteres")
        if not all(c.isalnum() or c in ('_', '-', '.') for c in v):
            raise ValueError("Solo letras, números, _ - .")
        return v