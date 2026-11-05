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


def _request(sock, url: str, cseq: int, auth: str = "") -> Tuple[int, dict]:
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
    head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    first, *rest = head.split("\r\n")
    m = re.match(r"RTSP/\d\.\d\s+(\d{3})", first)
    if not m:
        raise ConnectionError(f"Not an RTSP reply: {first[:60]!r}")
    headers = {}
    for line in rest:
        if ":" in line:
            k, v = line.split(":", 1)
            headers.setdefault(k.strip().lower(), []).append(v.strip())
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
    try:
        host, port, user, pwd, clean = _split(url)
    except Exception:
        return OTHER, "The stream address is not valid."
    if not host:
        return OTHER, "The stream address has no IP address."
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError:
        return UNREACHABLE, MESSAGES[UNREACHABLE].format(host=host, port=port)
    try:
        sock.settimeout(timeout)
        code, headers = _request(sock, clean, 1)
        if code == 401 and user:
            challenges = headers.get("www-authenticate", [])
            digest = next((c for c in challenges if c.lower().startswith("digest")), None)
            if digest:
                auth = _digest(digest[6:].strip(), user, pwd, clean)
            else:
                auth = "Basic " + base64.b64encode(f"{user}:{pwd}".encode()).decode()
            code, headers = _request(sock, clean, 2, auth)
        return classify(code)
    except socket.timeout:
        return OTHER, f"{host}:{port} accepted the connection but didn't answer."
    except Exception as e:
        return OTHER, f"Unexpected reply from {host}:{port}: {e}"
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
