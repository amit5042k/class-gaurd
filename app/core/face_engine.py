import logging
import os
import pickle
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

MAX_ENCODINGS_PER_PERSON = 10
BACKEND = "dlib"


def decode_encodings(blob: Optional[bytes]) -> List[np.ndarray]:
    """Return the dlib encodings stored for a person ([] if none/unusable)."""
    if not blob:
        return []
    try:
        data = pickle.loads(blob)
    except Exception:
        return []
    if isinstance(data, dict) and data.get("backend") == BACKEND:
        return [np.asarray(e, dtype=np.float64) for e in data.get("encodings", [])]
    # Version 1.0 stored a single bare dlib vector.
    if isinstance(data, np.ndarray) and data.shape == (128,):
        return [data.astype(np.float64)]
    return []


def encode_encodings(encodings: List[np.ndarray]) -> bytes:
    return pickle.dumps({"backend": BACKEND,
                         "encodings": [np.asarray(e) for e in encodings]})


class FaceEngine:
    def __init__(self, faces_dir: str):
        self.faces_dir = faces_dir
        self._lock = threading.Lock()
        # (encodings matrix, person ids, names), swapped atomically
        self._known: Tuple[np.ndarray, List[int], List[str]] = (np.empty((0, 128)), [], [])
        self._cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        self.recognition_available = self._probe_face_recognition()

    @staticmethod
    def _probe_face_recognition() -> bool:
        try:
            import face_recognition  # noqa: F401
            return True
        except ImportError:
            logger.warning("face_recognition not installed: faces can be detected "
                           "but not identified, so attendance cannot work")
            return False

    # ------------------------------------------------------------------ load
    def load_known_faces(self, persons: List[Dict]):
        vectors, ids, names = [], [], []
        for p in persons:
            for enc in decode_encodings(p.get("face_encoding")):
                vectors.append(enc)
                ids.append(p["id"])
                names.append(p["name"])
        matrix = np.vstack(vectors) if vectors else np.empty((0, 128))
        with self._lock:
            self._known = (matrix, ids, names)
        logger.info("Loaded %d face encodings for %d people",
                    len(ids), len(set(ids)))

    @property
    def known_count(self) -> int:
        return len(set(self._known[1]))

    # ---------------------------------------------------------------- enroll
    def compute_encoding(self, image: np.ndarray) -> Tuple[Optional[np.ndarray], str]:
        """Return (encoding, message). Requires exactly one clear face."""
        if not self.recognition_available:
            return None, ("Face recognition is not installed. Install it with:\n"
                          "pip install -r requirements-ai.txt")
        import face_recognition as fr
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        locs = fr.face_locations(rgb, model="hog")
        if not locs:
            locs = fr.face_locations(rgb, number_of_times_to_upsample=2, model="hog")
        if not locs:
            return None, "No face found. Use a clear, front-facing, well-lit photo."
        if len(locs) > 1:
            return None, f"{len(locs)} faces found. Use a photo with only one person."
        top, right, bottom, left = locs[0]
        if (bottom - top) < 60:
            return None, "The face is too small. Move closer or use a larger photo."
        encs = fr.face_encodings(rgb, locs, num_jitters=3)
        if not encs:
            return None, "Could not read the face. Try another photo."
        return encs[0], "ok"

    @staticmethod
    def add_encoding(existing_blob: Optional[bytes], encoding: np.ndarray) -> bytes:
        encs = decode_encodings(existing_blob)
        encs.append(encoding)
        return encode_encodings(encs[-MAX_ENCODINGS_PER_PERSON:])

    # --------------------------------------------------------------- detect
    def detect_faces(self, frame: np.ndarray) -> List[Tuple[int, int, int, int]]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(gray, 1.1, 5, minSize=(50, 50))
        return [(int(x), int(y), int(x + w), int(y + h)) for (x, y, w, h) in faces]

    # ------------------------------------------------------------- recognize
    def recognize_faces(self, frame: np.ndarray, threshold: float = 0.50,
                        scale: float = 0.5) -> List[Dict]:
        if not self.recognition_available:
            return [{"bbox": bb, "person_id": None, "name": "Unknown",
                     "confidence": 0.0} for bb in self.detect_faces(frame)]
        import face_recognition as fr
        with self._lock:
            known, ids, names = self._known
        small = cv2.resize(frame, (0, 0), fx=scale, fy=scale) if scale != 1 else frame
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        locs = fr.face_locations(rgb, model="hog")
        encs = fr.face_encodings(rgb, locs) if locs else []
        inv = 1.0 / scale
        results = []
        for enc, (top, right, bottom, left) in zip(encs, locs):
            bbox = (int(left * inv), int(top * inv), int(right * inv), int(bottom * inv))
            match = {"bbox": bbox, "person_id": None, "name": "Unknown",
                     "confidence": 0.0, "distance": None}
            if len(ids):
                dists = np.linalg.norm(known - enc, axis=1)
                idx = int(np.argmin(dists))
                match["distance"] = float(dists[idx])
                if dists[idx] <= threshold:
                    match.update(person_id=ids[idx], name=names[idx],
                                 confidence=max(0.0, 1.0 - float(dists[idx])))
            results.append(match)
        return results

    # ------------------------------------------------------------------ draw
    @staticmethod
    def draw_faces(frame: np.ndarray, faces: List[Dict]) -> np.ndarray:
        for f in faces:
            x1, y1, x2, y2 = f["bbox"]
            known = f["person_id"] is not None
            color = (0, 220, 60) if known else (0, 60, 220)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            label = f"{f['name']} {f['confidence']:.0%}" if known else "Unknown"
            cv2.putText(frame, label, (x1, max(12, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        return frame

    # ------------------------------------------------------------------ util
    def save_face_photo(self, image: np.ndarray, person_id: int,
                        suffix: str = "") -> str:
        folder = os.path.join(self.faces_dir, f"person_{person_id}")
        os.makedirs(folder, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(folder, f"{ts}{suffix}.jpg")
        cv2.imwrite(path, image)
        return path
