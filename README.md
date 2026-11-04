# ClassGuard — Camera Management Desktop App

A desktop video management system similar to Hikvision iVMS, built with Python + PyQt6.
It shows cameras and NVRs from several brands on one screen, records them, and adds
face-recognition attendance, emotion monitoring and safety alerts on top of the video.

## Features

| Area | What it does |
|---|---|
| **Cameras** | Hikvision, Dahua, UNV, CP Plus, ONVIF or any RTSP camera — wired, PoE or Wi-Fi. Main/sub-stream, background connection test, ONVIF network scan, "get stream address from camera" over ONVIF. |
| **NVR / DVR** | Add a recorder, test it (reads model and serial), import its channels as cameras (using the channel names stored on Hikvision/Dahua recorders). Imported cameras follow the recorder's IP and login. |
| **Live View** | Grids from 1×1 to 4×4, pages for more cameras, filter by group, double-click for single view, full screen (F11), auto-reconnect, snapshots, PTZ panel (pan/tilt/zoom/presets) for Hikvision, Dahua/CP Plus and ONVIF (UNV etc.). |
| **Recording** | Per camera: off, continuous or motion-triggered (with pre/post-roll), plus manual recording from Live View. 10-minute files, automatic clean-up by age, total size and free disk space. |
| **Playback** | Day timeline per camera, built-in player (speed 0.5–8×, jump, frame step, snapshot), export, and "Video" on each alert to jump to that moment. |
| **Face Enrollment** | People with ID/class/department; several photos each from webcam or file. |
| **Attendance** | Automatic from cameras with *Attendance Tracking* on: one record per person per day (first seen / last seen), Present / Late / Absent with shift start, grace and weekly off days, daily and monthly CSV. |
| **Expression Monitor** | Live emotion labels and chart (needs DeepFace); logged; alerts on angry / fear / disgust. |
| **Safety Watch** | Person detection, optional YOLO model (ONNX or Darknet) for PPE (`no_helmet`, `NO-Hardhat`, …), restricted zones drawn on the picture. |
| **Alerts** | Snapshot + linked video, cooldown against floods, pop-up and sound for high priority. |
| **Users & security** | Sign-in with Administrator / Operator / Viewer roles, lock screen (recording continues), audit log of who did what, camera and NVR passwords encrypted on disk. |
| **Backup** | Back up / restore (database, settings, face photos, encryption key) and an automatic daily backup (last 7 kept). |

All AI and all recording run on background threads, so the window never freezes.
Cameras that record or use an AI feature connect automatically at start-up, like an NVR.

### Roles

| | Viewer | Operator | Administrator |
|---|:-:|:-:|:-:|
| Live view, playback, attendance, alerts | ✓ | ✓ | ✓ |
| Acknowledge alerts, record, PTZ, export video | | ✓ | ✓ |
| Face enrollment, Safety & Expression monitoring | | ✓ | ✓ |
| Cameras, NVRs, settings, users, backups, delete recordings | | | ✓ |

---

## Install on Windows

Download `ClassGuard-Setup-<version>.exe` (built by the GitHub Actions workflow in
`.github/workflows/build.yml` — *Actions → Test and build → Artifacts*), run it, and start
ClassGuard. On first start you create the administrator account.

Tick *Start ClassGuard when I sign in to Windows* in the installer for 24×7 recording.
The installed app includes face recognition. Emotion analysis is not bundled (it adds about
1 GB); build with `CLASSGUARD_EMOTION=1` to include it.

To check an installation: Start menu → *ClassGuard self-test* (writes
`classguard-selftest.txt` in Documents).

## Run from source (Windows, macOS, Linux)

Needs **Python 3.9–3.12** (64-bit).

```bash
git clone https://github.com/amit5042k/class-gaurd.git
cd class-gaurd
```

- **Windows:** `install.bat`, then `run.bat`
- **macOS / Linux:** `./install.sh`, then `./run.sh`

