"""Direct RTSP/RTP video reader ("direct decoder").

Reads the RTSP stream over TCP with ClassGuard's own RTSP client, rebuilds the
H.264 / H.265 pictures from the RTP packets itself and decodes them with
PyAV's codec only, bypassing FFmpeg's RTSP demuxer. Some recorders send
streams that FFmpeg's demuxer cannot turn into pictures although every
packet and keyframe arrives (seen on a Hikvision NVR); this path decodes them.

Same interface as cv2.VideoCapture: isOpened(), read(), release().
"""
import base64
import logging
import queue
import socket
import threading
import time
from typing import List, Optional

from app.protocols.rtsp_diag import _Client, _rtp_payload, _video_codec, _video_control

logger = logging.getLogger(__name__)

try:
    import av
    AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    av = None
    AVAILABLE = False

START = b"\x00\x00\x00\x01"
KEEPALIVE_S = 25


def _sprop(sdp: str, codec: str) -> List[bytes]:
    """Parameter sets (SPS/PPS, or VPS/SPS/PPS) announced in the SDP."""
    out = []
    in_video = False
    for line in sdp.splitlines():
        if line.startswith("m="):
            in_video = line.startswith("m=video")
        elif in_video and line.startswith("a=fmtp:"):
            for part in line.split(" ", 1)[-1].split(";"):
                key, _, value = part.strip().partition("=")
                key = key.lower()
                names = ("sprop-parameter-sets",) if codec == "H264" else \
                    ("sprop-vps", "sprop-sps", "sprop-pps")
                if key in names:
                    for item in value.split(","):
                        try:
                            if item.strip():
                                out.append(base64.b64decode(item.strip() + "=="))
                        except Exception:
                            pass
    return out


class Depacketizer:
    """RTP payloads -> access units (Annex-B bytes) + keyframe flag."""

    def __init__(self, codec: str):
        self.hevc = codec in ("H265", "HEVC")
        self._nals: List[bytes] = []
        self._fu: Optional[bytearray] = None
        self._ts = None
        self._last_seq = None
        self._key = False
        self.params_seen = False

    def _nal_type(self, nal: bytes) -> int:
        return (nal[0] >> 1) & 0x3F if self.hevc else nal[0] & 0x1F

    def _is_key(self, t: int) -> bool:
        return 16 <= t <= 21 if self.hevc else t == 5

    def _is_param(self, t: int) -> bool:
        return t in (32, 33, 34) if self.hevc else t in (7, 8)

    def _add(self, nal: bytes):
        if not nal:
            return
        t = self._nal_type(nal)
        self._key |= self._is_key(t)
        self.params_seen |= self._is_param(t)
        self._nals.append(nal)

    def _flush(self):
        if not self._nals:
            return None
        au = (b"".join(START + n for n in self._nals), self._key)
        self._nals, self._key = [], False
        return au

    def push(self, packet: bytes) -> List[tuple]:
        """Feed one RTP packet; returns finished access units [(bytes, keyframe)]."""
        if len(packet) < 12:
            return []
        out = []
        seq = int.from_bytes(packet[2:4], "big")
        ts = packet[4:8]
        marker = bool(packet[1] & 0x80)
        lost = self._last_seq is not None and seq != (self._last_seq + 1) & 0xFFFF
        self._last_seq = seq
        if lost:
            self._fu = None            # a fragment is missing: drop that picture part
        if self._ts is not None and ts != self._ts:
            au = self._flush()
            if au:
                out.append(au)
        self._ts = ts
        p = _rtp_payload(packet)
        if p:
            self._payload(p)
        if marker:
            au = self._flush()
            if au:
                out.append(au)
        return out

    def _payload(self, p: bytes):
        if self.hevc:
            if len(p) < 3:
                return
            t = (p[0] >> 1) & 0x3F
            if t == 48:                                   # aggregation packet
                i = 2
                while i + 2 <= len(p):
                    n = int.from_bytes(p[i:i + 2], "big")
                    self._add(p[i + 2:i + 2 + n])
                    i += 2 + n
            elif t == 49:                                 # fragmentation unit
                fu = p[2]
                if fu & 0x80:
                    hdr = bytes([(p[0] & 0x81) | ((fu & 0x3F) << 1), p[1]])
                    self._fu = bytearray(hdr + p[3:])
                elif self._fu is not None:
                    self._fu += p[3:]
                if fu & 0x40 and self._fu is not None:
                    self._add(bytes(self._fu))
                    self._fu = None
            else:
                self._add(p)
            return
        t = p[0] & 0x1F
        if t == 24:                                       # STAP-A
            i = 1
            while i + 2 <= len(p):
                n = int.from_bytes(p[i:i + 2], "big")
                self._add(p[i + 2:i + 2 + n])
                i += 2 + n
        elif t == 28 and len(p) > 1:                      # FU-A
            fu = p[1]
            if fu & 0x80:
                self._fu = bytearray(bytes([(p[0] & 0xE0) | (fu & 0x1F)]) + p[2:])
            elif self._fu is not None:
                self._fu += p[2:]
            if fu & 0x40 and self._fu is not None:
                self._add(bytes(self._fu))
                self._fu = None
        elif 1 <= t <= 23:
            self._add(p)


