import logging
from typing import Callable

from PyQt6.QtCore import QObject, QThread, pyqtSignal

logger = logging.getLogger(__name__)

_running = set()


class _Task(QThread):
    done = pyqtSignal(object, object)  # result, error

    def __init__(self, fn: Callable, args, kwargs):
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs

    def run(self):
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs), None)
        except Exception as e:
            logger.exception("Background task failed")
            self.done.emit(None, e)


def run_async(fn: Callable, on_done: Callable, *args, receiver: QObject = None, **kwargs):
    """Run fn(*args) off the UI thread; call on_done(result, error) on the UI thread.

    Widgets must only be touched from on_done, never from fn.
    """
    task = _Task(fn, args, kwargs)
    _running.add(task)

    def finish(result, error):
        if receiver is not None:
            try:
                receiver.objectName()  # raises if the widget was deleted
            except RuntimeError:
                return
        on_done(result, error)

    task.done.connect(finish)
    task.finished.connect(lambda: _running.discard(task))
    task.start()
    return task
