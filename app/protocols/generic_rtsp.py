import cv2
import logging
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)


class RTSPProbe:
    """Probe an RTSP URL to verify connectivity and get stream info."""

    def __init__(self, rtsp_url: str):
        self.rtsp_url = rtsp_url

    def test_connection(self, timeout: int = 8) -> bool:
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            return False
        ret, _ = cap.read()
        cap.release()
        return ret

    def get_info(self) -> dict:
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            return {}
        info = {
            "width":  int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps":    cap.get(cv2.CAP_PROP_FPS),
        }
        cap.release()
        return info

    def capture_snapshot(self) -> Optional[np.ndarray]:
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            return None
        ret, frame = cap.read()
        cap.release()
        return frame if ret else None
