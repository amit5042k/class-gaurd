"""Playback of recordings stored on the NVR/DVR (or a camera's SD card).

* search_recordings(): which periods of a day the device recorded
  (Hikvision ISAPI search, Dahua/CP Plus mediaFileFind CGI).
* playback_url(): an RTSP address that plays the recording from a given time
  (Hikvision /Streaming/tracks, Dahua/CP Plus /cam/playback, UNV replay).

Times are the device's local time, which is how these recorders expect and
report them (Hikvision writes them with a "Z" all the same).
"""
import logging
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import requests

from app.config import _escape

logger = logging.getLogger(__name__)

TIMEOUT = 8
PLAYBACK_BRANDS = ("Hikvision", "Dahua", "CP Plus", "UNV")


def supports_playback(cam: Dict) -> bool:
    return cam.get("brand") in PLAYBACK_BRANDS and bool(cam.get("host")) \
        and not cam.get("custom_rtsp_url")


def can_search(cam: Dict) -> bool:
    return supports_playback(cam) and cam.get("brand") in ("Hikvision", "Dahua", "CP Plus")


def _auth(cam: Dict) -> str:
    user = cam.get("username") or ""
    return f"{_escape(user)}:{_escape(cam.get('password') or '')}@" if user else ""


def track_id(cam: Dict) -> int:
    """Hikvision recording track: channel 1 main stream = 101."""
    return int(cam.get("channel") or 1) * 100 + 1


def playback_url(cam: Dict, start: datetime, end: Optional[datetime] = None) -> Optional[str]:
    """RTSP address playing the device's recording from `start`."""
    if not supports_playback(cam):
        return None
    end = end or datetime.combine(start.date(), datetime.max.time()).replace(microsecond=0)
    host, port = cam["host"], cam.get("port") or 554
    ch = int(cam.get("channel") or 1)
    base = f"rtsp://{_auth(cam)}{host}:{port}"
    brand = cam["brand"]
    if brand == "Hikvision":
        fmt = "%Y%m%dT%H%M%SZ"
        return (f"{base}/Streaming/tracks/{track_id(cam)}/?starttime={start.strftime(fmt)}"
                f"&endtime={end.strftime(fmt)}")
    if brand in ("Dahua", "CP Plus"):
        fmt = "%Y_%m_%d_%H_%M_%S"
        return (f"{base}/cam/playback?channel={ch}&subtype=0&starttime={start.strftime(fmt)}"
                f"&endtime={end.strftime(fmt)}")
    if brand == "UNV":
        return f"{base}/c{ch}/b{int(start.timestamp())}/e{int(end.timestamp())}/replay/"
    return None


# ------------------------------------------------------------------ search
def search_recordings(cam: Dict, day: date) -> List[Dict]:
    """[{start, end, kind}] recorded on the device that day. Raises on errors
    (wrong password, unreachable) so the page can say why."""
    brand = cam.get("brand")
    if brand == "Hikvision":
        return _search_hikvision(cam, day)
    if brand in ("Dahua", "CP Plus"):
        return _search_dahua(cam, day)
    return []


def _session(cam: Dict) -> requests.Session:
    s = requests.Session()
    s.auth = requests.auth.HTTPDigestAuth(cam.get("username") or "", cam.get("password") or "")
    return s


def _check(r: requests.Response):
    if r.status_code == 401:
        raise PermissionError("The recorder refused the login (check the camera's "
                              "username and password).")
    r.raise_for_status()


