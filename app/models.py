from sqlalchemy import (
    Column, Integer, String, Float, Boolean,
    DateTime, ForeignKey, Text, LargeBinary
)
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()


class Camera(Base):
    __tablename__ = "cameras"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    name            = Column(String(100), nullable=False)
    brand           = Column(String(50), nullable=False, default="Generic RTSP")
    camera_type     = Column(String(50), default="IP Camera")
    connection_type = Column(String(50), default="Wired (Ethernet)")
    host            = Column(String(150), nullable=False)
    port            = Column(Integer, default=554)
    http_port       = Column(Integer, default=80)
    username        = Column(String(100), default="admin")
    password        = Column(String(200), default="")
    channel         = Column(Integer, default=1)
    rtsp_url        = Column(String(600))
    use_substream   = Column(Boolean, default=False)
    onvif_enabled   = Column(Boolean, default=False)
    is_active       = Column(Boolean, default=True)
    location        = Column(String(200))
    group_name      = Column(String(100), default="Default")
    nvr_id          = Column(Integer, ForeignKey("nvrs.id"), nullable=True)
    face_detection      = Column(Boolean, default=False)
    attendance_tracking = Column(Boolean, default=False)
    expression_monitor  = Column(Boolean, default=False)
    safety_watch        = Column(Boolean, default=False)
    recording_enabled   = Column(Boolean, default=False)
    recording_mode      = Column(String(20), default="off")  # off | continuous | motion
    created_at      = Column(DateTime, default=datetime.now)
    updated_at      = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class NVR(Base):
    __tablename__ = "nvrs"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    name           = Column(String(100), nullable=False)
    brand          = Column(String(50), nullable=False, default="Hikvision")
    host           = Column(String(150), nullable=False)
    port           = Column(Integer, default=8000)
    http_port      = Column(Integer, default=80)
    username       = Column(String(100), default="admin")
    password       = Column(String(200), default="")
    total_channels = Column(Integer, default=4)
    is_active      = Column(Boolean, default=True)
    location       = Column(String(200))
    created_at     = Column(DateTime, default=datetime.now)
    cameras        = relationship("Camera", backref="nvr", foreign_keys=[Camera.nvr_id])


class Person(Base):
    __tablename__ = "persons"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    name          = Column(String(100), nullable=False)
    employee_id   = Column(String(50), unique=True, nullable=True)
    department    = Column(String(100))
    role          = Column(String(100))
    email         = Column(String(200))
    phone         = Column(String(20))
    photo_path    = Column(String(500))
    face_encoding = Column(LargeBinary)
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime, default=datetime.now)
    attendances   = relationship("AttendanceRecord", back_populates="person")


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    person_id     = Column(Integer, ForeignKey("persons.id"))
    camera_id     = Column(Integer, ForeignKey("cameras.id"), nullable=True)
    check_in      = Column(DateTime)
    check_out     = Column(DateTime)
    date          = Column(String(20))
    confidence    = Column(Float)
    snapshot_path = Column(String(500))
    person        = relationship("Person", back_populates="attendances")


class Alert(Base):
    __tablename__ = "alerts"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    camera_id        = Column(Integer, ForeignKey("cameras.id"), nullable=True)
    alert_type       = Column(String(50))
    description      = Column(Text)
    severity         = Column(String(20), default="medium")
    snapshot_path    = Column(String(500))
    is_acknowledged  = Column(Boolean, default=False)
    created_at       = Column(DateTime, default=datetime.now)


class ExpressionLog(Base):
    __tablename__ = "expression_logs"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    camera_id     = Column(Integer, ForeignKey("cameras.id"), nullable=True)
    person_id     = Column(Integer, ForeignKey("persons.id"), nullable=True)
    expression    = Column(String(50))
    confidence    = Column(Float)
    snapshot_path = Column(String(500))
    created_at    = Column(DateTime, default=datetime.now)


class Recording(Base):
    __tablename__ = "recordings"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    camera_id  = Column(Integer, ForeignKey("cameras.id"), nullable=False, index=True)
    path       = Column(String(600), nullable=False)
    kind       = Column(String(20), default="continuous")  # continuous | motion | manual
    start_time = Column(DateTime, nullable=False, index=True)
    end_time   = Column(DateTime)
    size_bytes = Column(Integer, default=0)


class Zone(Base):
    __tablename__ = "zones"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    camera_id  = Column(Integer, ForeignKey("cameras.id"), nullable=False)
    name       = Column(String(100), default="Restricted zone")
    points     = Column(Text, nullable=False)  # JSON [[x, y], ...] in frame pixels
    created_at = Column(DateTime, default=datetime.now)


class SafetyViolation(Base):
    __tablename__ = "safety_violations"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    camera_id      = Column(Integer, ForeignKey("cameras.id"), nullable=True)
    violation_type = Column(String(100))
    description    = Column(Text)
    snapshot_path  = Column(String(500))
    is_resolved    = Column(Boolean, default=False)
    created_at     = Column(DateTime, default=datetime.now)


class User(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    username      = Column(String(80), unique=True, nullable=False)
    full_name     = Column(String(120))
    password_hash = Column(String(300), nullable=False)
    role          = Column(String(20), nullable=False, default="viewer")
    is_active     = Column(Boolean, default=True)
    last_login    = Column(DateTime)
    created_at    = Column(DateTime, default=datetime.now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    username   = Column(String(80))
    action     = Column(String(60))
    details    = Column(Text)
