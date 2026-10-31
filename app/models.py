import enum
from sqlalchemy import Column, Integer, String, DateTime, BigInteger, Float, ForeignKey, Enum, JSON, UniqueConstraint, Index, text
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
    __table_args__ = (Index("ix_tasks_queue", "status", "available_at"),)

    id = Column(Integer, primary_key=True)
    # ForeignKey links the task to a specific telegram_id in users table
    user_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    chat_id = Column(BigInteger, index=True, nullable=True)
    file_id = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    source_identity = Column(String, nullable=True)
    reserved_seconds = Column(Integer, nullable=False, default=0, server_default="0")
    progress = Column(Integer, nullable=False, default=0, server_default="0")
    source_size = Column(BigInteger, nullable=True)
    generation = Column(Integer, nullable=False, default=0, server_default="0")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    lease_token = Column(String, nullable=True)
    lease_until = Column(DateTime, nullable=True)
    available_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    status_message_id = Column(BigInteger, nullable=True)
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


class Event(Base):
    __tablename__ = 'events'
    id = Column(Integer, primary_key=True)
    kind = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=text('CURRENT_TIMESTAMP'))
    processed_at = Column(DateTime, nullable=True, index=True)


class Delivery(Base):
    __tablename__ = 'notification_deliveries'
    __table_args__ = (UniqueConstraint('event_id', 'chat_id', name='uq_delivery_recipient'),)
    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey('events.id'), nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    status = Column(String, nullable=False, default='pending', index=True)
    attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    lease_token = Column(String, nullable=True)
    lease_until = Column(DateTime, nullable=True)
    last_error = Column(String, nullable=True)
    message_id = Column(BigInteger, nullable=True)


class MediaAttempt(Base):
    __tablename__ = 'media_attempts'
    id = Column(String, primary_key=True)
    task_id = Column(Integer, ForeignKey('tasks.id'), nullable=False, index=True)
    task_token = Column(String, nullable=False)
    directory = Column(String, nullable=False, unique=True)
    source_key = Column(String, nullable=False)
    state = Column(String, nullable=False, default='active')
    telegram_acquiring = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class MediaFile(Base):
    __tablename__ = 'media_files'
    __table_args__ = (UniqueConstraint('path', 'generation', name='uq_media_file_generation'),
                      Index('ix_media_files_cleanup', 'scope', 'state', 'available_at'))
    id = Column(Integer, primary_key=True)
    scope = Column(String, nullable=False)  # workspace or telegram
    kind = Column(String, nullable=False)
    path = Column(String, nullable=False)
    generation = Column(String, nullable=False)
    fingerprint = Column(String, nullable=True)
    state = Column(String, nullable=False, default='creating')
    attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    lease_token = Column(String, nullable=True)
    lease_until = Column(DateTime, nullable=True)
    last_error = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)


class MediaFileUse(Base):
    __tablename__ = 'media_file_uses'
    file_id = Column(Integer, ForeignKey('media_files.id'), primary_key=True)
    attempt_id = Column(String, ForeignKey('media_attempts.id'), primary_key=True)
    released_at = Column(DateTime, nullable=True)


class MediaCleanupEpoch(Base):
    __tablename__ = 'media_cleanup_epoch'
    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
