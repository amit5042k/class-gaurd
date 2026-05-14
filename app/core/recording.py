import cv2
import os
import logging
from datetime import datetime
from typing import Optional, Dict
from app.config import Config

logger = logging.getLogger(__name__)


class Recorder:
    def __init__(self):
        self._writers: Dict[int, cv2.VideoWriter] = {}
        self._paths: Dict[int, str] = {}

    def start(self, camera_id: int, frame_width: int,
              frame_height: int) -> str:
        if camera_id in self._writers:
            self.stop(camera_id)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"cam{camera_id}_{ts}.mp4"
        path = os.path.join(Config.RECORDINGS_DIR, filename)
        fourcc = cv2.VideoWriter_fourcc(*Config.RECORDING_CODEC)
        writer = cv2.VideoWriter(
            path, fourcc, Config.RECORDING_FPS, (frame_width, frame_height)
        )
        self._writers[camera_id] = writer
        self._paths[camera_id] = path
        logger.info("Recording started: %s", path)
        return path

    def write(self, camera_id: int, frame):
        writer = self._writers.get(camera_id)
        if writer and writer.isOpened():
            writer.write(frame)

    def stop(self, camera_id: int) -> Optional[str]:
        writer = self._writers.pop(camera_id, None)
        path = self._paths.pop(camera_id, None)
        if writer:
            writer.release()
            logger.info("Recording stopped: %s", path)
        return path

    def stop_all(self):
        for cid in list(self._writers):
            self.stop(cid)

    def is_recording(self, camera_id: int) -> bool:
        return camera_id in self._writers
