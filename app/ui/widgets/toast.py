from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

COLORS = {"high": "#e74c3c", "medium": "#f39c12", "low": "#3498db", "info": "#27ae60"}


class Toast(QWidget):
    """Small non-blocking notification stacked in the window's bottom-right corner."""

    clicked = pyqtSignal()

    def __init__(self, parent, title: str, text: str, severity: str = "info",
                 timeout_ms: int = 8000):
        super().__init__(parent)
        color = COLORS.get(severity, COLORS["info"])
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"Toast{{background:#1b1b30; border:1px solid {color};"
                           f"border-left:5px solid {color}; border-radius:6px;}}")
        self.setFixedWidth(340)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 6, 8)
        col = QVBoxLayout()
        t = QLabel(title)
        t.setStyleSheet(f"color:{color}; font-weight:bold;")
        b = QLabel(text)
        b.setWordWrap(True)
        b.setStyleSheet("color:#dde1ec;")
        col.addWidget(t)
        col.addWidget(b)
        lay.addLayout(col, 1)
        close = QPushButton("✕")
        close.setFixedWidth(26)
        close.setStyleSheet("background:transparent; color:#888; border:none;")
        close.clicked.connect(self.close)
        lay.addWidget(close, alignment=Qt.AlignmentFlag.AlignTop)
        self.adjustSize()
        QTimer.singleShot(timeout_ms, self.close)

    def mousePressEvent(self, event):
        self.clicked.emit()
        self.close()


class ToastStack:
    MAX = 4

    def __init__(self, window: QWidget):
        self._window = window
        self._toasts = []

    def show(self, title: str, text: str, severity: str = "info", on_click=None):
        toast = Toast(self._window, title, text, severity)
        if on_click:
            toast.clicked.connect(on_click)
        toast.destroyed.connect(lambda *_: self._remove(toast))
        toast.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._toasts.append(toast)
        while len(self._toasts) > self.MAX:
            self._toasts.pop(0).close()
        toast.show()
        toast.raise_()
        self.relayout()

    def _remove(self, toast):
        if toast in self._toasts:
            self._toasts.remove(toast)
        self.relayout()

    def relayout(self):
        try:
            bar = self._window.statusBar().height() if hasattr(self._window, "statusBar") else 0
            y = self._window.height() - bar - 12
        except RuntimeError:  # window already destroyed during shutdown
            return
        for t in reversed(self._toasts):
            try:
                y -= t.height()
                t.move(self._window.width() - t.width() - 16, y)
                y -= 8
            except RuntimeError:
                pass
