"""Tiny RTSP camera simulator for tests (needs GStreamer + python3-gi).

usage: python3 rtsp_test_server.py <port> <image.png>
Serves the image as a live 10 fps H.264 stream at rtsp://127.0.0.1:<port>/live
"""
import sys

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstRtspServer", "1.0")
from gi.repository import GLib, Gst, GstRtspServer  # noqa: E402


def main():
    port, image = sys.argv[1], sys.argv[2]
    Gst.init(None)
    server = GstRtspServer.RTSPServer()
    server.set_address("127.0.0.1")
    server.set_service(port)
    factory = GstRtspServer.RTSPMediaFactory()
    factory.set_launch(
        f"( filesrc location={image} ! pngdec ! imagefreeze ! videoconvert ! "
        "video/x-raw,format=I420,framerate=10/1 ! "
        "x264enc tune=zerolatency speed-preset=ultrafast key-int-max=10 ! "
        "rtph264pay name=pay0 pt=96 )")
    factory.set_shared(True)
    server.get_mount_points().add_factory("/live", factory)
    server.attach(None)
    print("READY", flush=True)
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
