from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget
)

from app.core.security import audit
from app.config import Config
from app.core.camera_manager import CameraManager
from app.protocols.generic_rtsp import RTSPProbe
from app.ui.async_task import run_async
from app.ui.styles import style_table

NVR_COLS = ["ID", "Name", "Brand", "Host", "Channels", "Cameras", "Location", "Actions"]


def probe_nvr(d: dict) -> dict:
    """Connect to an NVR. Returns {"info": str, "names": {channel: name}}; raises on failure."""
    brand = d["brand"]
    if brand == "Hikvision":
        from app.protocols.hikvision import HikvisionAPI
        api = HikvisionAPI(d["host"], d["http_port"], d["username"], d["password"])
    elif brand in ("Dahua", "CP Plus"):
        from app.protocols.dahua import DahuaAPI
        api = DahuaAPI(d["host"], d["http_port"], d["username"], d["password"])
    else:
        url = Config.build_rtsp_url(brand, d["host"], 554, d["username"], d["password"], 1)
        ok, msg = RTSPProbe(url).test_connection()
        if not ok:
            raise ConnectionError(f"Channel 1: {msg}")
        return {"info": f"Channel 1 OK. {msg}", "names": {}}
    info = api.get_device_info()
    names = api.get_channel_names()
    parts = [p for p in (info.get("model"), info.get("serial") and f"S/N {info['serial']}",
                         info.get("firmware")) if p]
    return {"info": " · ".join(parts) or "Connected", "names": names}


