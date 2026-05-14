import os
import pickle
import cv2
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QDialog,
    QFormLayout, QLineEdit, QMessageBox, QDialogButtonBox,
    QFileDialog, QScrollArea, QGridLayout, QSplitter
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap
from app.core.face_engine import FaceEngine
from app.config import Config

PERSON_COLS = ["ID", "Name", "Emp ID", "Department", "Role", "Enrolled", "Actions"]


class PersonDialog(QDialog):
    def __init__(self, data: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Person" if not data else "Edit Person")
        self.setMinimumWidth(400)
        form = QFormLayout(self)
        def lbl(t): l = QLabel(t); l.setObjectName("form_label"); return l
        self._name = QLineEdit()
        self._emp  = QLineEdit()
        self._dept = QLineEdit()
        self._role = QLineEdit()
        self._email= QLineEdit()
        self._phone= QLineEdit()
        form.addRow(lbl("Full Name:"),   self._name)
        form.addRow(lbl("Employee ID:"), self._emp)
        form.addRow(lbl("Department:"),  self._dept)
        form.addRow(lbl("Role:"),        self._role)
        form.addRow(lbl("Email:"),       self._email)
        form.addRow(lbl("Phone:"),       self._phone)
        if data:
            self._name.setText(data.get("name",""))
            self._emp.setText(data.get("employee_id","") or "")
            self._dept.setText(data.get("department","") or "")
            self._role.setText(data.get("role","") or "")
            self._email.setText(data.get("email","") or "")
            self._phone.setText(data.get("phone","") or "")
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)

    def get_data(self):
        return {
            "name":        self._name.text().strip(),
            "employee_id": self._emp.text().strip() or None,
            "department":  self._dept.text().strip(),
            "role":        self._role.text().strip(),
            "email":       self._email.text().strip(),
            "phone":       self._phone.text().strip(),
        }


