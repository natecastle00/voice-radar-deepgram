#!/bin/bash
# Agenda o radar para rodar todo dia às 23:50 no macOS (LaunchAgent).
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$(command -v python3)"
PLIST="$HOME/Library/LaunchAgents/com.voiceradar.nightly.plist"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.voiceradar.nightly</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/radar/radar.py</string><string>nightly</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>23</integer><key>Minute</key><integer>50</integer></dict>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string></dict>
</dict>
</plist>
PL
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Radar agendado para 23:50: $PLIST"
