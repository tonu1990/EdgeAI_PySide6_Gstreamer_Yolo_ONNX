import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

import threading


class GStreamerPreviewDetect:
    def __init__(self,
                 camera_device: str = "/dev/video0",
                 mjpeg_width: int = 640,
                 mjpeg_height: int = 480,
                 mjpeg_fps_num: int = 30,
                 detect_width: int = 416,
                 detect_height: int = 416):
        # Init GStreamer once per process
        Gst.init(None)

        # --- config ---
        self.camera_device = camera_device
        self.mjpeg_width = mjpeg_width
        self.mjpeg_height = mjpeg_height
        self.mjpeg_fps_num = mjpeg_fps_num
        self.detect_width = detect_width
        self.detect_height = detect_height

        # --- runtime (set/cleared per Start/Stop) ---
        self.pipeline = None
        self.bus = None
        self.main_loop = None
        self._glib_thread = None
        self._running = False

        # named elements (available after build_pipeline)
        self.preview_sink = None
        self.detect_sink = None
        self.overlay = None
        self.appsink = None
        self.detect_valve = None
        self.apps_valve = None
        self.tee = None

        # tracking detection toggle
        self._detection_enabled = False

    # ---------------------------------------------------------------------
    # PIPELINE
    # ---------------------------------------------------------------------
    def _make_pipeline_str(self) -> str:
        """
        Build the validated 3-branch graph with valves after tee.

        Branch A: Preview (always flowing when pipeline PLAYING)
        Branch B: Detection window (valved OFF at start)
        Branch C: Appsink / inference (valved OFF at start)
        """
        return (
            # Camera → MJPEG caps → decode → convert → tee
            f"v4l2src device={self.camera_device} ! "
            f"image/jpeg,width={self.mjpeg_width},height={self.mjpeg_height},framerate={self.mjpeg_fps_num}/1 ! "
            "jpegdec ! "
            "videoconvert ! "
            "tee name=t "

            # A) PREVIEW (always on when PLAYING)
            "t. ! queue leaky=downstream max-size-buffers=1 ! "
            "videoconvert ! videoscale ! "
            "xvimagesink name=preview_sink sync=false async=false force-aspect-ratio=true "

            # B) DETECTION DISPLAY (OFF at start via valve)
            "t. ! valve name=detect_valve drop=true ! "
            "queue leaky=downstream max-size-buffers=1 ! "
            "videoconvert ! "
            "video/x-raw,format=BGRA ! "          # <-- pin cairo-friendly format
            "cairooverlay name=overlay ! "
            "videoconvert ! "
            "xvimagesink name=detect_sink sync=false async=false force-aspect-ratio=true "

            # C) APPSINK / INFERENCE (OFF at start via valve)
            "t. ! valve name=apps_valve drop=true ! "
            "queue leaky=downstream max-size-buffers=1 ! "
            "videoconvert ! videoscale ! "
            f"video/x-raw,format=RGB,width={self.detect_width},height={self.detect_height} ! "
            "appsink name=det_sink emit-signals=true max-buffers=1 drop=true"
        )

    def build_pipeline(self) -> None:
        """Build a FRESH pipeline + GLib loop. Called at the start of every run."""
        if self.pipeline:
            raise RuntimeError("Pipeline already exists. Call stop() before build_pipeline().")

        # Create pipeline from string
        self.pipeline = Gst.parse_launch(self._make_pipeline_str())
        print("[PIPELINE] Created")

        # Cache elements we need later
        self.preview_sink = self.pipeline.get_by_name("preview_sink")
        self.detect_sink = self.pipeline.get_by_name("detect_sink")
        self.overlay = self.pipeline.get_by_name("overlay")
        self.appsink = self.pipeline.get_by_name("det_sink")
        self.detect_valve = self.pipeline.get_by_name("detect_valve")
        self.apps_valve = self.pipeline.get_by_name("apps_valve")
        self.tee = self.pipeline.get_by_name("t")

        if not all([self.preview_sink, self.detect_valve, self.apps_valve, self.appsink, self.tee]):
            raise RuntimeError("[PIPELINE] Missing expected elements")

        # GLib main loop & bus watch
        self.main_loop = GLib.MainLoop()
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect("message", self._on_bus_message)

        # Overlay draw callback (no boxes yet; we just keep it hooked)
        if self.overlay:
            self.overlay.connect("draw", self._on_draw_noop)

        # detection starts off (both valves closed)
        self._detection_enabled = False

    # ---------------------------------------------------------------------
    # START / STOP
    # ---------------------------------------------------------------------
    def start(self) -> None:
        """
        Build fresh pipeline, start GLib loop thread, set pipeline to PLAYING,
        and block until the state settles.
        """
        # Always rebuild fresh to avoid Xv "stale window" behavior
        self.build_pipeline()

        # Spin GLib in background
        self._running = True
        self._glib_thread = threading.Thread(target=self._run_glib, daemon=True)
        self._glib_thread.start()

        # Kick the pipeline
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._running = False
            self.pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("[MAIN] ERROR: set_state(PLAYING) failed")

        # Wait for the state to settle (accept PLAYING; PAUSED may still render)
        change, state, pending = self.pipeline.get_state(Gst.CLOCK_TIME_NONE)
        if state == Gst.State.PLAYING:
            print("[MAIN] Preview started (PLAYING)")
        else:
            # With async=false on sinks, PLAYING is expected; but log truthfully if not.
            print(f"[MAIN] WARNING: Pipeline settled in {state.value_nick}, not PLAYING")

    def stop(self) -> None:
        """
        Clean shutdown: set pipeline to NULL, quit GLib loop, join thread (if safe),
        and clear all references so next Start is truly fresh.
        """
        if not self.pipeline:
            return

        print("[MAIN] Stopping preview...")

        # 1) stop elements quickly
        try:
            self.pipeline.set_state(Gst.State.NULL)
        except Exception as e:
            print(f"[MAIN] Warning: set_state(NULL) raised {e}")

        # 2) stop GLib loop
        try:
            if self.main_loop and self._running and self.main_loop.is_running():
                self.main_loop.quit()
        except Exception as e:
            print(f"[MAIN] Warning: main_loop.quit() raised {e}")

        # 3) join GLib thread if we're not on it
        if self._glib_thread and threading.current_thread() is not self._glib_thread:
            self._glib_thread.join(timeout=2.0)

        # 4) remove bus watch (guarded)
        try:
            if self.bus:
                self.bus.remove_signal_watch()
        except Exception:
            pass

        # 5) clear runtime
        self._running = False
        self._glib_thread = None
        self.main_loop = None
        self.bus = None

        # 6) clear element refs & pipeline
        self.preview_sink = None
        self.detect_sink = None
        self.overlay = None
        self.appsink = None
        self.detect_valve = None
        self.apps_valve = None
        self.tee = None
        self.pipeline = None

        print("[MAIN] Pipeline stopped")

    # ---------------------------------------------------------------------
    # DETECTION TOGGLE (valves)
    # ---------------------------------------------------------------------
    def set_detection_enabled(self, enabled: bool) -> None:
        """
        Turn the detection DISPLAY window on/off and (optionally) the appsink.
        - Display branch: detect_valve.drop = False (on) / True (off)
        - Apps branch:   apps_valve.drop   = False (on) / True (off)

        NOTE: Call from Qt thread is fine; we marshal to GLib with idle_add.
        """
        if not self.pipeline:
            return  # ignore if not running

        enabled = bool(enabled)
        if self._detection_enabled == enabled:
            return  # no change

        def _apply():
            try:
                if self.detect_valve:
                    self.detect_valve.set_property("drop", not enabled)
                if self.apps_valve:
                    # Turn apps branch ON only when enabling detection.
                    # (You can keep it ON while display OFF if you want warm-up.)
                    self.apps_valve.set_property("drop", not enabled)
            except Exception as e:
                print(f"[DETECTION] Valve toggle failed: {e}")
            return False  # remove idle source

        GLib.idle_add(_apply)
        self._detection_enabled = enabled

    # ---------------------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------------------
    def _run_glib(self):
        try:
            self.main_loop.run()
        except Exception as e:
            print(f"[GLIB] Loop error: {e}")

    def _on_bus_message(self, bus, message):
        t = message.type

        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"[GST ERROR] {err}\nDEBUG: {debug}")
            # Avoid joining from GLib thread: schedule stop on next idle
            GLib.idle_add(self.stop)

        elif t == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            print(f"[GST WARN]  {warn}\nDEBUG: {debug}")

        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == self.pipeline:
                old, new, pending = message.parse_state_changed()
                print(f"[STATE] {old.value_nick} → {new.value_nick}")

        elif t == Gst.MessageType.EOS:
            print("[BUS] End of stream")
            GLib.idle_add(self.stop)

        return True

    def _on_draw_noop(self, overlay, context, timestamp, duration):
        """
        Placeholder draw handler for detection window.
        (We’ll draw boxes after we wire the detector thread.)
        """
        return
