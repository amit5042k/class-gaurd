import logging
import time
from typing import Optional, Tuple

import numpy as np

from app.core.stream_manager import open_capture

logger = logging.getLogger(__name__)


class RTSPProbe:
    """Open a stream once to check it works and read its basic properties."""

    def __init__(self, rtsp_url: str):
        self.rtsp_url = rtsp_url

    def test_connection(self) -> Tuple[bool, str]:
        if self.rtsp_url.startswith("rtsp"):
            from app.protocols.rtsp_diag import OK, describe
            kind, msg = describe(self.rtsp_url)
            if kind != OK:
                return False, msg
            # The device offers the stream; now check a picture really arrives.
            time.sleep(1.0)
            ok, detail = self._read_one("tcp")
            if not ok:
                ok, detail = self._read_one("udp")
                if ok:
                    detail += " (over UDP: set RTSP transport to Auto or UDP)"
            return ok, detail
        return self._read_one("tcp")

    def _read_one(self, transport: str) -> Tuple[bool, str]:
        cap = open_capture(self.rtsp_url, transport)
        try:
            if not cap.isOpened():
                return False, "Could not open the stream (check IP, port, login and path)."
            ok, frame = cap.read()
            if not ok or frame is None:
                return False, "Connected, but no video was received."
            return True, f"Receiving video: {frame.shape[1]}x{frame.shape[0]}"
        finally:
            cap.release()

    def capture_snapshot(self) -> Optional[np.ndarray]:
        cap = open_capture(self.rtsp_url)
        try:
            if not cap.isOpened():
                return None
            ok, frame = cap.read()
            return frame if ok else None
        finally:
            cap.release()
