"""Remote configuration window for an NVR / DVR or a camera."""
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget
)

from app.core.security import audit
from app.protocols.device_config import HIK_QUALITY, device_config, recommended
from app.ui.async_task import run_async
from app.ui.styles import style_table

CODECS = ["H.264", "H.265"]
FPS_DEFAULT = [25, 20, 15, 12, 10, 8, 6, 4, 2, 1]


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color:#999; font-size:11px;")
    return lbl


class DeviceConfigDialog(QDialog):
    def __init__(self, dev: Dict, channel: int = 1, is_nvr: bool = False, db=None,
                 parent=None):
        super().__init__(parent)
        self.dev = dev
        self.db = db
        self.is_nvr = is_nvr
        self.cfg = device_config(dev)
        self._video: Optional[Dict] = None
        self.setWindowTitle(f"Configure — {dev.get('name') or dev.get('host')}")
        self.resize(760, 600)
        root = QVBoxLayout(self)
        head = QLabel(f"<b>{dev.get('name', '')}</b>  ·  {dev.get('brand')}  ·  {dev.get('host')}"
                      f":{dev.get('http_port') or 80}")
        root.addWidget(head)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_video(channel), "Video / encoding")
        if is_nvr:
            self.tabs.addTab(self._build_channels(), "Cameras")
            self.tabs.addTab(self._build_storage(), "Storage")
        self.tabs.addTab(self._build_device(), "Device")
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close)
        root.addLayout(row)
        self.tabs.currentChanged.connect(self._on_tab)
        self.load_video()

    # ------------------------------------------------------------- helpers
    def _busy(self, text: str):
        self.status.setStyleSheet("color:#f3c068;")
        self.status.setText(text)

    def _ok(self, text: str):
        self.status.setStyleSheet("color:#27ae60;")
        self.status.setText(text)

    def _fail(self, error):
        self.status.setStyleSheet("color:#ef4565;")
        self.status.setText(str(error))

    def _run(self, fn, done, *args, busy="Talking to the device…"):
        self._busy(busy)

        def finish(result, error):
            if error:
                self._fail(error)
            else:
                done(result)
        run_async(fn, finish, *args, receiver=self)

    # ---------------------------------------------------------------- video
    def _build_video(self, channel: int) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        top.addWidget(QLabel("Channel:"))
        self.channel = QSpinBox()
        self.channel.setRange(1, 256)
        self.channel.setValue(int(channel or 1))
        top.addWidget(self.channel)
        top.addWidget(QLabel("Stream:"))
        self.stream = QComboBox()
        self.stream.addItem("Sub stream (used for Live View)", True)
        self.stream.addItem("Main stream (recording)", False)
        top.addWidget(self.stream)
        load = QPushButton("Load")
        load.setObjectName("secondary_btn")
        load.clicked.connect(self.load_video)
        top.addWidget(load)
        top.addStretch()
        lay.addLayout(top)
        self.channel.valueChanged.connect(lambda _v: self.load_video())
        self.stream.currentIndexChanged.connect(lambda _i: self.load_video())

        form = QFormLayout()
        self.codec = QComboBox()
        self.resolution = QComboBox()
        self.bitrate_type = QComboBox()
        self.bitrate = QSpinBox()
        self.bitrate.setRange(32, 16384)
        self.bitrate.setSuffix(" Kbps")
        self.bitrate.setSingleStep(64)
        self.fps = QComboBox()
        self.gop = QSpinBox()
        self.gop.setRange(1, 400)
        self.gop.valueChanged.connect(self._gop_hint)
        self.fps.currentIndexChanged.connect(lambda _i: self._gop_hint())
        self.quality = QComboBox()
        self.smart = QCheckBox("H.264+ / H.265+ / Smart Codec")
        self.gop_lbl = _hint("")
        form.addRow("Video encoding:", self.codec)
        form.addRow("Resolution:", self.resolution)
        form.addRow("Bitrate type:", self.bitrate_type)
        form.addRow("Max. bitrate:", self.bitrate)
        form.addRow("Frame rate:", self.fps)
        form.addRow("I-frame interval:", self.gop)
        form.addRow("", self.gop_lbl)
        form.addRow("Video quality:", self.quality)
        form.addRow("", self.smart)
        lay.addLayout(form)
        lay.addWidget(_hint("For ClassGuard's live view and AI, use H.264 with H.264+ / Smart "
                            "Codec off and an I-frame (full picture) every 1–2 seconds on the "
                            "sub stream. Otherwise pictures can stay grey or start slowly."))
        btns = QHBoxLayout()
        self.btn_reco = QPushButton("Use recommended settings")
        self.btn_reco.clicked.connect(self._apply_recommended)
        self.btn_save = QPushButton("Save to device")
        self.btn_save.setObjectName("primary_btn")
        self.btn_save.clicked.connect(self.save_video)
        btns.addWidget(self.btn_reco)
        btns.addStretch()
        btns.addWidget(self.btn_save)
        lay.addLayout(btns)
        lay.addStretch()
        self._enable_video(False)
        return w

    def _enable_video(self, on: bool):
        for wdg in (self.codec, self.resolution, self.bitrate_type, self.bitrate, self.fps,
                    self.gop, self.quality, self.smart, self.btn_reco, self.btn_save):
            wdg.setEnabled(on)

    def _sel(self):
        return self.channel.value(), self.stream.currentData()

    def load_video(self):
        ch, sub = self._sel()
        self._enable_video(False)

        def fetch():
            video = self.cfg.get_video(ch, sub)
            try:
                opts = self.cfg.video_options(ch, sub)
            except Exception:
                opts = {}
            return video, opts
        self._run(fetch, lambda r: self._show_video(*r), busy="Reading video settings…")

    @staticmethod
    def _fill(combo: QComboBox, items: List, current, label=str):
        combo.blockSignals(True)
        combo.clear()
        values = list(items)
        if current not in values and current not in (None, "", 0):
            values.insert(0, current)
        for v in values:
            combo.addItem(label(v), v)
        idx = combo.findData(current)
        combo.setCurrentIndex(max(0, idx))
        combo.blockSignals(False)

    def _show_video(self, video: Dict, opts: Dict):
        self._video = video
        self._fill(self.codec, opts.get("codec") or CODECS, video["codec"])
        res = opts.get("resolution") or []
        cur = (video["width"], video["height"])
        self._fill(self.resolution, res, cur, label=lambda r: f"{r[0]}×{r[1]}")
        self._fill(self.bitrate_type, opts.get("bitrate_type") or ["VBR", "CBR"],
                   video["bitrate_type"])
        self.bitrate.setValue(int(video["bitrate"] or 512))
        fps = opts.get("fps") or FPS_DEFAULT
        self._fill(self.fps, fps, video["fps"] if video["fps"] in fps else
                   (int(video["fps"]) if video["fps"] else None),
                   label=lambda f: f"{f:g} fps")
        self.gop.setValue(int(video["gop"] or 50))
        qualities = list(HIK_QUALITY) if self.dev.get("brand") == "Hikvision" else \
            [str(i) for i in range(1, 7)]
        self._fill(self.quality, qualities, video["quality"])
        self.smart.setVisible(video["smart_codec"] is not None)
        self.smart.setChecked(bool(video["smart_codec"]))
        self._gop_hint()
        self._enable_video(True)
        self._ok("Settings loaded from the device.")

    def _gop_hint(self):
        fps = self.fps.currentData() or 0
        if fps:
            secs = self.gop.value() / float(fps)
            warn = "  ⚠ long: live view can start slowly or stay grey" if secs > 4 else ""
            self.gop_lbl.setText(f"A full picture every {secs:.1f} s{warn}")
        else:
            self.gop_lbl.setText("")

    def _apply_recommended(self):
        if not self._video:
            return
        fps = self.fps.currentData() or self._video.get("fps") or 20
        rec = recommended({**self._video, "fps": fps})
        idx = self.codec.findData(rec["codec"])
        if idx >= 0:
            self.codec.setCurrentIndex(idx)
        self.gop.setValue(rec["gop"])
        if "smart_codec" in rec:
            self.smart.setChecked(False)
        self._busy("Recommended values filled in. Press Save to device.")

    def changes(self) -> Dict:
        v = self._video or {}
        out = {}
        if self.codec.currentData() != v.get("codec"):
            out["codec"] = self.codec.currentData()
        res = self.resolution.currentData()
        if res and tuple(res) != (v.get("width"), v.get("height")):
            out["width"], out["height"] = res
        if self.bitrate_type.currentData() != v.get("bitrate_type"):
            out["bitrate_type"] = self.bitrate_type.currentData()
        if self.bitrate.value() != v.get("bitrate"):
            out["bitrate"] = self.bitrate.value()
        if self.fps.currentData() and float(self.fps.currentData()) != float(v.get("fps") or 0):
            out["fps"] = self.fps.currentData()
        if self.gop.value() != v.get("gop"):
            out["gop"] = self.gop.value()
        if self.quality.currentData() != v.get("quality"):
            out["quality"] = self.quality.currentData()
        if v.get("smart_codec") is not None and self.smart.isChecked() != v["smart_codec"]:
            out["smart_codec"] = self.smart.isChecked()
        return out

    def save_video(self):
        ch, sub = self._sel()
        changes = self.changes()
        if not changes:
            self._ok("Nothing changed.")
            return

        def done(result):
            audit(self.db, "device_config",
                  f"{self.dev.get('name')} channel {ch} {'sub' if sub else 'main'} stream: "
                  + ", ".join(f"{k}={v}" for k, v in changes.items()))
            if result == "reboot":
                self._ok("Saved. The device needs a reboot for the change to take effect.")
            else:
                self._ok("Saved to the device.")
            self.load_video()
        self._run(self.cfg.set_video, done, ch, sub, changes, busy="Saving…")

    # ------------------------------------------------------------- channels
    def _build_channels(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.chan_table = QTableWidget(0, 4)
        style_table(self.chan_table)
        self.chan_table.setHorizontalHeaderLabels(["Channel", "Name", "Camera IP", "Status"])
        self.chan_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.chan_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.chan_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.chan_table.cellDoubleClicked.connect(self._open_channel)
        lay.addWidget(self.chan_table)
        lay.addWidget(_hint("Double-click a channel to edit its video settings."))
        self._channels_loaded = False
        return w

    def _load_channels(self):
        def show(chans):
            self._channels_loaded = True
            self.chan_table.setRowCount(0)
            for c in chans:
                r = self.chan_table.rowCount()
                self.chan_table.insertRow(r)
                state = "—" if c["online"] is None else ("Online" if c["online"] else "OFFLINE")
                for col, val in enumerate([str(c["id"]), c["name"], c["ip"], state]):
                    item = QTableWidgetItem(val)
                    item.setData(Qt.ItemDataRole.UserRole, c["id"])
                    if col == 3 and c["online"] is False:
                        item.setForeground(Qt.GlobalColor.red)
                    self.chan_table.setItem(r, col, item)
            online = sum(1 for c in chans if c["online"])
            self._ok(f"{len(chans)} channels, {online} online.")
        self._run(self.cfg.channels, show, busy="Reading the camera list…")

    def _open_channel(self, row: int, _col: int):
        item = self.chan_table.item(row, 0)
        if item:
            self.channel.setValue(int(item.data(Qt.ItemDataRole.UserRole)))
            self.tabs.setCurrentIndex(0)

    # -------------------------------------------------------------- storage
    def _build_storage(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.disk_table = QTableWidget(0, 4)
        style_table(self.disk_table)
        self.disk_table.setHorizontalHeaderLabels(["Disk", "Capacity", "Free", "Status"])
        self.disk_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.disk_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.disk_table)
        self._storage_loaded = False
        return w

    def _load_storage(self):
        def show(disks):
            self._storage_loaded = True
            self.disk_table.setRowCount(0)
            for d in disks:
                r = self.disk_table.rowCount()
                self.disk_table.insertRow(r)
                for col, val in enumerate([d["name"] or f"HDD {d['id']}",
                                           f"{d['capacity_gb']:.0f} GB",
                                           f"{d['free_gb']:.0f} GB", d["status"]]):
                    self.disk_table.setItem(r, col, QTableWidgetItem(val))
            self._ok(f"{len(disks)} disk(s)." if disks else "No disk information.")
        self._run(self.cfg.storage, show, busy="Reading disks…")

    # --------------------------------------------------------------- device
    def _build_device(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.info = QFormLayout()
        self.info_labels = {}
        for key, label in (("name", "Device name"), ("model", "Model"), ("serial", "Serial no."),
                           ("firmware", "Firmware"), ("type", "Type"), ("time", "Device time")):
            lbl = QLabel("—")
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.info_labels[key] = lbl
            self.info.addRow(f"{label}:", lbl)
        lay.addLayout(self.info)
        row = QHBoxLayout()
        sync = QPushButton("Set device time to this PC's time")
        sync.clicked.connect(self._sync_time)
        reboot = QPushButton("Reboot device")
        reboot.setObjectName("danger_btn")
        reboot.clicked.connect(self._reboot)
        row.addWidget(sync)
        row.addStretch()
        row.addWidget(reboot)
        lay.addLayout(row)
        lay.addWidget(_hint("Correct device time matters for playback and for matching "
                            "alerts to recordings."))
        lay.addStretch()
        self._device_loaded = False
        return w

    def _load_device(self):
        def fetch():
            info = self.cfg.device_info()
            try:
                info["time"] = self.cfg.get_time().get("local", "")
            except Exception:
                info["time"] = ""
            return info

        def show(info):
            self._device_loaded = True
            for k, lbl in self.info_labels.items():
                lbl.setText(info.get(k) or "—")
            self._ok("Device information loaded.")
        self._run(fetch, show, busy="Reading device information…")

    def _sync_time(self):
        def done(_r):
            audit(self.db, "device_config", f"{self.dev.get('name')}: time set to PC time")
            self._ok("Device time set.")
            self._load_device()
        self._run(self.cfg.sync_time, done, busy="Setting the time…")

    def _reboot(self):
        if QMessageBox.question(
                self, "Reboot device",
                f"Reboot {self.dev.get('name') or self.dev.get('host')}? All its cameras stop "
                "for a few minutes and it stops recording until it is back.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return

        def done(_r):
            audit(self.db, "device_config", f"{self.dev.get('name')}: reboot")
            self._ok("The device is rebooting. It will be back in a few minutes.")
        self._run(self.cfg.reboot, done, busy="Sending reboot…")

    def _on_tab(self, index: int):
        w = self.tabs.widget(index)
        if self.is_nvr and w is self.tabs.widget(1) and not self._channels_loaded:
            self._load_channels()
        elif self.is_nvr and w is self.tabs.widget(2) and not self._storage_loaded:
            self._load_storage()
        elif self.tabs.tabText(index) == "Device" and not self._device_loaded:
            self._load_device()
