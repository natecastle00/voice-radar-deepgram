"""Streaming Deepgram (WebSocket) + digitação em tempo real via SendInput."""
import ctypes
import json
import logging
import queue
import threading
import time
import urllib.parse
from ctypes import wintypes

from websockets.sync.client import connect

logger = logging.getLogger("DeepgramDictation")

user32 = ctypes.windll.user32
MAGIC = 0x4D4F4F4E  # marca eventos injetados por nós (o gancho ignora)

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


def _key(vk=0, scan=0, flags=0):
    i = INPUT()
    i.type = 1
    i.u.ki = KEYBDINPUT(vk, scan, flags, 0, MAGIC)
    return i


def _send(events):
    if not events:
        return
    arr = (INPUT * len(events))(*events)
    user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


class Typer:
    """Digita texto Unicode no app em foco. Solta Ctrl/Win físicos durante a digitação."""

    def __init__(self, held_fn):
        # held_fn() -> lista de VKs de modificadores fisicamente pressionados (ex.: [0xA2, 0x5B])
        self.held_fn = held_fn
        self.lock = threading.Lock()

    def type_text(self, text):
        if not text:
            return
        units = text.encode("utf-16-le")
        codes = [int.from_bytes(units[i:i + 2], "little") for i in range(0, len(units), 2)]
        with self.lock:
            held = list(self.held_fn())
            _send([_key(vk=v, flags=KEYEVENTF_KEYUP) for v in held])
            ev = []
            for c in codes:
                ev.append(_key(scan=c, flags=KEYEVENTF_UNICODE))
                ev.append(_key(scan=c, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
            for k in range(0, len(ev), 200):
                _send(ev[k:k + 200])
            # restaura só o que ainda está fisicamente pressionado
            still = set(self.held_fn())
            _send([_key(vk=v) for v in held if v in still])


class LiveTranscriber:
    def __init__(self, api_key, cfg, on_interim, on_final, on_error):
        self.api_key = api_key
        self.cfg = cfg
        self.on_interim = on_interim
        self.on_final = on_final
        self.on_error = on_error
        self.q = queue.Queue()
        self.ws = None
        self.closed = False
        self.failed = False
        self.sender = None
        self.receiver = None

    def start(self):
        p = {
            "model": self.cfg.get("model", "nova-3"),
            "language": self.cfg.get("language", "pt-BR"),
            "encoding": "linear16",
            "sample_rate": int(self.cfg.get("samplerate", 16000)),
            "channels": int(self.cfg.get("channels", 1)),
            "interim_results": "true",
            "smart_format": str(self.cfg.get("smart_format", True)).lower(),
            "punctuate": str(self.cfg.get("punctuate", True)).lower(),
            "endpointing": int(self.cfg.get("endpointing_ms", 300)),
        }
        url = "wss://api.deepgram.com/v1/listen?" + urllib.parse.urlencode(p)
        self.ws = connect(url, additional_headers={"Authorization": f"Token {self.api_key}"},
                          open_timeout=5, max_size=None)
        self.sender = threading.Thread(target=self._send_loop, daemon=True)
        self.receiver = threading.Thread(target=self._recv_loop, daemon=True)
        self.sender.start()
        self.receiver.start()

    def feed(self, data):
        if not self.closed:
            self.q.put(data)

    def _send_loop(self):
        try:
            while True:
                data = self.q.get()
                if data is None:
                    self.ws.send(json.dumps({"type": "Finalize"}))
                    self.ws.send(json.dumps({"type": "CloseStream"}))
                    return
                self.ws.send(data)
        except Exception as e:
            self._fail(e)

    def _recv_loop(self):
        try:
            for msg in self.ws:
                if isinstance(msg, bytes):
                    continue
                d = json.loads(msg)
                if d.get("type") == "Results":
                    alts = d.get("channel", {}).get("alternatives", [])
                    text = alts[0].get("transcript", "").strip() if alts else ""
                    if d.get("is_final"):
                        if text:
                            self.on_final(text)
                    else:
                        self.on_interim(text)
        except Exception as e:
            if not self.closed:
                self._fail(e)

    def _fail(self, e):
        if self.failed:
            return
        self.failed = True
        logger.error(f"Streaming falhou: {e}")
        try:
            self.on_error(e)
        except Exception:
            pass

    def stop(self, timeout=4.0):
        """Fecha o stream e aguarda os resultados finais pendentes."""
        if self.closed:
            return
        self.closed = True
        self.q.put(None)
        if self.receiver:
            self.receiver.join(timeout)
        try:
            self.ws.close()
        except Exception:
            pass
