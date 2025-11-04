#!/usr/bin/env python3
"""
Static GStreamer pipeline with:
- tee → Preview (always visible)
- tee → Detection path (BGRA → cairooverlay → videoconvert → outputselector → [fakesink | xvimagesink])
- tee → Appsink (RGB 416x416) behind a valve

We keep one pipeline the whole run. We *show/hide* the detection window by
switching outputselector's active-pad (fakesink ↔ xvimagesink). We *open/close*
the apps branch via a valve. No live unlink/relink.

Notes:
- We build a FRESH pipeline on each Start to avoid Xv "stale window" quirks.
- Sinks use sync=false *and* async=false to avoid preroll stalls.
- We pin BGRA before cairooverlay to avoid "not-negotiated".
- All element property changes are marshalled to the GLib thread via idle_add.
"""

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
        Gst.init(None)

        # Config
        self.camera_device = camera_device
        self.mjpeg_width = mjpeg_width
        self.mjpeg_height = mjpeg_height
        self.mjpeg_fps_num = mjpeg_fps_num
        self.detect_width = detect_width
        self.detect_height = detect_height

        # Runtime (reset on every Start/Stop)
        self.pipeline = None
        self.bus = None
        self.main_loop = None
        self._glib_thread = None
        self._running = False

        # Named elements
        self.preview_sink = None
        self.detect_sink = None
        self.detect_hidden = None  # fakesink
        self.overlay = None
        self.appsink = None
        self.apps_valve = None
        self.det_selector = None   # outputselector
        self.tee = None

        # Track UI state
        self._detection_enabled = False

    # ---------------------------------------------------------------------
    # Build the pipeline (fresh each Start)
    # ---------------------------------------------------------------------
    def _pipeline_str(self) -> str:
        return (
            # Camera → MJPEG caps → decode → convert → tee
            f"v4l2src device={self.camera_device} ! "
            f"image/jpeg,width={self.mjpeg_width},height={self.mjpeg_height},framerate={self.mjpeg_fps_num}/1 ! "
            "jpegdec ! videoconvert ! tee name=t "

            # A) PREVIEW (always visible)
            "t. ! queue leaky=downstream max-size-buffers=1 ! "
            "videoconvert ! videoscale ! "
            "xvimagesink name=preview_sink sync=false async=false force-aspect-ratio=true "

            # B) DETECTION DISPLAY PATH (BGRA → cairooverlay → outputselector)
            "t. ! queue leaky=downstream max-size-buffers=1 ! "
            "videoconvert ! video/x-raw,format=BGRA ! "
            "cairooverlay name=overlay ! "
            "videoconvert ! "
            "outputselector name=det_sel pad-negotiation-mode=none "
            # det_sel → hidden (default)
            "det_sel. ! queue ! fakesink name=detect_hidden sync=false "
            # det_sel → visible (we'll switch to this on demand)
            "det_sel. ! queue ! xvimagesink name=detect_sink sync=false async=false force-aspect-ratio=true "

            # C) APPS / INFERENCE (valved OFF at start)
            "t. ! queue leaky=downstream max-size-buffers=1 ! "
            "videoconvert ! videoscale ! "
            f"video/x-raw,format=RGB,width={self.detect_width},height={self.detect_height} ! "
            "valve name=apps_valve drop=true ! "
            "appsink name=det_sink emit-signals=true max-buffers=1 drop=true"
        )

    def build_pipeline(self) -> None:
        if self.pipeline is not None:
            raise RuntimeError("Pipeline already exists. Call stop() before build_pipeline().")

        self.pipeline = Gst.parse_launch(self._pipeline_str())
        print("[PIPELINE] Created")

        # Cache elements
        self.preview_sink  = self.pipeline.get_by_name("preview_sink")
        self.detect_sink   = self.pipeline.get_by_name("detect_sink")
        self.detect_hidden = self.pipeline.get_by_name("detect_hidden")
        self.overlay       = self.pipeline.get_by_name("overlay")
        self.appsink       = self.pipeline.get_by_name("det_sink")
        self.apps_valve    = self.pipeline.get_by_name("apps_valve")
        self.det_selector  = self.pipeline.get_by_name("det_sel")
        self.tee           = self.pipeline.get_by_name("t")

        if not all([self.preview_sink, self.detect_hidden, self.detect_sink,
                    self.appsink, self.apps_valve, self.det_selector, self.tee]):
            raise RuntimeError("[PIPELINE] Missing expected elements")

        # Bus + GLib loop
        self.main_loop = GLib.MainLoop()
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect("message", self._on_bus_message)

        # Overlay draw (noop for now)
        if self.overlay:
            self.overlay.connect("draw", self._on_draw_noop)

        # Default: detection UI hidden (selector to fakesink), apps OFF
        self._set_selector_target(hidden=True)
        self._detection_enabled = False
        if self.apps_valve:
            self.apps_valve.set_property("drop", True)

    # ---------------------------------------------------------------------
    # Start / Stop
    # ---------------------------------------------------------------------
    def start(self) -> None:
        """Fresh build, start GLib, set PLAYING, wait for settle."""
        self.build_pipeline()

        self._running = True
        self._glib_thread = threading.Thread(target=self._run_glib, daemon=True)
        self._glib_thread.start()

        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._running = False
            self.pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("[MAIN] set_state(PLAYING) failed")

        # Wait for settle; PLAYING preferred, PAUSED may still render
        _chg, state, _pend = self.pipeline.get_state(Gst.CLOCK_TIME_NONE)
        if state == Gst.State.PLAYING:
            print("[MAIN] Preview started (PLAYING)")
        else:
            print(f"[MAIN] WARNING: Pipeline settled in {state.value_nick}")

    def stop(self) -> None:
        """Clean shutdown and clear references so next Start is fresh."""
        if not self.pipeline:
            return

        print("[MAIN] Stopping preview...")
        try:
            self.pipeline.set_state(Gst.State.NULL)
        except Exception as e:
            print(f"[MAIN] Warning: set_state(NULL): {e}")

        try:
            if self.main_loop and self._running and self.main_loop.is_running():
                self.main_loop.quit()
        except Exception as e:
            print(f"[MAIN] Warning: main_loop.quit(): {e}")

        # Join GLib thread (but not from inside it)
        if self._glib_thread and threading.current_thread() is not self._glib_thread:
            self._glib_thread.join(timeout=2.0)

        try:
            if self.bus:
                self.bus.remove_signal_watch()
        except Exception:
            pass

        # Clear runtime
        self._running = False
        self._glib_thread = None
        self.main_loop = None
        self.bus = None

        # Clear element refs and pipeline
        self.preview_sink = None
        self.detect_sink = None
        self.detect_hidden = None
        self.overlay = None
        self.appsink = None
        self.apps_valve = None
        self.det_selector = None
        self.tee = None
        self.pipeline = None

        print("[MAIN] Pipeline stopped")

    # ---------------------------------------------------------------------
    # Detection show/hide + apps valve
    # ---------------------------------------------------------------------
    def set_detection_enabled(self, enabled: bool) -> None:
        """Show/hide detection window (selector) and open/close apps branch (valve)."""
        if not self.pipeline:
            return

        enabled = bool(enabled)
        if self._detection_enabled == enabled:
            return

        def _apply():
            # Switch selector target (fakesink ↔ xvimagesink)
            self._set_selector_target(hidden=not enabled)

            # Toggle apps branch
            if self.apps_valve:
                self.apps_valve.set_property("drop", not enabled)

            self._detection_enabled = enabled
            return False  # remove idle source

        GLib.idle_add(_apply)

    # Helper: set outputselector's active-pad to hidden or visible branch
    def _set_selector_target(self, hidden: bool) -> None:
        """
        Find which src pad of det_selector feeds the fakesink vs. detect xvimagesink,
        then set that pad as 'active-pad'.
        """
        if not self.det_selector:
            return

        # Starting from the sinks, walk upstream peers until we hit the selector.
        target_elem = self.detect_hidden if hidden else self.detect_sink
        pad = self._find_selector_src_pad_for_downstream(self.det_selector, target_elem)

        if pad is not None:
            try:
                self.det_selector.set_property("active-pad", pad)
            except Exception as e:
                print(f"[SELECTOR] Failed to set active-pad: {e}")

    def _find_selector_src_pad_for_downstream(self, selector_elem, downstream_elem):
        """
        Walk upstream from the given 'downstream_elem' (fakesink or xvimagesink),
        following pad peers through simple elements (queue/videoconvert/etc.)
        until we reach 'selector_elem'. Return the selector's src pad that feeds
        that downstream elem, or None if not found.
        """
        # Start: sink pad of the downstream element
        pad = downstream_elem.get_static_pad("sink")
        seen = set()

        while pad:
            peer = pad.get_peer()
            if not peer:
                return None
            up_elem = peer.get_parent_element()
            if not up_elem or up_elem in seen:
                return None
            seen.add(up_elem)

            if up_elem == selector_elem:
                # 'peer' is one of the selector's src pads
                return peer

            # Move further upstream: from the upstream element, take its sink pad
            pad = up_elem.get_static_pad("sink")

        return None

    # ---------------------------------------------------------------------
    # GLib + Bus + Overlay (noop)
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
            GLib.idle_add(self.stop)

        elif t == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            print(f"[GST WARN]  {warn}\nDEBUG: {debug}")

        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == self.pipeline:
                old, new, _ = message.parse_state_changed()
                print(f"[STATE] {old.value_nick} → {new.value_nick}")

        elif t == Gst.MessageType.EOS:
            print("[BUS] End of stream")
            GLib.idle_add(self.stop)

        return True

    def _on_draw_noop(self, overlay, context, timestamp, duration):
        # We’ll draw boxes here once the detector is wired
        return
