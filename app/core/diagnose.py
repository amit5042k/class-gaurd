"""Step-by-step connection report for one camera ("Diagnose connection…")."""
import os
import socket
import sys
import time
from datetime import datetime
from typing import Dict, Optional
from urllib.parse import quote, urlsplit

from app.config import Config

NATIVE_LOG = "video-errors.log"
_native_handle = None


def native_log_path() -> str:
    return os.path.join(Config.LOGS_DIR, NATIVE_LOG)


def capture_native_errors():
    """Send FFmpeg/OpenCV messages (written by C code to stderr) to a log file.

    A windowed Windows app has no console, so these messages were simply lost.
    Must run before OpenCV opens its first video.
    """
    global _native_handle
    if _native_handle is not None:
        return
    if not (getattr(sys, "frozen", False) or sys.stderr is None):
        return
    os.makedirs(Config.LOGS_DIR, exist_ok=True)
    path = native_log_path()
    try:
        if os.path.exists(path) and os.path.getsize(path) > 2_000_000:
            os.replace(path, path + ".old")
        _native_handle = open(path, "a", buffering=1, encoding="utf-8", errors="replace")
        os.dup2(_native_handle.fileno(), 2)
        if sys.platform == "win32":
            _redirect_windows_crts()
    except Exception:
        _native_handle = None


def _redirect_windows_crts():
    """OpenCV's FFmpeg DLL writes through the old msvcrt C runtime, whose stderr
    was fixed at start-up, so redirect that runtime's descriptor 2 as well."""
    import ctypes
    import msvcrt
    handle = msvcrt.get_osfhandle(2)
    ctypes.windll.kernel32.SetStdHandle(-12, handle)
    try:
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.DuplicateHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32,
                                        ctypes.c_int, ctypes.c_uint32]
        old_crt = ctypes.cdll.msvcrt
        old_crt._open_osfhandle.argtypes = [ctypes.c_void_p, ctypes.c_int]
        old_crt._dup2.argtypes = [ctypes.c_int, ctypes.c_int]
        proc = k32.GetCurrentProcess()
        new = ctypes.c_void_p()
        if k32.DuplicateHandle(proc, handle, proc, ctypes.byref(new), 0, False, 2):
            fd = old_crt._open_osfhandle(new.value, 0x0008)  # _O_APPEND
            if fd >= 0:
                old_crt._dup2(fd, 2)  # msvcrt's stderr is unbuffered: writes go straight to fd 2
    except Exception:
        pass


def _native_offset() -> int:
    try:
        return os.path.getsize(native_log_path())
    except OSError:
        return 0


