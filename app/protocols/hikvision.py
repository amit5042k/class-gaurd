import requests
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class HikvisionAPI:
    """Hikvision ISAPI integration."""

    def __init__(self, host: str, http_port: int, username: str, password: str):
        self.base = f"http://{host}:{http_port}/ISAPI"
        self.auth = requests.auth.DigestAuth(username, password)
        self.session = requests.Session()
        self.session.auth = self.auth

    def _get(self, path: str, timeout: int = 5) -> Optional[Dict]:
        try:
            r = self.session.get(f"{self.base}{path}", timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.error("Hikvision GET %s: %s", path, e)
            return None

    def get_device_info(self) -> Dict:
        data = self._get("/System/deviceInfo")
        return {"raw": data} if data else {}

    def get_channels(self) -> List[Dict]:
        data = self._get("/Streaming/channels")
        return [{"raw": data}] if data else []

    def get_status(self) -> bool:
        return self._get("/System/status") is not None

    def ptz_control(self, channel: int, command: str, speed: int = 5) -> bool:
        ptz_map = {
            "up":     "<PTZData><pan>0</pan><tilt>{s}</tilt></PTZData>",
            "down":   "<PTZData><pan>0</pan><tilt>-{s}</tilt></PTZData>",
            "left":   "<PTZData><pan>-{s}</pan><tilt>0</tilt></PTZData>",
            "right":  "<PTZData><pan>{s}</pan><tilt>0</tilt></PTZData>",
            "zoom_in":  "<PTZData><zoom>{s}</zoom></PTZData>",
            "zoom_out": "<PTZData><zoom>-{s}</zoom></PTZData>",
        }
        body = ptz_map.get(command, "").format(s=speed)
        if not body:
            return False
        try:
            r = self.session.put(
                f"{self.base}/PTZCtrl/channels/{channel}/continuous",
                data=body, headers={"Content-Type": "application/xml"}, timeout=5
            )
            return r.status_code in (200, 201)
        except Exception as e:
            logger.error("PTZ control: %s", e)
            return False

    def capture_snapshot(self, channel: int = 1) -> Optional[bytes]:
        try:
            r = self.session.get(
                f"{self.base}/Streaming/channels/{channel}01/picture", timeout=10
            )
            r.raise_for_status()
            return r.content
        except Exception as e:
            logger.error("Snapshot: %s", e)
            return None