def _strip_ns(root):
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _hik_time(text: str) -> Optional[datetime]:
    text = (text or "").strip().replace("Z", "")
    text = re.sub(r"[+-]\d{2}:\d{2}$", "", text)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y%m%dT%H%M%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _search_hikvision(cam: Dict, day: date) -> List[Dict]:
    url = f"http://{cam['host']}:{cam.get('http_port') or 80}/ISAPI/ContentMgmt/search"
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1) - timedelta(seconds=1)
    s = _session(cam)
    out: List[Dict] = []
    position, search_id = 0, str(uuid.uuid4()).upper()
    for _page in range(20):
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<CMSearchDescription version="1.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
            f"<searchID>{search_id}</searchID>"
            f"<trackList><trackID>{track_id(cam)}</trackID></trackList>"
            f"<timeSpanList><timeSpan><startTime>{start:%Y-%m-%dT%H:%M:%S}Z</startTime>"
            f"<endTime>{end:%Y-%m-%dT%H:%M:%S}Z</endTime></timeSpan></timeSpanList>"
            "<maxResults>100</maxResults>"
            f"<searchResultPostion>{position}</searchResultPostion>"
            "<metadataList><metadataDescriptor>//recordType.meta.std-cgi.com"
            "</metadataDescriptor></metadataList></CMSearchDescription>")
        r = s.post(url, data=body, headers={"Content-Type": "application/xml"}, timeout=TIMEOUT)
        _check(r)
        root = _strip_ns(ET.fromstring(r.content))
        items = list(root.iter("searchMatchItem"))
        for item in items:
            st = _hik_time(item.findtext("timeSpan/startTime"))
            en = _hik_time(item.findtext("timeSpan/endTime"))
            if st and en:
                meta = (item.findtext("mediaSegmentDescriptor/metadataDescriptor") or "").lower()
                kind = "motion" if ("motion" in meta or "vmd" in meta) else \
                    "event" if any(k in meta for k in ("alarm", "event", "smart")) else "continuous"
                out.append({"start": max(st, start), "end": min(en, end + timedelta(seconds=1)),
                            "kind": kind})
        status = (root.findtext("responseStatusStrg") or "").upper()
        if status != "MORE" or not items:
            break
        position += len(items)
    return _merge(out)


def _search_dahua(cam: Dict, day: date) -> List[Dict]:
    base = f"http://{cam['host']}:{cam.get('http_port') or 80}/cgi-bin/mediaFileFind.cgi"
    s = _session(cam)
    r = s.get(base, params={"action": "factory.create"}, timeout=TIMEOUT)
    _check(r)
    m = re.search(r"result=(\d+)", r.text)
    if not m:
        raise RuntimeError(f"Unexpected answer from the recorder: {r.text[:80]!r}")
    obj = m.group(1)
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1) - timedelta(seconds=1)
    out: List[Dict] = []
    try:
        r = s.get(base, params={"action": "findFile", "object": obj,
                                "condition.Channel": int(cam.get("channel") or 1),
                                "condition.StartTime": f"{start:%Y-%m-%d %H:%M:%S}",
                                "condition.EndTime": f"{end:%Y-%m-%d %H:%M:%S}",
                                "condition.Types[0]": "dav"}, timeout=TIMEOUT)
        _check(r)
        for _page in range(50):
            r = s.get(base, params={"action": "findNextFile", "object": obj, "count": 100},
                      timeout=TIMEOUT)
            _check(r)
            items = _parse_dahua_items(r.text)
            for it in items:
                st, en = it.get("StartTime"), it.get("EndTime")
                try:
                    st = datetime.strptime(st, "%Y-%m-%d %H:%M:%S")
                    en = datetime.strptime(en, "%Y-%m-%d %H:%M:%S")
                except (TypeError, ValueError):
                    continue
                events = " ".join(v for k, v in it.items() if k.startswith("Events")).lower()
                kind = "motion" if "videomotion" in events else \
                    "event" if events else "continuous"
                out.append({"start": max(st, start), "end": min(en, end + timedelta(seconds=1)),
                            "kind": kind})
            if len(items) < 100:
                break
    finally:
        for action in ("close", "destroy"):
            try:
                s.get(base, params={"action": action, "object": obj}, timeout=TIMEOUT)
            except requests.RequestException:
                pass
    return _merge(out)


def _parse_dahua_items(text: str) -> List[Dict]:
    items: Dict[int, Dict] = {}
    for line in text.splitlines():
        m = re.match(r"items\[(\d+)\]\.([^=]+)=(.*)", line.strip())
        if m:
            items.setdefault(int(m.group(1)), {})[m.group(2)] = m.group(3).strip()
    return [items[i] for i in sorted(items)]


def _merge(segments: List[Dict]) -> List[Dict]:
    """Sort and join touching segments of the same kind."""
    out: List[Dict] = []
    for seg in sorted(segments, key=lambda x: x["start"]):
        if out and seg["kind"] == out[-1]["kind"] and \
                seg["start"] <= out[-1]["end"] + timedelta(seconds=2):
            out[-1]["end"] = max(out[-1]["end"], seg["end"])
        else:
            out.append(dict(seg))
    return out
