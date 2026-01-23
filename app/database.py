from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import DATABASE_URL

# Create SQLAlchemy engine
# pool_pre_ping=True helps to recover from lost connections (common in Docker)
engine = create_engine(
    DATABASE_URL, 
    pool_pre_ping=True
)

# Configure session factory
SessionLocal = sessionmaker(
    autocommit=False, 
    autoflush=False, 
    bind=engine
)

# Base class for database models
Base = declarative_base()