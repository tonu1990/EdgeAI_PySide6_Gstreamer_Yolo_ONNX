# ======================================================
# Base image: Debian 12 (Bookworm) - good match for Pi OS
# ======================================================
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# ------------------------------------------------------
# System packages for:
# - Python + pip
# - GStreamer core + plugins (jpegdec/xvimagesink/etc.)
# - GI bindings (python3-gi) + cairo for overlay
# - Qt/X11 deps for PySide6 GUI
# - ONNX Runtime runtime deps (libstdc++, libgomp)
# - Basic fonts for cairo text rendering
# ------------------------------------------------------
RUN apt-get update && apt-get install -y \
    python3 python3-pip \
    # GStreamer core + plugins (jpegdec in -good, xvimagesink in -x)
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-x \
    # GI bindings for Python + introspection
    python3-gi python3-gi-cairo \
    gir1.2-gstreamer-1.0 \
    gir1.2-gst-plugins-base-1.0 \
    # Cairo for overlay
    libcairo2 \
    # X11 + Xv + GL/EGL + Qt deps for PySide6
    libx11-6 libx11-xcb1 libxext6 libxrender1 libsm6 libice6 \
    libxcb1 libxcb-cursor0 libxcb-glx0 libxcb-icccm4 libxcb-image0 \
    libxcb-keysyms1 libxcb-randr0 libxcb-render0 libxcb-render-util0 \
    libxcb-shape0 libxcb-shm0 libxcb-sync1 libxcb-xfixes0 libxcb-xinerama0 \
    libxcb-xkb1 libxkbcommon0 libxkbcommon-x11-0 \
    libegl1 libgl1 \
    libxv1 \
    # Fonts so cairooverlay text renders properly
    fonts-dejavu-core \
    # ONNX Runtime common deps
    libstdc++6 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------
# Workdir
# ------------------------------------------------------
WORKDIR /app

# ------------------------------------------------------
# Python deps (from requirements.txt)
# (PEP 668 on Debian - allow installing into system Python)
# ------------------------------------------------------
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt \
 || pip3 install --no-cache-dir -r requirements.txt

# ------------------------------------------------------
# App code
# ------------------------------------------------------
COPY src/ ./src/

# ------------------------------------------------------
# Env defaults (override at `docker run -e ...`)
# ------------------------------------------------------
ENV DISPLAY=:0 \
    QT_QPA_PLATFORM=xcb \
    QT_DEBUG_PLUGINS=0 \
    MODEL_PATH=/models/current.onnx \
    CAMERA_DEVICE=/dev/video0 \
    DISPLAY_WIDTH=640 \
    DISPLAY_HEIGHT=480 \
    MJPEG_FPS=30 \
    DETECT_WIDTH=416 \
    DETECT_HEIGHT=416

# ------------------------------------------------------
# Default entrypoint (your PySide6 + GStreamer app)
# ------------------------------------------------------
CMD ["python3", "src/main_pyside_Gst.py"]
