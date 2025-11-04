from pydantic import BaseModel, EmailStr
from typing import Literal

class SendCodeIn(BaseModel):
    email: EmailStr
    purpose: Literal["register", "reset_password"]

class VerifyCodeIn(BaseModel):
    email: EmailStr
    code: str
    purpose: Literal["register", "reset_password"]

class SimpleMsg(BaseModel):
    message: str
