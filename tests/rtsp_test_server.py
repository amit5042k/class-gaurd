"""Tiny RTSP camera simulator for tests (needs GStreamer + python3-gi).

usage: python3 rtsp_test_server.py <port> <image.png> [h264|h265] [keyframe_interval] [udp-only]
Serves the image as a live 10 fps stream at rtsp://127.0.0.1:<port>/live.
A long keyframe interval mimics NVR "H.264+ / H.265+ / Smart Codec" streams.
"""
import sys

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstRtspServer", "1.0")
gi.require_version("GstRtsp", "1.0")
from gi.repository import GLib, Gst, GstRtsp, GstRtspServer  # noqa: E402


def main():
    port, image = sys.argv[1], sys.argv[2]
    codec = sys.argv[3] if len(sys.argv) > 3 else "h264"
    keyint = int(sys.argv[4]) if len(sys.argv) > 4 else 10
    Gst.init(None)
    server = GstRtspServer.RTSPServer()
    server.set_address("127.0.0.1")
    server.set_service(port)
    if codec == "h265":
        enc = (f"x265enc tune=zerolatency speed-preset=ultrafast key-int-max={keyint} ! "
               "rtph265pay name=pay0 pt=96 config-interval=-1")
    else:
        enc = (f"x264enc tune=zerolatency speed-preset=ultrafast key-int-max={keyint} ! "
               "rtph264pay name=pay0 pt=96 config-interval=-1")
    factory = GstRtspServer.RTSPMediaFactory()
    factory.set_launch(
        f"( filesrc location={image} ! pngdec ! imagefreeze is-live=true ! videoconvert ! "
        "videobalance ! video/x-raw,format=I420,framerate=10/1 ! "
        f"timeoverlay ! {enc} )")
    factory.set_shared(True)
    if len(sys.argv) > 5 and sys.argv[5] == "udp-only":
        # Like recorders that refuse RTSP over TCP.
        factory.set_protocols(GstRtsp.RTSPLowerTrans.UDP)
    server.get_mount_points().add_factory("/live", factory)
    server.attach(None)
    print("READY", flush=True)
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
