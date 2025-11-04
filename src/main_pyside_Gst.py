import os
import sys
from PySide6.QtWidgets import (QApplication, QLabel, QPushButton, 
                                QVBoxLayout, QWidget, QMessageBox)

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("ui")

from gstreamer_controller import GStreamerController


def main():
    """Main application entry point"""
    
    # ========== Create Qt Application ==========
    app = QApplication(sys.argv)
    
    # ========== Create Main Window ==========
    window = QWidget()
    window.resize(400, 200)
    window.setWindowTitle("EdgeAI Camera - Single Pipeline Demo")
    
    # ========== Create UI Elements ==========
    title_label = QLabel("Camera Control Panel")
    title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
    
    status_label = QLabel("Status: Ready")
    status_label.setStyleSheet("color: gray;")
    
    btn_preview = QPushButton("Start Preview")
    btn_detect = QPushButton("Start Detection")
    btn_detect.setEnabled(False)  # Disabled until preview starts
    
    # ========== Load Configuration ==========
    camera_device = os.environ.get("CAMERA_DEVICE", "/dev/video0")
    camera_width = int(os.environ.get("DISPLAY_WIDTH", "640"))
    camera_height = int(os.environ.get("DISPLAY_HEIGHT", "480"))
    camera_fps = int(os.environ.get("MJPEG_FPS", "30"))
    inference_width = int(os.environ.get("DETECT_WIDTH", "416"))
    inference_height = int(os.environ.get("DETECT_HEIGHT", "416"))
    
    # ========== Create GStreamer Controller ==========
    controller = GStreamerController(
        camera_device=camera_device,
        camera_width=camera_width,
        camera_height=camera_height,
        camera_fps=camera_fps,
        inference_width=inference_width,
        inference_height=inference_height
    )
    
    # Build the pipeline once at startup
    try:
        controller.build_pipeline()
        logger.info("Pipeline built successfully")
    except Exception as e:
        logger.error(f"Failed to build pipeline: {e}")
        QMessageBox.critical(window, "Error", f"Failed to build pipeline:\n{e}")
        sys.exit(1)
    
    # ========== Button Click Handlers ==========
    
    def on_preview_clicked():
        """Handle Preview button click"""
        if btn_preview.text() == "Start Preview":
            # Start preview mode
            try:
                controller.start_preview()
                btn_preview.setText("Stop Preview")
                btn_detect.setEnabled(True)
                btn_detect.setText("Start Detection")
                status_label.setText("Status: Preview Mode (1 window)")
                status_label.setStyleSheet("color: green;")
                logger.info("Preview started")
            except Exception as e:
                logger.error(f"Failed to start preview: {e}")
                QMessageBox.critical(window, "Error", f"Failed to start preview:\n{e}")
                status_label.setText("Status: Error")
                status_label.setStyleSheet("color: red;")
        else:
            # Stop preview/detection
            try:
                controller.stop_preview()
                btn_preview.setText("Start Preview")
                btn_detect.setEnabled(False)
                btn_detect.setText("Start Detection")
                status_label.setText("Status: Stopped")
                status_label.setStyleSheet("color: gray;")
                logger.info("Preview stopped")
            except Exception as e:
                logger.error(f"Failed to stop preview: {e}")
    
    def on_detect_clicked():
        """Handle Detection button click"""
        if btn_detect.text() == "Start Detection":
            # Enable detection mode
            try:
                controller.start_detection()
                btn_detect.setText("Stop Detection")
                status_label.setText("Status: Detection Mode (2 windows)")
                status_label.setStyleSheet("color: blue;")
                logger.info("Detection started")
            except Exception as e:
                logger.error(f"Failed to start detection: {e}")
                QMessageBox.critical(window, "Error", f"Failed to start detection:\n{e}")
        else:
            # Disable detection mode (return to preview)
            try:
                controller.stop_detection()
                btn_detect.setText("Start Detection")
                status_label.setText("Status: Preview Mode (1 window)")
                status_label.setStyleSheet("color: green;")
                logger.info("Detection stopped")
            except Exception as e:
                logger.error(f"Failed to stop detection: {e}")
    
    # Connect button signals
    btn_preview.clicked.connect(on_preview_clicked)
    btn_detect.clicked.connect(on_detect_clicked)
    
    # ========== Layout ==========
    layout = QVBoxLayout()
    layout.addWidget(title_label)
    layout.addWidget(status_label)
    layout.addWidget(btn_preview)
    layout.addWidget(btn_detect)
    layout.addStretch()
    
    window.setLayout(layout)
    
    # ========== Show Window and Run ==========
    window.show()
    logger.info("Application started")
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()