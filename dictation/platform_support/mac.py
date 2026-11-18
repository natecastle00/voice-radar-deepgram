"""macOS: atalho global (pynput), digitação ao vivo (Quartz), app em foco (AppKit) e colar (Cmd+V).

Permissões necessárias (Ajustes do Sistema > Privacidade e Segurança), para o app de Terminal
ou Python que roda o ditado: Microfone, Acessibilidade e Monitoramento de Entrada.
"""
import logging
import subprocess
import threading
import time

from pynput import keyboard as pk

logger = logging.getLogger("DeepgramDictation")

HOTKEY_LABEL = "Ctrl+Option"

_CTRL = {pk.Key.ctrl, pk.Key.ctrl_l, pk.Key.ctrl_r}
_ALT = {k for k in (getattr(pk.Key, n, None) for n in ("alt", "alt_l", "alt_r", "alt_gr")) if k is not None}


def setup_env():
    pass


def open_file(path):
    subprocess.Popen(["open", path])


def paste():
    """Cola com Cmd+V (o clipboard já foi preenchido pelo app)."""
    time.sleep(0.05)
    c = pk.Controller()
    with c.pressed(pk.Key.cmd):
        c.press("v")
        c.release("v")


def foreground_info():
    """(nome do app, título da janela). O título só aparece com a permissão de Gravação de Tela."""
    try:
        from AppKit import NSWorkspace
        from Quartz import CGWindowListCopyWindowInfo, kCGNullWindowID, kCGWindowListOptionOnScreenOnly

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        name = str(app.localizedName() or "")
        pid = app.processIdentifier()
        title = ""
        for w in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []:
            if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer", 1) == 0:
                title = str(w.get("kCGWindowName") or "")
                if title:
                    break
        return name, title
    except Exception:
        return "", ""


class Typer:
    """Digita texto Unicode no app em foco, com flags de modificador zeradas
    (assim Ctrl/Option ainda pressionados não viram atalhos)."""

    def __init__(self, held_fn=None):
        self.lock = threading.Lock()

    def type_text(self, text):
        if not text:
            return
        from Quartz import (CGEventCreateKeyboardEvent, CGEventKeyboardSetUnicodeString, CGEventPost,
                            CGEventSetFlags, kCGHIDEventTap)
        with self.lock:
            for i in range(0, len(text), 16):
                chunk = text[i:i + 16]
                n = len(chunk.encode("utf-16-le")) // 2
                for down in (True, False):
                    ev = CGEventCreateKeyboardEvent(None, 0, down)
                    CGEventSetFlags(ev, 0)
                    CGEventKeyboardSetUnicodeString(ev, n, chunk)
                    CGEventPost(kCGHIDEventTap, ev)
                time.sleep(0.002)


class Hotkeys:
    """Ctrl+Option (segurar ou tocar) e uma tecla secundária (padrão F8)."""

    def __init__(self, on_press_both, on_release_both, on_f8, secondary="f8"):
        self.on_press_both = on_press_both
        self.on_release_both = on_release_both
        self.on_f8 = on_f8
        self.ctrl_down = False
        self.alt_down = False
        self.stop_event = threading.Event()
        self.listener = None
        self.secondary_key = self._resolve(secondary)

    @staticmethod
    def _resolve(name):
        name = (name or "").strip().lower()
        if not name:
            return None
        key = getattr(pk.Key, name, None)
        if key is not None:
            return key
        return pk.KeyCode.from_char(name) if len(name) == 1 else None

    def held_modifiers(self):
        return []  # o Typer do macOS zera as flags, não precisa soltar nada

    def start(self):
        if not getattr(pk.Listener, "IS_TRUSTED", True):
            logger.warning("Sem permissão de Acessibilidade/Monitoramento de Entrada: "
                           "o atalho não vai funcionar até liberar nos Ajustes do Sistema.")
        self.listener = pk.Listener(on_press=self._on_press, on_release=self._on_release)
        self.listener.daemon = True
        self.listener.start()
        threading.Thread(target=self._watch_stop, daemon=True).start()
        logger.info("Atalho global macOS (Ctrl+Option & %s) ativo.", self.secondary_key)

    def _watch_stop(self):
        self.stop_event.wait()
        if self.listener:
            self.listener.stop()

    def _fire(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def _on_press(self, key):
        prev = self.ctrl_down and self.alt_down
        if key in _CTRL:
            self.ctrl_down = True
        elif key in _ALT:
            self.alt_down = True
        elif self.secondary_key is not None and key == self.secondary_key:
            self._fire(self.on_f8)
            return
        if not prev and self.ctrl_down and self.alt_down:
            self._fire(self.on_press_both)

    def _on_release(self, key):
        prev = self.ctrl_down and self.alt_down
        if key in _CTRL:
            self.ctrl_down = False
        elif key in _ALT:
            self.alt_down = False
        if prev and not (self.ctrl_down and self.alt_down):
            self._fire(self.on_release_both)
