"""Camada de plataforma: atalho global, digitação ao vivo, app em foco, colar e abrir arquivo."""
import sys

if sys.platform == "win32":
    from .win import HOTKEY_LABEL, Hotkeys, Typer, foreground_info, open_file, paste, setup_env
elif sys.platform == "darwin":
    from .mac import HOTKEY_LABEL, Hotkeys, Typer, foreground_info, open_file, paste, setup_env
else:
    raise RuntimeError("Voice Radar Deepgram suporta apenas Windows e macOS.")

__all__ = ["HOTKEY_LABEL", "Hotkeys", "Typer", "foreground_info", "open_file", "paste", "setup_env"]