class NVRDialog(QDialog):
    def __init__(self, data: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit NVR / DVR" if data else "Add NVR / DVR")
        self.setMinimumWidth(460)
        self.names = {}
        self._build()
        if data:
            self._populate(data)

    def _build(self):
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. Main building NVR")
        self._brand = QComboBox()
        self._brand.addItems([b for b in Config.CAMERA_BRANDS if b != "Generic RTSP"])
        self._host = QLineEdit()
        self._host.setPlaceholderText("192.168.1.50")
        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(8000)
        self._http = QSpinBox()
        self._http.setRange(1, 65535)
        self._http.setValue(80)
        self._user = QLineEdit("admin")
        self._pwd = QLineEdit()
        self._pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self._ch = QSpinBox()
        self._ch.setRange(1, 256)
        self._ch.setValue(8)
        self._loc = QLineEdit()
        for label, w in (("Name:", self._name), ("Brand:", self._brand), ("IP address:", self._host),
                         ("HTTP port:", self._http), ("SDK / service port:", self._port),
                         ("Username:", self._user), ("Password:", self._pwd),
                         ("Number of channels:", self._ch), ("Location:", self._loc)):
            form.addRow(label, w)
        lay.addLayout(form)
        row = QHBoxLayout()
        self._btn_test = QPushButton("Test connection")
        self._btn_test.setObjectName("secondary_btn")
        self._btn_test.clicked.connect(self._test)
        self._result = QLabel()
        self._result.setWordWrap(True)
        row.addWidget(self._btn_test)
        row.addWidget(self._result, 1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _populate(self, d):
        self._name.setText(d.get("name", ""))
        idx = self._brand.findText(d.get("brand", ""))
        if idx >= 0:
            self._brand.setCurrentIndex(idx)
        self._host.setText(d.get("host", ""))
        self._port.setValue(d.get("port") or 8000)
        self._http.setValue(d.get("http_port") or 80)
        self._user.setText(d.get("username", "admin"))
        self._pwd.setText(d.get("password", ""))
        self._ch.setValue(d.get("total_channels") or 4)
        self._loc.setText(d.get("location", ""))

    def _test(self):
        if not self._host.text().strip():
            self._result.setText("Enter the IP address first.")
            return
        self._btn_test.setEnabled(False)
        self._result.setStyleSheet("color:#9098b1;")
        self._result.setText("Connecting…")
        run_async(probe_nvr, self._on_test, self.get_data(), receiver=self)

    def _on_test(self, result, error):
        self._btn_test.setEnabled(True)
        if error:
            self._result.setStyleSheet("color:#ef4565;")
            self._result.setText(f"Failed: {error}")
            return
        self.names = result["names"]
        if self.names and max(self.names) > self._ch.value():
            self._ch.setValue(max(self.names))
        extra = f" · {len(self.names)} named channel(s)" if self.names else ""
        self._result.setStyleSheet("color:#27ae60;")
        self._result.setText(f"Connected: {result['info']}{extra}")

    def _accept(self):
        if not self._name.text().strip() or not self._host.text().strip():
            QMessageBox.warning(self, "Missing details", "Name and IP address are required.")
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "name": self._name.text().strip(), "brand": self._brand.currentText(),
            "host": self._host.text().strip(), "port": self._port.value(),
            "http_port": self._http.value(), "username": self._user.text(),
            "password": self._pwd.text(), "total_channels": self._ch.value(),
            "location": self._loc.text(),
        }


class NVRManagement(QWidget):
    cameras_changed = pyqtSignal()

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
        hdr.addWidget(title)
        hdr.addStretch()
        self._btn_add = QPushButton("+ Add NVR / DVR")
        self._btn_add.setObjectName("primary_btn")
        self._btn_add.clicked.connect(self._add)
        hdr.addWidget(self._btn_add)
        root.addLayout(hdr)
        sub = QLabel("Add a recorder, then use 'Import channels' to create one camera per "
                     "channel. Imported cameras follow the recorder's IP and login.")
        sub.setObjectName("page_subtitle")
        sub.setWordWrap(True)
        root.addWidget(sub)
        self._table = QTableWidget(0, len(NVR_COLS))
        style_table(self._table)
        self._table.setHorizontalHeaderLabels(NVR_COLS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        root.addWidget(self._table)

    def refresh(self):
        self._table.setRowCount(0)
        for nvr in self.mgr.get_all_nvrs():
            r = self._table.rowCount()
            self._table.insertRow(r)
            for c, v in enumerate([str(nvr["id"]), nvr["name"], nvr["brand"], nvr["host"],
                                   str(nvr["total_channels"]), str(nvr["camera_count"]),
                                   nvr["location"]]):
                self._table.setItem(r, c, QTableWidgetItem(v))
            act = QWidget()
            al = QHBoxLayout(act)
            al.setContentsMargins(4, 2, 4, 2)
            nid = nvr["id"]
            for text, name, fn in (("Import channels", "success_btn", self._import),
                                   ("Edit", "secondary_btn", self._edit),
                                   ("Delete", "danger_btn", self._delete)):
                b = QPushButton(text)
                b.setObjectName(name)
                b.clicked.connect(lambda _, i=nid, f=fn: f(i))
                al.addWidget(b)
            self._table.setCellWidget(r, len(NVR_COLS) - 1, act)

    def _add(self):
        dlg = NVRDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            nvr = self.mgr.add_nvr(dlg.get_data())
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        audit(self.db, "nvr_add", f"{dlg.get_data()['name']} ({dlg.get_data()['host']})")
        self.refresh()
        if QMessageBox.question(
                self, "Import channels",
                f"Create {dlg.get_data()['total_channels']} cameras for this recorder's "
                "channels now?", QMessageBox.StandardButton.Yes |
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self._do_import(nvr.id, dlg.names)

    def _edit(self, nvr_id: int):
        nvr = self.mgr.get_nvr(nvr_id)
        if not nvr:
            return
        dlg = NVRDialog(nvr, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.mgr.update_nvr(nvr_id, dlg.get_data())
            audit(self.db, "nvr_edit", f"{nvr['name']} (id {nvr_id})")
            self.refresh()
            self.cameras_changed.emit()

    def _import(self, nvr_id: int):
        nvr = self.mgr.get_nvr(nvr_id)
        if not nvr:
            return
        self.setCursor(Qt.CursorShape.WaitCursor)
        # Fetch channel names if the recorder supports it; import even if not.
        run_async(lambda d: probe_nvr(d)["names"],
                  lambda names, err: self._do_import(nvr_id, names or {}), nvr, receiver=self)

    def _do_import(self, nvr_id: int, names: dict):
        self.unsetCursor()
        n = self.mgr.import_nvr_channels(nvr_id, names)
        audit(self.db, "nvr_import", f"NVR id {nvr_id}: {n} camera(s) created")
        self.refresh()
        self.cameras_changed.emit()
        QMessageBox.information(self, "Channels imported",
                                f"Created {n} camera(s)." if n else
                                "Every channel already has a camera.")

    def _delete(self, nvr_id: int):
        nvr = next((n for n in self.mgr.get_all_nvrs() if n["id"] == nvr_id), None)
        if not nvr:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Delete recorder")
        box.setText(f"Delete '{nvr['name']}'?")
        also = QCheckBox(f"Also delete its {nvr['camera_count']} camera(s)")
        also.setChecked(True)
        if nvr["camera_count"]:
            box.setCheckBox(also)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        with_cams = also.isChecked() and nvr["camera_count"] > 0
        self.mgr.delete_nvr(nvr_id, delete_cameras=with_cams)
        audit(self.db, "nvr_delete", f"{nvr['name']}" + (" and its cameras" if with_cams else ""))
        self.refresh()
        self.cameras_changed.emit()
