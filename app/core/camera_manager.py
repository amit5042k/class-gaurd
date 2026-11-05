import logging
from typing import List, Optional, Dict
from sqlalchemy import func

from app.models import NVR, Camera
from app.config import Config
from app.core.security import decrypt_secret, encrypt_secret, is_encrypted

logger = logging.getLogger(__name__)


def _natural_key(text: str):
    import re
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", text or "")]


class CameraManager:
    def __init__(self, db):
        self.db = db

    # ---------------------------------------------------------------- Camera
    # Passwords are stored encrypted. The RTSP address is only stored when it
    # is a custom one (encrypted too, since it usually embeds the password);
    # otherwise it is rebuilt from the connection details every time it is read.

    @staticmethod
    def _prepare(data: Dict) -> Dict:
        data = dict(data)
        if "recording_mode" in data:
            data["recording_enabled"] = data["recording_mode"] != "off"
        if "password" in data:
            data["password"] = encrypt_secret(data["password"] or "")
        if "rtsp_url" in data:
            data["rtsp_url"] = encrypt_secret(data["rtsp_url"]) if data["rtsp_url"] else None
        return data

    def add_camera(self, data: Dict) -> Camera:
        session = self.db.get_session()
        try:
            cam = Camera(**self._prepare(data))
            session.add(cam)
            session.commit()
            session.refresh(cam)
            logger.info("Camera added: %s (id=%d)", cam.name, cam.id)
            return cam
        except Exception as e:
            session.rollback()
            logger.error("add_camera: %s", e)
            raise
        finally:
            session.close()

    def update_camera(self, camera_id: int, data: Dict) -> Optional[Camera]:
        """rtsp_url=None switches back to the automatic address."""
        session = self.db.get_session()
        try:
            cam = session.get(Camera, camera_id)
            if not cam:
                return None
            for k, v in self._prepare(data).items():
                if hasattr(cam, k):
                    setattr(cam, k, v)
            session.commit()
            return cam
        except Exception as e:
            session.rollback()
            logger.error("update_camera: %s", e)
            raise
        finally:
            session.close()

    def delete_camera(self, camera_id: int) -> bool:
        session = self.db.get_session()
        try:
            cam = session.get(Camera, camera_id)
            if not cam:
                return False
            from app.models import Zone
            session.query(Zone).filter_by(camera_id=camera_id).delete()
            session.delete(cam)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error("delete_camera: %s", e)
            return False
        finally:
            session.close()

    def get_all_cameras(self, active_only: bool = False) -> List[Dict]:
        session = self.db.get_session()
        try:
            q = session.query(Camera)
            if active_only:
                q = q.filter(Camera.is_active.is_(True))
            cams = q.all()
            # Natural order: "Channel 2" before "Channel 10".
            cams.sort(key=lambda c: (c.group_name or "Default", _natural_key(c.name)))
            return [self._cam_dict(c) for c in cams]
        finally:
            session.close()

    def get_camera(self, camera_id: int) -> Optional[Dict]:
        session = self.db.get_session()
        try:
            cam = session.get(Camera, camera_id)
            return self._cam_dict(cam) if cam else None
        finally:
            session.close()

    @staticmethod
    def auto_url(d: Dict) -> str:
        return Config.build_rtsp_url(
            d.get("brand", "Generic RTSP"), d["host"], d.get("port", 554),
            d.get("username", "admin"), d.get("password", ""),
            d.get("channel", 1), bool(d.get("use_substream")))

    @classmethod
    def _cam_dict(cls, cam: Camera) -> Dict:
        d = {
            "id": cam.id, "name": cam.name, "brand": cam.brand,
            "camera_type": cam.camera_type, "connection_type": cam.connection_type,
            "host": cam.host, "port": cam.port, "http_port": cam.http_port,
            "username": cam.username, "password": decrypt_secret(cam.password),
            "channel": cam.channel, "custom_rtsp_url": decrypt_secret(cam.rtsp_url),
            "use_substream": bool(cam.use_substream),
            "rtsp_transport": cam.rtsp_transport or "auto",
            "onvif_enabled": cam.onvif_enabled, "is_active": cam.is_active,
            "location": cam.location or "", "group_name": cam.group_name or "Default",
            "nvr_id": cam.nvr_id,
            "face_detection": cam.face_detection,
            "attendance_tracking": cam.attendance_tracking,
            "expression_monitor": cam.expression_monitor,
            "safety_watch": cam.safety_watch,
            "recording_enabled": cam.recording_enabled,
            "recording_mode": cam.recording_mode or "off",
        }
        d["rtsp_url"] = d["custom_rtsp_url"] or cls.auto_url(d)
        return d

    def migrate_secrets(self) -> int:
        """Encrypt passwords saved by older versions; drop stored automatic URLs."""
        changed = 0
        session = self.db.get_session()
        try:
            for cam in session.query(Camera):
                before = (cam.password, cam.rtsp_url)
                if cam.rtsp_url and not is_encrypted(cam.rtsp_url):
                    plain = dict(self._cam_dict(cam), custom_rtsp_url="")
                    cam.rtsp_url = None if cam.rtsp_url == self.auto_url(plain) \
                        else encrypt_secret(cam.rtsp_url)
                if cam.password and not is_encrypted(cam.password):
                    cam.password = encrypt_secret(cam.password)
                changed += before != (cam.password, cam.rtsp_url)
            for nvr in session.query(NVR):
                if nvr.password and not is_encrypted(nvr.password):
                    nvr.password = encrypt_secret(nvr.password)
                    changed += 1
            session.commit()
        finally:
            session.close()
        if changed:
            logger.info("Encrypted stored credentials for %d device(s)", changed)
        return changed

    # ------------------------------------------------------------------- NVR
    def add_nvr(self, data: Dict) -> NVR:
        session = self.db.get_session()
        try:
            data = dict(data)
            data["password"] = encrypt_secret(data.get("password") or "")
            nvr = NVR(**data)
            session.add(nvr)
            session.commit()
            session.refresh(nvr)
            logger.info("NVR added: %s (id=%d)", nvr.name, nvr.id)
            return nvr
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_all_nvrs(self) -> List[Dict]:
        session = self.db.get_session()
        try:
            nvrs = session.query(NVR).order_by(NVR.name).all()
            counts = dict(session.query(Camera.nvr_id, func.count(Camera.id))
                          .filter(Camera.nvr_id.isnot(None)).group_by(Camera.nvr_id).all())
            out = []
            for n in nvrs:
                d = self._nvr_dict(n)
                d["camera_count"] = counts.get(n.id, 0)
                out.append(d)
            return out
        finally:
            session.close()

    def get_nvr(self, nvr_id: int) -> Optional[Dict]:
        session = self.db.get_session()
        try:
            n = session.get(NVR, nvr_id)
            return self._nvr_dict(n) if n else None
        finally:
            session.close()

    @staticmethod
    def _nvr_dict(n: NVR) -> Dict:
        return {
            "id": n.id, "name": n.name, "brand": n.brand,
            "host": n.host, "port": n.port, "http_port": n.http_port,
            "username": n.username, "password": decrypt_secret(n.password),
            "total_channels": n.total_channels,
            "is_active": n.is_active, "location": n.location or "",
        }

    def update_nvr(self, nvr_id: int, data: Dict, update_cameras: bool = True):
        """Save NVR changes; cameras imported from it follow its IP and login."""
        session = self.db.get_session()
        try:
            n = session.get(NVR, nvr_id)
            if not n:
                return
            data = dict(data)
            if "password" in data:
                data["password"] = encrypt_secret(data["password"] or "")
            for k, v in data.items():
                if hasattr(n, k):
                    setattr(n, k, v)
            if update_cameras:
                # Imported cameras follow the recorder; automatic URLs are
                # rebuilt on read, custom ones are left alone.
                for cam in session.query(Camera).filter_by(nvr_id=nvr_id):
                    cam.host, cam.username, cam.password = n.host, n.username, n.password
                    cam.http_port, cam.brand = n.http_port, n.brand
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def channel_url(self, nvr: Dict, channel: int, rtsp_port: int = 554) -> str:
        return self.auto_url({"brand": nvr["brand"], "host": nvr["host"], "port": rtsp_port,
                              "username": nvr["username"], "password": nvr["password"],
                              "channel": channel,
                              "use_substream": int(nvr["total_channels"]) > 4})

    def probe_nvr_channels(self, nvr_id: int, channels=None, workers: int = 4) -> Dict[int, tuple]:
        """Ask the NVR which channels have video. Returns {channel: (kind, message)}."""
        from concurrent.futures import ThreadPoolExecutor
        from app.protocols.rtsp_diag import describe
        nvr = self.get_nvr(nvr_id)
        if not nvr:
            return {}
        channels = list(channels or range(1, int(nvr["total_channels"]) + 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = pool.map(lambda ch: describe(self.channel_url(nvr, ch)), channels)
            return dict(zip(channels, results))

    def import_nvr_channels(self, nvr_id: int, names: Dict[int, str] = None,
                            rtsp_port: int = 554, only=None) -> int:
        """Create a camera for each NVR channel that doesn't have one yet
        (only the channels in `only`, when given)."""
        nvr = self.get_nvr(nvr_id)
        if not nvr:
            return 0
        names = names or {}
        session = self.db.get_session()
        try:
            have = {c.channel for c in session.query(Camera).filter_by(nvr_id=nvr_id)}
        finally:
            session.close()
        created = 0
        for ch in range(1, int(nvr["total_channels"]) + 1):
            if ch in have or (only is not None and ch not in only):
                continue
            title = (names.get(ch) or "").strip() or f"Channel {ch}"
            self.add_camera({
                "name": f"{nvr['name']} - {title}", "brand": nvr["brand"],
                "camera_type": "NVR Channel", "host": nvr["host"], "port": rtsp_port,
                "http_port": nvr["http_port"], "username": nvr["username"],
                "password": nvr["password"], "channel": ch, "nvr_id": nvr_id,
                "location": nvr["location"], "group_name": nvr["name"],
                "use_substream": int(nvr["total_channels"]) > 4,
            })
            created += 1
        return created

    def delete_nvr(self, nvr_id: int, delete_cameras: bool = False) -> bool:
        session = self.db.get_session()
        try:
            nvr = session.get(NVR, nvr_id)
            if not nvr:
                return False
            cam_ids = [c.id for c in session.query(Camera).filter_by(nvr_id=nvr_id)]
            if delete_cameras:
                from app.models import Zone
                if cam_ids:
                    session.query(Zone).filter(Zone.camera_id.in_(cam_ids)).delete(
                        synchronize_session=False)
                    session.query(Camera).filter(Camera.id.in_(cam_ids)).delete(
                        synchronize_session=False)
            else:
                session.query(Camera).filter_by(nvr_id=nvr_id).update(
                    {"nvr_id": None}, synchronize_session=False)
            session.delete(nvr)
            session.commit()
            return True
        except Exception:
            session.rollback()
            logger.exception("delete_nvr failed")
            return False
        finally:
            session.close()
