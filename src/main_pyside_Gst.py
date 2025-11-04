#!/usr/bin/env python3
"""
PySide6 launcher with two buttons:

- Start/Stop Cam Preview: builds a FRESH pipeline and shows the preview window.
- Start/Stop Object Detection: toggles the detection DISPLAY branch (second window)
  and the appsink branch using valves. (No detection boxes yet.)

Beginner-friendly: small, linear handlers; lots of comments.
"""

import os
import sys
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QLabel
from PySide6.QtCore import Qt

from gstreamer_preview_detect import GStreamerPreviewDetect


def main():
    app = QApplication(sys.argv)

    # --- Window ---
    win = QWidget()
    win.setWindowTitle("EdgeAI Camera — Static Pipeline with Valves")
    win.resize(380, 180)

    # --- Buttons ---
    btn_preview = QPushButton("Start Cam Preview")
    btn_detect  = QPushButton("Start Object Detection")
    btn_detect.setEnabled(False)  # enabled after preview starts

    status = QLabel("Status: idle")
    status.setAlignment(Qt.AlignCenter)

    # --- GStreamer controller (created on first start) ---
    gst = {"app": None}

    # --- Helpers to read env with defaults ---
    def _conf():
        return dict(
            camera_device=os.environ.get("CAMERA_DEVICE", "/dev/video0"),
            mjpeg_width=int(os.environ.get("DISPLAY_WIDTH", "640")),
            mjpeg_height=int(os.environ.get("DISPLAY_HEIGHT", "480")),
            mjpeg_fps_num=int(os.environ.get("MJPEG_FPS", "30")),
            detect_width=int(os.environ.get("DETECT_WIDTH", "416")),
            detect_height=int(os.environ.get("DETECT_HEIGHT", "416")),
        )

    # --- Button handlers ---
    def on_toggle_preview():
        if btn_preview.text().startswith("Start"):
            # Start
            if gst["app"] is not None:
                # safety (shouldn't happen): stop the previous one
                try:
                    gst["app"].stop()
                except Exception:
                    pass
                gst["app"] = None

            cfg = _conf()
            gst["app"] = GStreamerPreviewDetect(**cfg)

            try:
                gst["app"].start()
                btn_preview.setText("Stop Cam Preview")
                btn_detect.setEnabled(True)   # detection toggle now allowed
                status.setText("Status: preview running")
            except Exception as e:
                status.setText(f"Status: failed to start preview — {e}")
                # ensure clean slate
                try:
                    gst["app"].stop()
                except Exception:
                    pass
                gst["app"] = None
        else:
            # Stop
            if gst["app"] is not None:
                try:
                    # Always disable detection before stopping (optional but nice)
                    gst["app"].set_detection_enabled(False)
                    gst["app"].stop()
                finally:
                    gst["app"] = None
            btn_preview.setText("Start Cam Preview")
            btn_detect.setText("Start Object Detection")
            btn_detect.setEnabled(False)
            status.setText("Status: idle")

    def on_toggle_detection():
        if gst["app"] is None:
            return

        if btn_detect.text().startswith("Start"):
            gst["app"].set_detection_enabled(True)
            btn_detect.setText("Stop Object Detection")
            status.setText("Status: preview + detection window")
        else:
            gst["app"].set_detection_enabled(False)
            btn_detect.setText("Start Object Detection")
            status.setText("Status: preview only")

    btn_preview.clicked.connect(on_toggle_preview)
    btn_detect.clicked.connect(on_toggle_detection)

    # --- Layout ---
    lay = QVBoxLayout()
    lay.addWidget(QLabel("Static pipeline with valves (rebuild per Start)"))
    lay.addWidget(btn_preview)
    lay.addWidget(btn_detect)
    lay.addWidget(status)
    win.setLayout(lay)

    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
