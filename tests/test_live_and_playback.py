import time
from datetime import datetime, timedelta

import cv2
import numpy as np
import pytest

from app.config import Config


class FakeResponse:
    def __init__(self, status=200, text="", content=b""):
        self.status_code, self.text, self.content = status, text, content or text.encode()
        self.ok = status < 400

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, responses=None):
        self.calls, self.responses, self.auth = [], responses or {}, None

    def _do(self, method, url, **kw):
        self.calls.append((method, url, kw.get("data")))
        for key, resp in self.responses.items():
            if key in url:
                return resp
        return FakeResponse()

    def get(self, url, **kw):
        return self._do("GET", url, **kw)

    def put(self, url, **kw):
        return self._do("PUT", url, **kw)


# ---------------------------------------------------------------- PTZ
def test_hikvision_ptz_move_and_stop():
    from app.protocols.hikvision import HikvisionAPI
    api = HikvisionAPI("10.0.0.2", 80, "admin", "pw")
    api.session = FakeSession()
    assert api.ptz_move(2, "left", 40)
    assert api.ptz_stop(2)
    (m1, u1, b1), (m2, u2, b2) = api.session.calls
    assert m1 == "PUT" and u1.endswith("/ISAPI/PTZCtrl/channels/2/continuous")
    assert "<pan>-40</pan><tilt>0</tilt><zoom>0</zoom>" in b1
    assert "<pan>0</pan><tilt>0</tilt><zoom>0</zoom>" in b2


def test_dahua_ptz_uses_zero_based_channel_and_stop():
    from app.protocols.dahua import DahuaAPI
    api = DahuaAPI("10.0.0.3", 80, "admin", "pw")
    api.session = FakeSession()
    api.ptz_move(1, "zoom_in", 100)
    api.ptz_stop(1)
    start, stop = api.session.calls[0][1], api.session.calls[1][1]
    assert "action=start&channel=0&code=ZoomTele" in start and "arg2=8" in start
    assert "action=stop&channel=0&code=ZoomTele" in stop


def test_nvr_channel_names_are_parsed():
    from app.protocols.dahua import DahuaAPI
    from app.protocols.hikvision import HikvisionAPI
    hik = HikvisionAPI("h", 80, "u", "p")
    hik.session = FakeSession({"InputProxy": FakeResponse(text=(
        '<InputProxyChannelList xmlns="http://www.hikvision.com/ver20/XMLSchema">'
        "<InputProxyChannel><id>1</id><name>Gate</name></InputProxyChannel>"
        "<InputProxyChannel><id>3</id><name>Lab</name></InputProxyChannel>"
        "</InputProxyChannelList>"))})
    assert hik.get_channel_names() == {1: "Gate", 3: "Lab"}
    dah = DahuaAPI("d", 80, "u", "p")
    dah.session = FakeSession({"ChannelTitle": FakeResponse(text=(
        "table.ChannelTitle[0].Name=Front\r\ntable.ChannelTitle[1].Name=Back\r\n"))})
    assert dah.get_channel_names() == {1: "Front", 2: "Back"}


def test_wrong_password_is_reported():
    from app.protocols.hikvision import HikvisionAPI
    api = HikvisionAPI("h", 80, "u", "bad")
    api.session = FakeSession({"deviceInfo": FakeResponse(401)})
    with pytest.raises(PermissionError):
        api.get_device_info()


def test_ptz_controller_choice():
    from app.protocols.dahua import DahuaAPI
    from app.protocols.hikvision import HikvisionAPI
    from app.protocols.ptz import ONVIFPTZ, controller_for
    base = {"host": "h", "http_port": 80, "username": "u", "password": "p"}
    assert isinstance(controller_for({**base, "brand": "Hikvision"}), HikvisionAPI)
    assert isinstance(controller_for({**base, "brand": "CP Plus"}), DahuaAPI)
    assert isinstance(controller_for({**base, "brand": "UNV"}), ONVIFPTZ)


# ----------------------------------------------------------- live view
def _cams(n, group=lambda i: "A" if i % 2 else "B"):
    return [{"id": i, "name": f"Cam {i}", "group_name": group(i), "rtsp_url": "",
             "camera_type": "PTZ Camera" if i == 1 else "IP Camera"} for i in range(1, n + 1)]


