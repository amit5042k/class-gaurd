import logging
import re
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 5
DIRECTIONS = {"up": "Up", "down": "Down", "left": "Left", "right": "Right",
              "zoom_in": "ZoomTele", "zoom_out": "ZoomWide"}


def parse_kv(text: str) -> Dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class DahuaAPI:
    """Dahua HTTP CGI (also used by CP Plus devices)."""

    def __init__(self, host: str, http_port: int, username: str, password: str):
        self.base = f"http://{host}:{http_port}/cgi-bin"
        self.session = requests.Session()
        self.session.auth = requests.auth.HTTPDigestAuth(username, password)

    def _get(self, path: str) -> requests.Response:
        r = self.session.get(f"{self.base}/{path}", timeout=TIMEOUT)
        if r.status_code == 401:
            raise PermissionError("Wrong username or password")
        r.raise_for_status()
        return r

    def get_device_info(self) -> Dict:
        info = parse_kv(self._get("magicBox.cgi?action=getSystemInfo").text)
        return {"name": info.get("deviceType", ""), "model": info.get("deviceType", ""),
                "serial": info.get("serialNumber", ""), "firmware": "",
                "type": info.get("deviceType", "")}

    def get_channel_names(self) -> Dict[int, str]:
        try:
            kv = parse_kv(self._get("configManager.cgi?action=getConfig&name=ChannelTitle").text)
        except Exception as e:
            logger.info("No channel titles: %s", e)
            return {}
        out = {}
        for k, v in kv.items():
            m = re.match(r"table\.ChannelTitle\[(\d+)\]\.Name$", k)
            if m:
                out[int(m.group(1)) + 1] = v  # API is 0-based, channels shown 1-based
        return out

    # ---------------------------------------------------------------- PTZ
    # ptz.cgi channel numbers are 0-based (as used by the widely deployed
    # amcrest/Dahua client libraries); the app's channels are 1-based.
    def _ptz(self, action: str, channel: int, code: str, arg2: int = 0) -> bool:
        r = self.session.get(
            f"{self.base}/ptz.cgi?action={action}&channel={max(0, channel - 1)}"
            f"&code={code}&arg1=0&arg2={arg2}&arg3=0", timeout=TIMEOUT)
        return r.ok

    def ptz_move(self, channel: int, direction: str, speed: int = 50) -> bool:
        code = DIRECTIONS.get(direction)
        step = max(1, min(8, round(int(speed) / 12.5)))  # 1..100 -> 1..8
        self._last_code = code
        return bool(code) and self._ptz("start", channel, code, step)

    def ptz_stop(self, channel: int) -> bool:
        code = getattr(self, "_last_code", None) or "Up"
        return self._ptz("stop", channel, code)

    def ptz_goto_preset(self, channel: int, preset: int) -> bool:
        return self._ptz("start", channel, "GotoPreset", preset)

    def capture_snapshot(self, channel: int = 1) -> Optional[bytes]:
        try:
            r = self._get(f"snapshot.cgi?channel={channel}")
            return r.content
        except Exception as e:
            logger.error("Dahua snapshot: %s", e)
            return None
