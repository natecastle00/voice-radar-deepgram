import os
import sys

import platform_support as plat
plat.setup_env()

import io
import time
import json
import wave
import logging
import threading
import datetime
import urllib.request
import urllib.error
import numpy as np
import sounddevice as sd
import pyperclip
import pystray
from PIL import Image, ImageDraw
import tkinter as tk
from live import LiveTranscriber
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "radar"))
try:
    import radar
except Exception:
    radar = None
from tkinter import ttk

# Paths & Logging
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "dictation.log")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.txt")

handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
if sys.stdout is not None:
    handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=handlers
)
logger = logging.getLogger("DeepgramDictation")

def load_config():
    default_config = {
        "hotkey": "ctrl+win",
        "secondary_hotkey": "f8",
        "model": "nova-3",
        "language": "pt-BR",
        "smart_format": True,
        "punctuate": True,
        "auto_paste": True,
        "copy_to_clipboard": True,
        "sound_feedback": True,
        "sound_volume": 0.35,
        "samplerate": 16000,
        "channels": 1,
        "min_duration_sec": 0.3,
        "max_duration_sec": 180.0,
        "hud_duration_sec": 4.5,
        "env_file_path": ""
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                default_config.update(loaded)
        except Exception as e:
            logger.error(f"Erro ao ler config.json: {e}")
    return default_config

config = load_config()

# Audio Synthesizer via sounddevice
AUDIO_SR = 44100
vol = float(config.get("sound_volume", 0.35))

def make_tone(freqs, durs, pause=0.015, volume=vol):
    chunks = []
    for f, d in zip(freqs, durs):
        t = np.linspace(0, d, int(AUDIO_SR * d), False)
        w = np.sin(2 * np.pi * f * t)
        fade_len = min(int(AUDIO_SR * 0.008), len(w) // 4)
        if fade_len > 0:
            fade = np.linspace(0, 1, fade_len)
            w[:fade_len] *= fade
            w[-fade_len:] *= fade[::-1]
        chunks.append(w * volume)
        if pause > 0:
            chunks.append(np.zeros(int(AUDIO_SR * pause)))
    return np.concatenate(chunks).astype(np.float32)

SOUND_START = make_tone([880, 1200], [0.06, 0.08], pause=0.015)
SOUND_STOP = make_tone([1200, 880], [0.05, 0.07], pause=0.015)
SOUND_SUCCESS = make_tone([1046, 1318, 1568], [0.05, 0.05, 0.09], pause=0.012)
SOUND_EMPTY = make_tone([440], [0.12], pause=0)
SOUND_ERROR = make_tone([350], [0.2], pause=0)

def play_sound(sound_type):
    if not config.get("sound_feedback", True):
        return
    def _run():
        try:
            tone_map = {
                "start": SOUND_START,
                "stop": SOUND_STOP,
                "success": SOUND_SUCCESS,
                "empty": SOUND_EMPTY,
                "error": SOUND_ERROR
            }
            tone = tone_map.get(sound_type)
            if tone is not None:
                sd.play(tone, AUDIO_SR)
        except Exception as e:
            logger.debug(f"Erro ao tocar som: {e}")
    threading.Thread(target=_run, daemon=True).start()

def get_deepgram_api_key():
    key = os.environ.get("DEEPGRAM_API_KEY")
    if key and len(key.strip()) > 10:
        return key.strip()

    # Opcional: config "env_file_path"; padrão: .env ao lado do dictation.py
    env_path = config.get("env_file_path") or os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DEEPGRAM_API_KEY="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val
        except Exception as e:
            logger.error(f"Erro ao ler .env: {e}")
    return None

_last_sync = [0.0]

def record_activity(text, app="", title=""):
    """Registra a fala no Radar e envia para a VPS em segundo plano."""
    if radar is None:
        return
    radar.log_event(text, app, title)
    if time.time() - _last_sync[0] < 20:
        return
    _last_sync[0] = time.time()
    threading.Thread(target=radar.sync, daemon=True).start()

def save_to_history(text):
    try:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{now_str}] {text}\n"
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        logger.error(f"Erro ao salvar histórico: {e}")

# Floating On-Screen HUD (Tkinter)
class DictationHUD:
    def __init__(self, root):
        self.root = root
        self.window = tk.Toplevel(root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg="#18181b")

        self.container = tk.Frame(
            self.window,
            bg="#18181b",
            highlightbackground="#3f3f46",
            highlightthickness=1
        )
        self.container.pack(fill="both", expand=True, padx=2, pady=2)

        self.title_label = tk.Label(
            self.container,
            text="",
            font=("Segoe UI", 10, "bold"),
            bg="#18181b",
            fg="#ef4444"
        )
        self.title_label.pack(anchor="w", padx=16, pady=(8, 2))

        self.body_label = tk.Label(
            self.container,
            text="",
            font=("Segoe UI", 11),
            bg="#18181b",
            fg="#f4f4f5",
            wraplength=480,
            justify="left"
        )
        self.body_label.pack(anchor="w", padx=16, pady=(0, 8))

        self.window.withdraw()
        self.hide_timer = None

    def _reposition(self):
        self.window.update_idletasks()
        w = max(440, self.window.winfo_reqwidth())
        h = max(60, self.window.winfo_reqheight())
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = (screen_w - w) // 2
        y = screen_h - 135
        self.window.geometry(f"{w}x{h}+{x}+{y}")

    def show_recording(self, mode="hold"):
        if self.hide_timer:
            self.root.after_cancel(self.hide_timer)
            self.hide_timer = None
        self.title_label.config(text="🔴 GRAVANDO...", fg="#ef4444")
        msg = f"Fale agora • Solte {plat.HOTKEY_LABEL} ou clique novamente para finalizar"
        self.body_label.config(text=msg)
        self._reposition()
        self.window.deiconify()

    def show_live(self, typed, interim):
        if self.hide_timer:
            self.root.after_cancel(self.hide_timer)
            self.hide_timer = None
        self.title_label.config(text="🔴 DIGITANDO AO VIVO...", fg="#ef4444")
        tail = typed[-160:]
        body = (tail + (" " if tail and interim else "") + (f"[{interim}]" if interim else "")).strip()
        self.body_label.config(text=body or f"Fale agora • Solte {plat.HOTKEY_LABEL} ou clique novamente para finalizar")
        self._reposition()
        self.window.deiconify()

    def show_transcribing(self):
        if self.hide_timer:
            self.root.after_cancel(self.hide_timer)
            self.hide_timer = None
        self.title_label.config(text="⏳ TRANSCREVENDO COM DEEPGRAM...", fg="#fbbf24")
        self.body_label.config(text="Processando fala com IA (Nova-3 pt-BR)...")
        self._reposition()
        self.window.deiconify()

    def show_result(self, text, title="✅ COPIADO E COLADO!"):
        if self.hide_timer:
            self.root.after_cancel(self.hide_timer)
            self.hide_timer = None
        self.title_label.config(text=title, fg="#4ade80")
        preview = (f'"{text}"') if len(text) < 220 else (f'"{text[:220]}..."')
        self.body_label.config(text=preview)
        self._reposition()
        self.window.deiconify()
        dur = int(float(config.get("hud_duration_sec", 4.5)) * 1000)
        self.hide_timer = self.root.after(dur, self.hide)

    def show_empty(self):
        if self.hide_timer:
            self.root.after_cancel(self.hide_timer)
            self.hide_timer = None
        self.title_label.config(text="⚠️ NENHUMA FALA DETECTADA", fg="#9ca3af")
        self.body_label.config(text="Áudio muito curto ou silencioso")
        self._reposition()
        self.window.deiconify()
        self.hide_timer = self.root.after(2500, self.hide)

    def show_error(self, err_msg):
        if self.hide_timer:
            self.root.after_cancel(self.hide_timer)
            self.hide_timer = None
        self.title_label.config(text="❌ ERRO NA TRANSCRIÇÃO", fg="#ef4444")
        self.body_label.config(text=str(err_msg)[:120])
        self._reposition()
        self.window.deiconify()
        self.hide_timer = self.root.after(4000, self.hide)

    def hide(self):
        self.window.withdraw()

# History Window (Tkinter)
class HistoryWindow:
    def __init__(self, root):
        self.root = root
        self.window = None

    def show(self):
        if self.window and tk.Toplevel.winfo_exists(self.window):
            self.window.lift()
            self.window.focus_force()
            return

        self.window = tk.Toplevel(self.root)
        self.window.title("Histórico de Transcrições - Deepgram Dictation")
        self.window.geometry("620x450")
        self.window.configure(bg="#18181b")

        header = tk.Label(
            self.window,
            text="📋 Histórico de Transcrições Recentes",
            font=("Segoe UI", 12, "bold"),
            bg="#18181b",
            fg="#f4f4f5"
        )
        header.pack(anchor="w", padx=16, pady=12)

        # Text area with scrollbar
        frame = tk.Frame(self.window, bg="#18181b")
        frame.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")

        self.text_area = tk.Text(
            frame,
            bg="#27272a",
            fg="#f4f4f5",
            insertbackground="#ffffff",
            font=("Consolas", 10),
            yscrollcommand=scrollbar.set,
            wrap="word",
            padx=10,
            pady=10
        )
        self.text_area.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.text_area.yview)

        # Load file contents
        content = "Nenhum histórico registrado ainda."
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    if lines:
                        content = "".join(lines)
            except Exception as e:
                content = f"Erro ao ler histórico: {e}"

        self.text_area.insert("1.0", content)
        self.text_area.see(tk.END)

        # Buttons
        btn_frame = tk.Frame(self.window, bg="#18181b")
        btn_frame.pack(fill="x", padx=16, pady=10)

        copy_btn = tk.Button(
            btn_frame,
            text="Copiar Última Transcrição",
            font=("Segoe UI", 9, "bold"),
            bg="#3b82f6",
            fg="#ffffff",
            relief="flat",
            padx=12,
            pady=6,
            command=self.copy_last
        )
        copy_btn.pack(side="left")

        clear_btn = tk.Button(
            btn_frame,
            text="Limpar Histórico",
            font=("Segoe UI", 9),
            bg="#3f3f46",
            fg="#ffffff",
            relief="flat",
            padx=12,
            pady=6,
            command=self.clear_history
        )
        clear_btn.pack(side="left", padx=10)

    def copy_last(self):
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    lines = [l.strip() for l in f if l.strip()]
                    if lines:
                        last = lines[-1]
                        if "]" in last:
                            last = last.split("]", 1)[1].strip()
                        pyperclip.copy(last)
            except Exception:
                pass

    def clear_history(self):
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                f.write("")
            self.text_area.delete("1.0", tk.END)
            self.text_area.insert("1.0", "Histórico limpo.")
        except Exception:
            pass

