"""Remote configuration of recorders and cameras (like iVMS "Remote Config").

One interface, two implementations:
* HikvisionConfig: ISAPI over HTTP (Digest login)
* DahuaConfig: Dahua / CP Plus CGI (configManager, magicBox, global)

Video settings use one normalised form for both:
    {"codec": "H.264", "width": 640, "height": 360, "bitrate_type": "VBR",
     "bitrate": 512, "fps": 20.0, "gop": 40, "quality": "Medium",
     "smart_codec": False or None (None = setting not offered)}
"""
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 8
CONFIG_BRANDS = ("Hikvision", "Dahua", "CP Plus")
HIK_QUALITY = {"Lowest": 1, "Lower": 20, "Low": 40, "Medium": 60, "Higher": 80, "Highest": 100}


def supports_config(dev: Dict) -> bool:
    return dev.get("brand") in CONFIG_BRANDS and bool(dev.get("host"))


def device_config(dev: Dict):
    """Configuration client for a device dict (NVR or camera: brand, host,
    http_port, username, password)."""
    if dev.get("brand") == "Hikvision":
        return HikvisionConfig(dev)
    if dev.get("brand") in ("Dahua", "CP Plus"):
        return DahuaConfig(dev)
    raise ValueError(f"Remote configuration is not available for {dev.get('brand')} devices.")


def recommended(video: Dict) -> Dict:
    """Settings that make live view start fast and never stay grey: H.264,
    H.264+/Smart Codec off, a full picture (I-frame) every 2 seconds."""
    fps = float(video.get("fps") or 20) or 20
    out = {"codec": "H.264", "gop": int(round(fps * 2))}
    if video.get("smart_codec") is not None:
        out["smart_codec"] = False
    return out


class _Base:
    def __init__(self, dev: Dict):
        self.dev = dev
        self.base = f"http://{dev['host']}:{dev.get('http_port') or 80}"
        self.s = requests.Session()
        self.s.auth = requests.auth.HTTPDigestAuth(dev.get("username") or "",
                                                   dev.get("password") or "")

    def _req(self, method: str, path: str, **kw) -> requests.Response:
        try:
            r = self.s.request(method, self.base + path, timeout=TIMEOUT, **kw)
        except requests.ConnectionError:
            raise ConnectionError(f"Can't reach {self.dev['host']} on HTTP port "
                                  f"{self.dev.get('http_port') or 80}.") from None
        except requests.Timeout:
            raise ConnectionError(f"{self.dev['host']} did not answer in time.") from None
        if r.status_code == 401:
            raise PermissionError("The device refused the login. Check the username and "
                                  "password (and that this user may change settings).")
        return r


# ===================================================================== Hikvision
def _ns(root: ET.Element) -> str:
    return root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""


def _find(root: ET.Element, path: str) -> Optional[ET.Element]:
    ns = _ns(root)
    if ns:
        path = "/".join(f"{{{ns}}}{p}" for p in path.split("/"))
    return root.find(path)


def _text(root, path, default=""):
    el = _find(root, path)
    return (el.text or "").strip() if el is not None and el.text else default


def _set(root, path, value) -> bool:
    el = _find(root, path)
    if el is None:
        return False
    el.text = str(value)
    return True


def _opts(root, path) -> List[str]:
    el = _find(root, path)
    if el is None:
        return []
    opt = el.get("opt")
    return [o.strip() for o in opt.split(",") if o.strip()] if opt else []


def _xml_bytes(root: ET.Element) -> bytes:
    ns = _ns(root)
    if ns:
        ET.register_namespace("", ns)
    return b'<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root)


