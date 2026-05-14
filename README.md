# ClassGuard — Camera Management Desktop App

A full-featured desktop application similar to Hikvision iVMS, built with Python + PyQt6.

## Features

| Module | Description |
|---|---|
| **Camera Management** | Add/edit/delete IP cameras from Hikvision, Dahua, UNV, CP Plus, ONVIF, or any RTSP source. Wired & wireless. |
| **NVR / DVR Management** | Register NVRs and DVRs; link cameras to channels. |
| **Live View** | Multi-layout grid (1×1 to 4×4), RTSP streaming with auto-reconnect. |
| **Face Enrollment** | Enroll persons via webcam capture or image file. Stores 128-D face embeddings. |
| **Attendance** | Real-time check-in / check-out via face recognition. CSV export. |
| **Expression Monitor** | Live emotion analysis (happy, sad, angry, fear, surprise, disgust, neutral) with per-emotion progress bars. |
| **Safety Watch** | Person detection with HOG, optional YOLO model for PPE violation detection (helmet, mask, vest). Configurable restricted zones. |
| **Alerts** | Severity-tagged alert log with acknowledge workflow. |
| **Recordings** | Per-camera video recording to local MP4 files. |
| **Settings** | Tune thresholds, reconnect delays, recording FPS. |

---

## Requirements

- Python 3.9+
- OS: Windows 10/11, macOS 12+, Ubuntu 20.04+

## Quick Install

```bash
# 1. Clone the repo
git clone https://github.com/amit5042k/class-gaurd.git
cd class-gaurd

# 2. Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install base dependencies
pip install -r requirements.txt

# 4. (Optional) Install full AI stack
pip install cmake dlib face-recognition deepface
```

## Run

```bash
python main.py
# or
bash run.sh
```

---

## Camera Setup

### RTSP URL Templates (auto-built from host/user/pass/channel)

| Brand | Template |
|---|---|
| Hikvision | `rtsp://user:pass@host:554/Streaming/Channels/{channel}01` |
| Dahua | `rtsp://user:pass@host:554/cam/realmonitor?channel={channel}&subtype=0` |
| UNV | `rtsp://user:pass@host:554/media/video{channel}` |
| CP Plus | `rtsp://user:pass@host:554/h264Preview_01_main` |
| ONVIF | Auto-discovered via WS-Discovery |
| Generic RTSP | Paste any custom URL |

### Adding a Camera

1. Go to **Camera Management → + Add Camera**
2. Fill in brand, host, credentials, channel
3. Click **Test Connection** to verify RTSP
4. Enable AI features (face detection, attendance, expression, safety) per camera
5. Save — the camera appears immediately in Live View

### ONVIF Auto-Discovery

Click **ONVIF Discover** to scan the local network. Found devices appear in a list; copy the IP into the Add Camera form.

---

## AI Features

### Face Enrollment

1. Go to **Face Enrollment → + Add Person** — fill name, employee ID, department
2. Select the person row
3. Click **Start Camera Capture** (uses webcam) or **Enroll from Image File**
4. Click **Capture & Enroll** — the face embedding is stored in the database

### Attendance

- Any camera with **Attendance Tracking** enabled will automatically log check-in/check-out as enrolled persons appear
- View records by date on the **Attendance** page
- Export to CSV with one click

### Expression Monitor

- Select a camera and click **Start Monitoring**
- Emotions are displayed in real time on the live feed with color-coded overlays
- A running log shows time-stamped emotion events
- Requires `deepface` package for full analysis; falls back to basic face detection otherwise

### Safety Watch

- Uses OpenCV HOG by default for person detection
- Load a custom **YOLO weights + config + names** file for PPE violation detection (no-helmet, no-mask, no-vest)
- Violations are logged in the table and saved to the database as `SafetyViolation` records

---

## Project Structure

```
class-gaurd/
├── main.py                       # Entry point
├── requirements.txt
├── app/
│   ├── config.py                   # All configuration constants
│   ├── database.py                 # SQLAlchemy engine + session factory
│   ├── models.py                   # Camera, NVR, Person, Attendance, Alert ...
│   ├── core/
│   │   ├── stream_manager.py       # RTSP QThread streaming
│   │   ├── camera_manager.py       # Camera/NVR CRUD
│   │   ├── face_engine.py          # Face detection & recognition
│   │   ├── attendance_engine.py    # Attendance processing
│   │   ├── expression_engine.py    # Emotion analysis
│   │   ├── safety_engine.py        # PPE / zone detection
│   │   └── recording.py            # Video recording
│   ├── protocols/
│   │   ├── onvif_handler.py        # ONVIF discovery & stream URI
│   │   ├── hikvision.py            # Hikvision ISAPI + PTZ
│   │   ├── dahua.py                # Dahua CGI API
│   │   └── generic_rtsp.py         # RTSP probe / snapshot
│   └── ui/
│       ├── styles.py               # Dark theme QSS
│       ├── main_window.py          # Main window + navigation
│       ├── widgets/
│       │   ├── sidebar.py
│       │   ├── video_cell.py
│       │   └── camera_grid.py
│       └── pages/
│           ├── dashboard.py
│           ├── live_view.py
│           ├── camera_management.py
│           ├── nvr_management.py
│           ├── face_enrollment.py
│           ├── attendance.py
│           ├── expression_monitor.py
│           ├── safety_watch.py
│           ├── alerts.py
│           ├── recordings.py
│           └── settings.py
```

---

## Data Storage

All data is stored in `~/.classguard/`:

| Path | Contents |
|---|---|
| `classguard.db` | SQLite database |
| `faces/` | Enrolled face photos |
| `snapshots/` | Attendance & alert snapshots |
| `recordings/` | Video recordings (MP4) |
| `logs/` | Application logs |

---

## Supported Brands

- **Hikvision** — RTSP + ISAPI (HTTP) + PTZ control
- **Dahua** — RTSP + CGI API + PTZ control
- **UNV (Uniview)** — RTSP
- **CP Plus** — RTSP
- **ONVIF** — auto-discovery + stream URI fetch
- **Generic RTSP** — any IP camera with RTSP stream

Both **wired (Ethernet / PoE)** and **wireless (WiFi)** cameras are supported — connection type is a metadata field and does not affect the RTSP transport.
