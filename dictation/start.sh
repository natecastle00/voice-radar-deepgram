#!/bin/bash
# Inicia o ditado no macOS (terminal aberto). Para rodar em segundo plano use install_launchagent.sh.
cd "$(dirname "$0")" || exit 1
exec python3 dictation.py
