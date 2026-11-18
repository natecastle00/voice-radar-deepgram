#!/bin/bash
# Inicia o ditado automaticamente ao fazer login no macOS (LaunchAgent).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3)"
PLIST="$HOME/Library/LaunchAgents/com.voiceradar.dictation.plist"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.voiceradar.dictation</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/dictation.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/stdout.log</string>
  <key>StandardErrorPath</key><string>$DIR/stderr.log</string>
</dict>
</plist>
PL
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "LaunchAgent instalado: $PLIST"
echo "Libere Microfone, Acessibilidade e Monitoramento de Entrada para: $PY"
