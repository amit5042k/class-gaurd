# ClassGuard — Camera Management Desktop App

A desktop app similar to Hikvision iVMS, built with Python + PyQt6. It shows cameras from
several brands on one screen and adds face-recognition attendance, emotion monitoring and
safety alerts on top of the video.

## Features

| Module | What it does |
|---|---|
| **Camera Management** | Add/edit/delete cameras from Hikvision, Dahua, UNV, CP Plus, ONVIF or any RTSP source (wired, PoE or Wi-Fi). Main or sub-stream, background connection test, ONVIF network scan with one-click add. |
| **Live View** | Grids from 1×1 to 4×4, auto-reconnect with back-off, snapshots. |
| **Face Enrollment** | People with ID/class/department; enroll several photos per person from a webcam or photo file. |
| **Attendance** | Automatic from live video on cameras with *Attendance Tracking* on. One record per person per day: check-in = first seen, last seen = check-out. Present / Late / Absent, weekly off days, daily and monthly CSV export. |
| **Expression Monitor** | Live emotion labels and chart for a camera (needs DeepFace). Logs to the database; alerts on angry / fear / disgust. |
| **Safety Watch** | Person detection; optional YOLO model (ONNX or Darknet) for PPE violations (`no_helmet`, `NO-Hardhat`, …). Draw restricted zones on the picture; anyone whose feet are inside raises a high-priority alert. |
| **Alerts** | Created automatically with a snapshot photo and a cooldown (no alert floods). High-priority alerts pop up with a sound. |
| **Settings** | Saved to disk: shift start, late grace, weekly off days, match threshold, alert options, auto-start streams. |

All AI runs on one background thread that always analyses the newest frame of each camera,
so the window never freezes. Which analyses run on a camera is set by its AI switches in
*Camera Management* (or by the Expression / Safety page while it is monitoring that camera).

Recording/playback, PTZ controls and NVR channel import are not built yet (roadmap phase 3).

---

## Install

Needs **Python 3.9–3.12** (64-bit) on Windows 10/11, macOS 12+ or Ubuntu 20.04+.

```bash
git clone https://github.com/amit5042k/class-gaurd.git
cd class-gaurd
```

- **Windows:** double-click `install.bat`, then `run.bat`
- **macOS / Linux:** `./install.sh`, then `./run.sh`

The installer creates a `.venv` folder and asks whether to install:

- **Face recognition** (needed for attendance) — uses `dlib-bin`, a prebuilt dlib, so no
  CMake or Visual Studio is needed.
- **Emotion analysis** (DeepFace + TensorFlow, about 1 GB) — optional. Its model weights are
  downloaded from GitHub the first time it runs, so that PC needs internet access once.

Manual install:

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-ai.txt                  # optional
pip install --no-deps face_recognition              # optional, after the line above
python main.py
```

> OpenCV is pinned to 4.x (`opencv-python-headless<5`): OpenCV 5 removed the face and person
> detectors this app uses, and the non-headless wheel conflicts with PyQt6's Qt plugins.

---

## Camera setup

1. **Camera Management → + Add Camera** (or **ONVIF Discover → Add selected camera**).
2. Choose the brand, enter IP, username, password and channel. Leave *Custom RTSP URL* blank
   and the address is built for you (the hint under the field shows it).
3. **Test Connection**, then **OK**. Turn on the AI features you want for this camera.
4. **Live View → Start All** (or enable *Start all camera streams when the app opens* in
   Settings). AI features only run on cameras whose stream is running.

| Brand | Address built (main stream; sub-stream in brackets) |
|---|---|
| Hikvision | `rtsp://user:pass@IP:554/Streaming/Channels/101` (`102`); channel 3 → `301` |
| Dahua, CP Plus | `rtsp://user:pass@IP:554/cam/realmonitor?channel=1&subtype=0` (`subtype=1`) |
| UNV | `rtsp://user:pass@IP:554/unicast/c1/s0/live` (`s1`) |
| ONVIF / other | `rtsp://user:pass@IP:554/stream1` — or paste the camera's own URL |

Passwords containing `@ : / # ?` are escaped automatically. Streams use RTSP over TCP, which
is more reliable on Wi-Fi. Use the sub-stream for Wi-Fi cameras and grids larger than 2×2.

> These paths are the brands' documented defaults but have **not yet been verified on real
> hardware**. If a camera won't connect, paste its RTSP URL from the manual into
> *Custom RTSP URL*.

---

## Data

Everything is stored locally in `~/.classguard/` (Windows: `C:\Users\<you>\.classguard\`):
`classguard.db` (SQLite), `settings.json`, `faces/`, `snapshots/` (attendance and alert
photos) and `logs/classguard.log`. Databases from older versions are upgraded automatically.

Face data is biometric data: inform the people being recorded, obtain any consent your
local law requires, and restrict access to this PC.

---

## Development

```bash
pip install -r requirements-dev.txt -r requirements-ai.txt
pip install --no-deps face_recognition scikit-image
QT_QPA_PLATFORM=offscreen python -m pytest tests
```

The suite (38 tests, about 20 s) covers URL building, settings, DB upgrades, attendance rules,
alert cooldown, zones, ONNX parsing, face enrollment/recognition, the AI worker, the main
screens, and an end-to-end run against a simulated RTSP camera, including unplugging and
reconnecting it. The end-to-end test needs GStreamer's RTSP server
(`apt install gstreamer1.0-rtsp gir1.2-gst-rtsp-server-1.0 python3-gi gstreamer1.0-plugins-ugly`)
and is skipped otherwise.

```
app/
  config.py            settings, brand RTSP templates
  database.py, models.py
  core/
    stream_manager.py  one thread per camera, timeouts, reconnect
    ai_worker.py       single background thread for all AI
    face_engine.py     enrollment + recognition (face_recognition / dlib)
    attendance_engine.py, alerts.py, expression_engine.py, safety_engine.py
  protocols/           ONVIF, Hikvision ISAPI, Dahua CGI, RTSP probe
  ui/                  main window, pages, widgets
tests/
```
