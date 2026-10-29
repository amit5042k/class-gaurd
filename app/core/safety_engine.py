import logging
import os
from typing import Dict, List, Optional, Sequence

import cv2
import numpy as np

logger = logging.getLogger(__name__)

CONF_THRESHOLD = 0.45
NMS_THRESHOLD = 0.45


def normalise_class(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(" ", "_")


def is_violation_class(name: str) -> bool:
    # PPE datasets name missing equipment "no_helmet", "NO-Hardhat",
    # "NO-Safety Vest", ... so anything starting with "no_" is a violation.
    return normalise_class(name).startswith("no_")


class SafetyEngine:
    """Person / PPE detection plus restricted-zone checks.

    Without a model it uses OpenCV's built-in HOG person detector.
    A custom model can be Darknet (.weights + .cfg) or ONNX (YOLOv5 / YOLOv8).
    """

    def __init__(self):
        self._net = None
        self._kind: Optional[str] = None  # "darknet" | "onnx"
        self._input_size = 416
        self._classes: List[str] = []
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    @property
    def model_loaded(self) -> bool:
        return self._net is not None

    def load_model(self, weights: str, names: str, cfg: str = "") -> str:
        """Load a model; returns '' on success or an error message."""
        try:
            with open(names, encoding="utf-8") as f:
                classes = [line.strip() for line in f if line.strip()]
            ext = os.path.splitext(weights)[1].lower()
            if ext == ".onnx":
                net = cv2.dnn.readNetFromONNX(weights)
                kind, size = "onnx", 640
            elif ext == ".weights":
                if not cfg:
                    return "A Darknet .weights file also needs its .cfg file."
                net = cv2.dnn.readNetFromDarknet(cfg, weights)
                kind, size = "darknet", 416
            elif ext == ".pt":
                return ("PyTorch .pt files can't be loaded directly. Export to ONNX "
                        "first, e.g.: yolo export model=best.pt format=onnx")
            else:
                return f"Unsupported model file type: {ext}"
        except Exception as e:
            logger.exception("Model load failed")
            return f"Could not load model: {e}"
        self._net, self._kind, self._input_size, self._classes = net, kind, size, classes
        logger.info("Safety model loaded (%s, %d classes)", kind, len(classes))
        return ""

    def unload_model(self):
        self._net, self._kind, self._classes = None, None, []

    # ------------------------------------------------------------- detection
    def detect(self, frame: np.ndarray,
               zones: Sequence[np.ndarray] = ()) -> List[Dict]:
        if self._net is not None:
            try:
                dets = self._detect_onnx(frame) if self._kind == "onnx" \
                    else self._detect_darknet(frame)
            except Exception:
                logger.exception("Model inference failed; falling back to HOG")
                dets = self._detect_hog(frame)
        else:
            dets = self._detect_hog(frame)
        self._check_zones(dets, zones)
        return dets

    def _make(self, x1, y1, x2, y2, cls: str, conf: float) -> Dict:
        return {"bbox": (int(x1), int(y1), int(x2), int(y2)), "class": cls,
                "confidence": float(conf), "violation": is_violation_class(cls),
                "zone_intrusion": False}

    def _nms(self, boxes, scores, class_ids) -> List[Dict]:
        if not boxes:
            return []
        keep = cv2.dnn.NMSBoxes(boxes, scores, CONF_THRESHOLD, NMS_THRESHOLD)
        out = []
        for i in np.array(keep).flatten():
            x, y, w, h = boxes[i]
            cid = class_ids[i]
            cls = self._classes[cid] if cid < len(self._classes) else f"class_{cid}"
            out.append(self._make(x, y, x + w, y + h, cls, scores[i]))
        return out

    def _detect_darknet(self, frame: np.ndarray) -> List[Dict]:
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(frame, 1 / 255.0, (self._input_size,) * 2,
                                     swapRB=True, crop=False)
        self._net.setInput(blob)
        outputs = self._net.forward(self._net.getUnconnectedOutLayersNames())
        boxes, scores, ids = [], [], []
        for out in outputs:
            for det in out:
                cls_scores = det[5:]
                cid = int(np.argmax(cls_scores))
                conf = float(cls_scores[cid] * det[4]) if det[4] > 0 else float(cls_scores[cid])
                if conf < CONF_THRESHOLD:
                    continue
                cx, cy, bw, bh = det[:4] * np.array([w, h, w, h])
                boxes.append([int(cx - bw / 2), int(cy - bh / 2), int(bw), int(bh)])
                scores.append(conf)
                ids.append(cid)
        return self._nms(boxes, scores, ids)

    def _detect_onnx(self, frame: np.ndarray) -> List[Dict]:
        h, w = frame.shape[:2]
        size = self._input_size
        blob = cv2.dnn.blobFromImage(frame, 1 / 255.0, (size, size),
                                     swapRB=True, crop=False)
        self._net.setInput(blob)
        out = np.squeeze(self._net.forward())
        if out.ndim != 2:
            raise ValueError(f"Unexpected ONNX output shape {out.shape}")
        n_cls = len(self._classes)
        # YOLOv8 outputs (4+C, N); YOLOv5 outputs (N, 5+C).
        if out.shape[0] in (4 + n_cls, 5 + n_cls) and out.shape[0] < out.shape[1]:
            out = out.T
        has_objectness = out.shape[1] == 5 + n_cls if n_cls else out.shape[1] > 85
        sx, sy = w / size, h / size
        boxes, scores, ids = [], [], []
        for row in out:
            cls_scores = row[5:] if has_objectness else row[4:]
            cid = int(np.argmax(cls_scores))
            conf = float(cls_scores[cid])
            if has_objectness:
                conf *= float(row[4])
            if conf < CONF_THRESHOLD:
                continue
            cx, cy, bw, bh = row[:4]
            boxes.append([int((cx - bw / 2) * sx), int((cy - bh / 2) * sy),
                          int(bw * sx), int(bh * sy)])
            scores.append(conf)
            ids.append(cid)
        return self._nms(boxes, scores, ids)

    def _detect_hog(self, frame: np.ndarray) -> List[Dict]:
        h, w = frame.shape[:2]
        scale = min(1.0, 640 / w)
        small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame
        rects, weights = self._hog.detectMultiScale(
            small, winStride=(8, 8), padding=(8, 8), scale=1.05)
        boxes, scores = [], []
        for (x, y, bw, bh), wt in zip(rects, np.array(weights).flatten()):
            boxes.append([int(x / scale), int(y / scale), int(bw / scale), int(bh / scale)])
            scores.append(float(min(1.0, max(0.5, wt))))
        if not boxes:
            return []
        keep = cv2.dnn.NMSBoxes(boxes, scores, 0.0, 0.3)
        return [self._make(boxes[i][0], boxes[i][1], boxes[i][0] + boxes[i][2],
                           boxes[i][1] + boxes[i][3], "person", scores[i])
                for i in np.array(keep).flatten()]

    @staticmethod
    def _check_zones(detections: List[Dict], zones: Sequence[np.ndarray]):
        if not len(zones):
            return
        for d in detections:
            x1, y1, x2, y2 = d["bbox"]
            # Use the bottom-centre (feet) so a person standing outside
            # a floor zone isn't counted because their head overlaps it.
            px, py = float((x1 + x2) / 2), float(y2)
            for zone in zones:
                if cv2.pointPolygonTest(zone, (px, py), False) >= 0:
                    d["zone_intrusion"] = True
                    break

    # ------------------------------------------------------------------ draw
    @staticmethod
    def draw(frame: np.ndarray, detections: List[Dict],
             zones: Sequence[np.ndarray] = ()) -> np.ndarray:
        if len(zones):
            overlay = frame.copy()
            for zone in zones:
                cv2.fillPoly(overlay, [zone], (0, 100, 100))
            cv2.addWeighted(overlay, 0.25, frame, 0.75, 0, frame)
            for zone in zones:
                cv2.polylines(frame, [zone], True, (0, 220, 220), 2)
        for d in detections:
            x1, y1, x2, y2 = d["bbox"]
            bad = d.get("violation") or d.get("zone_intrusion")
            color = (0, 0, 220) if bad else (0, 200, 60)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            label = f"{d['class']} {d['confidence']:.0%}"
            if d.get("zone_intrusion"):
                label += " IN ZONE"
            cv2.putText(frame, label, (x1, max(12, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
        return frame