def _native_since(offset: int, limit: int = 25):
    try:
        with open(native_log_path(), encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            lines = [line.rstrip() for line in f if line.strip()]
    except OSError:
        return []
    return lines[-limit:]


def mask(text: str, password: str) -> str:
    if not password:
        return text
    for variant in {password, quote(password, safe=""), password.replace("@", "%40")}:
        if variant:
            text = text.replace(variant, "****")
    return text


def _try_stream(url: str, transport: str, seconds: float = 6.0,
                backend: str = "opencv") -> Dict:
    from app.core.stream_manager import open_capture
    t0 = time.monotonic()
    cap = open_capture(url, transport, backend)
    out = {"opened": cap.isOpened(), "open_s": time.monotonic() - t0, "frames": 0,
           "size": None, "first_frame_s": None, "read_errors": 0}
    try:
        end = time.monotonic() + seconds
        while out["opened"] and time.monotonic() < end:
            ok, frame = cap.read()
            if not ok or frame is None:
                out["read_errors"] += 1
                if out["read_errors"] >= 3:
                    break
                continue
            if out["frames"] == 0:
                out["first_frame_s"] = time.monotonic() - t0
                out["size"] = f"{frame.shape[1]}x{frame.shape[0]}"
            out["frames"] += 1
    finally:
        cap.release()
    return out


def run_diagnosis(cam: Dict, stream_seconds: float = 6.0) -> str:
    from app.protocols.rtsp_diag import OK, describe_detail
    url, pwd = cam["rtsp_url"], cam.get("password", "")
    lines = [f"ClassGuard {Config.VERSION} connection report — {datetime.now():%Y-%m-%d %H:%M:%S}",
             f"Camera: {cam.get('name')}  (brand {cam.get('brand')}, channel {cam.get('channel')},"
             f" sub-stream {'yes' if cam.get('use_substream') else 'no'},"
             f" transport {cam.get('rtsp_transport', 'auto')})",
             f"Address: {mask(url, pwd)}", ""]
    conclusion: Optional[str] = None
    u = urlsplit(url)
    host, port = u.hostname, u.port or 554

    t0 = time.monotonic()
    try:
        socket.create_connection((host, port), timeout=4).close()
        lines.append(f"1. Network: {host}:{port} reachable ({(time.monotonic() - t0) * 1000:.0f} ms)")
    except OSError as e:
        lines.append(f"1. Network: {host}:{port} NOT reachable ({e})")
        conclusion = ("The PC can't reach the device. Check the IP address, the network "
                      "and that RTSP (port 554) is enabled.")

    if conclusion is None and url.startswith("rtsp"):
        d = describe_detail(url)
        lines.append(f"2. RTSP request: {d['message']} (code {d['code']}, login {d['auth']})")
        if d["media"]:
            lines.append(f"   Media offered: {', '.join(d['media'])}")
        if d["kind"] != OK:
            conclusion = d["message"]

    probe = None
    if conclusion is None and url.startswith("rtsp"):
        from app.protocols.rtsp_diag import media_probe
        time.sleep(1)
        probe = media_probe(url, seconds=5)
        if probe["error"]:
            lines.append(f"3. Video data (no decoder, TCP): failed: {probe['error']}")
        elif probe["packets"]:
            lines.append(f"3. Video data (no decoder, TCP): {probe['packets']} packets, "
                         f"{probe['bytes'] // 1024} KB in 5 s, first after "
                         f"{probe['first_s']:.1f} s")
        else:
            lines.append("3. Video data (no decoder, TCP): NONE. The device accepted the "
                         "request but sent no video in 5 s")

    if conclusion is None:
        from app.core.av_capture import AVAILABLE as pyav_ok
        attempts = [("tcp", "opencv"), ("udp", "opencv")]
        if pyav_ok:
            attempts += [("tcp", "pyav"), ("udp", "pyav")]
        results = {}
        for step, (transport, backend) in enumerate(attempts, start=4):
            time.sleep(1)  # some devices briefly refuse a request right after another
            offset = _native_offset()
            r = _try_stream(url, transport, stream_seconds, backend)
            results[(transport, backend)] = r
            if r["frames"]:
                summary = (f"{r['frames']} frames, {r['size']}, first picture after "
                           f"{r['first_frame_s']:.1f} s")
            elif r["opened"]:
                summary = f"opened in {r['open_s']:.1f} s but NO picture ({r['read_errors']} read errors)"
            else:
                summary = f"could not open (after {r['open_s']:.1f} s)"
            decoder = "standard decoder" if backend == "opencv" else "alternative decoder"
            lines.append(f"{step}. Video over {transport.upper()}, {decoder}: {summary}")
            for msg in _native_since(offset):
                lines.append(f"     video library: {mask(msg, pwd)}")
            if r["frames"]:
                break  # found a working combination
        working = [k for k, r in results.items() if r["frames"]]
        if working:
            transport, backend = working[0]
            if (transport, backend) == ("tcp", "opencv"):
                conclusion = ("Video works. If Live View still fails, too many streams may be "
                              "open at once: show fewer cameras per page.")
            elif backend == "pyav":
                conclusion = (f"Video works with the alternative decoder over {transport.upper()}."
                              " ClassGuard switches to it automatically (Auto transport).")
            else:
                conclusion = ("Video works only over UDP. Leave RTSP transport on Auto, or set "
                              "it to UDP for this camera.")
        elif probe and not probe["error"] and probe["packets"] == 0:
            conclusion = ("The NVR accepts the request but sends NO video data, so this is not "
                          "a decoding problem. Most likely: (1) the camera on this NVR channel "
                          "is offline: check it shows Online in the NVR's camera list; or "
                          "(2) security software / firewall on this PC is blocking RTSP video: "
                          "test the same address in VLC on this PC, or briefly disable the "
                          "antivirus web/network protection.")
        elif probe and probe["packets"]:
            conclusion = (f"Video data arrives ({probe['packets']} packets in 5 s) but no "
                          "decoder could turn it into pictures. Set this stream to H.264 "
                          "with H.264+/Smart Codec off, and send this report to support.")
        else:
            media = " ".join(lines).upper()
            if "H265" in media or "HEVC" in media:
                conclusion = ("The device sends H.265 but no picture could be decoded. In the "
                              "NVR/camera video settings, set this stream to H.264 and turn off "
                              "H.265+/Smart Codec, then try again.")
            else:
                conclusion = ("The device accepts the login and offers the stream, but no "
                              "picture arrived. Try the sub-stream, set the stream to H.264, "
                              "and check the NVR's limit on simultaneous remote streams.")
    lines += ["", f"Result: {conclusion}"]
    return "\n".join(lines)