def create_tray_image(state="idle"):
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color_map = {
        "idle": (34, 197, 94, 255),       # Green
        "recording": (239, 68, 68, 255),   # Red
        "processing": (234, 179, 8, 255),  # Yellow
        "error": (156, 163, 175, 255)      # Gray
    }
    fill_color = color_map.get(state, (34, 197, 94, 255))
    draw.ellipse((4, 4, size - 4, size - 4), outline=(255, 255, 255, 220), width=3)
    draw.ellipse((10, 10, size - 10, size - 10), fill=fill_color)
    draw.rounded_rectangle((26, 18, 38, 36), radius=5, fill=(255, 255, 255, 255))
    draw.arc((22, 24, 42, 42), start=0, end=180, fill=(255, 255, 255, 255), width=2)
    draw.line((32, 42, 32, 48), fill=(255, 255, 255, 255), width=2)
    draw.line((26, 48, 38, 48), fill=(255, 255, 255, 255), width=2)
    return image

# Core Controller
class DeepgramDictationApp:
    def __init__(self, tk_root):
        self.root = tk_root
        self.hud = DictationHUD(self.root)
        self.history_win = HistoryWindow(self.root)
        self.api_key = get_deepgram_api_key()
        if not self.api_key:
            logger.warning("Credencial Deepgram não encontrada no .env!")
        else:
            logger.info("Credencial Deepgram carregada com sucesso.")

        self.samplerate = int(config.get("samplerate", 16000))
        self.channels = int(config.get("channels", 1))
        self.is_recording = False
        self.frames = []
        self.stream = None
        self.lock = threading.Lock()
        
        # State machine tracking
        self.both_down_start = 0
        self.action_in_cycle = None
        self.last_f8_time = 0
        self.tray_icon = None
        self.watchdog_timer = None
        self.hook = None
        self.live = None
        self.live_typed = []
        self.live_error = False
        self.typer = plat.Typer(lambda: self.hook.held_modifiers() if self.hook else [])

    def audio_callback(self, indata, frames_count, time_info, status):
        if self.is_recording:
            self.frames.append(indata.copy())
            live = self.live
            if live is not None:
                live.feed(indata.tobytes())

    def _live_interim(self, text):
        typed = " ".join(self.live_typed)
        self.root.after(0, lambda: self.hud.show_live(typed, text))

    def _live_final(self, text):
        prefix = " " if self.live_typed else ""
        self.live_typed.append(text)
        self.typer.type_text(prefix + text)
        typed = " ".join(self.live_typed)
        self.root.after(0, lambda: self.hud.show_live(typed, ""))

    def _live_error(self, e):
        self.live_error = True

    def _start_live(self):
        self.live = None
        self.live_typed = []
        self.live_error = False
        if not config.get("live_typing", True):
            return
        if not self.api_key:
            self.api_key = get_deepgram_api_key()
        if not self.api_key:
            return
        try:
            lt = LiveTranscriber(self.api_key, config, self._live_interim, self._live_final, self._live_error)
            lt.start()
            self.live = lt
            logger.info("Streaming ao vivo conectado (%s, %s).", config.get("model", "nova-3"), config.get("language", "pt-BR"))
        except Exception as e:
            logger.error(f"Streaming indisponível, usando modo em lote: {e}")
            self.live = None

    def update_tray_state(self, state, tooltip=None):
        if not self.tray_icon:
            return
        try:
            self.tray_icon.icon = create_tray_image(state)
            if tooltip:
                self.tray_icon.title = tooltip
        except Exception:
            pass

    def on_press_both(self):
        """Disparado quando as duas teclas do atalho estão pressionadas."""
        now = time.time()
        self.both_down_start = now
        with self.lock:
            if not self.is_recording:
                # Inicia gravação
                self.action_cycle = "started"
                self._start_recording_internal()
            else:
                # Já estava gravando em modo toggle: para agora
                self.action_cycle = "stopped"
                self._stop_recording_internal()

    def on_release_both(self):
        """Disparado quando Ctrl ou Win é solto."""
        now = time.time()
        hold_duration = now - self.both_down_start

        with self.lock:
            if self.is_recording and getattr(self, "action_cycle", None) == "started":
                if hold_duration >= 0.35:
                    # MODO SEGURAR (Push-to-Talk): o usuário segurou enquanto falava e agora soltou!
                    logger.info(f"Modo Segurar detectado ({hold_duration:.2f}s). Finalizando gravação...")
                    self._stop_recording_internal()
                else:
                    # MODO CLIQUE / 2X (Toggle): clique rápido! Mantém gravando mãos livres!
                    logger.info(f"Modo Clique rápido detectado ({hold_duration:.2f}s). Mantendo gravação ativa...")
                    self.root.after(0, lambda: self.hud.show_recording(mode="toggle"))

    def on_f8_toggle(self):
        now = time.time()
        if now - self.last_f8_time < 0.35:
            return
        self.last_f8_time = now
        self.toggle_recording()

    def toggle_recording(self):
        with self.lock:
            if not self.is_recording:
                self.action_cycle = "started"
                self._start_recording_internal()
            else:
                self.action_cycle = "stopped"
                self._stop_recording_internal()

    def _start_recording_internal(self):
        self.frames = []
        self.is_recording = True
        self.start_time = time.time()
        self.fg_app, self.fg_title = plat.foreground_info()
        play_sound("start")
        logger.info(f"🎙️ Gravação iniciada via {plat.HOTKEY_LABEL}...")

        self.root.after(0, self.hud.show_recording)
        self.update_tray_state("recording", f"Deepgram: Gravando áudio... ({plat.HOTKEY_LABEL})")
        self._start_live()

        try:
            self.stream = sd.InputStream(
                samplerate=self.samplerate,
                channels=self.channels,
                dtype='int16',
                callback=self.audio_callback
            )
            self.stream.start()
        except Exception as e:
            logger.error(f"Erro ao abrir microfone: {e}")
            self.is_recording = False
            if self.live:
                self.live.stop(0.5)
                self.live = None
            play_sound("error")
            self.root.after(0, lambda: self.hud.show_error(f"Erro no microfone: {e}"))
            self.update_tray_state("error", f"Erro no microfone: {e}")
            return

        max_dur = float(config.get("max_duration_sec", 180.0))
        if self.watchdog_timer:
            self.watchdog_timer.cancel()
        self.watchdog_timer = threading.Timer(max_dur, self.on_watchdog_timeout)
        self.watchdog_timer.daemon = True
        self.watchdog_timer.start()

    def on_watchdog_timeout(self):
        logger.warning("Tempo limite de gravação atingido.")
        with self.lock:
            if self.is_recording:
                self._stop_recording_internal()

    def _stop_recording_internal(self):
        if not self.is_recording:
            return
        self.is_recording = False
        if self.watchdog_timer:
            self.watchdog_timer.cancel()
            self.watchdog_timer = None

        play_sound("stop")
        live = self.live
        self.live = None
        if live is None:
            self.root.after(0, self.hud.show_transcribing)
        self.update_tray_state("processing", "Deepgram: Transcrevendo...")

        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.error(f"Erro ao fechar stream: {e}")
            self.stream = None

        audio_data = None
        if self.frames:
            audio_data = np.concatenate(self.frames, axis=0)
        self.frames = []

        duration = time.time() - self.start_time
        logger.info(f"Gravação parada. Duração: {duration:.2f}s")

        if duration < float(config.get("min_duration_sec", 0.3)) or audio_data is None:
            if live is not None:
                threading.Thread(target=live.stop, args=(1.0,), daemon=True).start()
            play_sound("empty")
            self.root.after(0, self.hud.show_empty)
            self.update_tray_state("idle", f"Deepgram Dictation ({plat.HOTKEY_LABEL}) - Pronto")
            return

        if live is not None:
            threading.Thread(target=self._finish_live, args=(live, audio_data), daemon=True).start()
            return

        threading.Thread(target=self.process_audio, args=(audio_data,), daemon=True).start()

    def _finish_live(self, live, audio_data):
        live.stop()
        text = " ".join(self.live_typed).strip()
        logger.info(f"Transcrição ao vivo: '{text}'")
        if not text:
            if self.live_error:
                logger.info("Streaming sem texto após erro; reprocessando em lote.")
                self.process_audio(audio_data)
                return
            play_sound("empty")
            self.root.after(0, self.hud.show_empty)
            self.update_tray_state("idle", f"Deepgram Dictation ({plat.HOTKEY_LABEL}) - Nenhuma fala")
            return
        play_sound("success")
        save_to_history(text)
        record_activity(text, getattr(self, "fg_app", ""), getattr(self, "fg_title", ""))
        if config.get("copy_to_clipboard", True):
            try:
                pyperclip.copy(text)
            except Exception:
                pass
        self.root.after(0, lambda: self.hud.show_result(text, "✅ DIGITADO (copiado também)"))
        preview = (text[:45] + "...") if len(text) > 45 else text
        self.update_tray_state("idle", f"Última: {preview}")

    def convert_to_wav(self, audio_np):
        bio = io.BytesIO()
        with wave.open(bio, 'wb') as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(2)
            wav.setframerate(self.samplerate)
            wav.writeframes(audio_np.tobytes())
        return bio.getvalue()

    def process_audio(self, audio_np):
        try:
            if not self.api_key:
                self.api_key = get_deepgram_api_key()
            if not self.api_key:
                logger.error("Credencial Deepgram indisponível.")
                play_sound("error")
                self.root.after(0, lambda: self.hud.show_error("Credencial Deepgram não encontrada no .env"))
                self.update_tray_state("error", "Erro: Credencial não encontrada")
                return

            wav_bytes = self.convert_to_wav(audio_np)
            model = config.get("model", "nova-3")
            lang = config.get("language", "pt-BR")
            smart = str(config.get("smart_format", True)).lower()
            punct = str(config.get("punctuate", True)).lower()

            url = f"https://api.deepgram.com/v1/listen?model={model}&language={lang}&smart_format={smart}&punctuate={punct}"
            req = urllib.request.Request(
                url,
                data=wav_bytes,
                headers={
                    "Authorization": f"Token {self.api_key}",
                    "Content-Type": "audio/wav"
                }
            )

            t0 = time.time()
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    resp_data = json.loads(resp.read().decode('utf-8'))
            except urllib.error.HTTPError as e:
                if model == "nova-3":
                    logger.warning(f"Falha com nova-3 ({e.code}), tentando nova-2...")
                    url2 = f"https://api.deepgram.com/v1/listen?model=nova-2&language={lang}&smart_format={smart}&punctuate={punct}"
                    req2 = urllib.request.Request(
                        url2,
                        data=wav_bytes,
                        headers={
                            "Authorization": f"Token {self.api_key}",
                            "Content-Type": "audio/wav"
                        }
                    )
                    with urllib.request.urlopen(req2, timeout=15) as resp2:
                        resp_data = json.loads(resp2.read().decode('utf-8'))
                else:
                    raise e

            elapsed = time.time() - t0
            transcript = ""
            channels = resp_data.get("results", {}).get("channels", [])
            if channels:
                alts = channels[0].get("alternatives", [])
                if alts:
                    transcript = alts[0].get("transcript", "").strip()

            logger.info(f"Transcrição ({elapsed:.2f}s): '{transcript}'")

            if not transcript:
                play_sound("empty")
                self.root.after(0, self.hud.show_empty)
                self.update_tray_state("idle", f"Deepgram Dictation ({plat.HOTKEY_LABEL}) - Nenhuma fala")
                return

            # Sucesso
            play_sound("success")
            save_to_history(transcript)
            record_activity(transcript, getattr(self, "fg_app", ""), getattr(self, "fg_title", ""))

            # Exibe no HUD visual na tela
            self.root.after(0, lambda: self.hud.show_result(transcript))

            # Copia para Área de Transferência
            if config.get("copy_to_clipboard", True):
                pyperclip.copy(transcript)

            # Cola no cursor ativo
            if config.get("auto_paste", True):
                time.sleep(0.12)
                plat.paste()

            preview = (transcript[:45] + "...") if len(transcript) > 45 else transcript
            self.update_tray_state("idle", f"Última: {preview}")

        except Exception as e:
            logger.error(f"Erro ao transcrever: {e}")
            play_sound("error")
            self.root.after(0, lambda: self.hud.show_error(str(e)))
            self.update_tray_state("error", f"Erro: {e}")
            time.sleep(2)
            self.update_tray_state("idle", f"Deepgram Dictation ({plat.HOTKEY_LABEL}) - Pronto")

    def open_history(self, icon=None, item=None):
        self.root.after(0, self.history_win.show)

    def open_config(self, icon=None, item=None):
        try:
            plat.open_file(CONFIG_PATH)
        except Exception as e:
            logger.error(f"Erro ao abrir config: {e}")

    def exit_app(self, icon=None, item=None):
        logger.info("Encerrando Deepgram Dictation...")
        if self.is_recording:
            self._stop_recording_internal()
        if self.hook:
            self.hook.stop_event.set()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.after(0, self.root.destroy)
        os._exit(0)

    def run(self):
        logger.info(f"Iniciando Deepgram Dictation com HUD e {plat.HOTKEY_LABEL}...")
        
        # Inicia Gancho de Baixo Nível
        self.hook = plat.Hotkeys(
            on_press_both=self.on_press_both,
            on_release_both=self.on_release_both,
            on_f8=self.on_f8_toggle,
            secondary=config.get("secondary_hotkey", "f8")
        )
        self.hook.start()

        # Configura Bandeja do Sistema (Ícones Ocultos)
        menu = pystray.Menu(
            pystray.MenuItem(f"🎙️ Alternar Gravação ({plat.HOTKEY_LABEL})", lambda icon, item: self.toggle_recording()),
            pystray.MenuItem("📋 Ver Histórico de Transcrições", self.open_history),
            pystray.MenuItem("⚙️ Configurações (config.json)", self.open_config),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ Sair", self.exit_app)
        )

        initial_icon = create_tray_image("idle")
        self.tray_icon = pystray.Icon(
            "DeepgramDictation",
            initial_icon,
            f"Deepgram Dictation ({plat.HOTKEY_LABEL}) - Pronto",
            menu
        )
        self.tray_icon.run_detached()

        logger.info("Serviço ativo na barra de tarefas (Ocultos). Atalho: " + plat.HOTKEY_LABEL + " (Segurar ou 2x/Clique) | F8")
        
        # Tkinter Event Loop
        self.root.mainloop()

if __name__ == "__main__":
    try:
        tk_root = tk.Tk()
        tk_root.withdraw() # Janela raiz oculta
        app = DeepgramDictationApp(tk_root)
        app.run()
    except Exception as e:
        logger.critical(f"Crash geral: {e}", exc_info=True)
