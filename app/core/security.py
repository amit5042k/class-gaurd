"""Users, roles, audit log and encryption of stored camera passwords."""
import base64
import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from app.config import Config

logger = logging.getLogger(__name__)

ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER = "admin", "operator", "viewer"
ROLES = [ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER]
ROLE_LABELS = {ROLE_ADMIN: "Administrator", ROLE_OPERATOR: "Operator", ROLE_VIEWER: "Viewer"}

# Pages each role may open (see Sidebar keys).
PAGES = {
    ROLE_VIEWER: {"dashboard", "live_view", "attendance", "alerts", "recordings"},
    ROLE_OPERATOR: {"dashboard", "live_view", "attendance", "alerts", "recordings",
                    "face_enrollment", "expression", "safety"},
    ROLE_ADMIN: {"dashboard", "live_view", "attendance", "alerts", "recordings",
                 "face_enrollment", "expression", "safety", "cameras", "nvr",
                 "settings", "users"},
}
MIN_PASSWORD_LENGTH = 8
PBKDF2_ITERATIONS = 260_000


# ----------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt),
                                   int(iters))
        return hmac.compare_digest(calc, base64.b64decode(digest))
    except Exception:
        return False


def password_problem(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if password.isdigit() or password.isalpha():
        return "Use a mix of letters and numbers."
    return ""


# --------------------------------------------------------- secret storage
_ENC_PREFIX = "enc1:"
_fernet = None
_fernet_lock = threading.Lock()


def key_path() -> str:
    return os.path.join(Config.DATA_DIR, "secret.key")


def _get_fernet():
    global _fernet
    with _fernet_lock:
        if _fernet is not None and getattr(_fernet, "_path", None) == key_path():
            return _fernet
        from cryptography.fernet import Fernet
        path = key_path()
        if os.path.exists(path):
            with open(path, "rb") as f:
                key = f.read().strip()
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            key = Fernet.generate_key()
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key)
        _fernet = Fernet(key)
        _fernet._path = path
        return _fernet


def reset_key_cache():
    global _fernet
    with _fernet_lock:
        _fernet = None


def encrypt_secret(value: Optional[str]) -> Optional[str]:
    if not value or value.startswith(_ENC_PREFIX):
        return value
    return _ENC_PREFIX + _get_fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: Optional[str]) -> str:
    if not value:
        return ""
    if not value.startswith(_ENC_PREFIX):
        return value  # stored before encryption was introduced
    try:
        return _get_fernet().decrypt(value[len(_ENC_PREFIX):].encode()).decode()
    except Exception:
        logger.warning("A stored password could not be decrypted (secret.key changed?)")
        return ""


def is_encrypted(value: Optional[str]) -> bool:
    return bool(value) and value.startswith(_ENC_PREFIX)


# ------------------------------------------------------------------ session
@dataclass
class CurrentUser:
    id: int
    username: str
    role: str
    full_name: str = ""

    def can(self, page: str) -> bool:
        return page in PAGES.get(self.role, set())

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def can_operate(self) -> bool:
        return self.role in (ROLE_ADMIN, ROLE_OPERATOR)


class Session:
    current: Optional[CurrentUser] = None


def current_username() -> str:
    return Session.current.username if Session.current else "system"


