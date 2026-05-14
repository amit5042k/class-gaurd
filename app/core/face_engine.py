import os
import cv2
import pickle
import logging
import numpy as np
from datetime import datetime
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger(__name__)


class FaceEngine:
    def __init__(self, faces_dir: str):
        self.faces_dir = faces_dir
        self.known_encodings: List[np.ndarray] = []
        self.known_ids: List[int] = []
        self.known_names: List[str] = []
        self._cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        self._fr_ok = self._probe_face_recognition()

    def _probe_face_recognition(self) -> bool:
        try:
            import face_recognition  # noqa: F401
            return True
        except ImportError:
            logger.warning("face_recognition unavailable — using OpenCV Haar cascade")
            return False

    # ------------------------------------------------------------------ load
    def load_known_faces(self, persons: List[Dict]):
        self.known_encodings.clear()
        self.known_ids.clear()
        self.known_names.clear()
        for p in persons:
            enc_bytes = p.get("face_encoding")
            if enc_bytes:
                try:
                    enc = pickle.loads(enc_bytes)
                    self.known_encodings.append(enc)
                    self.known_ids.append(p["id"])
                    self.known_names.append(p["name"])
                except Exception:
                    pass
        logger.info("Loaded %d face encodings", len(self.known_encodings))

    # ---------------------------------------------------------------- enroll
    def enroll_face(self, image: np.ndarray) -> Optional[bytes]:
        if self._fr_ok:
            return self._enroll_fr(image)
        return self._enroll_cascade(image)

    def _enroll_fr(self, image: np.ndarray) -> Optional[bytes]:
        try:
            import face_recognition as fr
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            locs = fr.face_locations(rgb, model="hog")
            if not locs:
                return None
            encs = fr.face_encodings(rgb, locs)
            return pickle.dumps(encs[0]) if encs else None
        except Exception as e:
            logger.error("Enroll (fr): %s", e)
            return None

    def _enroll_cascade(self, image: np.ndarray) -> Optional[bytes]:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(gray, 1.1, 5, minSize=(60, 60))
        if len(faces) == 0:
            return None
        x, y, w, h = faces[0]
        crop = cv2.resize(gray[y:y+h, x:x+w], (128, 128)).flatten().astype(np.float64)
        return pickle.dumps(crop)

    # --------------------------------------------------------------- detect
    def detect_faces(self, frame: np.ndarray) -> List[Tuple[int, int, int, int]]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(gray, 1.1, 4, minSize=(50, 50))
        return [(x, y, x+w, y+h) for (x, y, w, h) in faces]

    # ------------------------------------------------------------- recognize
    def recognize_faces(self, frame: np.ndarray,
                        threshold: float = 0.50) -> List[Dict]:
        if self._fr_ok:
            return self._recognize_fr(frame, threshold)
        bboxes = self.detect_faces(frame)
        return [{"bbox": bb, "person_id": None, "name": "Unknown",
                 "confidence": 0.0} for bb in bboxes]

    def _recognize_fr(self, frame: np.ndarray, threshold: float) -> List[Dict]:
        try:
            import face_recognition as fr
            small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            locs = fr.face_locations(rgb)
            encs = fr.face_encodings(rgb, locs)
            results = []
            for enc, (top, right, bottom, left) in zip(encs, locs):
                bbox = (left*2, top*2, right*2, bottom*2)
                if self.known_encodings:
                    dists = fr.face_distance(self.known_encodings, enc)
                    idx = int(np.argmin(dists))
                    if dists[idx] <= threshold:
                        results.append({"bbox": bbox,
                                        "person_id": self.known_ids[idx],
                                        "name": self.known_names[idx],
                                        "confidence": 1.0 - float(dists[idx])})
                        continue
                results.append({"bbox": bbox, "person_id": None,
                                "name": "Unknown", "confidence": 0.0})
            return results
        except Exception as e:
            logger.error("Recognize (fr): %s", e)
            return []

    # ------------------------------------------------------------------ draw
    def draw_faces(self, frame: np.ndarray, faces: List[Dict]) -> np.ndarray:
        for f in faces:
            x1, y1, x2, y2 = f["bbox"]
            known = f["person_id"] is not None
            color = (0, 220, 60) if known else (0, 60, 220)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            label = f"{f['name']} {f['confidence']:.0%}" if known else "Unknown"
            cv2.putText(frame, label, (x1, max(0, y1-6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        return frame

    # ------------------------------------------------------------------ util
    def save_face_photo(self, image: np.ndarray, person_id: int,
                        suffix: str = "") -> str:
        folder = os.path.join(self.faces_dir, f"person_{person_id}")
        os.makedirs(folder, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(folder, f"{ts}{suffix}.jpg")
        cv2.imwrite(path, image)
        return path
