from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QDialog,
    QFormLayout, QLineEdit, QComboBox, QSpinBox,
    QMessageBox, QDialogButtonBox
)
from PyQt6.QtCore import Qt
from app.core.camera_manager import CameraManager
from app.config import Config

NVR_COLS = ["ID", "Name", "Brand", "Host", "Port", "Channels", "Location", "Status", "Actions"]


class NVRDialog(QDialog):
    def __init__(self, data: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add NVR" if not data else "Edit NVR")
        self.setMinimumWidth(420)
        self._data = data or {}
        self._build()
        if data:
            self._populate(data)

    def _build(self):
        l = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        def lbl(t): lb = QLabel(t); lb.setObjectName("form_label"); return lb

        self._name   = QLineEdit(); self._name.setPlaceholderText("NVR name")
        self._brand  = QComboBox(); self._brand.addItems(Config.CAMERA_BRANDS)
        self._host   = QLineEdit(); self._host.setPlaceholderText("192.168.1.50")
        self._port   = QSpinBox(); self._port.setRange(1,65535); self._port.setValue(8000)
        self._http   = QSpinBox(); self._http.setRange(1,65535); self._http.setValue(80)
        self._user   = QLineEdit(); self._user.setText("admin")
        self._pwd    = QLineEdit(); self._pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self._ch     = QSpinBox(); self._ch.setRange(1,64); self._ch.setValue(4)
        self._loc    = QLineEdit()

        form.addRow(lbl("Name:"),     self._name)
        form.addRow(lbl("Brand:"),    self._brand)
        form.addRow(lbl("Host:"),     self._host)
        form.addRow(lbl("SDK Port:"), self._port)
        form.addRow(lbl("HTTP Port:"),self._http)
        form.addRow(lbl("Username:"), self._user)
        form.addRow(lbl("Password:"), self._pwd)
        form.addRow(lbl("Channels:"), self._ch)
        form.addRow(lbl("Location:"), self._loc)
        l.addLayout(form)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        l.addWidget(bb)

    def _populate(self, d):
        self._name.setText(d.get("name", ""))
        idx = self._brand.findText(d.get("brand", "")); 
        if idx >= 0: self._brand.setCurrentIndex(idx)
        self._host.setText(d.get("host", ""))
        self._port.setValue(d.get("port", 8000))
        self._http.setValue(d.get("http_port", 80))
        self._user.setText(d.get("username", "admin"))
        self._pwd.setText(d.get("password", ""))
        self._ch.setValue(d.get("total_channels", 4))
        self._loc.setText(d.get("location", ""))

    def get_data(self) -> dict:
        return {
            "name": self._name.text().strip(),
            "brand": self._brand.currentText(),
            "host": self._host.text().strip(),
            "port": self._port.value(),
            "http_port": self._http.value(),
            "username": self._user.text(),
            "password": self._pwd.text(),
            "total_channels": self._ch.value(),
            "location": self._loc.text(),
        }


class NVRManagement(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.mgr = CameraManager(db)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel("NVR / DVR Management")
        title.setObjectName("page_title")
        sub = QLabel("Add and manage Network Video Recorders")
        sub.setObjectName("page_subtitle")
        hdr.addWidget(title); hdr.addStretch()
        btn = QPushButton("+ Add NVR")
        btn.setObjectName("primary_btn")
        btn.clicked.connect(self._add)
        hdr.addWidget(btn)
        root.addLayout(hdr)
        root.addWidget(sub)

        self._table = QTableWidget(0, len(NVR_COLS))
        self._table.setHorizontalHeaderLabels(NVR_COLS)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        root.addWidget(self._table)

    def refresh(self):
        nvrs = self.mgr.get_all_nvrs()
        self._table.setRowCount(0)
        for nvr in nvrs:
            r = self._table.rowCount(); self._table.insertRow(r)
            for c, v in enumerate([str(nvr["id"]), nvr["name"], nvr["brand"],
                                    nvr["host"], str(nvr["port"]),
                                    str(nvr["total_channels"]), nvr["location"],
                                    "Active" if nvr["is_active"] else "Inactive"]):
                self._table.setItem(r, c, QTableWidgetItem(v))
            act = QWidget(); al = QHBoxLayout(act); al.setContentsMargins(4,2,4,2)
            nid = nvr["id"]
            d_btn = QPushButton("Del"); d_btn.setObjectName("danger_btn")
            d_btn.clicked.connect(lambda _, i=nid: self._delete(i))
            al.addWidget(d_btn)
            self._table.setCellWidget(r, len(NVR_COLS)-1, act)

    def _add(self):
        dlg = NVRDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            if not data["name"] or not data["host"]:
                QMessageBox.warning(self, "Error", "Name and host required.")
                return
            try:
                self.mgr.add_nvr(data); self.refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _delete(self, nvr_id: int):
        if QMessageBox.question(self, "Confirm", "Delete this NVR?",
                                QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No) == \
                QMessageBox.StandardButton.Yes:
            self.mgr.delete_nvr(nvr_id); self.refresh()