The installer script creates `.venv` and asks whether to add face recognition (prebuilt
`dlib-bin`, no compiler needed) and emotion analysis (DeepFace + TensorFlow, about 1 GB;
its model weights download from GitHub on first use).

Options: `--data-dir PATH` keeps all data in another folder (portable use);
`--self-test` checks every component and exits.

> OpenCV is pinned to 4.x (`opencv-python-headless<5`): OpenCV 5 removed the face and person
> detectors this app uses, and the non-headless wheel conflicts with PyQt6.

## Build the installer yourself

On Windows: `build_windows.bat` (needs Python; Inno Setup 6 for the setup .exe).
It runs PyInstaller with `classguard.spec`, self-tests the result and writes
`dist\ClassGuard-Setup-<version>.exe`.

---

## Camera setup

1. **Camera Management → + Add Camera** (or **ONVIF Discover**, or **NVR / DVR → Import
   channels**).
2. Brand, IP, username, password, channel. Leave *Custom RTSP URL* blank — the hint shows the
   address that will be used — or press *Get from camera (ONVIF)*.
3. **Test Connection**, choose AI features and the recording mode, **OK**.

| Brand | Address built (main stream; sub-stream in brackets) |
|---|---|
| Hikvision | `rtsp://user:pass@IP:554/Streaming/Channels/101` (`102`); channel 3 → `301` |
| Dahua, CP Plus | `rtsp://user:pass@IP:554/cam/realmonitor?channel=1&subtype=0` (`subtype=1`) |
| UNV | `rtsp://user:pass@IP:554/unicast/c1/s0/live` (`s1`) |
| ONVIF / other | `rtsp://user:pass@IP:554/stream1` — or the camera's own URL |

PTZ controls appear for cameras whose type is *PTZ Camera*. Hikvision uses ISAPI, Dahua and
CP Plus use the HTTP CGI API, other brands use ONVIF (uses the camera's HTTP port).

> These paths and PTZ commands follow the brands' published APIs but have **not yet been
> verified on real hardware**. If a camera won't connect, paste its RTSP URL from the manual
> into *Custom RTSP URL*.

---

## Data

Everything is stored in `~/.classguard/` (Windows: `C:\Users\<you>\.classguard\`):
`classguard.db` (SQLite), `settings.json`, `secret.key` (unlocks saved camera passwords),
`faces/`, `snapshots/`, `recordings/cam<id>/<date>/`, `backups/`, `logs/classguard.log`.
Databases from older versions are upgraded and their passwords encrypted automatically.
Uninstalling keeps this folder.

Face data is biometric data: inform the people being recorded, obtain any consent your
local law requires, and restrict access to the PC (use Viewer accounts for guards).

---

## Development

```bash
pip install -r requirements-dev.txt -r requirements-ai.txt scikit-image onnx
pip install --no-deps face_recognition
QT_QPA_PLATFORM=offscreen python -m pytest tests
```

71 tests (about 50 s) cover URL building, settings, database upgrades, encryption and
migration, users/roles/lockout, audit, attendance rules, alerts, zones, ONNX parsing, PTZ
commands, NVR import, recording (continuous / motion / manual / segments / retention),
playback, backups, the main screens, and end-to-end runs against a simulated RTSP camera:
recognition and check-in, unplug/reconnect, and record-then-play. The simulator needs
GStreamer's RTSP server (`apt install gstreamer1.0-rtsp gir1.2-gst-rtsp-server-1.0
python3-gi gstreamer1.0-plugins-ugly`) and is skipped otherwise.

```
app/
  config.py, database.py, models.py, selftest.py
  core/       stream_manager, recording, ai_worker, face_engine, attendance_engine,
              alerts, expression_engine, safety_engine, camera_manager, security, backup
  protocols/  onvif_handler, hikvision (ISAPI), dahua (CGI), ptz, generic_rtsp
  ui/         main_window, login, pages/, widgets/
packaging/installer.iss   classguard.spec   build_windows.bat   .github/workflows/build.yml
tests/
```