# --------------------------------------------------------------------- users
class UserService:
    MAX_ATTEMPTS = 5
    LOCKOUT_SECS = 60

    def __init__(self, db):
        self.db = db
        self._failures: Dict[str, list] = {}

    def has_users(self) -> bool:
        from app.models import User
        s = self.db.get_session()
        try:
            return s.query(User).count() > 0
        finally:
            s.close()

    def create(self, username: str, password: str, role: str, full_name: str = ""):
        from app.models import User
        username = username.strip().lower()
        if not username:
            raise ValueError("Enter a username.")
        problem = password_problem(password)
        if problem:
            raise ValueError(problem)
        if role not in ROLES:
            raise ValueError("Unknown role.")
        s = self.db.get_session()
        try:
            if s.query(User).filter_by(username=username).first():
                raise ValueError("That username is already taken.")
            u = User(username=username, password_hash=hash_password(password), role=role,
                     full_name=full_name.strip())
            s.add(u)
            s.commit()
            return u.id
        finally:
            s.close()

    def locked_for(self, username: str) -> int:
        times = [t for t in self._failures.get(username.lower(), [])
                 if time.monotonic() - t < self.LOCKOUT_SECS]
        self._failures[username.lower()] = times
        if len(times) >= self.MAX_ATTEMPTS:
            return int(self.LOCKOUT_SECS - (time.monotonic() - times[0])) + 1
        return 0

    def authenticate(self, username: str, password: str) -> Optional[CurrentUser]:
        from datetime import datetime
        from app.models import User
        name = username.strip().lower()
        if self.locked_for(name):
            audit(self.db, "login_blocked", f"Sign-in for '{name}' refused: too many "
                                            "wrong attempts", username=name)
            return None
        s = self.db.get_session()
        try:
            u = s.query(User).filter_by(username=name).first()
            if u and u.is_active and verify_password(password, u.password_hash):
                u.last_login = datetime.now()
                s.commit()
                self._failures.pop(name, None)
                audit(self.db, "login", f"{name} signed in", username=name)
                return CurrentUser(u.id, u.username, u.role, u.full_name or "")
        finally:
            s.close()
        self._failures.setdefault(name, []).append(time.monotonic())
        audit(self.db, "login_failed", f"Failed sign-in for '{name}'", username=name)
        return None

    def list(self):
        from app.models import User
        s = self.db.get_session()
        try:
            return [{"id": u.id, "username": u.username, "full_name": u.full_name or "",
                     "role": u.role, "is_active": u.is_active, "last_login": u.last_login}
                    for u in s.query(User).order_by(User.username)]
        finally:
            s.close()

    def _active_admins(self, s, exclude_id=None) -> int:
        from app.models import User
        q = s.query(User).filter(User.role == ROLE_ADMIN, User.is_active.is_(True))
        if exclude_id is not None:
            q = q.filter(User.id != exclude_id)
        return q.count()

    def update(self, user_id: int, role: str = None, is_active: bool = None,
               full_name: str = None):
        from app.models import User
        s = self.db.get_session()
        try:
            u = s.get(User, user_id)
            if not u:
                return
            demoting = (role and role != ROLE_ADMIN) or is_active is False
            if u.role == ROLE_ADMIN and demoting and self._active_admins(s, u.id) == 0:
                raise ValueError("There must always be at least one active administrator.")
            if role:
                u.role = role
            if is_active is not None:
                u.is_active = is_active
            if full_name is not None:
                u.full_name = full_name.strip()
            s.commit()
        finally:
            s.close()

    def set_password(self, user_id: int, new_password: str):
        from app.models import User
        problem = password_problem(new_password)
        if problem:
            raise ValueError(problem)
        s = self.db.get_session()
        try:
            u = s.get(User, user_id)
            if u:
                u.password_hash = hash_password(new_password)
                s.commit()
        finally:
            s.close()

    def check_password(self, user_id: int, password: str) -> bool:
        from app.models import User
        s = self.db.get_session()
        try:
            u = s.get(User, user_id)
            return bool(u and verify_password(password, u.password_hash))
        finally:
            s.close()

    def delete(self, user_id: int):
        from app.models import User
        s = self.db.get_session()
        try:
            u = s.get(User, user_id)
            if not u:
                return
            if u.role == ROLE_ADMIN and self._active_admins(s, u.id) == 0:
                raise ValueError("You can't delete the last administrator.")
            s.delete(u)
            s.commit()
        finally:
            s.close()


# --------------------------------------------------------------------- audit
def audit(db, action: str, details: str = "", username: str = None):
    """Record who did what. Never raises: auditing must not break the feature."""
    if db is None:
        return
    from app.models import AuditLog
    s = db.get_session()
    try:
        s.add(AuditLog(username=username or current_username(), action=action,
                       details=details[:1000]))
        s.commit()
    except Exception:
        logger.exception("Could not write audit log")
        s.rollback()
    finally:
        s.close()
