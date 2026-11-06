"""Ask an RTSP server *why* a stream won't open.

OpenCV only reports "could not open". This sends an RTSP DESCRIBE (with Basic
or Digest login, as cameras and NVRs require) and turns the answer into a
plain-language reason: unreachable, wrong password, no camera on that channel,
too many connections, and so on.
"""
import base64
import hashlib
import os
import re
import socket
import time
from typing import Optional, Tuple
from urllib.parse import unquote, urlsplit

OK, UNREACHABLE, AUTH, NOT_FOUND, BUSY, OTHER = (
    "ok", "unreachable", "auth", "not_found", "busy", "other")

MESSAGES = {
    UNREACHABLE: "Can't reach {host}:{port}. Check the IP address, network cable/Wi-Fi "
                 "and RTSP port.",
    AUTH: "Wrong username or password (the device rejected the login).",
    NOT_FOUND: "No video on this channel/path. On an NVR this usually means no "
               "camera is connected to this channel.",
    BUSY: "The device refused: too many video connections. Use the sub-stream or "
          "show fewer cameras at once.",
}


def _split(url: str):
    u = urlsplit(url)
    user = unquote(u.username) if u.username else ""
    pwd = unquote(u.password) if u.password else ""
    netloc = u.hostname + (f":{u.port}" if u.port else "")
    clean = f"{u.scheme}://{netloc}{u.path or '/'}" + (f"?{u.query}" if u.query else "")
    return u.hostname, u.port or 554, user, pwd, clean


def _request(sock, url: str, cseq: int, auth: str = "", want_body: bool = False):
    lines = [f"DESCRIBE {url} RTSP/1.0", f"CSeq: {cseq}", "Accept: application/sdp",
             "User-Agent: ClassGuard"]
    if auth:
        lines.append(f"Authorization: {auth}")
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    head = head.decode("latin-1")
    first, *rest = head.split("\r\n")
    m = re.match(r"RTSP/\d\.\d\s+(\d{3})", first)
    if not m:
        raise ConnectionError(f"Not an RTSP reply: {first[:60]!r}")
    headers = {}
    for line in rest:
        if ":" in line:
            k, v = line.split(":", 1)
            headers.setdefault(k.strip().lower(), []).append(v.strip())
    length = int((headers.get("content-length") or ["0"])[0] or 0)
    while len(body) < length:
        chunk = sock.recv(4096)
        if not chunk:
            break
        body += chunk
    headers["_body"] = body[:length].decode("latin-1", "replace")
    return int(m.group(1)), headers


def _digest(challenge: str, user: str, pwd: str, url: str) -> str:
    params = dict(re.findall(r'(\w+)="?([^",]*)"?', challenge))
    realm, nonce = params.get("realm", ""), params.get("nonce", "")
    ha1 = hashlib.md5(f"{user}:{realm}:{pwd}".encode()).hexdigest()
    ha2 = hashlib.md5(f"DESCRIBE:{url}".encode()).hexdigest()
    if "auth" in params.get("qop", ""):
        cnonce, nc = os.urandom(8).hex(), "00000001"
        resp = hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}".encode()).hexdigest()
        return (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{url}", '
                f'response="{resp}", qop=auth, nc={nc}, cnonce="{cnonce}"')
    resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
    return f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{url}", response="{resp}"'


def describe(url: str, timeout: float = 4.0) -> Tuple[str, str]:
    """Returns (kind, message). kind is one of OK, UNREACHABLE, AUTH, NOT_FOUND, BUSY, OTHER."""
    d = describe_detail(url, timeout)
    return d["kind"], d["message"]


def parse_sdp(sdp: str) -> list:
    """Media in an SDP, e.g. ['video H265/90000', 'audio PCMA/8000']."""
    out, media = [], None
    for line in sdp.splitlines():
        if line.startswith("m="):
            media = line[2:].split(" ")[0]
        elif line.startswith("a=rtpmap:") and media:
            out.append(f"{media} {line.split(' ', 1)[1].strip()}")
    return out


