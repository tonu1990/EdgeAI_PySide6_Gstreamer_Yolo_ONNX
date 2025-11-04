#!/usr/bin/env python3
"""
PySide6 UI for the static tee + outputselector pipeline.

- Start/Stop Cam Preview → builds fresh pipeline and shows the preview window
- Start/Stop Object Detection → switches detection path (fakesink ↔ xvimagesink)
                               and opens/closes the apps branch valve

No detector integrated yet; this just proves reliable window behavior + toggling.
"""

import os
import sys
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QLabel
from PySide6.QtCore import Qt

from gstreamer_preview_detect import GStreamerPreviewDetect


def main():
    app = QApplication(sys.argv)

    win = QWidget()
    win.setWindowTitle("EdgeAI — Single Pipeline (tee + outputselector)")
    win.resize(420, 200)

    btn_preview = QPushButton("Start Cam Preview")
    btn_detect  = QPushButton("Start Object Detection")
    btn_detect.setEnabled(False)

    status = QLabel("Status: idle")
    status.setAlignment(Qt.AlignCenter)

    gst = {"app": None}

    def _conf():
        return dict(
            camera_device=os.environ.get("CAMERA_DEVICE", "/dev/video0"),
            mjpeg_width=int(os.environ.get("DISPLAY_WIDTH", "640")),
            mjpeg_height=int(os.environ.get("DISPLAY_HEIGHT", "480")),
            mjpeg_fps_num=int(os.environ.get("MJPEG_FPS", "30")),
            detect_width=int(os.environ.get("DETECT_WIDTH", "416")),
            detect_height=int(os.environ.get("DETECT_HEIGHT", "416")),
        )

    def on_toggle_preview():
        if btn_preview.text().startswith("Start"):
            # Start fresh
            if gst["app"] is not None:
                try:
                    gst["app"].stop()
                except Exception:
                    pass
                gst["app"] = None

            gst["app"] = GStreamerPreviewDetect(**_conf())

            try:
                gst["app"].start()
                btn_preview.setText("Stop Cam Preview")
                btn_detect.setEnabled(True)
                status.setText("Status: preview running")
            except Exception as e:
                status.setText(f"Status: failed to start — {e}")
                try:
                    gst["app"].stop()
                except Exception:
                    pass
                gst["app"] = None
        else:
            # Stop
            if gst["app"] is not None:
                try:
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

    lay = QVBoxLayout()
    lay.addWidget(QLabel("Static pipeline with tee + outputselector"))
    lay.addWidget(btn_preview)
    lay.addWidget(btn_detect)
    lay.addWidget(status)
    win.setLayout(lay)

    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
