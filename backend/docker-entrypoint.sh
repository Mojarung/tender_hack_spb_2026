#!/bin/bash
set -e

# Start Xvfb virtual display so headed Chrome can run inside the container.
# Without a real display, nodriver in headed mode (BROWSER_HEADLESS=false)
# crashes immediately — Xvfb provides a dummy X11 server at :99.
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
export DISPLAY=:99

exec uvicorn pricepulse.main:app --host 0.0.0.0 --port 8000