def describe_detail(url: str, timeout: float = 4.0) -> dict:
    """Like describe(), plus the RTSP status code and the media the device offers."""
    result = {"kind": OTHER, "message": "", "code": None, "media": [], "auth": "none"}

    def done(kind, message, code=None):
        result.update(kind=kind, message=message, code=code)
        return result
    try:
        host, port, user, pwd, clean = _split(url)
    except Exception:
        return done(OTHER, "The stream address is not valid.")
    if not host:
        return done(OTHER, "The stream address has no IP address.")
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError:
        return done(UNREACHABLE, MESSAGES[UNREACHABLE].format(host=host, port=port))
    try:
        sock.settimeout(timeout)
        code, headers = _request(sock, clean, 1)
        if code == 401 and user:
            challenges = headers.get("www-authenticate", [])
            digest = next((c for c in challenges if c.lower().startswith("digest")), None)
            if digest:
                auth = _digest(digest[6:].strip(), user, pwd, clean)
                result["auth"] = "digest"
            else:
                auth = "Basic " + base64.b64encode(f"{user}:{pwd}".encode()).decode()
                result["auth"] = "basic"
            code, headers = _request(sock, clean, 2, auth)
        result["media"] = parse_sdp(headers.get("_body", ""))
        kind, msg = classify(code)
        return done(kind, msg, code)
    except socket.timeout:
        return done(OTHER, f"{host}:{port} accepted the connection but didn't answer.")
    except Exception as e:
        return done(OTHER, f"Unexpected reply from {host}:{port}: {e}")
    finally:
        sock.close()


def classify(code: int) -> Tuple[str, str]:
    if code == 200:
        return OK, "The device says the stream is available."
    if code in (401, 403):
        return AUTH, MESSAGES[AUTH]
    if code in (404, 400):
        return NOT_FOUND, MESSAGES[NOT_FOUND]
    if code in (453, 454, 503):
        return BUSY, MESSAGES[BUSY]
    return OTHER, f"The device answered with RTSP error {code}."


def explain_failure(url: str) -> Optional[str]:
    """Message for a stream OpenCV couldn't open, or None if the device says it's fine."""
    kind, msg = describe(url)
    if kind == OK:
        return ("The device offers the stream, but it couldn't be decoded. Try the "
                "sub-stream, or set the camera to H.264.")
    return msg