class HikvisionConfig(_Base):
    def _get_xml(self, path: str) -> ET.Element:
        r = self._req("GET", "/ISAPI" + path)
        if r.status_code == 404:
            raise LookupError(path)
        r.raise_for_status()
        return ET.fromstring(r.content)

    def _put_xml(self, path: str, root: ET.Element):
        r = self._req("PUT", "/ISAPI" + path, data=_xml_bytes(root),
                      headers={"Content-Type": "application/xml"})
        if r.status_code not in (200, 201):
            msg = ""
            try:
                resp = ET.fromstring(r.content)
                msg = _text(resp, "subStatusCode") or _text(resp, "statusString")
            except ET.ParseError:
                pass
            raise RuntimeError(f"The device rejected the change ({r.status_code}"
                               + (f": {msg}" if msg else "") + ").")
        try:
            resp = ET.fromstring(r.content)
            if _text(resp, "statusCode") == "7" or "reboot" in _text(resp, "statusString").lower():
                return "reboot"
        except ET.ParseError:
            pass
        return "ok"

    # --------------------------------------------------------------- device
    def device_info(self) -> Dict:
        root = self._get_xml("/System/deviceInfo")
        return {k: _text(root, t) for k, t in (
            ("name", "deviceName"), ("model", "model"), ("serial", "serialNumber"),
            ("firmware", "firmwareVersion"), ("type", "deviceType"),
            ("mac", "macAddress"))}

    def get_time(self) -> Dict:
        root = self._get_xml("/System/time")
        return {"mode": _text(root, "timeMode"), "local": _text(root, "localTime"),
                "zone": _text(root, "timeZone")}

    def sync_time(self, when: Optional[datetime] = None) -> str:
        root = self._get_xml("/System/time")
        _set(root, "timeMode", "manual")
        _set(root, "localTime", (when or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S"))
        return self._put_xml("/System/time", root)

    def reboot(self):
        r = self._req("PUT", "/ISAPI/System/reboot")
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Reboot refused ({r.status_code}).")

    def storage(self) -> List[Dict]:
        try:
            root = self._get_xml("/ContentMgmt/Storage")
        except LookupError:
            return []
        ns = _ns(root)
        tag = f"{{{ns}}}hdd" if ns else "hdd"
        out = []
        for hdd in root.iter(tag):
            cap = int(_text(hdd, "capacity", "0") or 0)
            free = int(_text(hdd, "freeSpace", "0") or 0)
            out.append({"id": _text(hdd, "id"), "name": _text(hdd, "hddName"),
                        "capacity_gb": cap / 1024, "free_gb": free / 1024,
                        "status": _text(hdd, "status")})
        return out

    def channels(self) -> List[Dict]:
        """NVR: connected IP cameras with name and online state."""
        try:
            root = self._get_xml("/ContentMgmt/InputProxy/channels")
        except LookupError:
            return []
        ns = _ns(root)
        tag = f"{{{ns}}}InputProxyChannel" if ns else "InputProxyChannel"
        chans = {}
        for ch in root.iter(tag):
            try:
                cid = int(_text(ch, "id"))
            except ValueError:
                continue
            chans[cid] = {"id": cid, "name": _text(ch, "name"),
                          "ip": _text(ch, "sourceInputPortDescriptor/ipAddress"),
                          "online": None}
        try:
            st = self._get_xml("/ContentMgmt/InputProxy/channels/status")
            stag = f"{{{_ns(st)}}}InputProxyChannelStatus" if _ns(st) else \
                "InputProxyChannelStatus"
            for s in st.iter(stag):
                try:
                    cid = int(_text(s, "id"))
                except ValueError:
                    continue
                if cid in chans:
                    chans[cid]["online"] = _text(s, "online").lower() == "true"
        except (LookupError, requests.HTTPError, ET.ParseError):
            pass
        return [chans[k] for k in sorted(chans)]

    # ---------------------------------------------------------------- video
    def _stream_paths(self, channel: int, sub: bool) -> List[str]:
        sid = channel * 100 + (2 if sub else 1)
        # Recorders keep IP-camera settings under StreamingProxy; cameras and
        # DVR inputs under Streaming.
        return [f"/ContentMgmt/StreamingProxy/channels/{sid}", f"/Streaming/channels/{sid}"]

    def _stream_xml(self, channel: int, sub: bool) -> Tuple[str, ET.Element]:
        last = None
        preferred = getattr(self, "_path_kind", None)
        paths = self._stream_paths(channel, sub)
        if preferred is not None:
            paths.sort(key=lambda p: 0 if p.startswith(preferred) else 1)
        for path in paths:
            try:
                root = self._get_xml(path)
            except (LookupError, requests.HTTPError) as e:
                last = e
                continue
            if _find(root, "Video") is not None:
                self._path_kind = path.rsplit("/channels", 1)[0]
                return path, root
        raise RuntimeError(f"The device has no video settings for channel {channel}"
                           + (f" ({last})" if last else "") + ".")

    def get_video(self, channel: int, sub: bool) -> Dict:
        _, root = self._stream_xml(channel, sub)
        vbr = _text(root, "Video/videoQualityControlType").upper() or "VBR"
        bitrate = _text(root, "Video/vbrUpperCap" if vbr == "VBR" else "Video/constantBitRate")
        q = _text(root, "Video/fixedQuality")
        quality = next((k for k, v in HIK_QUALITY.items() if str(v) == q), q)
        smart = _find(root, "Video/SmartCodec/enabled")
        return {
            "codec": _text(root, "Video/videoCodecType"),
            "width": int(_text(root, "Video/videoResolutionWidth", "0") or 0),
            "height": int(_text(root, "Video/videoResolutionHeight", "0") or 0),
            "bitrate_type": vbr, "bitrate": int(bitrate or 0),
            "fps": int(_text(root, "Video/maxFrameRate", "0") or 0) / 100.0,
            "gop": int(_text(root, "Video/GovLength", "0") or 0),
            "quality": quality,
            "smart_codec": (smart.text or "").strip().lower() == "true"
            if smart is not None else None,
        }

    def video_options(self, channel: int, sub: bool) -> Dict:
        path, _ = self._stream_xml(channel, sub)
        try:
            cap = self._get_xml(path + "/capabilities")
        except (LookupError, requests.HTTPError):
            return {}
        widths = _opts(cap, "Video/videoResolutionWidth")
        heights = _opts(cap, "Video/videoResolutionHeight")
        res = []
        if widths and len(widths) == len(heights):
            res = [(int(w), int(h)) for w, h in zip(widths, heights)]
        fps = []
        for v in _opts(cap, "Video/maxFrameRate"):
            try:
                if int(v) > 0:
                    fps.append(int(v) / 100.0)
            except ValueError:
                pass
        return {"codec": _opts(cap, "Video/videoCodecType"), "resolution": res,
                "fps": sorted(set(fps), reverse=True),
                "bitrate_type": [o.upper() for o in _opts(cap, "Video/videoQualityControlType")]}

    def set_video(self, channel: int, sub: bool, changes: Dict) -> str:
        path, root = self._stream_xml(channel, sub)
        if "codec" in changes:
            _set(root, "Video/videoCodecType", changes["codec"])
        if "width" in changes and "height" in changes:
            _set(root, "Video/videoResolutionWidth", int(changes["width"]))
            _set(root, "Video/videoResolutionHeight", int(changes["height"]))
        vbr = changes.get("bitrate_type") or _text(root, "Video/videoQualityControlType").upper()
        if "bitrate_type" in changes:
            _set(root, "Video/videoQualityControlType", changes["bitrate_type"])
        if "bitrate" in changes:
            _set(root, "Video/vbrUpperCap" if vbr == "VBR" else "Video/constantBitRate",
                 int(changes["bitrate"]))
        if "fps" in changes:
            _set(root, "Video/maxFrameRate", int(round(float(changes["fps"]) * 100)))
        if "gop" in changes:
            _set(root, "Video/GovLength", int(changes["gop"]))
        if "quality" in changes and changes["quality"] in HIK_QUALITY:
            _set(root, "Video/fixedQuality", HIK_QUALITY[changes["quality"]])
        if "smart_codec" in changes and changes["smart_codec"] is not None:
            _set(root, "Video/SmartCodec/enabled", "true" if changes["smart_codec"] else "false")
        return self._put_xml(path, root)


# ===================================================================== Dahua
def _kv(text: str) -> Dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


DAHUA_RES = {"D1": (704, 576), "720P": (1280, 720), "1080P": (1920, 1080),
             "CIF": (352, 288), "VGA": (640, 480), "QVGA": (320, 240)}


class DahuaConfig(_Base):
    def _get(self, path: str, **params) -> str:
        r = self._req("GET", path, params=params)
        if r.status_code == 404:
            raise LookupError(path)
        r.raise_for_status()
        return r.text

    def _set_config(self, values: Dict[str, object]) -> str:
        params = {"action": "setConfig", **{k: str(v) for k, v in values.items()}}
        text = self._get("/cgi-bin/configManager.cgi", **params)
        if "OK" not in text:
            raise RuntimeError(f"The device rejected the change: {text.strip()[:80]}")
        return "ok"

    def device_info(self) -> Dict:
        def one(action, key):
            try:
                return _kv(self._get("/cgi-bin/magicBox.cgi", action=action)).get(key, "")
            except (LookupError, requests.HTTPError):
                return ""
        return {"name": one("getMachineName", "name"), "model": one("getDeviceType", "type"),
                "serial": one("getSerialNo", "sn"),
                "firmware": one("getSoftwareVersion", "version"),
                "type": one("getDeviceClass", "class"), "mac": ""}

    def get_time(self) -> Dict:
        t = _kv(self._get("/cgi-bin/global.cgi", action="getCurrentTime")).get("result", "")
        return {"mode": "", "local": t, "zone": ""}

    def sync_time(self, when: Optional[datetime] = None) -> str:
        text = self._get("/cgi-bin/global.cgi", action="setCurrentTime",
                         time=(when or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"))
        if "OK" not in text:
            raise RuntimeError(f"Time change refused: {text.strip()[:80]}")
        return "ok"

    def reboot(self):
        self._get("/cgi-bin/magicBox.cgi", action="reboot")

    def storage(self) -> List[Dict]:
        try:
            kv = _kv(self._get("/cgi-bin/storageDevice.cgi", action="getDeviceAllInfo"))
        except (LookupError, requests.HTTPError):
            return []
        disks: Dict[str, Dict] = {}
        for k, v in kv.items():
            m = re.match(r"list\.info\[(\d+)\]\.(Detail\[0\]\.)?(\w+)", k)
            if m:
                d = disks.setdefault(m.group(1), {"id": m.group(1), "name": "",
                                                  "capacity_gb": 0.0, "free_gb": 0.0,
                                                  "status": ""})
                key = m.group(3)
                if key == "Name":
                    d["name"] = v
                elif key == "TotalBytes":
                    d["capacity_gb"] = float(v) / 1024 ** 3
                elif key == "UsedBytes":
                    d["used"] = float(v) / 1024 ** 3
                elif key == "State":
                    d["status"] = v
        for d in disks.values():
            d["free_gb"] = max(0.0, d["capacity_gb"] - d.pop("used", 0.0))
        return list(disks.values())

    def channels(self) -> List[Dict]:
        try:
            kv = _kv(self._get("/cgi-bin/configManager.cgi", action="getConfig",
                               name="ChannelTitle"))
        except (LookupError, requests.HTTPError):
            return []
        out = []
        for k, v in kv.items():
            m = re.match(r"table\.ChannelTitle\[(\d+)\]\.Name", k)
            if m:
                out.append({"id": int(m.group(1)) + 1, "name": v, "ip": "", "online": None})
        return sorted(out, key=lambda c: c["id"])

    def _prefix(self, channel: int, sub: bool) -> str:
        return f"Encode[{channel - 1}].{'ExtraFormat' if sub else 'MainFormat'}[0].Video"

    def get_video(self, channel: int, sub: bool) -> Dict:
        kv = _kv(self._get("/cgi-bin/configManager.cgi", action="getConfig", name="Encode"))
        p = "table." + self._prefix(channel, sub) + "."
        if not any(k.startswith(p) for k in kv):
            raise RuntimeError(f"The device has no video settings for channel {channel}.")
        w = int(kv.get(p + "Width", "0") or 0)
        h = int(kv.get(p + "Height", "0") or 0)
        if not w and kv.get(p + "resolution", "").upper() in DAHUA_RES:
            w, h = DAHUA_RES[kv[p + "resolution"].upper()]
        smart = kv.get("table." + self._prefix(channel, sub).rsplit(".", 1)[0] + ".SmartCodec")
        return {"codec": kv.get(p + "Compression", ""), "width": w, "height": h,
                "bitrate_type": kv.get(p + "BitRateControl", "").upper(),
                "bitrate": int(kv.get(p + "BitRate", "0") or 0),
                "fps": float(kv.get(p + "FPS", "0") or 0),
                "gop": int(kv.get(p + "GOP", "0") or 0),
                "quality": kv.get(p + "Quality", ""),
                "smart_codec": None if smart is None else smart.lower() == "on"}

    def video_options(self, channel: int, sub: bool) -> Dict:
        return {"codec": ["H.264", "H.265"], "bitrate_type": ["CBR", "VBR"]}

    def set_video(self, channel: int, sub: bool, changes: Dict) -> str:
        p = self._prefix(channel, sub) + "."
        values = {}
        if "codec" in changes:
            values[p + "Compression"] = changes["codec"]
        if "width" in changes and "height" in changes:
            values[p + "Width"] = int(changes["width"])
            values[p + "Height"] = int(changes["height"])
        if "bitrate_type" in changes:
            values[p + "BitRateControl"] = changes["bitrate_type"]
        if "bitrate" in changes:
            values[p + "BitRate"] = int(changes["bitrate"])
        if "fps" in changes:
            values[p + "FPS"] = int(round(float(changes["fps"])))
        if "gop" in changes:
            values[p + "GOP"] = int(changes["gop"])
        if "quality" in changes and str(changes["quality"]).isdigit():
            values[p + "Quality"] = int(changes["quality"])
        if changes.get("smart_codec") is not None:
            values[self._prefix(channel, sub).rsplit(".", 1)[0] + ".SmartCodec"] = \
                "On" if changes["smart_codec"] else "Off"
        if not values:
            return "ok"
        return self._set_config(values)
