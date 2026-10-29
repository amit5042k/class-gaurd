from typing import List, Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QLabel, QSizePolicy


class VideoCanvas(QLabel):
    """Shows a frame scaled to fit; optionally collects polygon clicks
    in source-image pixel coordinates."""

    point_added = pyqtSignal(int)  # number of points so far

    def __init__(self, placeholder: str = "No video", parent=None):
        super().__init__(placeholder, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(320, 240)
        self.setStyleSheet("background:#09090f; border:1px solid #252550; color:#666;")
        self._image: Optional[QImage] = None
        self._drawing = False
        self._points: List[Tuple[int, int]] = []

    # ---------------------------------------------------------------- frames
    def set_image(self, img: QImage):
        self._image = img
        self.update()

    def clear_image(self, text: str = "No video"):
        self._image = None
        self.setText(text)
        self.update()

    def has_image(self) -> bool:
        return self._image is not None

    def _target_rect(self) -> Optional[QRectF]:
        if self._image is None or self._image.isNull():
            return None
        iw, ih = self._image.width(), self._image.height()
        scale = min(self.width() / iw, self.height() / ih)
        w, h = iw * scale, ih * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def paintEvent(self, event):
        rect = self._target_rect()
        if rect is None:
            return super().paintEvent(event)
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#09090f"))
        p.drawImage(rect, self._image)
        if self._points:
            sx = rect.width() / self._image.width()
            pts = [QPointF(rect.x() + x * sx, rect.y() + y * sx) for x, y in self._points]
            p.setPen(QPen(QColor("#ffd84a"), 2, Qt.PenStyle.DashLine))
            for a, b in zip(pts, pts[1:]):
                p.drawLine(a, b)
            if len(pts) >= 3:
                p.drawLine(pts[-1], pts[0])
            p.setBrush(QColor("#ffd84a"))
            for pt in pts:
                p.drawEllipse(pt, 4, 4)
        p.end()

    # --------------------------------------------------------------- drawing
    def start_drawing(self):
        self._drawing = True
        self._points = []
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def stop_drawing(self) -> List[Tuple[int, int]]:
        pts, self._points, self._drawing = self._points, [], False
        self.unsetCursor()
        self.update()
        return pts

    @property
    def drawing(self) -> bool:
        return self._drawing

    def mousePressEvent(self, event):
        rect = self._target_rect()
        if not self._drawing or rect is None or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        pos = event.position()
        if not rect.contains(pos):
            return
        scale = rect.width() / self._image.width()
        x = int((pos.x() - rect.x()) / scale)
        y = int((pos.y() - rect.y()) / scale)
        self._points.append((x, y))
        self.update()
        self.point_added.emit(len(self._points))

    def to_pixmap(self) -> QPixmap:
        return QPixmap.fromImage(self._image) if self._image else QPixmap()