# ------------------------------------------------------------ media probe
class _Client:
    """Just enough RTSP (with Digest/Basic login) to SETUP + PLAY over TCP."""

    def __init__(self, url: str, timeout: float):
        self.host, self.port, self.user, self.pwd, self.url = _split(url)
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = b""
        self.cseq = 0
        self.challenge = None  # ("digest"|"basic", params)

    def _auth(self, method: str, uri: str) -> str:
        if not self.challenge or not self.user:
            return ""
        kind, chal = self.challenge
        if kind == "basic":
            return "Basic " + base64.b64encode(f"{self.user}:{self.pwd}".encode()).decode()
        p = dict(re.findall(r'(\w+)="?([^",]*)"?', chal))
        ha1 = hashlib.md5(f"{self.user}:{p.get('realm', '')}:{self.pwd}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
        resp = hashlib.md5(f"{ha1}:{p.get('nonce', '')}:{ha2}".encode()).hexdigest()
        return (f'Digest username="{self.user}", realm="{p.get("realm", "")}", '
                f'nonce="{p.get("nonce", "")}", uri="{uri}", response="{resp}"')

    def _read_reply(self):
        while b"\r\n\r\n" not in self.buf:
            # Skip interleaved packets that may arrive before a reply.
            if self.buf[:1] == b"$" and len(self.buf) >= 4:
                n = int.from_bytes(self.buf[2:4], "big")
                if len(self.buf) >= 4 + n:
                    self.buf = self.buf[4 + n:]
                    continue
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("connection closed by the device")
            self.buf += chunk
        head, _, rest = self.buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        m = re.match(r"RTSP/\d\.\d\s+(\d{3})", lines[0])
        if not m:
            raise ConnectionError(f"unexpected reply {lines[0][:60]!r}")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers.setdefault(k.strip().lower(), []).append(v.strip())
        length = int((headers.get("content-length") or ["0"])[0] or 0)
        while len(rest) < length:
            chunk = self.sock.recv(65536)
            if not chunk:
                break
            rest += chunk
        headers["_body"] = rest[:length].decode("latin-1", "replace")
        self.buf = rest[length:]
        return int(m.group(1)), headers

    def request(self, method: str, uri: str, extra=()):
        for attempt in range(2):
            self.cseq += 1
            lines = [f"{method} {uri} RTSP/1.0", f"CSeq: {self.cseq}", "User-Agent: ClassGuard",
                     *extra]
            auth = self._auth(method, uri)
            if auth:
                lines.append(f"Authorization: {auth}")
            self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
            code, headers = self._read_reply()
            if code == 401 and attempt == 0 and self.user:
                chals = headers.get("www-authenticate", [])
                dig = next((c for c in chals if c.lower().startswith("digest")), None)
                self.challenge = ("digest", dig[6:].strip()) if dig else ("basic", "")
                continue
            return code, headers
        return code, headers

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _video_control(sdp: str, base: str) -> str:
    in_video, control = False, None
    for line in sdp.splitlines():
        if line.startswith("m="):
            in_video = line.startswith("m=video")
        elif in_video and line.startswith("a=control:"):
            control = line.split(":", 1)[1].strip()
            break
    if not control or control == "*":
        return base
    if control.lower().startswith("rtsp://"):
        return control
    return base.rstrip("/") + "/" + control


def _video_codec(sdp: str) -> str:
    in_video = False
    for line in sdp.splitlines():
        if line.startswith("m="):
            in_video = line.startswith("m=video")
        elif in_video and line.startswith("a=rtpmap:"):
            return line.split(" ", 1)[1].split("/")[0].strip().upper()
    return ""


def _nal_types(payload: bytes, codec: str) -> list:
    """NAL unit types that start in this RTP payload (H.264 / H.265)."""
    if not payload:
        return []
    if codec in ("H265", "HEVC"):
        if len(payload) < 3:
            return []
        t = (payload[0] >> 1) & 0x3F
        if t == 49:                                   # fragmentation unit
            return [payload[2] & 0x3F] if payload[2] & 0x80 else []
        if t == 48:                                   # aggregation packet
            out, i = [], 2
            while i + 3 <= len(payload):
                n = int.from_bytes(payload[i:i + 2], "big")
                out.append((payload[i + 2] >> 1) & 0x3F)
                i += 2 + n
            return out
        return [t]
    t = payload[0] & 0x1F
    if t == 28:                                       # FU-A
        return [payload[1] & 0x1F] if len(payload) > 1 and payload[1] & 0x80 else []
    if t == 24:                                       # STAP-A
        out, i = [], 1
        while i + 3 <= len(payload):
            n = int.from_bytes(payload[i:i + 2], "big")
            out.append(payload[i + 2] & 0x1F)
            i += 2 + n
        return out
    return [t]


def _is_keyframe(nal: int, codec: str) -> bool:
    return 16 <= nal <= 21 if codec in ("H265", "HEVC") else nal == 5


def _rtp_payload(packet: bytes) -> bytes:
    if len(packet) < 12 or packet[0] >> 6 != 2:
        return b""
    i = 12 + 4 * (packet[0] & 0x0F)
    if packet[0] & 0x10 and len(packet) >= i + 4:     # header extension
        i += 4 + 4 * int.from_bytes(packet[i + 2:i + 4], "big")
    end = len(packet)
    if packet[0] & 0x20 and end > i:                  # padding
        end -= packet[-1]
    return packet[i:end] if end > i else b""


def media_probe(url: str, seconds: float = 5.0, timeout: float = 5.0,
                save_path: str = None) -> dict:
    """Request the stream over RTSP/TCP and count the video packets that arrive,
    without decoding. Tells "the device sends nothing" apart from "the decoder
    can't decode what it sends"."""
    res = {"packets": 0, "bytes": 0, "first_s": None, "error": "", "codec": "",
           "keyframes": [], "param_sets": False, "sdp_params": False, "pictures": 0,
           "lost": 0}
    try:
        c = _Client(url, timeout)
    except OSError as e:
        res["error"] = f"can't connect: {e}"
        return res
    try:
        code, h = c.request("DESCRIBE", c.url, ["Accept: application/sdp"])
        if code != 200:
            res["error"] = f"DESCRIBE answered {code}"
            return res
        h_describe_body = h["_body"]
        base = (h.get("content-base") or h.get("content-location") or [c.url])[0]
        control = _video_control(h["_body"], base)
        codec = res["codec"] = _video_codec(h["_body"])
        res["sdp_params"] = "sprop-" in h["_body"].lower()
        depack = None
        if codec in ("H264", "H265", "HEVC"):
            from app.core.rtp_capture import Depacketizer
            depack = Depacketizer(codec)
        sample = bytearray()
        last_seq = None
        code, h = c.request("SETUP", control,
                            ["Transport: RTP/AVP/TCP;unicast;interleaved=0-1"])
        if code != 200:
            res["error"] = f"SETUP answered {code}"
            return res
        session = (h.get("session") or [""])[0].split(";")[0]
        code, h = c.request("PLAY", base, [f"Session: {session}", "Range: npt=0.000-"])
        if code != 200:
            res["error"] = f"PLAY answered {code}"
            return res
        t0 = time.monotonic()
        kf_stamp = None
        while time.monotonic() - t0 < seconds:
            while len(c.buf) >= 4 and c.buf[:1] == b"$":
                n = int.from_bytes(c.buf[2:4], "big")
                if len(c.buf) < 4 + n:
                    break
                if c.buf[1] == 0:  # RTP channel of the video track
                    res["packets"] += 1
                    res["bytes"] += n
                    if res["first_s"] is None:
                        res["first_s"] = time.monotonic() - t0
                    pkt = c.buf[4:4 + n]
                    if len(pkt) >= 4:
                        seq = int.from_bytes(pkt[2:4], "big")
                        if last_seq is not None and seq != (last_seq + 1) & 0xFFFF:
                            res["lost"] += (seq - last_seq - 1) & 0xFFFF
                        last_seq = seq
                    if depack is not None:
                        for au, _key in depack.push(pkt):
                            res["pictures"] += 1
                            if save_path and len(sample) < 8_000_000:
                                sample += au
                    for nal in _nal_types(_rtp_payload(pkt), codec):
                        if _is_keyframe(nal, codec):
                            if pkt[4:8] != kf_stamp:     # one picture, many slices
                                kf_stamp = pkt[4:8]
                                res["keyframes"].append(round(time.monotonic() - t0, 1))
                        elif nal in ((7, 8) if codec not in ("H265", "HEVC") else (32, 33, 34)):
                            res["param_sets"] = True
                c.buf = c.buf[4 + n:]
            if c.buf and c.buf[:1] != b"$":
                if b"\r\n\r\n" in c.buf:      # e.g. an RTSP message from the device
                    c.buf = c.buf.partition(b"\r\n\r\n")[2]
                    continue
            try:
                c.sock.settimeout(max(0.1, seconds - (time.monotonic() - t0)))
                chunk = c.sock.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                res["error"] = "the device closed the connection"
                break
            c.buf += chunk
        try:
            c.sock.settimeout(1)
            c.request("TEARDOWN", base, [f"Session: {session}"])
        except Exception:
            pass
        if save_path and sample:
            try:
                import os
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(sdp_param_prefix(h_describe_body, codec) + bytes(sample))
            except OSError:
                pass
    except Exception as e:
        res["error"] = str(e)
    finally:
        c.close()
    return res


def sdp_param_prefix(sdp: str, codec: str) -> bytes:
    """Annex-B parameter sets from the SDP, to make a saved sample playable."""
    from app.core.rtp_capture import START, _sprop
    return b"".join(START + p for p in _sprop(sdp, codec))
