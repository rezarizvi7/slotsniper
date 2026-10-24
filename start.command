#!/bin/bash
# Double-click me to start slotsniper on a Mac (Linux: run `bash start.command`).
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo
  echo "  Python isn't installed yet."
  echo "  Download it from https://www.python.org/downloads/ , install it,"
  echo "  then double-click start.command again."
  echo
  open "https://www.python.org/downloads/" 2>/dev/null
  read -r -p "Press Enter to close..."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "First run: setting things up. This takes a few minutes..."
  python3 -m venv .venv || { echo "Could not create the Python environment."; read -r; exit 1; }
fi
source .venv/bin/activate
python -m pip install --quiet --disable-pip-version-check -r requirements.txt \
  || { echo "Installing libraries failed. Check your internet connection."; read -r; exit 1; }
# Macs usually don't have Edge, so download Playwright's own Chromium (only the first time).
python -m playwright install chromium

[ -f config.yaml ] || cp config.example.yaml config.yaml
[ -f .env ] || cp .env.example .env

# The example config watches the built-in demo laundry site, so start it too.
if grep -q "localhost:8765" config.yaml; then
  python mock_portal/server.py >/dev/null 2>&1 &
  DEMO_PID=$!
  trap 'kill $DEMO_PID 2>/dev/null' EXIT
fi

echo
echo "  slotsniper is starting. Your browser will open the dashboard."
echo "  Keep this window open while it runs. Close it to stop."
echo
python -m slotsniper dashboard
