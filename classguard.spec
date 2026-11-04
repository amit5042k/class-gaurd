# PyInstaller build spec:  pyinstaller classguard.spec
# Produces dist/ClassGuard/ (a folder app). Set CLASSGUARD_EMOTION=1 to bundle
# DeepFace/TensorFlow for emotion analysis (adds roughly 1 GB).
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None
with_emotion = os.environ.get("CLASSGUARD_EMOTION") == "1"

datas = [("app/assets/icon.png", "app/assets"), ("app/assets/icon.ico", "app/assets")]
hiddenimports = ["app.ui.pages.users", "sqlalchemy.dialects.sqlite"]

datas += collect_data_files("cv2", includes=["data/*.xml"])      # Haar face detector
datas += collect_data_files("onvif")                            # ONVIF WSDL files
for optional in ("face_recognition_models",):
    try:
        datas += collect_data_files(optional)
        hiddenimports += collect_submodules(optional)
    except Exception:
        pass
hiddenimports += ["face_recognition", "dlib", "wsdiscovery", "zeep"]

excludes = ["tkinter", "matplotlib", "IPython", "pytest", "skimage", "scipy"]
if with_emotion:
    hiddenimports += collect_submodules("deepface")
else:
    excludes += ["tensorflow", "keras", "tf_keras", "deepface", "torch"]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClassGuard",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon="app/assets/icon.ico",
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="ClassGuard")
