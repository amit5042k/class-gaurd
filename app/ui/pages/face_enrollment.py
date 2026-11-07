import cv2
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget
)
from sqlalchemy.exc import IntegrityError

from app.core.security import audit
from app.core.face_engine import FaceEngine, decode_encodings
from app.core.stream_manager import to_qimage
from app.ui.async_task import run_async
from app.ui.widgets.zone_canvas import VideoCanvas
from app.ui.styles import style_table

PERSON_COLS = ["ID", "Name", "Emp ID", "Department", "Role", "Face photos", "Actions"]


class PersonDialog(QDialog):
    def __init__(self, data: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Person" if data else "Add Person")
        self.setMinimumWidth(400)
        form = QFormLayout(self)
        self._fields = {}
        for key, label in (("name", "Full Name:"), ("employee_id", "Employee / Roll No:"),
                           ("department", "Department / Class:"), ("role", "Role:"),
                           ("email", "Email:"), ("phone", "Phone:")):
            edit = QLineEdit((data or {}).get(key) or "")
            self._fields[key] = edit
            form.addRow(QLabel(label), edit)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _accept(self):
        if not self._fields["name"].text().strip():
            QMessageBox.warning(self, "Missing name", "Full name is required.")
            return
        self.accept()

    def get_data(self):
        data = {k: e.text().strip() for k, e in self._fields.items()}
        data["employee_id"] = data["employee_id"] or None
        return data


class FaceEnrollment(QWidget):
    faces_changed = pyqtSignal()

    def __init__(self, db, face_engine: FaceEngine, parent=None):
        super().__init__(parent)
        self.db = db
        self.fe = face_engine
        self._selected_pid = None
        self._feed = None              # CameraFeed while a picture source is on
        self._last_frame = None
        self._busy = False
        self._cameras = []
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel("Face Enrollment")
        title.setObjectName("page_title")
        hdr.addWidget(title)
        hdr.addStretch()
        btn_add = QPushButton("+ Add Person")
        btn_add.setObjectName("primary_btn")
        btn_add.clicked.connect(self._add_person)
        hdr.addWidget(btn_add)
        root.addLayout(hdr)

        if not self.fe.recognition_available:
            note = QLabel("Face recognition is not installed, so people can't be enrolled "
                          "or recognised and attendance won't be recorded. Install it with: "
                          "pip install -r requirements-ai.txt")
            note.setWordWrap(True)
            note.setStyleSheet("background:#3a2a10; color:#f3c068; padding:8px; border-radius:4px;")
            root.addWidget(note)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._table = QTableWidget(0, len(PERSON_COLS))
        style_table(self._table)
        self._table.setHorizontalHeaderLabels(PERSON_COLS)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.itemSelectionChanged.connect(self._on_select)
        splitter.addWidget(self._table)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setSpacing(8)
        self._preview = VideoCanvas("Select a person, then capture or choose a photo")
        self._preview.setMinimumSize(320, 240)
        rl.addWidget(self._preview, 1)
        src = QHBoxLayout()
        src.addWidget(QLabel("Picture from:"))
        self._source = QComboBox()
        self._source.setToolTip("This PC's webcam, or any CCTV camera: the person stands in "
                                "front of it, about 1–2 m away")
        self._source.addItem("Webcam on this PC", None)
        src.addWidget(self._source, 1)
        self._btn_cam = QPushButton("Start")
        self._btn_cam.setObjectName("secondary_btn")
        self._btn_cam.clicked.connect(self._toggle_camera)
        src.addWidget(self._btn_cam)
        self._btn_snap = QPushButton("Capture && Enroll")
        self._btn_snap.setObjectName("primary_btn")
        self._btn_snap.clicked.connect(self._capture_enroll)
        self._btn_file = QPushButton("Enroll from Photo File…")
        self._btn_file.setObjectName("secondary_btn")
        self._btn_file.clicked.connect(self._enroll_from_file)
        self._btn_clear = QPushButton("Delete this person's face photos")
        self._btn_clear.clicked.connect(self._clear_faces)
        tip = QLabel("Tip: enroll 3–5 photos per person (straight on, slightly left, "
                     "slightly right, with and without glasses) for reliable recognition.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#777; font-size:11px;")
        self._status = QLabel("No person selected")
        self._status.setWordWrap(True)
        rl.addLayout(src)
        for w in (self._btn_snap, self._btn_file, self._btn_clear, tip, self._status):
            rl.addWidget(w)
        splitter.addWidget(right)
        splitter.setSizes([600, 380])
        root.addWidget(splitter, 1)
        self._update_buttons()

    # -------------------------------------------------------------- people
    def refresh(self):
        from app.models import Person
        session = self.db.get_session()
        try:
            persons = session.query(Person).filter_by(is_active=True).order_by(Person.name).all()
            rows = [(p.id, p.name, p.employee_id or "", p.department or "", p.role or "",
                     len(decode_encodings(p.face_encoding))) for p in persons]
        finally:
            session.close()
        selected = self._selected_pid
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for pid, name, emp, dept, role, n in rows:
            r = self._table.rowCount()
            self._table.insertRow(r)
            for c, v in enumerate([str(pid), name, emp, dept, role,
                                   str(n) if n else "Not enrolled"]):
                item = QTableWidgetItem(v)
                item.setData(Qt.ItemDataRole.UserRole, pid)
                self._table.setItem(r, c, item)
            act = QWidget()
            al = QHBoxLayout(act)
            al.setContentsMargins(2, 2, 2, 2)
            eb = QPushButton("Edit")
            eb.setObjectName("secondary_btn")
            db_ = QPushButton("Remove")
            db_.setObjectName("danger_btn")
            eb.clicked.connect(lambda _, i=pid: self._edit_person(i))
            db_.clicked.connect(lambda _, i=pid: self._remove_person(i))
            al.addWidget(eb)
            al.addWidget(db_)
            self._table.setCellWidget(r, len(PERSON_COLS) - 1, act)
            if pid == selected:
                self._table.selectRow(r)
        self._table.blockSignals(False)
        if selected not in {r[0] for r in rows}:
            self._selected_pid = None
        self._reload_engine()
        self._update_buttons()

    def _on_select(self):
        items = self._table.selectedItems()
        self._selected_pid = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        if self._selected_pid is not None:
            self._status.setText(f"Selected: {items[1].text() if len(items) > 1 else ''}")
        self._update_buttons()

    def _update_buttons(self):
        ok = self._selected_pid is not None and self.fe.recognition_available and not self._busy
        self._btn_snap.setEnabled(ok and self._feed is not None
                                  and self._feed.last_frame is not None)
        self._btn_file.setEnabled(ok)
        self._btn_clear.setEnabled(self._selected_pid is not None and not self._busy)

    def _add_person(self):
        dlg = PersonDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        from app.models import Person
        session = self.db.get_session()
        try:
            p = Person(**dlg.get_data())
            session.add(p)
            session.commit()
            self._selected_pid = p.id
            audit(self.db, "person_add", p.name)
        except IntegrityError:
            session.rollback()
            QMessageBox.warning(self, "Duplicate", "Another person already has that Employee / Roll No.")
            return
        finally:
            session.close()
        self.refresh()

    def _edit_person(self, pid: int):
        from app.models import Person
        session = self.db.get_session()
        try:
            p = session.get(Person, pid)
            if not p:
                return
            data = {k: getattr(p, k) for k in
                    ("name", "employee_id", "department", "role", "email", "phone")}
        finally:
            session.close()
        dlg = PersonDialog(data=data, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        session = self.db.get_session()
        try:
            p = session.get(Person, pid)
            for k, v in dlg.get_data().items():
                setattr(p, k, v)
            session.commit()
        except IntegrityError:
            session.rollback()
            QMessageBox.warning(self, "Duplicate", "Another person already has that Employee / Roll No.")
        finally:
            session.close()
        self.refresh()

    def _remove_person(self, pid: int):
        if QMessageBox.question(
                self, "Remove person",
                "Remove this person? Their face data is deleted; past attendance "
                "records are kept.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        from app.models import Person
        session = self.db.get_session()
        try:
            p = session.get(Person, pid)
            if p:
                p.is_active = False
                p.face_encoding = None
                if p.employee_id:
                    p.employee_id = None  # frees the ID for re-use
                session.commit()
                audit(self.db, "person_remove", p.name)
        finally:
            session.close()
        self.refresh()

    def _clear_faces(self):
        if self._selected_pid is None:
            return
        from app.models import Person
        session = self.db.get_session()
        try:
            p = session.get(Person, self._selected_pid)
            if p:
                p.face_encoding = None
                session.commit()
                audit(self.db, "face_clear", p.name)
        finally:
            session.close()
        self._status.setText("Face photos deleted for this person.")
        self.refresh()

    # ------------------------------------------------------------ capture
    def set_cameras(self, cameras):
        """CCTV cameras offered as picture sources (besides the webcam)."""
        self._cameras = list(cameras)
        current = self._source.currentData()
        self._source.blockSignals(True)
        self._source.clear()
        self._source.addItem("Webcam on this PC", None)
        for c in self._cameras:
            self._source.addItem(f"Camera: {c['name']}", c["id"])
        idx = self._source.findData(current)
        self._source.setCurrentIndex(max(0, idx))
        self._source.blockSignals(False)

    def _toggle_camera(self):
        if self._feed is not None:
            self._stop_camera()
            return
        from app.core.camera_feed import CameraFeed
        cid = self._source.currentData()
        cam = next((c for c in self._cameras if c["id"] == cid), None)
        source = {"kind": "camera", "camera": cam} if cam else {"kind": "webcam"}
        feed = CameraFeed(source, self.fe)
        feed.frame_ready.connect(self._show_frame)
        feed.status.connect(self._status.setText)
        feed.failed.connect(self._feed_failed)
        self._feed = feed
        self._source.setEnabled(False)
        self._btn_cam.setText("Stop")
        self._preview.clear_image("Connecting…")
        feed.start()
        self._update_buttons()

    def _feed_failed(self, message: str):
        self._stop_camera()
        self._status.setText(message)
        self._preview.clear_image(message)

    def _stop_camera(self):
        feed, self._feed = self._feed, None
        if feed is not None:
            for sig in (feed.frame_ready, feed.status, feed.failed):
                try:
                    sig.disconnect()
                except TypeError:
                    pass
            feed.stop()
            feed.wait(200)
            self._retired = getattr(self, "_retired", [])
            if feed.isRunning():
                self._retired.append(feed)
                feed.finished.connect(lambda f=feed: f in self._retired and
                                      self._retired.remove(f))
        self._source.setEnabled(True)
        self._btn_cam.setText("Start")
        self._update_buttons()

    def hideEvent(self, event):
        self._stop_camera()
        super().hideEvent(event)

    def _show_frame(self, frame):
        self._preview.set_image(to_qimage(frame))
        if not self._btn_snap.isEnabled():
            self._update_buttons()

    def _capture_enroll(self):
        feed = self._feed
        if feed is None or feed.last_frame is None:
            return
        frame = feed.last_frame.copy()
        if self._source.currentData() is not None:
            # CCTV picture: may show several people; enroll the biggest face.
            from app.core.camera_feed import largest_face_crop
            frame = largest_face_crop(frame, list(feed.boxes))
        self._enroll(frame)

    def _enroll_from_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select photo", "", "Images (*.jpg *.jpeg *.png *.bmp)")
        if not path:
            return
        frame = cv2.imread(path)
        if frame is None:
            QMessageBox.warning(self, "Error", "Could not read that image file.")
            return
        self._preview.set_image(to_qimage(frame))
        self._enroll(frame)

    def _enroll(self, frame):
        pid = self._selected_pid
        if pid is None:
            return
        self._busy = True
        self._update_buttons()
        self._status.setText("Reading the face…")
        run_async(self.fe.compute_encoding,
                  lambda res, err: self._enroll_done(pid, frame, res, err),
                  frame, receiver=self)

    def _enroll_done(self, pid, frame, result, error):
        self._busy = False
        self._update_buttons()
        encoding, message = result if result else (None, str(error))
        if encoding is None:
            self._status.setText(message)
            QMessageBox.warning(self, "Enrollment failed", message)
            return
        from app.models import Person
        session = self.db.get_session()
        try:
            p = session.get(Person, pid)
            if p is None:
                return
            p.face_encoding = FaceEngine.add_encoding(p.face_encoding, encoding)
            p.photo_path = self.fe.save_face_photo(frame, pid)
            count = len(decode_encodings(p.face_encoding))
            session.commit()
            audit(self.db, "face_enroll", f"{p.name} ({count} photo(s))")
        finally:
            session.close()
        self._status.setText(f"Face enrolled. This person now has {count} photo(s).")
        self.refresh()

    def _reload_engine(self):
        from app.models import Person
        session = self.db.get_session()
        try:
            persons = session.query(Person).filter(
                Person.is_active.is_(True), Person.face_encoding.isnot(None)).all()
            data = [{"id": p.id, "name": p.name, "face_encoding": p.face_encoding}
                    for p in persons]
        finally:
            session.close()
        self.fe.load_known_faces(data)
        self.faces_changed.emit()
