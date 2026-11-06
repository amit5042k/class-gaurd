"""Class mood: the share of negative and positive expressions among all faces a
classroom camera saw over the last minute or so, with alarm levels."""
import time
from collections import Counter, deque
from typing import Dict, Iterable, Optional

from app.config import Config

NEGATIVE = ("angry", "fear", "disgust", "sad")
POSITIVE = ("happy", "surprise")

GOOD, MIXED, LOW, ALARM = "Good", "Mixed", "Low", "Alarm"
LEVEL_COLORS = {GOOD: "#27ae60", MIXED: "#f3c068", LOW: "#f39c12", ALARM: "#ef4565"}


def mood_level(negative_pct: float, alert_pct: float) -> str:
    if negative_pct >= alert_pct:
        return ALARM
    if negative_pct >= alert_pct * 0.66:
        return LOW
    if negative_pct >= alert_pct * 0.33:
        return MIXED
    return GOOD


class ClassMoodTracker:
    """Rolling window of expressions per camera."""

    def __init__(self):
        self._samples: Dict[int, deque] = {}     # cid -> deque[(t, emotion)]
        self._alarm_since: Dict[int, float] = {}

    def add(self, camera_id: int, emotions: Iterable[str],
            now: Optional[float] = None) -> Dict:
        now = time.monotonic() if now is None else now
        q = self._samples.setdefault(camera_id, deque())
        for emo in emotions:
            q.append((now, emo))
        window = float(Config.CLASS_MOOD_WINDOW)
        while q and now - q[0][0] > window:
            q.popleft()
        return self.summary(camera_id, now)

    def summary(self, camera_id: int, now: Optional[float] = None) -> Dict:
        now = time.monotonic() if now is None else now
        q = self._samples.get(camera_id, ())
        counts = Counter(e for _, e in q)
        total = sum(counts.values())
        neg = sum(counts[e] for e in NEGATIVE)
        pos = sum(counts[e] for e in POSITIVE)
        neg_pct = 100.0 * neg / total if total else 0.0
        pos_pct = 100.0 * pos / total if total else 0.0
        enough = total >= int(Config.CLASS_MOOD_MIN_FACES)
        level = mood_level(neg_pct, float(Config.CLASS_MOOD_ALERT_PERCENT)) if enough else GOOD
        if level == ALARM:
            self._alarm_since.setdefault(camera_id, now)
        else:
            self._alarm_since.pop(camera_id, None)
        top = counts.most_common(1)[0][0] if counts else ""
        neg_counts = Counter({e: counts[e] for e in NEGATIVE if counts[e]})
        top_neg = neg_counts.most_common(1)[0][0] if neg_counts else ""
        return {"camera_id": camera_id, "samples": total, "enough": enough,
                "negative_pct": neg_pct, "positive_pct": pos_pct,
                "neutral_pct": max(0.0, 100.0 - neg_pct - pos_pct),
                "level": level, "dominant": top, "dominant_negative": top_neg,
                "counts": dict(counts)}

    def alarm(self, summary: Dict) -> bool:
        return summary["level"] == ALARM
