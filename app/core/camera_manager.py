import logging
from typing import List, Optional, Dict
from app.models import Camera, NVR
from app.config import Config

logger = logging.getLogger(__name__)


class CameraManager:
    def __init__(self, db):
        self.db = db

    # ---------------------------------------------------------------- Camera
    def add_camera(self, data: Dict) -> Camera:
        session = self.db.get_session()
        try:
            if not data.get("rtsp_url"):
                data["rtsp_url"] = Config.build_rtsp_url(
                    data.get("brand", "Generic RTSP"),
                    data["host"], data.get("port", 554),
                    data.get("username", "admin"),
                    data.get("password", ""),
                    data.get("channel", 1),
                )
            cam = Camera(**data)
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
        session = self.db.get_session()
        try:
            cam = session.query(Camera).get(camera_id)
            if not cam:
                return None
            for k, v in data.items():
                if hasattr(cam, k):
                    setattr(cam, k, v)
            if not cam.rtsp_url:
                cam.rtsp_url = Config.build_rtsp_url(
                    cam.brand, cam.host, cam.port,
                    cam.username, cam.password, cam.channel
                )
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
            cam = session.query(Camera).get(camera_id)
            if not cam:
                return False
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
                q = q.filter(Camera.is_active == True)
            cams = q.order_by(Camera.group_name, Camera.name).all()
            return [self._cam_dict(c) for c in cams]
        finally:
            session.close()

    def get_camera(self, camera_id: int) -> Optional[Dict]:
        session = self.db.get_session()
        try:
            cam = session.query(Camera).get(camera_id)
            return self._cam_dict(cam) if cam else None
        finally:
            session.close()

    @staticmethod
    def _cam_dict(cam: Camera) -> Dict:
        return {
            "id": cam.id, "name": cam.name, "brand": cam.brand,
            "camera_type": cam.camera_type, "connection_type": cam.connection_type,
            "host": cam.host, "port": cam.port, "http_port": cam.http_port,
            "username": cam.username, "password": cam.password,
            "channel": cam.channel, "rtsp_url": cam.rtsp_url,
            "onvif_enabled": cam.onvif_enabled, "is_active": cam.is_active,
            "location": cam.location or "", "group_name": cam.group_name or "Default",
            "nvr_id": cam.nvr_id,
            "face_detection": cam.face_detection,
            "attendance_tracking": cam.attendance_tracking,
            "expression_monitor": cam.expression_monitor,
            "safety_watch": cam.safety_watch,
            "recording_enabled": cam.recording_enabled,
        }

    # ------------------------------------------------------------------- NVR
    def add_nvr(self, data: Dict) -> NVR:
        session = self.db.get_session()
        try:
            nvr = NVR(**data)
            session.add(nvr)
            session.commit()
            session.refresh(nvr)
            logger.info("NVR added: %s (id=%d)", nvr.name, nvr.id)
            return nvr
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()

    def get_all_nvrs(self) -> List[Dict]:
        session = self.db.get_session()
        try:
            nvrs = session.query(NVR).order_by(NVR.name).all()
            return [{
                "id": n.id, "name": n.name, "brand": n.brand,
                "host": n.host, "port": n.port, "http_port": n.http_port,
                "username": n.username, "password": n.password,
                "total_channels": n.total_channels,
                "is_active": n.is_active, "location": n.location or "",
            } for n in nvrs]
        finally:
            session.close()

    def delete_nvr(self, nvr_id: int) -> bool:
        session = self.db.get_session()
        try:
            nvr = session.query(NVR).get(nvr_id)
            if not nvr:
                return False
            session.delete(nvr)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            return False
        finally:
            session.close()
