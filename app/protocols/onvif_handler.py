import logging
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


class ONVIFHandler:
    def __init__(self, host: str, port: int, username: str, password: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.camera = None
        self._media = None

    def connect(self) -> bool:
        try:
            from onvif import ONVIFCamera
            self.camera = ONVIFCamera(self.host, self.port, self.username, self.password)
            self._media = self.camera.create_media_service()
            return True
        except Exception as e:
            logger.error("ONVIF connect failed %s:%s — %s", self.host, self.port, e)
            return False

    def get_stream_uri(self, profile_index: int = 0) -> Optional[str]:
        try:
            profiles = self._media.GetProfiles()
            if not profiles:
                return None
            token = profiles[profile_index].token
            req = self._media.create_type("GetStreamUri")
            req.ProfileToken = token
            req.StreamSetup = {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}}
            uri = self._media.GetStreamUri(req).Uri
            if "@" not in uri:
                uri = uri.replace("rtsp://", f"rtsp://{self.username}:{self.password}@")
            return uri
        except Exception as e:
            logger.error("get_stream_uri: %s", e)
            return None

    def get_device_info(self) -> Dict:
        try:
            svc = self.camera.create_devicemgmt_service()
            info = svc.GetDeviceInformation()
            return {
                "manufacturer": info.Manufacturer,
                "model": info.Model,
                "firmware": info.FirmwareVersion,
                "serial": info.SerialNumber,
            }
        except Exception as e:
            logger.error("get_device_info: %s", e)
            return {}

    def get_profiles(self) -> List[Dict]:
        try:
            profiles = self._media.GetProfiles()
            result = []
            for p in profiles:
                enc = p.VideoEncoderConfiguration
                result.append({
                    "token": p.token,
                    "name": p.Name,
                    "width":  enc.Resolution.Width  if enc else 0,
                    "height": enc.Resolution.Height if enc else 0,
                })
            return result
        except Exception as e:
            logger.error("get_profiles: %s", e)
            return []

    @staticmethod
    def discover(timeout: int = 5) -> List[Dict]:
        try:
            from wsdiscovery import WSDiscovery
            wsd = WSDiscovery()
            wsd.start()
            services = wsd.searchServices(timeout=timeout)
            cameras = []
            for svc in services:
                xaddrs = svc.getXAddrs()
                if xaddrs:
                    cameras.append({"url": xaddrs[0], "types": str(svc.getTypes())})
            wsd.stop()
            return cameras
        except Exception as e:
            logger.error("ONVIF discovery: %s", e)
            return []
