"""
GStreamer Controller - Single Pipeline with Dynamic Branch Control

This module manages a single GStreamer pipeline with multiple branches:
- Preview branch: Always active, shows clean camera feed
- Detection branch: Controlled by valve, shows feed with bounding boxes
- Inference branch: Controlled by valve, processes frames for AI detection

Author: Your Name
Date: 2025-11-04
"""

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

import threading
import time


class GStreamerController:
    """
    Manages a single GStreamer pipeline with switchable display modes.
    
    Modes:
    - PREVIEW: Shows only clean camera preview (one window)
    - DETECTION: Shows preview + detection window with boxes (two windows)
    """
    
    def __init__(self,
                 camera_device: str = "/dev/video0",
                 camera_width: int = 640,
                 camera_height: int = 480,
                 camera_fps: int = 30,
                 inference_width: int = 416,
                 inference_height: int = 416):
        """
        Initialize the controller.
        
        Args:
            camera_device: Path to camera device (e.g., /dev/video0)
            camera_width: Camera capture width
            camera_height: Camera capture height
            camera_fps: Camera frames per second
            inference_width: Width for AI inference input
            inference_height: Height for AI inference input
        """
        # Initialize GStreamer library (must be done once)
        Gst.init(None)
        
        # Store configuration
        self.camera_device = camera_device
        self.camera_width = camera_width
        self.camera_height = camera_height
        self.camera_fps = camera_fps
        self.inference_width = inference_width
        self.inference_height = inference_height
        
        # Pipeline components (will be set when pipeline is built)
        self.pipeline = None
        self.bus = None
        
        # Named elements we'll need to control
        self.preview_sink = None      # Preview window (always visible)
        self.detect_sink = None       # Detection window (toggled on/off)
        self.overlay = None           # Cairo overlay for drawing boxes
        self.detect_valve = None      # Controls detection display branch
        self.inference_valve = None   # Controls AI inference branch
        self.appsink = None          # Receives frames for AI processing
        
        # GLib main loop (processes GStreamer events in background)
        self.main_loop = None
        self.glib_thread = None
        self._running = False
        
        # Detection state
        self._detection_enabled = False
        self._detections = []  # Will store bounding boxes: [(x, y, w, h, label, confidence), ...]
        
    # ========================================
    # PUBLIC API - These are the main methods you'll call
    # ========================================
    
    def build_pipeline(self):
        """
        Build the complete GStreamer pipeline with all branches.
        
        Pipeline structure:
            camera → decoder → tee (split into 3 branches)
                              ├─> Preview window (always on)
                              ├─> Detection window (valve controlled)
                              └─> AI inference (valve controlled)
        
        This is called once at application startup.
        """
        print("[PIPELINE] Building pipeline...")
        
        # Build the pipeline string
        # Note: This is a bit long but it's clear what each part does
        pipeline_str = (
            # ========== CAMERA SOURCE ==========
            # Open camera and get MJPEG compressed stream
            f"v4l2src device={self.camera_device} ! "
            f"image/jpeg,width={self.camera_width},height={self.camera_height},"
            f"framerate={self.camera_fps}/1 ! "
            
            # Decode JPEG to raw video
            "jpegdec ! "
            
            # Convert to RGB format
            "videoconvert ! "
            
            # Split into multiple branches using 'tee'
            "tee name=t ! "
            
            # ========== BRANCH 1: PREVIEW WINDOW (Always Active) ==========
            # This branch always shows clean camera feed
            "t. ! queue max-size-buffers=1 leaky=downstream ! "
            "videoconvert ! "
            "xvimagesink name=preview_sink sync=false "
            
            # ========== BRANCH 2: DETECTION WINDOW (Valve Controlled) ==========
            # This branch shows camera feed with bounding boxes
            # Valve starts OPEN briefly to let cairooverlay initialize,
            # then closes automatically
            "t. ! valve name=detect_valve drop=false ! "  # Start OPEN
            "queue max-size-buffers=1 leaky=downstream ! "
            "videoconvert ! "
            "video/x-raw,format=BGRA ! "  # Cairo needs BGRA format
            "cairooverlay name=overlay ! "
            "videoconvert ! "
            "ximagesink name=detect_sink sync=false "  # Use ximagesink to avoid XVideo conflicts
            
            # ========== BRANCH 3: AI INFERENCE (Valve Controlled) ==========
            # This branch feeds frames to AI model
            "t. ! valve name=inference_valve drop=true ! "  # Start CLOSED
            "queue max-size-buffers=1 leaky=downstream ! "
            "videoconvert ! "
            "videoscale ! "  # Resize for AI model input
            f"video/x-raw,format=RGB,width={self.inference_width},height={self.inference_height} ! "
            "appsink name=inference_sink emit-signals=True max-buffers=1 drop=True"
        )
        
        try:
            # Parse the string and create the pipeline
            self.pipeline = Gst.parse_launch(pipeline_str)
            print("[PIPELINE] ✓ Pipeline created successfully")
        except Exception as e:
            raise RuntimeError(f"[PIPELINE] ✗ Failed to create pipeline: {e}")
        
        # Get references to elements we need to control
        self.preview_sink = self.pipeline.get_by_name("preview_sink")
        self.detect_sink = self.pipeline.get_by_name("detect_sink")
        self.overlay = self.pipeline.get_by_name("overlay")
        self.detect_valve = self.pipeline.get_by_name("detect_valve")
        self.inference_valve = self.pipeline.get_by_name("inference_valve")
        self.appsink = self.pipeline.get_by_name("inference_sink")
        
        # Set up message bus to receive pipeline events
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message', self._on_bus_message)
        
        # Connect the overlay draw callback
        if self.overlay:
            self.overlay.connect('draw', self._on_overlay_draw)
            print("[PIPELINE] ✓ Overlay callback connected")
    
    def start_preview(self):
        """
        Start the pipeline in PREVIEW mode.
        
        - Shows clean preview window
        - Detection window hidden (valve closed)
        - AI inference disabled (valve closed)
        
        This is called when user clicks "Start Preview" button.
        """
        if self._running:
            print("[PREVIEW] Already running")
            return
        
        print("[PREVIEW] Starting preview mode...")
        
        # Create GLib main loop (processes GStreamer events)
        self.main_loop = GLib.MainLoop()
        
        # Start GLib loop in background thread so Qt GUI stays responsive
        self._running = True
        self.glib_thread = threading.Thread(target=self._run_glib, daemon=True)
        self.glib_thread.start()
        
        # Start the pipeline
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._running = False
            raise RuntimeError("[PREVIEW] ✗ Failed to start pipeline")
        
        # Wait for pipeline to reach PLAYING state (max 5 seconds)
        state_change_ret, state, pending = self.pipeline.get_state(timeout=5 * Gst.SECOND)
        if state != Gst.State.PLAYING:
            self._running = False
            self.pipeline.set_state(Gst.State.NULL)
            raise RuntimeError(f"[PREVIEW] ✗ Pipeline stuck in {state.value_nick} state")
        
        print(f"[PREVIEW] ✓ Pipeline started (state: {state.value_nick})")
        
        # IMPORTANT: Close detection valve after cairooverlay has initialized
        # We started it open so cairooverlay could initialize properly,
        # now we close it so the detection window doesn't show
        GLib.timeout_add(500, self._close_detection_valve_initial)
    
    def stop_preview(self):
        """
        Stop the pipeline and clean up resources.
        
        This is called when user clicks "Stop Preview" or "Stop Detection".
        """
        if not self.pipeline or not self._running:
            print("[STOP] Pipeline not running")
            return
        
        print("[STOP] Stopping pipeline...")
        
        # Disable detection if it was enabled
        if self._detection_enabled:
            self._detection_enabled = False
        
        # Stop the pipeline
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline.get_state(timeout=2 * Gst.SECOND)
        
        # Stop GLib loop
        if self.main_loop and self._running:
            self.main_loop.quit()
        
        # Wait for GLib thread to finish
        if self.glib_thread and self.glib_thread.is_alive():
            self.glib_thread.join(timeout=2.0)
        
        print("[STOP] ✓ Pipeline stopped")
        
        # Reset state
        self._running = False
        self.glib_thread = None
        self.main_loop = None
    
    def start_detection(self):
        """
        Enable DETECTION mode.
        
        - Preview window stays visible (clean feed)
        - Detection window becomes visible (with bounding boxes)
        - AI inference starts processing frames
        
        This is called when user clicks "Start Detection" button.
        """
        if not self._running:
            print("[DETECTION] ✗ Start preview first!")
            return
        
        if self._detection_enabled:
            print("[DETECTION] Already enabled")
            return
        
        print("[DETECTION] Enabling detection mode...")
        
        # Open the detection display valve (shows detection window)
        if self.detect_valve:
            self.detect_valve.set_property('drop', False)
            print("[DETECTION] ✓ Detection window enabled")
        
        # Open the inference valve (starts AI processing)
        if self.inference_valve:
            self.inference_valve.set_property('drop', False)
            print("[DETECTION] ✓ Inference pipeline enabled")
        
        # TODO: In Phase 2, connect appsink signal to process frames
        # if self.appsink:
        #     self.appsink.connect('new-sample', self._on_new_frame)
        
        self._detection_enabled = True
        print("[DETECTION] ✓ Detection mode active")
    
    def stop_detection(self):
        """
        Disable DETECTION mode, return to PREVIEW mode.
        
        - Preview window stays visible
        - Detection window closes
        - AI inference stops
        
        This is called when user clicks "Stop Detection" button.
        """
        if not self._detection_enabled:
            print("[DETECTION] Not enabled")
            return
        
        print("[DETECTION] Disabling detection mode...")
        
        # Close the detection display valve (hides detection window)
        if self.detect_valve:
            self.detect_valve.set_property('drop', True)
            print("[DETECTION] ✓ Detection window disabled")
        
        # Close the inference valve (stops AI processing)
        if self.inference_valve:
            self.inference_valve.set_property('drop', True)
            print("[DETECTION] ✓ Inference pipeline disabled")
        
        # Clear detection results
        self._detections = []
        
        self._detection_enabled = False
        print("[DETECTION] ✓ Returned to preview mode")
    
    # ========================================
    # INTERNAL METHODS - These handle pipeline events
    # ========================================
    
    def _run_glib(self):
        """
        Run the GLib main loop in background thread.
        This processes all GStreamer events (state changes, errors, etc.)
        """
        try:
            self.main_loop.run()
        except Exception as e:
            print(f"[GLIB] ✗ Loop error: {e}")
    
    def _on_bus_message(self, bus, message):
        """
        Handle messages from the GStreamer pipeline.
        
        Messages include: errors, warnings, state changes, end-of-stream, etc.
        """
        msg_type = message.type
        
        if msg_type == Gst.MessageType.ERROR:
            # Pipeline error occurred
            err, debug = message.parse_error()
            print(f"[PIPELINE ERROR] {err}")
            print(f"[DEBUG] {debug}")
            self.stop_preview()
            
        elif msg_type == Gst.MessageType.WARNING:
            # Pipeline warning
            warn, debug = message.parse_warning()
            print(f"[PIPELINE WARNING] {warn}")
            
        elif msg_type == Gst.MessageType.STATE_CHANGED:
            # Pipeline state changed
            if message.src == self.pipeline:
                old, new, pending = message.parse_state_changed()
                print(f"[STATE] {old.value_nick} → {new.value_nick}")
                
        elif msg_type == Gst.MessageType.EOS:
            # End of stream
            print("[PIPELINE] End of stream")
            self.stop_preview()
        
        return True
    
    def _close_detection_valve_initial(self):
        """
        Close detection valve after initialization.
        
        This is called 500ms after pipeline starts to ensure
        cairooverlay has time to initialize properly.
        """
        if self.detect_valve and not self._detection_enabled:
            self.detect_valve.set_property('drop', True)
            print("[PIPELINE] ✓ Detection valve closed (preview mode)")
        return False  # Don't repeat this callback
    
    def _on_overlay_draw(self, overlay, context, timestamp, duration):
        """
        Draw bounding boxes on the detection window.
        
        This callback is called for every frame that goes through cairooverlay.
        
        Args:
            overlay: The cairooverlay element
            context: Cairo drawing context
            timestamp: Frame timestamp
            duration: Frame duration
        """
        # Only draw if detection is enabled
        if not self._detection_enabled:
            return
        
        # TODO: Phase 2 - Draw bounding boxes from self._detections
        # Example:
        # context.set_source_rgb(0, 1, 0)  # Green color
        # context.set_line_width(2)
        # for (x, y, w, h, label, conf) in self._detections:
        #     context.rectangle(x, y, w, h)
        #     context.stroke()
        #     context.move_to(x, y - 5)
        #     context.show_text(f"{label}: {conf:.2f}")
        
        pass
    
    def _on_new_frame(self, appsink):
        """
        Process frames from appsink for AI inference.
        
        This is called whenever a new frame is available for processing.
        
        Args:
            appsink: The appsink element
            
        Returns:
            Gst.FlowReturn.OK to continue receiving frames
        """
        # TODO: Phase 2 - Implement AI inference
        # 1. Pull sample from appsink
        # 2. Convert to numpy array
        # 3. Run through ONNX model
        # 4. Update self._detections with results
        # 5. Overlay will automatically draw on next frame
        
        return Gst.FlowReturn.OK