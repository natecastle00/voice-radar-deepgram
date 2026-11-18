#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.voiceradar.dictation.plist"
launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST" && echo "LaunchAgent removido."
