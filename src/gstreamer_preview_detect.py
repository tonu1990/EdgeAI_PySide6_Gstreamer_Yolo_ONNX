import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

import threading
import time


class GStreamerPreviewDetect:
    def __init__(self,
                 camera_device: str = "/dev/video0",
                 mjpeg_width: int = 640,
                 mjpeg_height: int = 480,
                 mjpeg_fps_num: int = 30,
                 detect_width: int = 416,
                 detect_height: int = 416):
        # Init GStreamer once
        Gst.init(None)

        # config
        self.camera_device = camera_device
        self.mjpeg_width = mjpeg_width
        self.mjpeg_height = mjpeg_height
        self.mjpeg_fps_num = mjpeg_fps_num
        self.detect_width = detect_width
        self.detect_height = detect_height

        # runtime
        self.pipeline = None
        self.main_loop = None
        self.bus = None

        # named elements we’ll cache (used later when we add detection)
        self.preview_sink = None
        self.detect_sink = None
        self.overlay = None
        self.detect_valve = None
        self.apps_valve = None
        self.appsink = None

        # threads
        self._glib_thread = None
        self._running = False

    # ----------------- PIPELINE -----------------

    def build_pipeline(self):
        """
        Build the 3-branch pipeline.
        Valves are OFF initially so only the preview branch runs work.
        """
        pipeline_str = (
            # Camera → MJPEG caps → decoder → RGB → tee
            f"v4l2src device={self.camera_device} ! "
            f"image/jpeg,width={self.mjpeg_width},height={self.mjpeg_height},framerate={self.mjpeg_fps_num}/1 ! "
            "jpegdec ! "
            "videoconvert ! "
            "tee name=t "

            # A) PREVIEW (always on when PLAYING)
            "t. ! queue leaky=downstream max-size-buffers=1 ! "
            "videoconvert ! "
            "ximagesink name=preview_sink sync=false "

            # B) DETECTION DISPLAY (OFF at start)
            "t. ! valve name=detect_valve drop=false ! "
            "queue leaky=downstream max-size-buffers=1 ! "
            "videoconvert ! "
            "cairooverlay name=overlay ! "
            "xvimagesink name=detect_sink sync=false "

            # C) APPSINK / INFERENCE (OFF at start)
            "t. ! valve name=apps_valve drop=false ! "
            "queue leaky=downstream max-size-buffers=1 ! "
            "videoconvert ! "
            "videoscale ! "
            f"video/x-raw,format=RGB,width={self.detect_width},height={self.detect_height} ! "
            "appsink name=det_sink emit-signals=True max-buffers=1 drop=True"
        )

        try:
            self.pipeline = Gst.parse_launch(pipeline_str)
            print("[PIPELINE]  Created")
        except Exception as e:
            raise RuntimeError(f"[PIPELINE] ERROR creating pipeline: {e}")

        # cache elements we may use later
        self.preview_sink = self.pipeline.get_by_name("preview_sink")
        self.detect_sink = self.pipeline.get_by_name("detect_sink")
        self.overlay = self.pipeline.get_by_name("overlay")
        self.detect_valve = self.pipeline.get_by_name("detect_valve")
        self.apps_valve = self.pipeline.get_by_name("apps_valve")
        self.appsink = self.pipeline.get_by_name("det_sink")

        # Bus watch
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message', self._on_bus_message)

        # If overlay exists, we can connect a dummy draw now (we’ll wire real drawing when detection is enabled)
        if self.overlay:
            self.overlay.connect('draw', self._on_draw_noop)

    # ----------------- START / STOP -----------------

    def start(self):
        """
        Start GLib loop in a small background thread and set the pipeline PLAYING.
        """
        if not self.pipeline:
            raise RuntimeError("Call build_pipeline() before start().")

        if self._running:
            print("[MAIN] Already running.")
            return

        # Create the GLib loop
        self.main_loop = GLib.MainLoop()
        self._running = True

        # Start GLib in a background thread (Qt stays responsive)
        self._glib_thread = threading.Thread(target=self._run_glib, daemon=True)
        self._glib_thread.start()

        # Set pipeline to PLAYING
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._running = False
            raise RuntimeError("[MAIN] ERROR: Could not start pipeline")

        print("[MAIN]  Preview started")

    def stop(self):
        """
        Stop gracefully: quit GLib loop, set pipeline to NULL, join thread.
        """
        if not self.pipeline:
            return

        print("[MAIN] Stopping preview...")
        try:
            if self.main_loop and self._running:
                # ask GLib loop to quit
                self.main_loop.quit()
        except Exception as e:
            print(f"[MAIN] Warning while quitting GLib loop: {e}")

        # Wait a moment for the loop to exit
        if self._glib_thread:
            self._glib_thread.join(timeout=2.0)

        # Stop the pipeline
        self.pipeline.set_state(Gst.State.NULL)
        print("[MAIN]  Pipeline stopped")

        # Reset flags
        self._running = False
        self._glib_thread = None
        self.main_loop = None

    # ----------------- INTERNAL -----------------

    def _run_glib(self):
        """Runs the GLib main loop; separate thread."""
        try:
            self.main_loop.run()
        except Exception as e:
            print(f"[GLIB] Loop error: {e}")

    def _on_bus_message(self, bus, message):
        """Basic bus handler for errors, warnings, state changes, EOS."""
        t = message.type

        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"[GSTREAMER ERROR] {err}\nDEBUG: {debug}")
            # Stop everything on error
            self.stop()

        elif t == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            print(f"[GSTREAMER WARNING] {warn}")

        elif t == Gst.MessageType.STATE_CHANGED:
            # Optional: log pipeline state transitions
            if message.src == self.pipeline:
                old, new, pending = message.parse_state_changed()
                print(f"[STATE] {old.value_nick} → {new.value_nick}")

        elif t == Gst.MessageType.EOS:
            print("[BUS] End of stream")
            self.stop()

        return True

    def _on_draw_noop(self, overlay, context, timestamp, duration):
        """Placeholder draw for detection window (does nothing in Phase 1)."""
        return
