import enum
from sqlalchemy import Column, Integer, String, DateTime, BigInteger, Float, ForeignKey, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base

# Define allowed plan types
class PlanType(str, enum.Enum):
    trial = "trial"
    plus = "plus"
    pro = "pro"
    admin = "admin"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    # Telegram ID as the unique identifier for the user
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    # Enum ensures only trial/plus/pro are allowed at the DB level
    plan = Column(Enum(PlanType), default=PlanType.trial, nullable=False)
    # Optional raw seconds field for future logic
    seconds = Column(Integer, nullable=True)
    reserved_seconds = Column(Integer, nullable=False, default=0, server_default="0")
    # Paid plan expiration (null for trial)
    paid_until = Column(DateTime, nullable=True)
    registered_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship to tasks
    tasks = relationship("Task", back_populates="user")
    payments = relationship("Payment", back_populates="user")

class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    # ForeignKey links the task to a specific telegram_id in users table
    user_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    chat_id = Column(BigInteger, index=True, nullable=True)
    file_id = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    reserved_seconds = Column(Integer, nullable=False, default=0, server_default="0")
    progress = Column(Integer, nullable=False, default=0, server_default="0")
    file_unique_id = Column(String, unique=True, index=True)
    status = Column(String, default="pending")
    result_text = Column(String, nullable=True)
    # Duration in seconds to track user limit consumption
    duration_seconds = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Link back to the user object
    user = relationship("User", back_populates="tasks")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True, nullable=False)
    plan = Column(Enum(PlanType), nullable=False)
    amount = Column(Integer, nullable=False)
    currency = Column(String, nullable=False)
    payload = Column(String, nullable=False)
    telegram_charge_id = Column(String, nullable=False, unique=True)
    provider_charge_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="payments")
