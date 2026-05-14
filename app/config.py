import os


class Config:
    APP_NAME = "ClassGuard - Camera Management System"
    VERSION = "1.0.0"

    DATA_DIR = os.path.join(os.path.expanduser("~"), ".classguard")
    DB_PATH = os.path.join(DATA_DIR, "classguard.db")
    FACES_DIR = os.path.join(DATA_DIR, "faces")
    RECORDINGS_DIR = os.path.join(DATA_DIR, "recordings")
    SNAPSHOTS_DIR = os.path.join(DATA_DIR, "snapshots")
    LOGS_DIR = os.path.join(DATA_DIR, "logs")

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

    EXPRESSIONS = ["angry", "disgust", "fear", "happy", "sad", "surprise", "neutral"]
    ALERT_EXPRESSIONS = ["angry", "fear", "disgust"]

    SAFETY_VIOLATION_CLASSES = ["no_helmet", "no_mask", "no_vest", "no_gloves"]
    SAFETY_PPE_CLASSES = ["helmet", "mask", "vest", "gloves", "goggles"]

    GRID_LAYOUTS = [(1, 1), (2, 2), (3, 3), (4, 4), (2, 3), (3, 4)]
    MAX_CAMERAS_DISPLAY = 16

    HIKVISION_RTSP = "rtsp://{user}:{password}@{host}:{port}/Streaming/Channels/{channel}01"
    DAHUA_RTSP     = "rtsp://{user}:{password}@{host}:{port}/cam/realmonitor?channel={channel}&subtype=0"
    UNV_RTSP       = "rtsp://{user}:{password}@{host}:{port}/media/video{channel}"
    CPPLUS_RTSP    = "rtsp://{user}:{password}@{host}:{port}/h264Preview_01_main"
    ONVIF_RTSP     = "rtsp://{user}:{password}@{host}:{port}/stream1"
    GENERIC_RTSP   = "rtsp://{user}:{password}@{host}:{port}/stream"

    RTSP_TEMPLATES = {
        "Hikvision":   HIKVISION_RTSP,
        "Dahua":       DAHUA_RTSP,
        "UNV":         UNV_RTSP,
        "CP Plus":     CPPLUS_RTSP,
        "ONVIF":       ONVIF_RTSP,
        "Generic RTSP": GENERIC_RTSP,
    }

    @classmethod
    def build_rtsp_url(cls, brand: str, host: str, port: int,
                       user: str, password: str, channel: int = 1) -> str:
        template = cls.RTSP_TEMPLATES.get(brand, cls.GENERIC_RTSP)
        return template.format(
            user=user, password=password,
            host=host, port=port, channel=channel
        )
