import requests
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)


class DahuaAPI:
    """Dahua HTTP CGI integration."""

    def __init__(self, host: str, http_port: int, username: str, password: str):
        self.base = f"http://{host}:{http_port}/cgi-bin"
        self.session = requests.Session()
        self.session.auth = requests.auth.DigestAuth(username, password)

    def get_device_info(self) -> Dict:
        try:
            r = self.session.get(f"{self.base}/magicBox.cgi?action=getSystemInfo", timeout=5)
            return {"raw": r.text} if r.ok else {}
        except Exception as e:
            logger.error("Dahua device info: %s", e)
            return {}

    def get_status(self) -> bool:
        try:
            r = self.session.get(f"{self.base}/magicBox.cgi?action=ping", timeout=5)
            return r.ok
        except Exception:
            return False

    def capture_snapshot(self, channel: int = 1) -> Optional[bytes]:
        try:
            r = self.session.get(
                f"{self.base}/snapshot.cgi?channel={channel}", timeout=10
            )
            r.raise_for_status()
            return r.content
        except Exception as e:
            logger.error("Dahua snapshot: %s", e)
            return None

    def ptz_control(self, channel: int, action: str, speed: int = 5) -> bool:
        code_map = {
            "up": "Up", "down": "Down", "left": "Left", "right": "Right",
            "zoom_in": "ZoomWide", "zoom_out": "ZoomTele",
        }
        code = code_map.get(action)
        if not code:
            return False
        try:
            r = self.session.get(
                f"{self.base}/ptz.cgi?action=start&channel={channel}&code={code}&arg1=0&arg2={speed}&arg3=0",
                timeout=5
            )
            return r.ok
        except Exception as e:
            logger.error("Dahua PTZ: %s", e)
            return False
