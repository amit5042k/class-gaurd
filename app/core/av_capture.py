"""Alternative decoder using PyAV (FFmpeg bundled with the full codec set).

Used when a camera connects but OpenCV's built-in FFmpeg produces no picture,
which happens with some H.265 streams (seen with Hikvision NVR sub-streams).
Same interface as cv2.VideoCapture: isOpened(), read(), release().
"""
import logging
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    import av
    AVAILABLE = True
except ImportError:  # optional dependency
    av = None
    AVAILABLE = False


class AVCapture:
    MAX_BAD_PACKETS = 600  # packets that fail to decode before giving up on a read

    def __init__(self, url: str, transport: str = "tcp", open_timeout_s: float = 10,
                 read_timeout_s: float = 10):
        self._container = None
        self._packets = None
        self._pending = []
        self.error: Optional[str] = None
        if not AVAILABLE:
            self.error = "PyAV is not installed"
            return
        # Note: "flags=low_delay" / "fflags=nobuffer" stop PyAV decoding H.265
        # entirely, so keep FFmpeg's defaults.
        options = {}
        if url.startswith("rtsp"):
            options["rtsp_transport"] = transport
            if transport == "udp":
                options["buffer_size"] = str(4 * 1024 * 1024)   # keep whole keyframes
        try:
            self._container = av.open(url, options=options,
                                      timeout=(open_timeout_s, read_timeout_s))
            stream = self._container.streams.video[0]
            stream.thread_type = "AUTO"
            self._packets = self._container.demux(stream)
        except Exception as e:
            self.error = str(e)
            logger.info("PyAV open failed: %s", e)
            self.release()

    def isOpened(self) -> bool:
        return self._container is not None

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self._container is None:
            return False, None
        if self._pending:
            return True, self._pending.pop(0)
        bad = 0
        try:
            for packet in self._packets:
                try:  # an empty packet at the end flushes the decoder
                    frames = packet.decode()
                except av.error.InvalidDataError:
                    bad += 1  # e.g. waiting for the first H.265 keyframe
                    if bad > self.MAX_BAD_PACKETS:
                        return False, None
                    continue
                images = [f.to_ndarray(format="bgr24") for f in frames]
                if images:
                    self._pending.extend(images[1:])
                    return True, images[0]
        except Exception as e:
            self.error = str(e)
            logger.info("PyAV read failed: %s", e)
        return False, None

    def get(self, _prop):
        return 0

    def release(self):
        if self._container is not None:
            try:
                self._container.close()
            except Exception:
                pass
        self._container = None
