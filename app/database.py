import logging

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Config
from app.models import Base

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, url: str = None):
        self.url = url or f"sqlite:///{Config.DB_PATH}"
        self.engine = None
        self.SessionLocal = None

    def init(self):
        self.engine = create_engine(
            self.url, connect_args={"check_same_thread": False, "timeout": 15}
        )

        @event.listens_for(self.engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            # WAL lets the AI worker write while the UI reads.
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=15000")
            cur.close()

        Base.metadata.create_all(self.engine)
        self._add_missing_columns()
        self._migrate_data()
        self.SessionLocal = sessionmaker(
            bind=self.engine, autocommit=False, autoflush=False,
            expire_on_commit=False,
        )
        logger.info("Database initialised at %s", self.url)

    def _add_missing_columns(self):
        """Upgrade databases created by older versions (create_all never alters)."""
        insp = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                existing = {c["name"] for c in insp.get_columns(table.name)}
                for col in table.columns:
                    if col.name in existing:
                        continue
                    col_type = col.type.compile(self.engine.dialect)
                    default = ""
                    if col.default is not None and col.default.is_scalar:
                        value = col.default.arg
                        if isinstance(value, bool):
                            value = int(value)
                        default = f" DEFAULT {value!r}" if isinstance(value, str) \
                            else f" DEFAULT {value}"
                    conn.execute(text(
                        f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}{default}'
                    ))
                    logger.info("Added column %s.%s", table.name, col.name)

    def _migrate_data(self):
        with self.engine.begin() as conn:
            # v1.1 had only an on/off recording flag.
            conn.execute(text("UPDATE cameras SET recording_mode='continuous' "
                              "WHERE recording_enabled=1 AND "
                              "(recording_mode IS NULL OR recording_mode='off')"))

    def get_session(self) -> Session:
        return self.SessionLocal()

    def close(self):
        if self.engine:
            self.engine.dispose()
