import cv2
import logging
import numpy as np
from typing import List, Dict

logger = logging.getLogger(__name__)

EMOTION_COLORS = {
    "happy":    (0, 220, 60),
    "neutral":  (180, 180, 180),
    "sad":      (30, 120, 200),
    "angry":    (0, 50, 220),
    "fear":     (140, 0, 180),
    "surprise": (0, 200, 220),
    "disgust":  (0, 140, 60),
}


class ExpressionEngine:
    def __init__(self):
        self._deepface_ok = self._probe_deepface()
        self._cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )

    def _probe_deepface(self) -> bool:
        try:
            from deepface import DeepFace  # noqa: F401
            return True
        except ImportError:
            logger.warning("DeepFace unavailable — expression analysis disabled")
            return False

    def analyze(self, frame: np.ndarray) -> List[Dict]:
        if self._deepface_ok:
            return self._analyze_deepface(frame)
        return self._fallback(frame)

    def _analyze_deepface(self, frame: np.ndarray) -> List[Dict]:
        try:
            from deepface import DeepFace
            results = DeepFace.analyze(
                frame, actions=["emotion"],
                enforce_detection=False, silent=True
            )
            if not isinstance(results, list):
                results = [results]
            output = []
            for r in results:
                reg = r.get("region", {})
                x, y, w, h = (reg.get(k, 0) for k in ("x", "y", "w", "h"))
                dominant = r.get("dominant_emotion", "neutral")
                emotions = r.get("emotion", {})
                output.append({
                    "bbox": (x, y, x+w, y+h),
                    "dominant_emotion": dominant,
                    "emotions": emotions,
                    "confidence": float(emotions.get(dominant, 0)),
                })
            return output
        except Exception as e:
            logger.debug("DeepFace: %s", e)
            return []

    def _fallback(self, frame: np.ndarray) -> List[Dict]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(gray, 1.1, 5, minSize=(60, 60))
        return [
            {"bbox": (x, y, x+w, y+h), "dominant_emotion": "neutral",
             "emotions": {}, "confidence": 0.0}
            for (x, y, w, h) in faces
        ]

    def draw(self, frame: np.ndarray, results: List[Dict]) -> np.ndarray:
        for r in results:
            x1, y1, x2, y2 = r["bbox"]
            emotion = r["dominant_emotion"]
            color = EMOTION_COLORS.get(emotion, (200, 200, 200))
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            label = f"{emotion.upper()} {r['confidence']:.0f}%"
            cv2.putText(frame, label, (x1, max(0, y1-6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        return frame
