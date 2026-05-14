from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.models import Base
from app.config import Config
import logging

logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        self.engine = None
        self.SessionLocal = None

    def init(self):
        self.engine = create_engine(
            f"sqlite:///{Config.DB_PATH}",
            connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(
            bind=self.engine, autocommit=False, autoflush=False
        )
        logger.info("Database initialised at %s", Config.DB_PATH)

    def get_session(self) -> Session:
        return self.SessionLocal()

    def close(self):
        if self.engine:
            self.engine.dispose()
