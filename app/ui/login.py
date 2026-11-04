from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout
)

from app.config import Config
from app.core.security import ROLE_ADMIN, UserService, password_problem
from app.ui.styles import DARK


def _pwd_field():
    e = QLineEdit()
    e.setEchoMode(QLineEdit.EchoMode.Password)
    return e


class _Base(QDialog):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet(DARK)
        self.setMinimumWidth(380)
        self.lay = QVBoxLayout(self)
        brand = QLabel("ClassGuard")
        brand.setObjectName("app_title")
        brand.setStyleSheet("color:#ef4565; font-size:20px; font-weight:bold;")
        self.lay.addWidget(brand)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color:#ef4565;")

    def fail(self, msg):
        self.error.setText(msg)


class FirstRunDialog(_Base):
    """Creates the first administrator account."""

    def __init__(self, users: UserService, parent=None):
        super().__init__("Create administrator", parent)
        self.users = users
        info = QLabel("Welcome. Create the administrator account. You'll use it to sign in "
                      "and to add accounts for other staff.")
        info.setWordWrap(True)
        self.lay.addWidget(info)
        form = QFormLayout()
        self.name = QLineEdit()
        self.username = QLineEdit("admin")
        self.pw1, self.pw2 = _pwd_field(), _pwd_field()
        form.addRow("Your name:", self.name)
        form.addRow("Username:", self.username)
        form.addRow("Password:", self.pw1)
        form.addRow("Repeat password:", self.pw2)
        self.lay.addLayout(form)
        self.lay.addWidget(self.error)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        self.lay.addWidget(bb)

    def _ok(self):
        if self.pw1.text() != self.pw2.text():
            return self.fail("The passwords don't match.")
        try:
            self.users.create(self.username.text(), self.pw1.text(), ROLE_ADMIN,
                              self.name.text())
        except ValueError as e:
            return self.fail(str(e))
        self.accept()


class LoginDialog(_Base):
    def __init__(self, users: UserService, parent=None, locked: bool = False):
        super().__init__("Screen locked" if locked else "Sign in", parent)
        self.users = users
        self.user = None
        if locked:
            note = QLabel("ClassGuard is locked. Cameras keep recording. Sign in to continue.")
            note.setWordWrap(True)
            self.lay.addWidget(note)
        form = QFormLayout()
        self.username = QLineEdit()
        self.password = _pwd_field()
        form.addRow("Username:", self.username)
        form.addRow("Password:", self.password)
        self.lay.addLayout(form)
        self.lay.addWidget(self.error)
        btn = QPushButton("Sign in")
        btn.setObjectName("primary_btn")
        btn.setDefault(True)
        btn.clicked.connect(self._ok)
        self.lay.addWidget(btn)
        quit_btn = QPushButton("Quit ClassGuard" if locked else "Cancel")
        quit_btn.clicked.connect(self.reject)
        self.lay.addWidget(quit_btn)
        self.password.returnPressed.connect(self._ok)
        self.username.returnPressed.connect(self.password.setFocus)
        if locked:
            self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)

    def _ok(self):
        name = self.username.text()
        wait = self.users.locked_for(name)
        if wait:
            return self.fail(f"Too many wrong attempts. Try again in {wait} seconds.")
        user = self.users.authenticate(name, self.password.text())
        if user is None:
            self.password.clear()
            wait = self.users.locked_for(name)
            return self.fail(f"Too many wrong attempts. Try again in {wait} seconds."
                             if wait else "Wrong username or password.")
        self.user = user
        self.accept()


class ChangePasswordDialog(_Base):
    def __init__(self, users: UserService, user, parent=None, require_current=True):
        super().__init__("Change password", parent)
        self.users, self.user = users, user
        self.require_current = require_current
        form = QFormLayout()
        self.current = _pwd_field()
        self.pw1, self.pw2 = _pwd_field(), _pwd_field()
        if require_current:
            form.addRow("Current password:", self.current)
        form.addRow("New password:", self.pw1)
        form.addRow("Repeat new password:", self.pw2)
        self.lay.addLayout(form)
        self.lay.addWidget(self.error)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        self.lay.addWidget(bb)

    def _ok(self):
        if self.require_current and not self.users.check_password(self.user.id,
                                                                  self.current.text()):
            return self.fail("The current password is wrong.")
        if self.pw1.text() != self.pw2.text():
            return self.fail("The new passwords don't match.")
        problem = password_problem(self.pw1.text())
        if problem:
            return self.fail(problem)
        self.users.set_password(self.user.id, self.pw1.text())
        self.accept()


def sign_in(db) -> "object | None":
    """Run first-run setup if needed, then the login dialog. Returns CurrentUser."""
    users = UserService(db)
    if not users.has_users():
        if FirstRunDialog(users).exec() != QDialog.DialogCode.Accepted:
            return None
    dlg = LoginDialog(users)
    dlg.setWindowTitle(f"Sign in — {Config.APP_NAME}")
    return dlg.user if dlg.exec() == QDialog.DialogCode.Accepted else None
