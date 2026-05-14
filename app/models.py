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
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    created_at     = Column(DateTime, default=datetime.utcnow)
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
    created_at    = Column(DateTime, default=datetime.utcnow)
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
    created_at       = Column(DateTime, default=datetime.utcnow)


class ExpressionLog(Base):
    __tablename__ = "expression_logs"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    camera_id     = Column(Integer, ForeignKey("cameras.id"), nullable=True)
    person_id     = Column(Integer, ForeignKey("persons.id"), nullable=True)
    expression    = Column(String(50))
    confidence    = Column(Float)
    snapshot_path = Column(String(500))
    created_at    = Column(DateTime, default=datetime.utcnow)


class SafetyViolation(Base):
    __tablename__ = "safety_violations"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    camera_id      = Column(Integer, ForeignKey("cameras.id"), nullable=True)
    violation_type = Column(String(100))
    description    = Column(Text)
    snapshot_path  = Column(String(500))
    is_resolved    = Column(Boolean, default=False)
    created_at     = Column(DateTime, default=datetime.utcnow)
