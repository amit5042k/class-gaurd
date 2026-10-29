import logging
from typing import Optional, Tuple

import numpy as np

from app.core.stream_manager import open_capture

logger = logging.getLogger(__name__)


class RTSPProbe:
    """Open a stream once to check it works and read its basic properties."""

    def __init__(self, rtsp_url: str):
        self.rtsp_url = rtsp_url

    def test_connection(self) -> Tuple[bool, str]:
        cap = open_capture(self.rtsp_url)
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
