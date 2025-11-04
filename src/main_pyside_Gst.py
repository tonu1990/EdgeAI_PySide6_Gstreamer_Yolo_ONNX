import os
import sys
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

# Logging (optional but helpful)
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("ui")

from gstreamer_preview_detect import GStreamerPreviewDetect


def main():
    # ---- Qt app ----
    app = QApplication(sys.argv)

    # ---- Window ----
    window = QWidget()
    window.resize(360, 160)
    window.setWindowTitle("EdgeAI Camera (Phase 1: Preview)")

    # ---- Buttons ----
    btn_preview = QPushButton("Start Cam Preview")
    btn_detect = QPushButton("Start Object Detection")
    btn_detect.setEnabled(False)  # Phase 1: disabled

    # ---- Controller (GStreamer) ----
    camera_device = os.environ.get("CAMERA_DEVICE", "/dev/video0")
    display_w = int(os.environ.get("DISPLAY_WIDTH", "640"))
    display_h = int(os.environ.get("DISPLAY_HEIGHT", "480"))
    detect_w = int(os.environ.get("DETECT_WIDTH", "416"))
    detect_h = int(os.environ.get("DETECT_HEIGHT", "416"))
    fps_num = int(os.environ.get("MJPEG_FPS", "30"))

    gst = GStreamerPreviewDetect(
        camera_device=camera_device,
        mjpeg_width=display_w,
        mjpeg_height=display_h,
        mjpeg_fps_num=fps_num,
        detect_width=detect_w,
        detect_height=detect_h,
    )

    # Build the pipeline once; valves are OFF by default
    gst.build_pipeline()

    # ---- Button handlers ----
    def on_preview_clicked():
        if btn_preview.text() == "Start Cam Preview":
            try:
                gst.start()
                btn_preview.setText("Stop Cam Preview")
                btn_detect.setEnabled(True)  # we’ll wire this in Phase 2
                logger.info("Preview started")
            except Exception as e:
                logger.error(f"Failed to start preview: {e}")
        else:
            # Stop
            try:
                gst.stop()
            finally:
                btn_preview.setText("Start Cam Preview")
                btn_detect.setEnabled(False)
                btn_detect.setText("Start Object Detection")
                logger.info("Preview stopped")

    def on_detect_clicked():
        # Phase 1: not implemented yet
        logger.info("Detection not enabled in Phase 1")

    btn_preview.clicked.connect(on_preview_clicked)
    btn_detect.clicked.connect(on_detect_clicked)

    # ---- Layout ----
    layout = QVBoxLayout()
    layout.addWidget(QLabel("Phase 1: Preview only"))
    layout.addWidget(btn_preview)
    layout.addWidget(btn_detect)
    window.setLayout(layout)

    # ---- Show & run ----
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
