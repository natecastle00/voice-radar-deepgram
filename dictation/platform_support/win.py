"""Windows: gancho de teclado Win32 (Ctrl+Win / F8), digitação via SendInput, app em foco."""
import ctypes
import logging
import os
import sys
import threading
import time
from ctypes import wintypes

logger = logging.getLogger("DeepgramDictation")

HOTKEY_LABEL = "Ctrl+Win"


def setup_env():
    """Garante System32 e a pasta do Python no PATH e nas DLLs."""
    sys_dir = os.environ.get("SystemRoot", r"C:\Windows") + r"\System32"
    py_dir = os.path.dirname(sys.executable)
    if "PATH" in os.environ:
        os.environ["PATH"] = f"{sys_dir};{py_dir};" + os.environ["PATH"]
    else:
        os.environ["PATH"] = f"{sys_dir};{py_dir}"
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(sys_dir)
            os.add_dll_directory(py_dir)
        except Exception:
            pass


def open_file(path):
    os.startfile(path)


def paste():
    """Solta modificadores físicos e cola com Ctrl+V."""
    import keyboard
    import pyautogui
    for k in ["ctrl", "alt", "shift", "windows"]:
        try:
            keyboard.release(k)
        except Exception:
            pass
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
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


# Win32 Constants

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_CONTROL = 0x11
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_F8 = 0x77


def foreground_info():
    try:
        hwnd = user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)
        app = ""
        if h:
            n = wintypes.DWORD(260)
            b2 = ctypes.create_unicode_buffer(260)
            if kernel32.QueryFullProcessImageNameW(h, 0, b2, ctypes.byref(n)):
                app = os.path.basename(b2.value)
            kernel32.CloseHandle(h)
        return app, buf.value
    except Exception:
        return "", ""


# Low-Level Kernel Keyboard Hook (Ctrl + Win & F8)
class Hotkeys:
    def __init__(self, on_press_both, on_release_both, on_f8, secondary=None):
        self.on_press_both = on_press_both
        self.on_release_both = on_release_both
        self.on_f8 = on_f8
        self.ctrl_down = False
        self.win_down = False
        self.hook = None
        self.thread = None
        self.stop_event = threading.Event()
        self._hook_proc_ref = None
        self.ctrl_vk = VK_LCONTROL
        self.win_vk = VK_LWIN

    def held_modifiers(self):
        out = []
        if self.ctrl_down:
            out.append(self.ctrl_vk)
        if self.win_down:
            out.append(self.win_vk)
        return out

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)
            ]

        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]

        def hook_proc(nCode, wParam, lParam):
            if nCode >= 0:
                kb = KBDLLHOOKSTRUCT.from_address(lParam)
                vk = kb.vkCode
                if kb.dwExtraInfo == MAGIC:
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)
                is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                is_up = wParam in (WM_KEYUP, WM_SYSKEYUP)

                prev_both = self.ctrl_down and self.win_down

                if vk in (VK_CONTROL, VK_LCONTROL, VK_RCONTROL):
                    self.ctrl_down = is_down
                    if is_down:
                        self.ctrl_vk = vk if vk != VK_CONTROL else VK_LCONTROL
                elif vk in (VK_LWIN, VK_RWIN):
                    self.win_down = is_down
                    if is_down:
                        self.win_vk = vk

                curr_both = self.ctrl_down and self.win_down

                # Transition to BOTH down
                if not prev_both and curr_both:
                    threading.Thread(target=self.on_press_both, daemon=True).start()
                    # Returning 0 ensures Windows knows Ctrl is down, so Start menu never opens!
                    return 0

                # Transition to RELEASE of either key
                elif prev_both and not curr_both:
                    threading.Thread(target=self.on_release_both, daemon=True).start()
                    return 0

                # F8 Toggle
                elif vk == VK_F8 and is_down:
                    threading.Thread(target=self.on_f8, daemon=True).start()
                    return 1

            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._hook_proc_ref = HOOKPROC(hook_proc)
        self.hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc_ref, None, 0)
        if not self.hook:
            logger.error(f"Falha ao instalar gancho Win32: {ctypes.GetLastError()}")
            return

        logger.info("Gancho Win32 (Ctrl + Win & F8) ativo.")
        msg = wintypes.MSG()
        while not self.stop_event.is_set():
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.01)

        user32.UnhookWindowsHookEx(self.hook)
        self.hook = None
