import json
import logging
import os
import sys
from urllib.parse import quote

logger = logging.getLogger(__name__)

_URL_BREAKING = "%@:/#? "


def _escape(value: str) -> str:
    # Only escape characters that break URL parsing, so ordinary passwords
    # are sent unchanged to cameras whose RTSP stack doesn't decode %XX.
    return "".join(quote(c, safe="") if c in _URL_BREAKING else c for c in value)


def resource_path(*parts: str) -> str:
    """Path to a bundled file, both from source and inside a PyInstaller build."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, *parts)


class Config:
    APP_NAME = "ClassGuard - Camera Management System"
    VERSION = "2.0.4"

    DATA_DIR = os.path.join(os.path.expanduser("~"), ".classguard")
    DB_PATH = os.path.join(DATA_DIR, "classguard.db")
    FACES_DIR = os.path.join(DATA_DIR, "faces")
    RECORDINGS_DIR = os.path.join(DATA_DIR, "recordings")
    SNAPSHOTS_DIR = os.path.join(DATA_DIR, "snapshots")
    LOGS_DIR = os.path.join(DATA_DIR, "logs")
    SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

    CAMERA_BRANDS = ["Hikvision", "Dahua", "UNV", "CP Plus", "ONVIF", "Generic RTSP"]
    CAMERA_TYPES = ["IP Camera", "NVR Channel", "DVR Channel", "PTZ Camera", "Fisheye"]
    CONN_TYPES = ["Wired (Ethernet)", "Wireless (WiFi)", "PoE"]

    DEFAULT_RTSP_PORT = 554
    DEFAULT_HTTP_PORT = 80
    STREAM_RECONNECT_DELAY = 5
    STREAM_TIMEOUT = 10

    FACE_RECOGNITION_THRESHOLD = 0.50
    FACE_DETECTION_SCALE = 0.5
    FACE_MIN_SIZE = 50

    RECORDING_FPS = 25
    RECORDING_CODEC = "mp4v"
    RECORDING_SEGMENT_MINUTES = 10
    RECORDING_RETENTION_DAYS = 30
    RECORDING_MAX_GB = 200.0
    RECORDING_MIN_FREE_GB = 5.0
    RECORDING_MAX_WIDTH = 1280  # 0 = keep original resolution
    MOTION_SENSITIVITY = "medium"  # low | medium | high
    MOTION_PRE_ROLL = 3
    MOTION_POST_ROLL = 10

    EXPRESSIONS = ["angry", "disgust", "fear", "happy", "sad", "surprise", "neutral"]
    ALERT_EXPRESSIONS = ["angry", "fear", "disgust"]
    EXPRESSION_ALERT_CONFIDENCE = 60.0

    GRID_LAYOUTS = [(1, 1), (2, 2), (3, 3), (4, 4), (2, 3), (3, 4)]
    MAX_CAMERAS_DISPLAY = 16

    # AI pipeline cadence (seconds between analyses of the same camera)
    FACE_INTERVAL = 1.0
    EXPRESSION_INTERVAL = 1.0
    SAFETY_INTERVAL = 0.5
    EXPRESSION_LOG_INTERVAL = 10.0
    ALERT_COOLDOWN = 60.0

    # Attendance
    SHIFT_START = "09:00"
    LATE_GRACE_MINUTES = 10
    ATTENDANCE_MIN_UPDATE_SECS = 30
    WEEKLY_OFF_DAYS = [6]  # Monday=0 ... Sunday=6

    # Behaviour
    AUTO_START_STREAMS = False
    ALERT_SOUND = True
    ALERT_UNKNOWN_FACES = False
    SAFETY_MODEL = {}  # {"weights": ..., "cfg": ..., "names": ...}
    AUTO_BACKUP = True

    PERSISTED_KEYS = [
        "FACE_RECOGNITION_THRESHOLD", "STREAM_RECONNECT_DELAY", "RECORDING_FPS",
        "SHIFT_START", "LATE_GRACE_MINUTES", "WEEKLY_OFF_DAYS", "AUTO_START_STREAMS",
        "ALERT_SOUND", "ALERT_UNKNOWN_FACES", "ALERT_COOLDOWN",
        "EXPRESSION_ALERT_CONFIDENCE", "SAFETY_MODEL",
        "RECORDING_SEGMENT_MINUTES", "RECORDING_RETENTION_DAYS", "RECORDING_MAX_GB",
        "RECORDING_MIN_FREE_GB", "RECORDING_MAX_WIDTH", "MOTION_SENSITIVITY",
        "MOTION_PRE_ROLL", "MOTION_POST_ROLL", "AUTO_BACKUP",
    ]

    # {stream}: Hikvision 01 = main / 02 = sub; {sub}: Dahua & UNV 0 = main / 1 = sub.
    # CP Plus cameras and recorders are Dahua-based and use the Dahua path.
    RTSP_TEMPLATES = {
        "Hikvision":    "rtsp://{auth}{host}:{port}/Streaming/Channels/{channel}{stream}",
        "Dahua":        "rtsp://{auth}{host}:{port}/cam/realmonitor?channel={channel}&subtype={sub}",
        "CP Plus":      "rtsp://{auth}{host}:{port}/cam/realmonitor?channel={channel}&subtype={sub}",
        "UNV":          "rtsp://{auth}{host}:{port}/unicast/c{channel}/s{sub}/live",
        "ONVIF":        "rtsp://{auth}{host}:{port}/stream1",
        "Generic RTSP": "rtsp://{auth}{host}:{port}/stream",
    }

    @classmethod
    def build_rtsp_url(cls, brand: str, host: str, port: int, user: str,
                       password: str, channel: int = 1,
                       substream: bool = False) -> str:
        template = cls.RTSP_TEMPLATES.get(brand, cls.RTSP_TEMPLATES["Generic RTSP"])
        auth = ""
        if user:
            # '@', ':' or '/' in credentials would otherwise break the URL.
            auth = f"{_escape(user)}:{_escape(password or '')}@"
        return template.format(
            auth=auth, host=host, port=port, channel=channel,
            stream="02" if substream else "01", sub=1 if substream else 0,
        )

    @classmethod
    def set_data_dir(cls, path: str):
        """Keep all data in another folder (portable installs, self-test)."""
        cls.DATA_DIR = os.path.abspath(path)
        cls.DB_PATH = os.path.join(cls.DATA_DIR, "classguard.db")
        cls.FACES_DIR = os.path.join(cls.DATA_DIR, "faces")
        cls.RECORDINGS_DIR = os.path.join(cls.DATA_DIR, "recordings")
        cls.SNAPSHOTS_DIR = os.path.join(cls.DATA_DIR, "snapshots")
        cls.LOGS_DIR = os.path.join(cls.DATA_DIR, "logs")
        cls.SETTINGS_PATH = os.path.join(cls.DATA_DIR, "settings.json")

    @classmethod
    def load_settings(cls):
        try:
            with open(cls.SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:
            logger.error("Could not read settings: %s", e)
            return
        for key in cls.PERSISTED_KEYS:
            if key in data:
                setattr(cls, key, data[key])

    @classmethod
    def save_settings(cls):
        os.makedirs(cls.DATA_DIR, exist_ok=True)
        data = {k: getattr(cls, k) for k in cls.PERSISTED_KEYS}
        tmp = cls.SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, cls.SETTINGS_PATH)