def test_live_view_pages_groups_and_single_view(qtbot):
    from app.core.stream_manager import StreamManager
    from app.ui.pages.live_view import LiveView
    lv = LiveView(None, StreamManager())
    qtbot.addWidget(lv)
    lv.load_cameras(_cams(9))                 # 2x2 grid -> 3 pages
    assert lv._grid.visible_ids() == [1, 2, 3, 4] and lv._page_lbl.text() == "Page 1/3"
    lv._go_page(1)
    assert lv._grid.visible_ids() == [5, 6, 7, 8]
    lv._group_cb.setCurrentText("A")          # odd ids only
    assert lv._grid.visible_ids() == [1, 3, 5, 7] and lv._page == 0
    lv._toggle_single(3)
    assert lv._grid.visible_ids() == [3] and lv._page_lbl.text() == "Single view"
    lv._toggle_single(3)
    assert len(lv._grid.visible_ids()) == 4


def test_ptz_panel_only_for_ptz_cameras(qtbot):
    from app.core.stream_manager import StreamManager
    from app.ui.pages.live_view import LiveView
    lv = LiveView(None, StreamManager())
    qtbot.addWidget(lv)
    lv.load_cameras(_cams(2))
    lv._select(1)
    assert lv.ptz._ptz_buttons[0].isEnabled() and lv.ptz._btn_rec.isEnabled()
    lv._select(2)
    assert not lv.ptz._ptz_buttons[0].isEnabled() and lv.ptz._btn_rec.isEnabled()


