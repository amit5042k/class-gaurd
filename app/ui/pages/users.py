from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget
)

from app.core.security import ROLE_LABELS, ROLES, Session, UserService, audit
from app.ui.login import ChangePasswordDialog
from app.ui.styles import style_table

ROLE_HELP = ("Viewer: watch cameras, play recordings, see attendance and alerts.\n"
             "Operator: also acknowledge alerts, record, PTZ, enroll faces, safety & "
             "expression monitoring.\n"
             "Administrator: also cameras, NVRs, settings, users and backups.")


class UserDialog(QDialog):
    def __init__(self, data=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit user" if data else "Add user")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.username = QLineEdit((data or {}).get("username", ""))
        self.username.setEnabled(data is None)
        self.full_name = QLineEdit((data or {}).get("full_name", ""))
        self.role = QComboBox()
        for r in ROLES:
            self.role.addItem(ROLE_LABELS[r], r)
        self.role.setCurrentIndex(max(0, self.role.findData((data or {}).get("role", "viewer"))))
        self.active = QCheckBox("Account enabled")
        self.active.setChecked((data or {}).get("is_active", True))
        form.addRow("Username:", self.username)
        form.addRow("Full name:", self.full_name)
        form.addRow("Role:", self.role)
        if data is None:
            self.pw = QLineEdit()
            self.pw.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow("Password:", self.pw)
        form.addRow("", self.active)
        lay.addLayout(form)
        help_lbl = QLabel(ROLE_HELP)
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet("color:#777; font-size:11px;")
        lay.addWidget(help_lbl)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class UsersPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.users = UserService(db)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        title = QLabel("Users & Audit Log")
        title.setObjectName("page_title")
        lay.addWidget(title)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        users_tab = QWidget()
        ul = QVBoxLayout(users_tab)
        row = QHBoxLayout()
        row.addStretch()
        add = QPushButton("+ Add user")
        add.setObjectName("primary_btn")
        add.clicked.connect(self._add)
        row.addWidget(add)
        ul.addLayout(row)
        self.table = QTableWidget(0, 6)
        style_table(self.table)
        self.table.setHorizontalHeaderLabels(["Username", "Name", "Role", "Status",
                                              "Last sign-in", "Actions"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        ul.addWidget(self.table)
        tabs.addTab(users_tab, "Users")

        audit_tab = QWidget()
        al = QVBoxLayout(audit_tab)
        frow = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by user, action or text…")
        self.filter.textChanged.connect(self.refresh_audit)
        frow.addWidget(self.filter, 1)
        ref = QPushButton("Refresh")
        ref.setObjectName("secondary_btn")
        ref.clicked.connect(self.refresh_audit)
        frow.addWidget(ref)
        al.addLayout(frow)
        self.audit_table = QTableWidget(0, 4)
        style_table(self.audit_table, 28)
        self.audit_table.setHorizontalHeaderLabels(["Time", "User", "Action", "Details"])
        self.audit_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.audit_table.horizontalHeader().setStretchLastSection(True)
        self.audit_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        al.addWidget(self.audit_table)
        tabs.addTab(audit_tab, "Audit log")
        self.refresh()

    def refresh(self):
        self.table.setRowCount(0)
        me = Session.current.id if Session.current else None
        for u in self.users.list():
            r = self.table.rowCount()
            self.table.insertRow(r)
            last = u["last_login"].strftime("%Y-%m-%d %H:%M") if u["last_login"] else "never"
            for c, v in enumerate([u["username"] + (" (you)" if u["id"] == me else ""),
                                   u["full_name"], ROLE_LABELS.get(u["role"], u["role"]),
                                   "Enabled" if u["is_active"] else "Disabled", last]):
                self.table.setItem(r, c, QTableWidgetItem(v))
            act = QWidget()
            h = QHBoxLayout(act)
            h.setContentsMargins(4, 2, 4, 2)
            for text, name, fn in (("Edit", "secondary_btn", self._edit),
                                   ("Reset password", "secondary_btn", self._reset),
                                   ("Delete", "danger_btn", self._delete)):
                b = QPushButton(text)
                b.setObjectName(name)
                b.clicked.connect(lambda _, x=u, f=fn: f(x))
                if text == "Delete" and u["id"] == me:
                    b.setEnabled(False)
                h.addWidget(b)
            self.table.setCellWidget(r, 5, act)
        self.refresh_audit()

    def refresh_audit(self):
        from app.models import AuditLog
        s = self.db.get_session()
        try:
            q = s.query(AuditLog).order_by(AuditLog.id.desc())
            text = self.filter.text().strip()
            if text:
                like = f"%{text}%"
                q = q.filter(AuditLog.username.like(like) | AuditLog.action.like(like) |
                             AuditLog.details.like(like))
            rows = q.limit(500).all()
        finally:
            s.close()
        self.audit_table.setRowCount(0)
        for a in rows:
            r = self.audit_table.rowCount()
            self.audit_table.insertRow(r)
            for c, v in enumerate([a.created_at.strftime("%Y-%m-%d %H:%M:%S"), a.username or "",
                                   a.action or "", a.details or ""]):
                self.audit_table.setItem(r, c, QTableWidgetItem(v))

    def _add(self):
        dlg = UserDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.users.create(dlg.username.text(), dlg.pw.text(), dlg.role.currentData(),
                              dlg.full_name.text())
            if not dlg.active.isChecked():
                uid = next(u["id"] for u in self.users.list()
                           if u["username"] == dlg.username.text().strip().lower())
                self.users.update(uid, is_active=False)
        except ValueError as e:
            QMessageBox.warning(self, "Could not add user", str(e))
            return
        audit(self.db, "user_add", f"Added {dlg.username.text().strip().lower()} "
                                   f"({dlg.role.currentData()})")
        self.refresh()

    def _edit(self, u):
        dlg = UserDialog(u, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.users.update(u["id"], role=dlg.role.currentData(),
                              is_active=dlg.active.isChecked(), full_name=dlg.full_name.text())
        except ValueError as e:
            QMessageBox.warning(self, "Not saved", str(e))
            return
        audit(self.db, "user_edit", f"{u['username']}: role {dlg.role.currentData()}, "
                                    f"{'enabled' if dlg.active.isChecked() else 'disabled'}")
        self.refresh()

    def _reset(self, u):
        from app.core.security import CurrentUser
        target = CurrentUser(u["id"], u["username"], u["role"])
        if ChangePasswordDialog(self.users, target, self, require_current=False).exec():
            audit(self.db, "password_reset", f"Reset password for {u['username']}")
            QMessageBox.information(self, "Password changed",
                                    f"New password set for {u['username']}.")

    def _delete(self, u):
        if QMessageBox.question(self, "Delete user", f"Delete the account '{u['username']}'?",
                                QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.users.delete(u["id"])
        except ValueError as e:
            QMessageBox.warning(self, "Not deleted", str(e))
            return
        audit(self.db, "user_delete", f"Deleted {u['username']}")
        self.refresh()
