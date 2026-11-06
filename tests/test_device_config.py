"""Remote configuration of recorders and cameras."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

NS = "http://www.hikvision.com/ver20/XMLSchema"
STREAM = f"""<?xml version="1.0" encoding="UTF-8"?>
<StreamingChannel version="2.0" xmlns="{NS}">
<id>102</id><channelName>IP Camera1</channelName><enabled>true</enabled>
<Video><enabled>true</enabled><videoInputChannelID>1</videoInputChannelID>
<videoCodecType>H.264</videoCodecType><videoResolutionWidth>640</videoResolutionWidth>
<videoResolutionHeight>360</videoResolutionHeight>
<videoQualityControlType>VBR</videoQualityControlType><fixedQuality>60</fixedQuality>
<vbrUpperCap>512</vbrUpperCap><maxFrameRate>2000</maxFrameRate><GovLength>200</GovLength>
<SmartCodec><enabled>true</enabled></SmartCodec></Video></StreamingChannel>"""
CAPS = f"""<?xml version="1.0" encoding="UTF-8"?>
<StreamingChannel xmlns="{NS}"><Video>
<videoCodecType opt="H.264,H.265">H.264</videoCodecType>
<videoResolutionWidth opt="640,704">640</videoResolutionWidth>
<videoResolutionHeight opt="360,576">360</videoResolutionHeight>
<videoQualityControlType opt="cbr,vbr">VBR</videoQualityControlType>
<maxFrameRate opt="2500,2000,1500,100">2000</maxFrameRate></Video></StreamingChannel>"""


class FakeHik(BaseHTTPRequestHandler):
    puts = []
    stream = STREAM

    def log_message(self, *a):
        pass

    def _send(self, code, body=""):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = self.path
        if p == "/ISAPI/ContentMgmt/StreamingProxy/channels/102":
            return self._send(200, FakeHik.stream)
        if p == "/ISAPI/ContentMgmt/StreamingProxy/channels/102/capabilities":
            return self._send(200, CAPS)
        if p == "/ISAPI/System/deviceInfo":
            return self._send(200, f'<DeviceInfo xmlns="{NS}"><deviceName>NVR</deviceName>'
                                   f'<model>DS-7732NI</model><serialNumber>S1</serialNumber>'
                                   f'<firmwareVersion>V4.1</firmwareVersion>'
                                   f'<deviceType>NVR</deviceType></DeviceInfo>')
        if p == "/ISAPI/System/time":
            return self._send(200, f'<Time xmlns="{NS}"><timeMode>NTP</timeMode><localTime>'
                                   f'2026-11-06T10:00:00+05:30</localTime>'
                                   f'<timeZone>CST-5:30:00</timeZone></Time>')
        if p == "/ISAPI/ContentMgmt/Storage":
            return self._send(200, f'<storage xmlns="{NS}"><hddList><hdd><id>1</id>'
                                   f'<hddName>hdd1</hddName><status>ok</status>'
                                   f'<capacity>3815447</capacity><freeSpace>1048576</freeSpace>'
                                   f'</hdd></hddList></storage>')
        if p == "/ISAPI/ContentMgmt/InputProxy/channels":
            return self._send(200, f'<InputProxyChannelList xmlns="{NS}">' + "".join(
                f'<InputProxyChannel><id>{i}</id><name>Cam {i}</name>'
                f'<sourceInputPortDescriptor><ipAddress>192.168.254.{i + 1}</ipAddress>'
                f'</sourceInputPortDescriptor></InputProxyChannel>' for i in (1, 2))
                + "</InputProxyChannelList>")
        if p == "/ISAPI/ContentMgmt/InputProxy/channels/status":
            return self._send(200, f'<InputProxyChannelStatusList xmlns="{NS}">'
                                   f'<InputProxyChannelStatus><id>1</id><online>true</online>'
                                   f'</InputProxyChannelStatus><InputProxyChannelStatus><id>2</id>'
                                   f'<online>false</online></InputProxyChannelStatus>'
                                   f'</InputProxyChannelStatusList>')
        return self._send(404, "")

    def do_PUT(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode()
        FakeHik.puts.append((self.path, body))
        if self.path == "/ISAPI/ContentMgmt/StreamingProxy/channels/102":
            FakeHik.stream = body
        return self._send(200, f'<ResponseStatus xmlns="{NS}"><statusCode>1</statusCode>'
                               f'<statusString>OK</statusString></ResponseStatus>')


@pytest.fixture
def hik():
    FakeHik.puts, FakeHik.stream = [], STREAM
    srv = HTTPServer(("127.0.0.1", 0), FakeHik)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"name": "NVR1", "brand": "Hikvision", "host": "127.0.0.1",
           "http_port": srv.server_address[1], "username": "admin", "password": "x"}
    srv.shutdown()


def test_hikvision_video_read_options_and_save(hik):
    from app.protocols.device_config import device_config, recommended
    cfg = device_config(hik)
    v = cfg.get_video(1, True)
    assert v == {"codec": "H.264", "width": 640, "height": 360, "bitrate_type": "VBR",
                 "bitrate": 512, "fps": 20.0, "gop": 200, "quality": "Medium",
                 "smart_codec": True}
    opts = cfg.video_options(1, True)
    assert opts["resolution"] == [(640, 360), (704, 576)] and opts["fps"][0] == 25.0
    assert opts["bitrate_type"] == ["CBR", "VBR"]
    rec = recommended(v)
    assert rec == {"codec": "H.264", "gop": 40, "smart_codec": False}
    cfg.set_video(1, True, {**rec, "fps": 15, "bitrate": 768})
    path, body = FakeHik.puts[-1]
    assert path == "/ISAPI/ContentMgmt/StreamingProxy/channels/102"
    assert f'xmlns="{NS}"' in body and "<GovLength>40</GovLength>" in body
    assert "<maxFrameRate>1500</maxFrameRate>" in body and "<vbrUpperCap>768</vbrUpperCap>" in body
    assert "<enabled>false</enabled></SmartCodec>" in body.replace("\n", "")
    assert cfg.get_video(1, True)["gop"] == 40                       # read back


def test_hikvision_device_storage_channels_time(hik):
    from app.protocols.device_config import device_config
    cfg = device_config(hik)
    assert cfg.device_info()["model"] == "DS-7732NI"
    disks = cfg.storage()
    assert round(disks[0]["capacity_gb"]) == 3726 and round(disks[0]["free_gb"]) == 1024
    chans = cfg.channels()
    assert [(c["id"], c["ip"], c["online"]) for c in chans] == [
        (1, "192.168.254.2", True), (2, "192.168.254.3", False)]
    from datetime import datetime
    cfg.sync_time(datetime(2026, 11, 6, 22, 5, 0))
    path, body = FakeHik.puts[-1]
    assert path == "/ISAPI/System/time" and "<timeMode>manual</timeMode>" in body
    assert "<localTime>2026-11-06T22:05:00</localTime>" in body
    assert "<timeZone>CST-5:30:00</timeZone>" in body                # kept


def test_config_dialog_recommended_and_save(qtbot, hik):
    from app.ui.pages.device_config_dialog import DeviceConfigDialog
    dlg = DeviceConfigDialog(hik, 1, is_nvr=True)
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg.btn_save.isEnabled(), timeout=10000)
    assert dlg.gop.value() == 200 and "10.0 s" in dlg.gop_lbl.text() and "⚠" in dlg.gop_lbl.text()
    assert dlg.smart.isChecked()
    dlg._apply_recommended()
    assert dlg.changes() == {"gop": 40, "smart_codec": False}
    dlg.save_video()
    qtbot.waitUntil(lambda: bool(FakeHik.puts), timeout=10000)
    assert "<GovLength>40</GovLength>" in FakeHik.stream
    dlg.tabs.setCurrentIndex(1)                                      # Cameras tab
    qtbot.waitUntil(lambda: dlg.chan_table.rowCount() == 2, timeout=10000)
    assert dlg.chan_table.item(1, 3).text() == "OFFLINE"
    dlg._open_channel(1, 0)
    assert dlg.channel.value() == 2 and dlg.tabs.currentIndex() == 0


def test_dahua_video_and_save(monkeypatch):
    from app.protocols import device_config as dc
    cfg = dc.device_config({"brand": "CP Plus", "host": "10.0.0.9", "http_port": 80})
    calls = []
    enc = ("table.Encode[1].ExtraFormat[0].Video.Compression=H.264\r\n"
           "table.Encode[1].ExtraFormat[0].Video.Width=704\r\n"
           "table.Encode[1].ExtraFormat[0].Video.Height=576\r\n"
           "table.Encode[1].ExtraFormat[0].Video.BitRateControl=VBR\r\n"
           "table.Encode[1].ExtraFormat[0].Video.BitRate=512\r\n"
           "table.Encode[1].ExtraFormat[0].Video.FPS=25\r\n"
           "table.Encode[1].ExtraFormat[0].Video.GOP=100\r\n"
           "table.Encode[1].ExtraFormat[0].Video.Quality=4\r\n")

    def fake_get(path, **params):
        calls.append((path, params))
        return enc if params.get("action") == "getConfig" else "OK"
    monkeypatch.setattr(cfg, "_get", fake_get)
    v = cfg.get_video(2, True)
    assert (v["width"], v["fps"], v["gop"], v["smart_codec"]) == (704, 25.0, 100, None)
    cfg.set_video(2, True, {"gop": 50})
    assert calls[-1][1] == {"action": "setConfig", "Encode[1].ExtraFormat[0].Video.GOP": "50"}