# ------------------------------------------------------------- playback
@pytest.fixture
def sample_video(tmp_path):
    path = str(tmp_path / "clip.mp4")
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
    for i in range(50):                               # 5 seconds
        f = np.zeros((120, 160, 3), np.uint8)
        cv2.putText(f, str(i), (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 2)
        w.write(f)
    w.release()
    return path


def test_player_open_seek_and_play(qtbot, sample_video):
    from app.ui.widgets.video_player import VideoPlayer
    p = VideoPlayer()
    qtbot.addWidget(p)
    start = datetime(2026, 1, 1, 9, 0, 0)
    assert p.open(sample_video, start, offset_s=2.0, autoplay=False)
    assert 19 <= p._pos <= 22 and "00:02" in p._time.text() and "09:00:02" in p._time.text()
    p.play()
    qtbot.waitUntil(lambda: not p.is_playing(), timeout=8000)   # plays to the end
    assert p._pos >= 48
    p._jump(-10)
    assert p._pos <= 1


def test_timeline_click_maps_to_time(qtbot):
    from PyQt6.QtCore import QPoint, Qt
    from app.ui.widgets.video_player import DayTimeline
    tl = DayTimeline()
    qtbot.addWidget(tl)
    tl.resize(496, 46)                       # 480 px of bar + 8 px margins
    day = datetime(2026, 1, 1).date()
    tl.set_data(day, [])
    got = []
    tl.time_clicked.connect(got.append)
    qtbot.mouseClick(tl, Qt.MouseButton.LeftButton, pos=QPoint(248, 10))   # middle
    assert abs((got[0] - datetime(2026, 1, 1, 12)).total_seconds()) < 200


def test_recordings_page_lists_and_plays(qtbot, db, sample_video):
    from app.models import Recording
    from app.ui.pages.recordings import RecordingsPage
    start = datetime.now().replace(microsecond=0) - timedelta(minutes=5)
    s = db.get_session()
    s.add(Recording(camera_id=1, path=sample_video, kind="motion", start_time=start,
                    end_time=start + timedelta(seconds=5), size_bytes=1000))
    s.commit()
    s.close()
    page = RecordingsPage(db)
    qtbot.addWidget(page)
    page.set_cameras([{"id": 1, "name": "Gate"}])
    assert page._table.rowCount() == 1 and page._table.item(0, 3).text() == "Motion"
    page._play_at(start + timedelta(seconds=3))
    assert page.player.path == sample_video and page.player._pos >= 25
    page.player.close_file()


# ------------------------------------------------------------------ NVR
def test_nvr_import_edit_and_delete(db):
    from app.core.camera_manager import CameraManager
    mgr = CameraManager(db)
    nvr = mgr.add_nvr({"name": "Block A", "brand": "Dahua", "host": "10.1.1.9",
                       "http_port": 80, "username": "admin", "password": "pw",
                       "total_channels": 4})
    assert mgr.import_nvr_channels(nvr.id, {1: "Gate", 3: "Lab"}) == 4
    assert mgr.import_nvr_channels(nvr.id) == 0                 # no duplicates
    cams = {c["channel"]: c for c in mgr.get_all_cameras()}
    assert cams[1]["name"] == "Block A - Gate" and cams[2]["name"] == "Block A - Channel 2"
    assert cams[3]["rtsp_url"] == "rtsp://admin:pw@10.1.1.9:554/cam/realmonitor?channel=3&subtype=0"
    assert cams[1]["group_name"] == "Block A"
    mgr.update_nvr(nvr.id, {"host": "10.1.1.10", "password": "new"})
    assert all("admin:new@10.1.1.10" in c["rtsp_url"] for c in mgr.get_all_cameras())
    assert mgr.get_all_nvrs()[0]["camera_count"] == 4
    mgr.delete_nvr(nvr.id, delete_cameras=False)
    assert len(mgr.get_all_cameras()) == 4 and all(c["nvr_id"] is None
                                                   for c in mgr.get_all_cameras())


def test_nvr_delete_with_cameras(db):
    from app.core.camera_manager import CameraManager
    mgr = CameraManager(db)
    nvr = mgr.add_nvr({"name": "B", "brand": "Hikvision", "host": "h", "total_channels": 2})
    mgr.import_nvr_channels(nvr.id)
    mgr.delete_nvr(nvr.id, delete_cameras=True)
    assert mgr.get_all_cameras() == [] and mgr.get_all_nvrs() == []


# ------------------------------------------------- VMS-style main view
def test_window_layouts_with_large_window():
    from app.ui.widgets.camera_grid import window_positions
    rows, cols, pos = window_positions(6)
    assert (rows, cols, len(pos)) == (3, 3, 6) and pos[0] == (0, 0, 2, 2)
    rows, cols, pos = window_positions(8)
    assert (rows, cols, len(pos)) == (4, 4, 8) and pos[0] == (0, 0, 3, 3)
    for n in (1, 4, 9, 16, 25, 36):
        r, c, p = window_positions(n)
        assert r * c == n and len(p) == n


def test_open_drag_swap_close_and_views(qtbot, tmp_path, monkeypatch):
    from app.config import Config
    from app.core.stream_manager import StreamManager
    from app.ui.pages.live_view import LiveView
    monkeypatch.setattr(Config, "SETTINGS_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(Config, "LIVE_VIEWS", {})
    lv = LiveView(None, StreamManager())
    qtbot.addWidget(lv)
    lv.load_cameras(_cams(9))
    assert lv.tree.tree.topLevelItemCount() == 2          # groups A and B
    lv.close_all_windows()
    assert lv._grid.visible_ids() == [] and lv._page_lbl.text() == "Custom view"
    lv.open_in_window(7)                                  # first empty window
    lv.open_in_window(3)
    assert lv._slots[:2] == [7, 3]
    lv._on_drop(3, 9, -1)                                 # dropped from the device tree
    lv._on_drop(0, 3, 1)                                  # window 2 dragged onto window 1
    assert lv._slots == [3, 7, None, 9]
    lv.close_window(7)
    assert lv._slots == [3, None, None, 9]
    lv.save_view("Gate")
    lv._go_page(1)                                        # back to camera pages
    assert lv._grid.visible_ids() == [1, 2, 3, 4]
    assert lv.load_view("Gate") and lv._grid.visible_ids() == [3, 9]
    assert "Gate" in Config.LIVE_VIEWS
    lv.open_many([1, 2, 3, 4, 5])                         # a whole recorder
    assert lv._grid.page_size == 9 and lv._grid.visible_ids() == [1, 2, 3, 4, 5]
    lv.stop_all_streams()


def test_auto_switch_cycles_pages(qtbot):
    from app.core.stream_manager import StreamManager
    from app.ui.pages.live_view import LiveView
    lv = LiveView(None, StreamManager())
    qtbot.addWidget(lv)
    lv.load_cameras(_cams(6))
    lv._btn_tour.setChecked(True)
    lv._tour_step()
    assert lv._grid.visible_ids() == [5, 6]
    lv._tour_step()
    assert lv._grid.visible_ids() == [1, 2, 3, 4]
    lv._btn_tour.setChecked(False)


def test_digital_zoom_and_event_list(qtbot):
    from datetime import datetime
    from PyQt6.QtGui import QImage
    from app.core.stream_manager import StreamManager
    from app.ui.pages.live_view import LiveView
    lv = LiveView(None, StreamManager())
    qtbot.addWidget(lv)
    lv.load_cameras(_cams(2))
    cell = lv._grid.get_cell(1)
    img = QImage(640, 360, QImage.Format.Format_RGB888)
    img.fill(0x336699)
    cell.update_frame(1, img)
    cell._b_zoom.setChecked(True)
    cell._toggle_zoom()
    cell.zoom_at(3)
    assert cell.zoom > 1.9
    cell.zoom_at(-10)
    assert cell.zoom == 1.0
    got = []
    lv.playback_requested.connect(lambda cid, when: got.append((cid, when)))
    lv.add_event({"camera_id": 2, "type": "Safety", "description": "Fall", "severity": "high",
                  "time": datetime(2026, 1, 1, 9, 0)})
    lv._on_event_double(lv._event_list.item(0))
    assert got == [(2, datetime(2026, 1, 1, 9, 0))]
    cell.playback_requested.emit(1)
    assert got[-1][0] == 1
