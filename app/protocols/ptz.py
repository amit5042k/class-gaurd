"""One PTZ interface for every brand: Hikvision ISAPI, Dahua/CP Plus CGI, ONVIF."""
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class ONVIFPTZ:
    def __init__(self, host, port, user, password):
        self._args = (host, port, user, password)
        self._ptz = None
        self._token = None

    def _connect(self):
        if self._ptz is None:
            from onvif import ONVIFCamera
            cam = ONVIFCamera(*self._args)
            media = cam.create_media_service()
            self._token = media.GetProfiles()[0].token
            self._ptz = cam.create_ptz_service()

    def ptz_move(self, _channel, direction, speed=50):
        self._connect()
        v = max(0.05, min(1.0, speed / 100))
        pan, tilt, zoom = {"up": (0, v, 0), "down": (0, -v, 0), "left": (-v, 0, 0),
                           "right": (v, 0, 0), "zoom_in": (0, 0, v),
                           "zoom_out": (0, 0, -v)}[direction]
        req = self._ptz.create_type("ContinuousMove")
        req.ProfileToken = self._token
        req.Velocity = {"PanTilt": {"x": pan, "y": tilt}, "Zoom": {"x": zoom}}
        self._ptz.ContinuousMove(req)
        return True

    def ptz_stop(self, _channel):
        self._connect()
        self._ptz.Stop({"ProfileToken": self._token, "PanTilt": True, "Zoom": True})
        return True

    def ptz_goto_preset(self, _channel, preset):
        self._connect()
        presets = self._ptz.GetPresets({"ProfileToken": self._token}) or []
        if not 1 <= preset <= len(presets):
            raise ValueError(f"Preset {preset} does not exist on this camera")
        self._ptz.GotoPreset({"ProfileToken": self._token,
                              "PresetToken": presets[preset - 1].token})
        return True


def controller_for(cam: Dict):
    """Pick the PTZ backend for a camera dict (as returned by CameraManager)."""
    brand = cam.get("brand", "")
    host, port = cam["host"], cam.get("http_port") or 80
    user, pwd = cam.get("username", ""), cam.get("password", "")
    if brand == "Hikvision":
        from app.protocols.hikvision import HikvisionAPI
        return HikvisionAPI(host, port, user, pwd)
    if brand in ("Dahua", "CP Plus"):
        from app.protocols.dahua import DahuaAPI
        return DahuaAPI(host, port, user, pwd)
    return ONVIFPTZ(host, port, user, pwd)  # UNV, ONVIF and others
