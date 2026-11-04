"""`ClassGuard --self-test`: start every component headlessly and report.

Used to check an installed or freshly built copy without touching real data.
Exit code 0 = everything needed to run works.
"""
import shutil
import sys
import tempfile
import traceback


def run(app) -> int:
    from app.config import Config
    tmp = tempfile.mkdtemp(prefix="classguard-selftest-")
    Config.set_data_dir(tmp)
    results = []

    def check(name, fn):
        try:
            detail = fn()
            results.append((True, name, detail or ""))
        except Exception as e:  # report every failure, keep going
            results.append((False, name, f"{e.__class__.__name__}: {e}"))
            traceback.print_exc()

    import cv2
    import numpy as np
    check("OpenCV", lambda: f"{cv2.__version__}, face detector "
          f"{'ok' if not cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml').empty() else 'MISSING'}")
    from app.database import Database
    db = Database()
    check("Database", db.init)
    from app.core.security import encrypt_secret, decrypt_secret
    check("Encryption", lambda: "ok" if decrypt_secret(encrypt_secret("x1")) == "x1" else 1 / 0)
    from app.core.face_engine import FaceEngine
    fe = FaceEngine(Config.FACES_DIR)
    check("Face recognition", lambda: "installed" if fe.recognition_available
          else "NOT installed (attendance unavailable)")
    from app.core.safety_engine import SafetyEngine
    check("Person detector", lambda: f"{len(SafetyEngine().detect(np.zeros((240, 320, 3), np.uint8)))} detections on blank frame")
    from app.core.expression_engine import ExpressionEngine
    check("Emotion model", lambda: "installed" if ExpressionEngine().available else "not installed (optional)")

    def window():
        from app.ui.main_window import MainWindow
        w = MainWindow(db)
        for key in list(w._pages):
            w._sidebar.set_active(key)
            app.processEvents()
        w.close()
        return f"{len(w._pages)} pages opened"
    check("Main window", window)

    def recording():
        import os
        import time
        from app.core.recording import RecordingManager
        mgr = RecordingManager(db)
        mgr.set_cameras([{"id": 1, "recording_mode": "continuous"}])
        for _ in range(20):
            mgr.on_frame(1, np.zeros((240, 320, 3), np.uint8))
            time.sleep(0.05)
        mgr.stop_all()
        files = [f for _, _, fs in os.walk(Config.RECORDINGS_DIR) for f in fs]
        if not files:
            raise RuntimeError("no video file written")
        return f"wrote {files[0]}"
    check("Recording", recording)

    db.close()
    shutil.rmtree(tmp, ignore_errors=True)
    ok = all(r[0] for r in results)
    lines = [f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}"
             for passed, name, detail in results]
    lines.append("SELF-TEST " + ("PASSED" if ok else "FAILED"))
    report = "\n".join(lines) + "\n"
    # Windowed Windows builds have no console, so also leave a report file.
    try:
        with open("classguard-selftest.txt", "w", encoding="utf-8") as f:
            f.write(report)
    except OSError:
        pass
    if sys.stdout:
        print(report, end="")
        sys.stdout.flush()
    return 0 if ok else 1
