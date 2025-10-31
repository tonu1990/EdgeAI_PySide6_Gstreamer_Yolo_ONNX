#!/usr/bin/env python3
"""
Runner script:
- Loads YOLO NMS-fused ONNX detector
- Builds the GStreamer preview+detection class
- Binds detector and starts the app
"""

import os
from gstreamer_preview_detect import GStreamerPreviewDetect
from yolo_detector_optimized_phase1 import YOLODetectorNMS as YOLODetector

# ---- Config (env + sensible defaults) ----
MODEL_PATH = os.environ.get('MODEL_PATH', '/models/current.onnx')

CAMERA_DEVICE = os.environ.get('CAMERA_DEVICE', '/dev/video0')
DISPLAY_WIDTH = int(os.environ.get('DISPLAY_WIDTH', 640))
DISPLAY_HEIGHT = int(os.environ.get('DISPLAY_HEIGHT', 480))
DETECT_W = int(os.environ.get('DETECT_WIDTH', 416))
DETECT_H = int(os.environ.get('DETECT_HEIGHT', 416))
CONF_THRES = float(os.environ.get('CONF_THRESHOLD', 0.50))


def main():
    # Ensure the model exists
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(f"Model file not found: {MODEL_PATH}")

    # Build YOLO detector (Phase-1 ORT tuning is inside this class)
    detector = YOLODetector(
        model_path=MODEL_PATH,
        input_size=DETECT_W,
        conf_threshold=CONF_THRES
    )

    # Build GStreamer wrapper
    app = GStreamerPreviewDetect(
        camera_device=CAMERA_DEVICE,
        display_width=DISPLAY_WIDTH,
        display_height=DISPLAY_HEIGHT,
        detect_width=DETECT_W,
        detect_height=DETECT_H
    )

    # Pipeline → Detector → Start
    app.build_pipeline()
    app.bind_detector(detector)
    app.start()


if __name__ == "__main__":
    main()


