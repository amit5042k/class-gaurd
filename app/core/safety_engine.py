import cv2
import numpy as np
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


class SafetyEngine:
    def __init__(self):
        self._yolo_ok = False
        self._net = None
        self._classes: List[str] = []
        self.zones: List[np.ndarray] = []
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def load_yolo(self, weights: str, cfg: str, names: str) -> bool:
        try:
            self._net = cv2.dnn.readNet(weights, cfg)
            with open(names) as f:
                self._classes = [l.strip() for l in f]
            self._yolo_ok = True
            logger.info("YOLO loaded: %d classes", len(self._classes))
            return True
        except Exception as e:
            logger.error("YOLO load: %s", e)
            return False

    def set_zones(self, zones: List[List[tuple]]):
        self.zones = [np.array(z, dtype=np.int32) for z in zones]

    def detect(self, frame: np.ndarray) -> List[Dict]:
        if self._yolo_ok and self._net is not None:
            return self._detect_yolo(frame)
        return self._detect_hog(frame)

    def _detect_yolo(self, frame: np.ndarray) -> List[Dict]:
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(frame, 1/255.0, (416, 416),
                                     swapRB=True, crop=False)
        self._net.setInput(blob)
        layer_names = self._net.getLayerNames()
        out_layers = [layer_names[i-1]
                      for i in self._net.getUnconnectedOutLayers().flatten()]
        outputs = self._net.forward(out_layers)
        results = []
        for out in outputs:
            for det in out:
                scores = det[5:]
                cid = int(np.argmax(scores))
                conf = float(scores[cid])
                if conf < 0.45:
                    continue
                cx, cy, bw, bh = (det[:4] * [w, h, w, h]).astype(int)
                x1, y1 = cx - bw//2, cy - bh//2
                cls = self._classes[cid] if cid < len(self._classes) else "obj"
                results.append({
                    "bbox": (x1, y1, x1+bw, y1+bh),
                    "class": cls,
                    "confidence": conf,
                    "violation": cls.lower() in {
                        "no_helmet", "no_mask", "no_vest", "no_gloves"
                    },
                    "zone_intrusion": False,
                })
        self._check_zones(results)
        return results

    def _detect_hog(self, frame: np.ndarray) -> List[Dict]:
        h, w = frame.shape[:2]
        scale = min(1.0, 640 / w)
        small = cv2.resize(frame, (int(w*scale), int(h*scale)))
        rects, _ = self._hog.detectMultiScale(
            small, winStride=(8, 8), padding=(4, 4), scale=1.05
        )
        results = []
        for (x, y, bw, bh) in rects:
            x1 = int(x/scale); y1 = int(y/scale)
            x2 = int((x+bw)/scale); y2 = int((y+bh)/scale)
            results.append({"bbox": (x1,y1,x2,y2), "class": "person",
                            "confidence": 0.7, "violation": False,
                            "zone_intrusion": False})
        self._check_zones(results)
        return results

    def _check_zones(self, detections: List[Dict]):
        if not self.zones:
            return
        for d in detections:
            x1, y1, x2, y2 = d["bbox"]
            cx, cy = (x1+x2)//2, (y1+y2)//2
            for zone in self.zones:
                if cv2.pointPolygonTest(zone, (cx, cy), False) >= 0:
                    d["zone_intrusion"] = True
                    break

    def draw(self, frame: np.ndarray, detections: List[Dict]) -> np.ndarray:
        for zone in self.zones:
            cv2.polylines(frame, [zone], True, (0, 220, 220), 2)
            overlay = frame.copy()
            cv2.fillPoly(overlay, [zone], (0, 100, 100))
            cv2.addWeighted(overlay, 0.2, frame, 0.8, 0, frame)

        for d in detections:
            x1, y1, x2, y2 = d["bbox"]
            color = (0, 0, 220) if d.get("violation") or d.get("zone_intrusion") \
                    else (0, 200, 60)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            label = f"{d['class']} {d['confidence']:.0%}"
            cv2.putText(frame, label, (x1, max(0, y1-6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
        return frame
