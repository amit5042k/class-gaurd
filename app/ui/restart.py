import sys

from PyQt6.QtCore import QProcess


def restart_app():
    """Start a fresh copy of the app (used after staging a restore)."""
    frozen = getattr(sys, "frozen", False)
    args = sys.argv[1:] if frozen else [sys.argv[0]] + sys.argv[1:]
    QProcess.startDetached(sys.executable, args)
