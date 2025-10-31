#!/usr/bin/env python3
"""
GStreamer + YOLO (NMS-fused) preview & detection — OOP wrapper.

This class:
- Builds and owns the GStreamer pipeline (tee → display + detection)
- Owns the GLib MainLoop and bus handler
- Runs a background detection thread that reads from appsink
- Exposes a simple API: build_pipeline() → bind_detector() → start()

Assumptions:
- Detection branch provides RGB frames that already match the model input size.
- Detector implements .detect(frame_rgb_uint8_hwc) → list of dicts for boxes.
"""

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

import numpy as np
import threading
import time


class GStreamerPreviewDetect:
    """Encapsulates the preview + detection pipeline and runtime."""

    def __init__(self,
                 camera_device: str = "/dev/video0",
                 display_width: int = 640,
                 display_height: int = 480,
                 detect_width: int = 416,
                 detect_height: int = 416):
        # Init GStreamer once per process
        Gst.init(None)

        # Config (owned by the instance)
        self.camera_device = camera_device
        self.display_width = display_width
        self.display_height = display_height
        self.detect_width = detect_width
        self.detect_height = detect_height

        # Runtime objects
        self.pipeline = None
        self.overlay = None
        self.appsink = None
        self.main_loop = GLib.MainLoop()
        self.bus = None

        # Detector (set later by bind_detector)
        self.detector = None

        # Shared state ("whiteboard") read by overlay; written by detect thread
        self.latest_detections = []

        # Threading & lifecycle
        self._running = True
        self._det_thread = None

    # ---------- Public API ----------

    def build_pipeline(self) -> None:
        """Create the pipeline, fetch elements, hook bus + overlay."""
        pipeline_str = (
            f"v4l2src device={self.camera_device} ! "
            "image/jpeg,width=640,height=480,framerate=30/1 ! "
            "jpegdec ! "
            "videoconvert ! "
            "tee name=t "
            # Display branch (leaky queue keeps latency bounded)
            "t. ! queue max-size-buffers=1 leaky=downstream ! "
            "videoconvert ! "
            "cairooverlay name=overlay ! "
            "videoconvert ! "
            "xvimagesink sync=false "
            # Detection branch → RGB WxH → appsink (drop frames, no backlog)
            "t. ! queue max-size-buffers=1 leaky=downstream ! "
            "videoconvert ! "
            "videoscale ! "
            f"video/x-raw,format=RGB,width={self.detect_width},height={self.detect_height} ! "
            "appsink name=sink emit-signals=True max-buffers=1 drop=True"
        )

        try:
            self.pipeline = Gst.parse_launch(pipeline_str)
            print("[PIPELINE] ✓ Created")
        except Exception as e:
            raise RuntimeError(f"[PIPELINE] ERROR creating pipeline: {e}")

        # Get important elements
        self.overlay = self.pipeline.get_by_name('overlay')
        self.appsink = self.pipeline.get_by_name('sink')
        if not self.overlay or not self.appsink:
            raise RuntimeError("[PIPELINE] ERROR: Could not get overlay/appsink")

        # Bus watch (attach before PLAYING so we catch early errors)
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message', self._on_bus_message)

        # Connect overlay draw callback
        self.overlay.connect('draw', self._on_draw)

    def bind_detector(self, detector) -> None:
        """Attach a detector that has a .detect(frame_rgb_uint8_hwc) method."""
        self.detector = detector

    def start(self) -> None:
        """Start detection thread, set pipeline PLAYING, and run the main loop."""
        if self.pipeline is None or self.overlay is None or self.appsink is None:
            raise RuntimeError("Call build_pipeline() before start().")
        if self.detector is None:
            raise RuntimeError("Call bind_detector(detector) before start().")

        # Spin up detection background worker
        self._det_thread = threading.Thread(target=self._detection_loop, daemon=True)
        self._det_thread.start()

        # Set PLAYING and enter the GLib loop
        print("[MAIN] Starting pipeline...")
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("[MAIN] ERROR: Could not start pipeline")

        try:
            self.main_loop.run()  # blocks until quit()
        except KeyboardInterrupt:
            print("\n[MAIN] Stopping (Ctrl+C)...")
            self._running = False

        # Cleanup
        self.pipeline.set_state(Gst.State.NULL)
        print("[MAIN] ✓ Pipeline stopped")

        if self._det_thread:
            self._det_thread.join(timeout=2)
            print("[MAIN] ✓ Detection thread stopped")

    def stop(self) -> None:
        """Request stop from outside (optional utility)."""
        self._running = False
        if self.main_loop.is_running():
            self.main_loop.quit()

    # ---------- Internal callbacks / threads ----------

    def _on_bus_message(self, bus, message):
        """GLib bus handler: errors, warnings, state changes, EOS."""
        t = message.type

        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"GSTREAMER ERROR: {err}\nDEBUG: {debug}")
            self._running = False
            if self.main_loop:
                self.main_loop.quit()

        elif t == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            print(f"WARNING: {warn}")

        elif t == Gst.MessageType.STATE_CHANGED:
            if isinstance(message.src, Gst.Pipeline):
                old, new, pending = message.parse_state_changed()
                print(f"[PIPELINE STATE] {old.value_nick} → {new.value_nick}")

        elif t == Gst.MessageType.EOS:
            print("[BUS] End of stream")
            self._running = False
            if self.main_loop:
                self.main_loop.quit()

        return True

    def _on_draw(self, overlay, context, timestamp, duration):
        """Draw boxes + labels each frame on the display branch."""
        detections = self.latest_detections
        if not detections:
            return

        for d in detections:
            x, y, w, h = d['x'], d['y'], d['w'], d['h']
            class_name, conf = d['class_name'], d['confidence']

            # Box
            context.set_line_width(2)
            context.rectangle(x, y, w, h)
            context.stroke()

            # Label background (simple opaque rectangle for readability)
            label = f"{class_name}: {conf:.2f}"
            label_h = 20
            label_w = len(label) * 8
            label_y = y - label_h if (y - label_h) >= 0 else y + label_h
            
            # Semi-transparent black background so video stays visible under text
            context.set_source_rgba(0, 0, 0, 0.6)
            context.rectangle(x, label_y, label_w, label_h)
            context.fill()

            # Label text
            context.set_source_rgb(1, 1, 1)
            context.select_font_face("Sans", 0, 1)
            context.set_font_size(12)
            context.move_to(x + 2, label_y + label_h - 5)
            context.show_text(label)

    def _detection_loop(self):
        """Read frames from appsink → detect → scale boxes → update whiteboard."""
        scale_x = self.display_width / self.detect_width
        scale_y = self.display_height / self.detect_height

        while self._running:
            try:
                sample = self.appsink.emit('pull-sample')
                if not sample:
                    time.sleep(0.01)
                    continue

                buffer = sample.get_buffer()
                ok, map_info = buffer.map(Gst.MapFlags.READ)
                if not ok:
                    continue

                # View into mapped memory (RGB, detect_width × detect_height)
                frame = np.ndarray(
                    shape=(self.detect_height, self.detect_width, 3),
                    dtype=np.uint8,
                    buffer=map_info.data
                )

                # Run detection BEFORE unmapping
                detections = self.detector.detect(frame)

                # Unmap after inference
                buffer.unmap(map_info)

                # Scale boxes from detect size → display size
                scaled = []
                for det in detections:
                    scaled.append({
                        'x': int(det['x'] * scale_x),
                        'y': int(det['y'] * scale_y),
                        'w': int(det['w'] * scale_x),
                        'h': int(det['h'] * scale_y),
                        'class_id': det['class_id'],
                        'class_name': det['class_name'],
                        'confidence': det['confidence']
                    })

                # Update shared whiteboard (atomic ref swap in CPython)
                self.latest_detections = scaled

            except Exception as e:
                print(f"[DETECTION] Error: {e}")
                time.sleep(0.1)

        print("[DETECTION] Thread exiting")
