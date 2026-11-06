import logging
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 5


def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


class HikvisionAPI:
    """Hikvision ISAPI (HTTP) — device info, NVR channels, PTZ, snapshots."""

    def __init__(self, host: str, http_port: int, username: str, password: str):
        self.base = f"http://{host}:{http_port}/ISAPI"
        self.session = requests.Session()
        self.session.auth = requests.auth.HTTPDigestAuth(username, password)

    def _get_xml(self, path: str) -> ET.Element:
        r = self.session.get(f"{self.base}{path}", timeout=TIMEOUT)
        if r.status_code == 401:
            raise PermissionError("Wrong username or password")
        r.raise_for_status()
        return _strip_ns(ET.fromstring(r.content))

    def get_device_info(self) -> Dict:
        """Raises on failure so callers can show the reason."""
        root = self._get_xml("/System/deviceInfo")
        pick = lambda tag: (root.findtext(tag) or "").strip()  # noqa: E731
        return {"name": pick("deviceName"), "model": pick("model"),
                "serial": pick("serialNumber"), "firmware": pick("firmwareVersion"),
                "type": pick("deviceType")}

    def get_channel_names(self) -> Dict[int, str]:
        """NVR: names of connected IP cameras, by channel number."""
        try:
            root = self._get_xml("/ContentMgmt/InputProxy/channels")
        except Exception as e:
            logger.info("No InputProxy channel list: %s", e)
            return {}
        out = {}
        for ch in root.iter("InputProxyChannel"):
            try:
                out[int(ch.findtext("id"))] = (ch.findtext("name") or "").strip()
            except (TypeError, ValueError):
                continue
        return out

    # ---------------------------------------------------------------- PTZ
    def _continuous(self, channel: int, pan: int, tilt: int, zoom: int) -> bool:
        body = (f'<?xml version="1.0" encoding="UTF-8"?><PTZData><pan>{pan}</pan>'
                f"<tilt>{tilt}</tilt><zoom>{zoom}</zoom></PTZData>")
        r = self.session.put(f"{self.base}/PTZCtrl/channels/{channel}/continuous",
                             data=body, headers={"Content-Type": "application/xml"},
                             timeout=TIMEOUT)
        return r.status_code in (200, 201)

    def ptz_move(self, channel: int, direction: str, speed: int = 50) -> bool:
        s = max(1, min(100, int(speed)))
        vec = {"up": (0, s, 0), "down": (0, -s, 0), "left": (-s, 0, 0), "right": (s, 0, 0),
               "zoom_in": (0, 0, s), "zoom_out": (0, 0, -s)}.get(direction)
        return bool(vec) and self._continuous(channel, *vec)

    def ptz_stop(self, channel: int) -> bool:
        return self._continuous(channel, 0, 0, 0)

    def request_keyframe(self, stream_id: int) -> bool:
        """Ask for an I-frame now (stream_id e.g. 102 = channel 1 sub-stream), so a
        viewer that just connected gets a full picture straight away."""
        for path in (f"/Streaming/channels/{stream_id}/requestKeyFrame",
                     f"/ContentMgmt/StreamingProxy/channels/{stream_id}/requestKeyFrame"):
            try:
                r = self.session.put(f"{self.base}{path}", timeout=TIMEOUT)
            except requests.RequestException as e:
                logger.info("requestKeyFrame %s: %s", path, e)
                return False
            if r.status_code in (200, 201):
                return True
        return False

    def snapshot(self, stream_id: int) -> Optional[bytes]:
        """JPEG still from the device (stream_id e.g. 102), or None. Works even when
        the RTSP video can't be decoded, since the NVR encodes the picture itself."""
        paths = [f"/Streaming/channels/{stream_id}/picture",
                 f"/ContentMgmt/StreamingProxy/channels/{stream_id}/picture"]
        if getattr(self, "_snap_path", None) in paths:
            paths.remove(self._snap_path)
            paths.insert(0, self._snap_path)
        for path in paths:
            try:
                r = self.session.get(f"{self.base}{path}", timeout=TIMEOUT)
            except requests.RequestException as e:
                logger.info("Snapshot %s: %s", path, e)
                return None
            if r.status_code == 200 and r.content[:2] == b"\xff\xd8":
                self._snap_path = path
                return r.content
        return None

    def ptz_goto_preset(self, channel: int, preset: int) -> bool:
        r = self.session.put(f"{self.base}/PTZCtrl/channels/{channel}/presets/{preset}/goto",
                             timeout=TIMEOUT)
        return r.status_code in (200, 201)

    def capture_snapshot(self, channel: int = 1) -> Optional[bytes]:
        try:
            r = self.session.get(f"{self.base}/Streaming/channels/{channel}01/picture",
                                 timeout=10)
            r.raise_for_status()
            return r.content
        except Exception as e:
            logger.error("Snapshot: %s", e)
            return None

    def list_channels(self) -> List[int]:
        return sorted(self.get_channel_names())
