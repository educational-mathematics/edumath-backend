from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, Index
from sqlalchemy.sql import func
from enum import Enum as PyEnum
from app.db import Base

class CodePurpose(str, PyEnum):
    register = "register"
    reset_password = "reset_password"

class EmailCode(Base):
    __tablename__ = "email_codes"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), index=True, nullable=False)
    code = Column(String(6), nullable=False)  # 6 dígitos
    purpose = Column(Enum(CodePurpose), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_email_purpose_active", "email", "purpose"),
    )