class FaceEnrollment(QWidget):
    def __init__(self, db, face_engine: FaceEngine, parent=None):
        super().__init__(parent)
        self.db = db
        self.fe = face_engine
        self._selected_pid: int = None
        self._cap = None
        self._capture_timer = QTimer()
        self._capture_timer.timeout.connect(self._update_camera_preview)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel("Face Enrollment")
        title.setObjectName("page_title")
        hdr.addWidget(title); hdr.addStretch()
        btn_add = QPushButton("+ Add Person")
        btn_add.setObjectName("primary_btn")
        btn_add.clicked.connect(self._add_person)
        hdr.addWidget(btn_add)
        root.addLayout(hdr)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: person table
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self._table = QTableWidget(0, len(PERSON_COLS))
        self._table.setHorizontalHeaderLabels(PERSON_COLS)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.itemSelectionChanged.connect(self._on_select)
        ll.addWidget(self._table)
        splitter.addWidget(left)

        # Right: enrollment panel
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setSpacing(8)

        self._preview = QLabel("Select a person and capture face")
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setFixedSize(300, 240)
        self._preview.setStyleSheet("background:#09090f; border:1px solid #252550; border-radius:4px;")
        rl.addWidget(self._preview, alignment=Qt.AlignmentFlag.AlignHCenter)

        btn_cam = QPushButton("Start Camera Capture")
        btn_cam.setObjectName("secondary_btn")
        btn_cam.clicked.connect(self._toggle_camera)
        self._btn_cam = btn_cam

        btn_snap = QPushButton("Capture & Enroll")
        btn_snap.setObjectName("primary_btn")
        btn_snap.clicked.connect(self._capture_enroll)

        btn_file = QPushButton("Enroll from Image File")
        btn_file.setObjectName("secondary_btn")
        btn_file.clicked.connect(self._enroll_from_file)

        self._status_lbl = QLabel("No person selected")
        self._status_lbl.setStyleSheet("color:#666; font-size:11px;")

        rl.addWidget(btn_cam)
        rl.addWidget(btn_snap)
        rl.addWidget(btn_file)
        rl.addWidget(self._status_lbl)
        rl.addStretch()
        splitter.addWidget(right)
        splitter.setSizes([600, 380])
        root.addWidget(splitter)

    def refresh(self):
        from app.models import Person
        session = self.db.get_session()
        try:
            persons = session.query(Person).filter_by(is_active=True).all()
            self._table.setRowCount(0)
            for p in persons:
                r = self._table.rowCount()
                self._table.insertRow(r)
                enrolled = "Yes" if p.face_encoding else "No"
                for c, v in enumerate([str(p.id), p.name, p.employee_id or "",
                                        p.department or "", p.role or "", enrolled]):
                    item = QTableWidgetItem(v)
                    item.setData(Qt.ItemDataRole.UserRole, p.id)
                    self._table.setItem(r, c, item)
                act = QWidget(); al = QHBoxLayout(act); al.setContentsMargins(2,2,2,2)
                pid = p.id
                eb = QPushButton("Edit"); eb.setObjectName("secondary_btn"); eb.setFixedWidth(45)
                db_ = QPushButton("Del"); db_.setObjectName("danger_btn"); db_.setFixedWidth(40)
                eb.clicked.connect(lambda _, i=pid: self._edit_person(i))
                db_.clicked.connect(lambda _, i=pid: self._del_person(i))
                al.addWidget(eb); al.addWidget(db_)
                self._table.setCellWidget(r, len(PERSON_COLS)-1, act)
        finally:
            session.close()
        self._reload_face_engine()

    def _on_select(self):
        rows = self._table.selectedItems()
        if rows:
            self._selected_pid = rows[0].data(Qt.ItemDataRole.UserRole)
            self._status_lbl.setText(f"Person ID {self._selected_pid} selected")

    def _add_person(self):
        dlg = PersonDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            if not data["name"]:
                QMessageBox.warning(self, "Error", "Name is required.")
                return
            from app.models import Person
            session = self.db.get_session()
            try:
                p = Person(**data)
                session.add(p); session.commit()
            finally:
                session.close()
            self.refresh()

    def _edit_person(self, pid: int):
        from app.models import Person
        session = self.db.get_session()
        try:
            p = session.query(Person).get(pid)
            if not p: return
            data = {
                "name": p.name, "employee_id": p.employee_id,
                "department": p.department, "role": p.role,
                "email": p.email, "phone": p.phone,
            }
        finally:
            session.close()
        dlg = PersonDialog(data=data, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_data = dlg.get_data()
            session = self.db.get_session()
            try:
                p = session.query(Person).get(pid)
                for k, v in new_data.items():
                    setattr(p, k, v)
                session.commit()
            finally:
                session.close()
            self.refresh()

    def _del_person(self, pid: int):
        if QMessageBox.question(self, "Confirm", "Delete this person?",
                                QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No) == \
                QMessageBox.StandardButton.Yes:
            from app.models import Person
            session = self.db.get_session()
            try:
                p = session.query(Person).get(pid)
                if p: session.delete(p); session.commit()
            finally:
                session.close()
            self.refresh()

    def _toggle_camera(self):
        if self._cap and self._cap.isOpened():
            self._cap.release(); self._cap = None
            self._capture_timer.stop()
            self._btn_cam.setText("Start Camera Capture")
            self._preview.setText("Camera stopped")
        else:
            self._cap = cv2.VideoCapture(0)
            if self._cap.isOpened():
                self._capture_timer.start(30)
                self._btn_cam.setText("Stop Camera")
            else:
                self._status_lbl.setText("No webcam found")

    def _update_camera_preview(self):
        if self._cap and self._cap.isOpened():
            ret, frame = self._cap.read()
            if ret:
                self._last_frame = frame
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                img = QImage(rgb.data.tobytes(), w, h, ch*w, QImage.Format.Format_RGB888)
                pix = QPixmap.fromImage(img).scaled(
                    self._preview.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self._preview.setPixmap(pix)

    def _capture_enroll(self):
        if not self._selected_pid:
            QMessageBox.warning(self, "Select Person", "Please select a person first.")
            return
        frame = getattr(self, "_last_frame", None)
        if frame is None:
            QMessageBox.warning(self, "No Frame", "Start camera first.")
            return
        self._do_enroll(frame)

    def _enroll_from_file(self):
        if not self._selected_pid:
            QMessageBox.warning(self, "Select Person", "Please select a person first.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Image", "", "Images (*.jpg *.jpeg *.png *.bmp)"
        )
        if not path:
            return
        frame = cv2.imread(path)
        if frame is None:
            QMessageBox.warning(self, "Error", "Cannot read image.")
            return
        self._do_enroll(frame)

    def _do_enroll(self, frame):
        enc = self.fe.enroll_face(frame)
        if enc is None:
            QMessageBox.warning(self, "No Face", "No face detected in image.")
            return
        photo_path = self.fe.save_face_photo(frame, self._selected_pid)
        from app.models import Person
        session = self.db.get_session()
        try:
            p = session.query(Person).get(self._selected_pid)
            if p:
                p.face_encoding = enc
                p.photo_path = photo_path
                session.commit()
                self._status_lbl.setText("Face enrolled successfully!")
                self.refresh()
        finally:
            session.close()

    def _reload_face_engine(self):
        from app.models import Person
        session = self.db.get_session()
        try:
            persons = session.query(Person).filter(
                Person.face_encoding.isnot(None)
            ).all()
            data = [{"id": p.id, "name": p.name,
                     "face_encoding": p.face_encoding} for p in persons]
        finally:
            session.close()
        self.fe.load_known_faces(data)
