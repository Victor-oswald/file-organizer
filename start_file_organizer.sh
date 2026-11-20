#!/bin/sh
# Start File Organizer. Installs missing components on first run.
cd "$(dirname "$0")" || exit 1
if ! python3 -c "import PIL, pystray, pypdf" 2>/dev/null; then
  echo "Installing the components File Organizer needs (first run only)..."
  python3 -m pip install --user -r requirements.txt 2>/dev/null || python3 -m pip install --user --break-system-packages -r requirements.txt
fi
nohup python3 -m organizer ui >/dev/null 2>&1 &