class RTPCapture:
    def __init__(self, url: str, open_timeout_s: float = 10, read_timeout_s: float = 10):
        self.url = url
        self.read_timeout = read_timeout_s
        self.error: Optional[str] = None
        self.codec = ""
        self._frames: "queue.Queue" = queue.Queue(maxsize=4)
        self._running = False
        self._client = None
        self._thread = None
        self._base = url
        self._session = ""
        if not AVAILABLE:
            self.error = "PyAV is not installed"
            return
        try:
            self._open(open_timeout_s)
        except Exception as e:
            self.error = str(e)
            self._close_client()

    # ---------------------------------------------------------------- setup
    def _open(self, timeout: float):
        c = self._client = _Client(self.url, timeout)
        code, h = c.request("DESCRIBE", c.url, ["Accept: application/sdp"])
        if code != 200:
            raise ConnectionError(f"DESCRIBE answered {code}")
        sdp = h["_body"]
        self.codec = _video_codec(sdp)
        if self.codec not in ("H264", "H265", "HEVC"):
            raise ValueError(f"unsupported video codec {self.codec or '?'}")
        self._base = (h.get("content-base") or h.get("content-location") or [c.url])[0]
        control = _video_control(sdp, self._base)
        code, h = c.request("SETUP", control, ["Transport: RTP/AVP/TCP;unicast;interleaved=0-1"])
        if code != 200:
            raise ConnectionError(f"SETUP answered {code}")
        self._session = (h.get("session") or [""])[0].split(";")[0]
        transport = (h.get("transport") or [""])[0]
        self._channel = 0
        if "interleaved=" in transport:
            try:
                self._channel = int(transport.split("interleaved=")[1].split("-")[0].split(";")[0])
            except ValueError:
                pass
        code, h = c.request("PLAY", self._base, [f"Session: {self._session}",
                                                 "Range: npt=0.000-"])
        if code != 200:
            raise ConnectionError(f"PLAY answered {code}")
        name = "hevc" if self.codec in ("H265", "HEVC") else "h264"
        self._decoder = av.CodecContext.create(name, "r")
        self._params = b"".join(START + p for p in _sprop(sdp, self.codec))
        self._depack = Depacketizer(self.codec)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------- receiving
    def _loop(self):
        c = self._client
        last_keepalive = time.monotonic()
        started = False                # decode only from the first keyframe on
        try:
            c.sock.settimeout(self.read_timeout)
            while self._running:
                while len(c.buf) >= 4 and c.buf[:1] == b"$":
                    n = int.from_bytes(c.buf[2:4], "big")
                    if len(c.buf) < 4 + n:
                        break
                    ch, pkt = c.buf[1], c.buf[4:4 + n]
                    c.buf = c.buf[4 + n:]
                    if ch != self._channel:
                        continue           # RTCP or another track
                    for au, key in self._depack.push(pkt):
                        if key:
                            started = True
                            if not self._depack.params_seen and self._params:
                                au = self._params + au
                        if started:
                            self._decode(au)
                if c.buf and c.buf[:1] != b"$":
                    i = c.buf.find(b"$")
                    head = c.buf if i < 0 else c.buf[:i]
                    if b"\r\n\r\n" in head:            # e.g. keep-alive reply
                        c.buf = c.buf[head.index(b"\r\n\r\n") + 4:]
                        continue
                    if i > 0 and b"RTSP/" not in head:
                        c.buf = c.buf[i:]               # resync on garbage
                        continue
                now = time.monotonic()
                if now - last_keepalive > KEEPALIVE_S:
                    last_keepalive = now
                    c.cseq += 1
                    msg = [f"GET_PARAMETER {self._base} RTSP/1.0", f"CSeq: {c.cseq}",
                           f"Session: {self._session}", "User-Agent: ClassGuard"]
                    auth = c._auth("GET_PARAMETER", self._base)
                    if auth:
                        msg.append(f"Authorization: {auth}")
                    c.sock.sendall(("\r\n".join(msg) + "\r\n\r\n").encode())
                chunk = c.sock.recv(65536)
                if not chunk:
                    raise ConnectionError("the device closed the connection")
                c.buf += chunk
        except (socket.timeout, OSError, ConnectionError) as e:
            if self._running:
                self.error = str(e) or "no data"
                logger.info("Direct decoder stopped: %s", self.error)
        except Exception:
            logger.exception("Direct decoder failed")
        finally:
            self._running = False
            self._push(None)

    def _decode(self, au: bytes):
        try:
            frames = self._decoder.decode(av.Packet(au))
        except Exception as e:      # corrupt picture: skip it, keep going
            logger.debug("decode: %s", e)
            return
        for f in frames:
            try:
                self._push(f.to_ndarray(format="bgr24"))
            except Exception:
                logger.debug("frame conversion failed", exc_info=True)

    def _push(self, item):
        try:
            self._frames.put_nowait(item)
        except queue.Full:
            try:
                self._frames.get_nowait()          # drop the oldest: stay live
            except queue.Empty:
                pass
            try:
                self._frames.put_nowait(item)
            except queue.Full:
                pass

    # ---------------------------------------------------------- VideoCapture
    def isOpened(self) -> bool:
        return self._thread is not None and self.error is None

    def read(self):
        if self._thread is None:
            return False, None
        try:
            frame = self._frames.get(timeout=self.read_timeout)
        except queue.Empty:
            return False, None
        if frame is None:
            self._push(None)          # keep reporting the end
            return False, None
        return True, frame

    def release(self):
        self._running = False
        if self._client is not None:
            try:
                self._client.sock.settimeout(1)
                self._client.sock.sendall(
                    f"TEARDOWN {self._base} RTSP/1.0\r\nCSeq: {self._client.cseq + 1}\r\n"
                    f"Session: {self._session}\r\n\r\n".encode())
            except Exception:
                pass
        self._close_client()
        if self._thread is not None:
            self._thread.join(2)

    def _close_client(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
