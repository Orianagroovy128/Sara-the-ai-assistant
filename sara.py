# ==============================================================================
# S.A.R.A  v5  —  JARVIS-class HUD
# ==============================================================================

import sys, os, time, json, sqlite3, threading, queue, subprocess, re, math, shutil
import logging, datetime, wave, webbrowser, urllib.parse

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(threadName)s: %(message)s',
    handlers=[logging.FileHandler("sara_system.log"), logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("SARA")

try:
    import torch, whisper, pyaudio, numpy as np, pyttsx3, psutil, pyautogui, requests
    from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                                 QHBoxLayout, QLabel, QListWidget, QListWidgetItem)
    from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QPoint, QRect, QRectF
    from PyQt5.QtGui import (QPainter, QColor, QPen, QFont, QBrush,
                             QLinearGradient, QRadialGradient, QConicalGradient,
                             QPainterPath)
except ImportError as e:
    logger.critical(f"Missing dependency: {e}. Run setup.bat first.")
    sys.exit(1)

# ==============================================================================
# CONSTANTS
# ==============================================================================
class S:
    IDLE = 0; LISTENING = 1; PROCESSING = 2; SPEAKING = 3; ERROR = 4

STATE_LABELS = {S.IDLE: "STANDBY", S.LISTENING: "LISTENING", S.PROCESSING: "PROCESSING",
                S.SPEAKING: "SPEAKING", S.ERROR: "ERROR"}

STATE_COLORS = {
    S.IDLE:       (QColor(0, 200, 255), QColor(0, 120, 255)),
    S.LISTENING:  (QColor(0, 255, 255), QColor(0, 180, 255)),
    S.PROCESSING: (QColor(180, 120, 255), QColor(80, 160, 255)),
    S.SPEAKING:   (QColor(0, 255, 160), QColor(0, 220, 255)),
    S.ERROR:      (QColor(255, 60, 60), QColor(255, 120, 0)),
}

# ==============================================================================
# CONFIG & DATABASE
# ==============================================================================
class DatabaseManager:
    def __init__(self, db_path="sara_memory.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.lock = threading.Lock()
        self.cursor = self.conn.cursor()
        self.cursor.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, role TEXT, content TEXT)")
        self.cursor.execute("CREATE TABLE IF NOT EXISTS schedule (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, event TEXT, event_time TEXT, completed INTEGER DEFAULT 0)")
        self.conn.commit()

    def add_message(self, role, content):
        with self.lock:
            self.cursor.execute("INSERT INTO history (timestamp,role,content) VALUES (?,?,?)",
                                (datetime.datetime.now().isoformat(), role, content))
            self.conn.commit()

    def get_context(self, limit=4):
        with self.lock:
            self.cursor.execute("SELECT role,content FROM history ORDER BY id DESC LIMIT ?", (limit,))
            return [{"role": r[0], "content": r[1]} for r in reversed(self.cursor.fetchall())]

    def add_schedule(self, event, event_time=""):
        with self.lock:
            self.cursor.execute("INSERT INTO schedule (created,event,event_time) VALUES (?,?,?)",
                                (datetime.datetime.now().isoformat(), event, event_time))
            self.conn.commit()

    def get_schedule(self, limit=10):
        with self.lock:
            self.cursor.execute("SELECT event,event_time FROM schedule WHERE completed=0 ORDER BY id DESC LIMIT ?", (limit,))
            return self.cursor.fetchall()

try:
    with open("sara_config.json", "r") as f:
        CONFIG = json.load(f)
except Exception:
    CONFIG = {
        "wake_words": ["hey sara", "sara", "sarah", "hey sarah"],
        "audio": {"sample_rate": 16000, "chunk_size": 512, "silence_threshold_rms": 15,
                  "silence_duration_sec": 1.5, "wake_listen_sec": 2.5, "max_record_sec": 15},
        "models": {"whisper_model": "small.en", "ollama_host": "http://localhost:11434",
                   "openclaw_host": "http://127.0.0.1:18789", "ollama_model": "llama3.2:latest",
                   "ollama_fallback_model": "llama3:latest", "openclaw_model": "openclaw/main",
                   "prefer_openclaw": True, "openclaw_timeout_sec": 180,
                   "num_ctx": 4096, "num_predict": 256},
        "privacy": {"local_core_only": True},
        "tts": {"rate": 175, "volume": 1.0},
        "ui": {"fps": 40}
    }

db = DatabaseManager()

# ==============================================================================
# SYSTEM CONTROLLER
# ==============================================================================
class Sys:
    @staticmethod
    def open_app(name):
        raw = str(name or "").strip()
        cleaned = re.sub(r"\s+", " ", raw).strip(" .,!?:;")
        if not cleaned:
            return False, "Please tell me which app to open."

        n = re.sub(r"^(the|app)\s+", "", cleaned.lower()).strip()

        # Known web/app aliases that should never be treated as local executables.
        if "youtube" in n or n in {"yt", "youtube.com"}:
            webbrowser.open("https://www.youtube.com")
            return True, "Opened YouTube."

        if "spotify" in n:
            try:
                subprocess.Popen(["cmd", "/c", "start", "", "spotify:"])
            except Exception:
                webbrowser.open("https://open.spotify.com")
            return True, "Opened Spotify."

        app_aliases = {
            "notepad": "notepad",
            "calculator": "calc",
            "calc": "calc",
            "paint": "mspaint",
            "command prompt": "cmd",
            "cmd": "cmd",
            "file explorer": "explorer",
            "explorer": "explorer",
            "settings": "ms-settings:",
            "vs code": "code",
            "vscode": "code",
            "code": "code",
            "chrome": "chrome",
            "google chrome": "chrome",
            "edge": "msedge",
            "microsoft edge": "msedge",
        }
        target = app_aliases.get(n, cleaned)

        if re.match(r"^https?://", target):
            webbrowser.open(target)
            return True, f"Opened {cleaned}."

        if target.endswith(":"):
            try:
                subprocess.Popen(["cmd", "/c", "start", "", target])
                return True, f"Opened {cleaned}."
            except Exception as e:
                return False, f"I could not open {cleaned}: {e}"

        # Avoid noisy Windows popups for unknown names by checking PATH first.
        exe_path = shutil.which(target)
        if exe_path:
            try:
                subprocess.Popen([exe_path])
                return True, f"Opened {cleaned}."
            except Exception as e:
                return False, f"I could not open {cleaned}: {e}"

        # Best-effort shell open for registered apps/files/shortcuts.
        if hasattr(os, "startfile"):
            try:
                os.startfile(target)
                return True, f"Opened {cleaned}."
            except Exception:
                pass

        return False, f"I could not find app '{cleaned}'."

    @staticmethod
    def volume(action):
        if action == "mute": pyautogui.press("volumemute")
        elif action == "up": [pyautogui.press("volumeup") for _ in range(5)]
        elif action == "down": [pyautogui.press("volumedown") for _ in range(5)]
        return True, "Volume adjusted."

    @staticmethod
    def brightness(action):
        try:
            r = subprocess.run(['powershell', '-Command',
                '(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness'],
                capture_output=True, text=True, timeout=5)
            cur = int(r.stdout.strip()) if r.stdout.strip().isdigit() else 50
            nv = min(100, cur+20) if action == "up" else max(10, cur-20)
            subprocess.run(['powershell', '-Command',
                f'(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{nv})'],
                capture_output=True, text=True, timeout=5)
            return True, f"Brightness {nv}%"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def media(action):
        m = {"play":"playpause","pause":"playpause","next":"nexttrack","prev":"prevtrack","stop":"stop"}
        pyautogui.press(m.get(action, "playpause")); return True, f"Media {action}."

    @staticmethod
    def screenshot():
        os.makedirs("sara_screenshots", exist_ok=True)
        fn = f"sara_screenshots/ss_{int(time.time())}.png"
        pyautogui.screenshot(fn); return True, "Screenshot saved."

    @staticmethod
    def type_text(text):
        pyautogui.write(text, interval=0.02); return True, "Typed."

    @staticmethod
    def play_youtube(q):
        webbrowser.open(f"https://www.youtube.com/results?search_query={urllib.parse.quote(q)}")
        return True, f"YouTube: {q}"

    @staticmethod
    def play_music(q):
        try: subprocess.Popen(["cmd", "/c", "start", f"spotify:search:{q}"])
        except: webbrowser.open(f"https://open.spotify.com/search/{urllib.parse.quote(q)}")
        return True, f"Music: {q}"

    @staticmethod
    def web_search(q):
        webbrowser.open(f"https://www.google.com/search?q={urllib.parse.quote(q)}")
        return True, f"Searching: {q}"

    @staticmethod
    def weather():
        try:
            r = requests.get("https://wttr.in/?format=%l:+%C+%t+%h+%w", timeout=8)
            return True, r.text.strip()
        except: return False, "Weather unavailable."

    @staticmethod
    def get_time():
        return True, datetime.datetime.now().strftime("It's %I:%M %p, %A %B %d")

    @staticmethod
    def sys_info():
        cpu = psutil.cpu_percent(interval=0.3)
        ram = psutil.virtual_memory()
        bat = psutil.sensors_battery()
        s = f"CPU {cpu}%, RAM {ram.percent}%"
        if bat: s += f", Battery {bat.percent}%" + (" charging" if bat.power_plugged else "")
        return True, s

    @staticmethod
    def schedule_add(event, t=""):
        db.add_schedule(event, t); return True, f"Scheduled: {event}"

    @staticmethod
    def schedule_view():
        items = db.get_schedule(10)
        if not items: return True, "Schedule clear."
        return True, "; ".join([f"• {e[0]}" + (f" at {e[1]}" if e[1] else "") for e in items])

# ==============================================================================
# FALLBACK PARSER
# ==============================================================================
class FallbackParser:
    """Keyword-based fallback when LLM is down or returns garbage."""

    @staticmethod
    def _extract_youtube_query(text):
        t = str(text or "").lower().strip()
        if not t:
            return ""

        quoted = re.search(r'["\']([^"\']+)["\']', str(text or ""))
        if quoted:
            return quoted.group(1).strip()

        patterns = [
            r"\bsearch\s+and\s+play\s+(.+)$",
            r"\bplay\s+(.+?)\s*(?:on\s+youtube)?$",
            r"\bsearch\s+(?:for\s+)?(.+?)\s*(?:on\s+youtube)?$",
            r"\bwatch\s+(.+?)\s*(?:on\s+youtube)?$",
            r"\bopen\s+youtube\s*(?:and\s*)?(?:search|play|watch)?\s*(.+)$",
            r"\byoutube\s+(?:for\s+)?(.+)$",
        ]
        for pat in patterns:
            m = re.search(pat, t, flags=re.I)
            if not m:
                continue
            q = m.group(1).strip(" .,!?:;")
            q = re.sub(r"^(for|about)\s+", "", q, flags=re.I)
            q = re.sub(r"^(and\s+)+", "", q, flags=re.I)
            if q and q not in {"youtube", "yt", "video", "videos", "music"}:
                return q

        cleaned = re.sub(r"\b(open|youtube|search|play|watch|for|on|and|please|sara)\b", " ", t, flags=re.I)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,!?:;")
        if cleaned and cleaned not in {"youtube", "yt"}:
            return cleaned
        return ""

    @staticmethod
    def parse(text):
        t = text.lower().strip()
        # --- YouTube ---
        if "youtube" in t:
            q = FallbackParser._extract_youtube_query(t)
            if q:
                return {"intent":"play_youtube","params":{"query":q},"speech_response":f"Opening YouTube for {q}."}
            if re.search(r"\bopen\s+youtube\b", t, flags=re.I):
                return {"intent":"open_app","params":{"app":"youtube"},"speech_response":"Opening YouTube."}
            return {"intent":"play_youtube","params":{"query":"trending"},"speech_response":"Opening YouTube trending."}
        # --- Media controls (must be checked before generic music) ---
        if any(w in t for w in ["pause", "resume", "continue", "unpause", "next", "previous", "prev", "skip", "stop"]):
            if "next" in t or "skip" in t:
                a = "next"
            elif "previous" in t or "prev" in t:
                a = "prev"
            elif "pause" in t or "stop" in t:
                a = "pause"
            else:
                a = "play"
            return {"intent":"media","params":{"action":a},"speech_response":f"Media {a}."}
        # --- Open app ---
        if "open" in t:
            app = re.sub(r'.*(open|launch|start|run)\s*', '', t, flags=re.I).strip()
            return {"intent":"open_app","params":{"app":app},"speech_response":f"Opening {app}."}
        # --- Music / Spotify ---
        wants_music = any(w in t for w in ["play", "music", "song", "track", "listen to", "put on"]) or (
            "spotify" in t and any(w in t for w in ["play", "music", "song", "track", "listen"])
        )
        if wants_music and "youtube" not in t:
            q = re.sub(r'.*(play|spotify|music|song|track|put on|listen to)\s*', '', t, flags=re.I).strip() or "top hits"
            return {"intent":"play_music","params":{"query":q},"speech_response":f"Playing {q}."}
        # --- Volume ---
        if "volume" in t or "loud" in t or "quiet" in t:
            a = "up" if any(w in t for w in ["up","increase","raise","loud","higher"]) else "down" if any(w in t for w in ["down","decrease","lower","quiet","softer"]) else "mute"
            return {"intent":"volume","params":{"action":a},"speech_response":f"Volume {a}."}
        # --- Brightness ---
        if "bright" in t:
            a = "up" if any(w in t for w in ["up","increase","higher","more"]) else "down"
            return {"intent":"brightness","params":{"action":a},"speech_response":f"Brightness {a}."}
        # --- Time ---
        if any(w in t for w in ["time","what time","clock"]):
            return {"intent":"time","params":{},"speech_response":"Checking time."}
        # --- Weather ---
        if "weather" in t:
            return {"intent":"weather","params":{},"speech_response":"Checking weather."}
        # --- Screenshot ---
        if "screenshot" in t or "screen shot" in t or "capture screen" in t:
            return {"intent":"screenshot","params":{},"speech_response":"Taking screenshot."}
        # --- System info ---
        if any(w in t for w in ["cpu","ram","battery","system info","system status"]):
            return {"intent":"system_info","params":{},"speech_response":"Checking system."}
        # --- Web search ---
        if any(w in t for w in ["search","google","look up","find"]):
            q = re.sub(r'.*(search|google|look up|find)\s*(for|about)?\s*', '', t, flags=re.I).strip() or t
            return {"intent":"web_search","params":{"query":q},"speech_response":f"Searching for {q}."}
        # --- Schedule ---
        if any(w in t for w in ["schedule","remind","reminder","add to schedule"]):
            if any(w in t for w in ["show","view","list","what's","whats"]):
                return {"intent":"schedule_view","params":{},"speech_response":"Here's your schedule."}
            event = re.sub(r'.*(schedule|remind me to|reminder|add)\s*', '', t, flags=re.I).strip() or t
            return {"intent":"schedule_add","params":{"event":event},"speech_response":f"Scheduled: {event}."}
        # --- Type text ---
        if any(w in t for w in ["type","write","type out"]):
            txt = re.sub(r'.*(type|write|type out)\s*', '', t, flags=re.I).strip()
            return {"intent":"type","params":{"text":txt},"speech_response":"Typing."}
        # --- Default chat ---
        return {"intent":"chat","params":{},"speech_response":"Neural network offline. Using backup systems."}

# ==============================================================================
# LLM ENGINE  (dual-backend + pre-warm)
# ==============================================================================
class LLMEngine:
    PROMPT = (
        "You are SARA, a witty sarcastic female AI assistant controlling a Windows PC.\n"
        "RULES:\n"
        "1. Output ONLY valid JSON. No text before or after.\n"
        "2. Always use this exact schema: {\"intent\":\"<type>\",\"params\":{...},\"speech_response\":\"<1-2 sentence reply>\"}\n"
        "3. Pick the BEST intent from this list:\n"
        "   open_app — params: {\"app\":\"<name>\"}\n"
        "   volume — params: {\"action\":\"up|down|mute\"}\n"
        "   brightness — params: {\"action\":\"up|down\"}\n"
        "   media — params: {\"action\":\"play|pause|next|prev|stop\"}\n"
        "   screenshot — params: {}\n"
        "   type — params: {\"text\":\"<text to type>\"}\n"
        "   play_youtube — params: {\"query\":\"<search terms>\"}\n"
        "   play_music — params: {\"query\":\"<song or artist>\"}\n"
        "   web_search — params: {\"query\":\"<search terms>\"}\n"
        "   weather — params: {}\n"
        "   time — params: {}\n"
        "   schedule_add — params: {\"event\":\"<event>\",\"event_time\":\"<time>\"}\n"
        "   schedule_view — params: {}\n"
        "   system_info — params: {}\n"
        "   chat — params: {}  (ONLY if none of the above fit)\n\n"
        "EXAMPLES:\n"
        'User: play lo-fi music on youtube\n'
        'Output: {"intent":"play_youtube","params":{"query":"lo-fi music"},"speech_response":"Loading lo-fi vibes on YouTube."}\n'
        'User: open vs code\n'
        'Output: {"intent":"open_app","params":{"app":"code"},"speech_response":"Opening VS Code for you."}\n'
        'User: increase the volume\n'
        'Output: {"intent":"volume","params":{"action":"up"},"speech_response":"Turning it up."}\n'
        'User: play next song\n'
        'Output: {"intent":"media","params":{"action":"next"},"speech_response":"Skipping to next track."}\n'
        'User: play Shape of You on Spotify\n'
        'Output: {"intent":"play_music","params":{"query":"Shape of You"},"speech_response":"Playing Shape of You."}\n'
        'User: take a screenshot\n'
        'Output: {"intent":"screenshot","params":{},"speech_response":"Screenshot captured."}\n'
        'User: what time is it\n'
        'Output: {"intent":"time","params":{},"speech_response":"Let me check."}\n'
        'User: search how to make pasta\n'
        'Output: {"intent":"web_search","params":{"query":"how to make pasta"},"speech_response":"Searching that for you."}\n'
        'User: reduce brightness\n'
        'Output: {"intent":"brightness","params":{"action":"down"},"speech_response":"Dimming the screen."}\n'
        'User: remind me to call mom at 5pm\n'
        'Output: {"intent":"schedule_add","params":{"event":"call mom","event_time":"5pm"},"speech_response":"Reminder set."}\n'
        'User: open chrome\n'
        'Output: {"intent":"open_app","params":{"app":"chrome"},"speech_response":"Launching Chrome."}\n'
        'User: how are you\n'
        'Output: {"intent":"chat","params":{},"speech_response":"I am doing great, thanks for asking!"}\n'
    )

    def __init__(self):
        self.ollama_host = CONFIG["models"].get("ollama_host", "http://localhost:11434")
        self.openclaw_host = CONFIG["models"].get("openclaw_host", "http://127.0.0.1:18789")
        self.model = CONFIG["models"]["ollama_model"]
        self.openclaw_model = CONFIG["models"].get("openclaw_model", "openclaw/main")
        self.fallback_model = CONFIG["models"].get("ollama_fallback_model", "")
        self.num_ctx = CONFIG["models"].get("num_ctx", 4096)
        self.num_predict = CONFIG["models"].get("num_predict", 256)
        self.prefer_openclaw = bool(CONFIG["models"].get("prefer_openclaw", False))
        self.openclaw_timeout_sec = max(30, int(CONFIG["models"].get("openclaw_timeout_sec", 180)))
        self.local_core_only = bool(CONFIG.get("privacy", {}).get("local_core_only", True))
        self.active_host = None
        self.backend_type = None
        self._detect_backend()

    def _detect_backend(self):
        """Select backend; in local-only mode, optionally prefer OpenClaw gateway first."""
        self.openclaw_token = CONFIG.get("models", {}).get("openclaw_token", "")
        if self.local_core_only and self.prefer_openclaw:
            logger.info("Local-only mode: preferring OpenClaw gateway.")

        if self.local_core_only and not self.prefer_openclaw:
            try:
                r = requests.get(f"{self.ollama_host}/api/tags", timeout=2)
                if r.status_code == 200:
                    self.active_host = self.ollama_host
                    self.backend_type = "ollama"
                    logger.info(f"Backend: ollama @ {self.ollama_host} (local-only mode)")
                    return
            except Exception:
                logger.warning("Local-only mode: Ollama probe failed, attempting OpenClaw gateway.")

        # Try OpenClaw
        try:
            headers = {}
            if self.openclaw_token:
                headers["Authorization"] = f"Bearer {self.openclaw_token}"
            r = requests.get(f"{self.openclaw_host}/v1/models", headers=headers, timeout=2)
            if r.status_code == 200:
                self.active_host = self.openclaw_host
                self.backend_type = "openclaw"
                logger.info(f"Backend: openclaw @ {self.openclaw_host}")
                return
            elif r.status_code == 401:
                logger.warning("OpenClaw returned 401 Unauthorized — missing/bad token. Falling back to Ollama.")
        except Exception:
            pass
        # Try Ollama direct
        try:
            r = requests.get(f"{self.ollama_host}/api/tags", timeout=2)
            if r.status_code == 200:
                self.active_host = self.ollama_host
                self.backend_type = "ollama"
                logger.info(f"Backend: ollama @ {self.ollama_host}")
                return
        except Exception:
            pass
        # Final default
        self.active_host, self.backend_type = self.ollama_host, "ollama"
        logger.warning(f"No backend responded, defaulting to {self.ollama_host}")

    def prewarm(self):
        """Load model into RAM with a tiny request."""
        try:
            warm_model = self.openclaw_model if self.backend_type == "openclaw" else self.model
            self._call([{"role":"user","content":"hi"}], warm_model, json_mode=False)
            logger.info("Model pre-warmed OK")
        except Exception as e:
            logger.warning(f"Pre-warm failed: {e}")

    def _is_local(self):
        h = (self.active_host or "").lower()
        return "localhost" in h or "127.0.0.1" in h

    def _call(self, messages, model, json_mode=True):
        if self.backend_type == "openclaw":
            model_to_use = model or self.openclaw_model
            if isinstance(model_to_use, str) and not model_to_use.startswith("openclaw/"):
                model_to_use = self.openclaw_model

            # Keep payload OpenAI-compatible and minimal for local OpenClaw gateway reliability.
            payload = {"model": model_to_use, "messages": messages, "stream": False}
            headers = {"Content-Type": "application/json"}
            if self.openclaw_token:
                headers["Authorization"] = f"Bearer {self.openclaw_token}"
            r = requests.post(f"{self.active_host}/v1/chat/completions",
                              json=payload, headers=headers, timeout=(5, self.openclaw_timeout_sec))
            r.raise_for_status()
            ch = r.json().get("choices", [])
            return ch[0]["message"]["content"] if ch else "{}"
        else:
            payload = {"model": model, "messages": messages, "stream": False,
                       "options": {"num_ctx": self.num_ctx, "num_predict": self.num_predict}}
            if json_mode:
                payload["format"] = "json"
            r = requests.post(f"{self.active_host}/api/chat", json=payload, timeout=(5, 120))
            r.raise_for_status()
            return r.json().get("message", {}).get("content", "{}")

    def _extract_json(self, text):
        text = (text or "").strip()
        # Strip markdown code fences if present
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON object in the text
            m = re.search(r'\{[\s\S]*\}', text)
            if not m:
                raise ValueError("No JSON found in response")
            return json.loads(m.group(0))

    def _ok(self, d):
        return isinstance(d, dict) and {"intent","params","speech_response"}.issubset(d.keys())

    # Fuzzy intent mapping for when the LLM returns close-but-wrong intents
    INTENT_MAP = {
        "youtube": "play_youtube", "play_video": "play_youtube", "video": "play_youtube",
        "watch": "play_youtube", "search_youtube": "play_youtube",
        "spotify": "play_music", "music": "play_music", "song": "play_music",
        "listen": "play_music", "play_song": "play_music",
        "search": "web_search", "google": "web_search", "browse": "web_search",
        "open": "open_app", "launch": "open_app", "start": "open_app", "run": "open_app",
        "vol": "volume", "sound": "volume", "mute": "volume",
        "bright": "brightness", "dim": "brightness", "screen_brightness": "brightness",
        "next": "media", "prev": "media", "skip": "media", "pause": "media",
        "resume": "media", "play_pause": "media", "stop_music": "media",
        "capture": "screenshot", "screen_capture": "screenshot",
        "check_time": "time", "clock": "time", "current_time": "time",
        "check_weather": "weather", "forecast": "weather",
        "schedule": "schedule_add", "remind": "schedule_add", "reminder": "schedule_add",
        "view_schedule": "schedule_view", "show_schedule": "schedule_view",
        "my_schedule": "schedule_view", "list_schedule": "schedule_view",
        "sysinfo": "system_info", "sys_info": "system_info", "status": "system_info",
        "battery": "system_info", "cpu": "system_info",
        "write": "type", "type_text": "type", "dictate": "type",
        "conversation": "chat", "talk": "chat", "hello": "chat", "hi": "chat",
    }
    VALID_INTENTS = {"open_app","volume","brightness","media","screenshot","type",
                     "play_youtube","play_music","web_search","weather","time",
                     "schedule_add","schedule_view","system_info","chat"}

    def _norm(self, d, user_text=""):
        if not isinstance(d, dict):
            return None
        raw_intent = d.get("intent", "chat").lower().strip()
        # Exact match first, then fuzzy map
        if raw_intent in self.VALID_INTENTS:
            intent = raw_intent
        elif raw_intent in self.INTENT_MAP:
            intent = self.INTENT_MAP[raw_intent]
        else:
            # Check if any map key is a substring
            intent = "chat"
            for key, mapped in self.INTENT_MAP.items():
                if key in raw_intent:
                    intent = mapped
                    break

        params = d.get("params") if isinstance(d.get("params"), dict) else {}
        speech = d.get("speech_response")
        if isinstance(speech, dict):
            speech = speech.get("text", json.dumps(speech))
        if not isinstance(speech, str) or not speech.strip():
            return None

        # Auto-populate empty params from user text when we know what's needed
        ut = user_text.lower()
        if intent == "play_youtube":
            q_user = FallbackParser._extract_youtube_query(ut)
            q0 = str(params.get("query", "")).strip()
            if not q0:
                params["query"] = q_user or "trending"
            elif q0.lower() in {"youtube", "video", "videos", "play", "search", "trending", "trend"} and q_user:
                params["query"] = q_user
        elif intent == "play_music":
            q0 = str(params.get("query", "")).strip().lower()
            if any(w in ut for w in ["resume", "continue", "unpause"]) and (not q0 or q0 in ["music", "the music"] or "resume" in q0):
                intent = "media"
                params = {"action": "play"}
            elif not params.get("query"):
                q = re.sub(r'.*(play|spotify|music|song|track|listen to|put on)\s*', '', ut, flags=re.I).strip()
                params["query"] = q or "top hits"
        elif intent == "open_app" and not params.get("app"):
            app = re.sub(r'.*(open|launch|start|run)\s*', '', ut, flags=re.I).strip()
            params["app"] = app
        elif intent == "volume" and not params.get("action"):
            if any(w in ut for w in ["up","increase","raise","louder","higher"]):
                params["action"] = "up"
            elif any(w in ut for w in ["down","decrease","lower","softer","quieter"]):
                params["action"] = "down"
            else:
                params["action"] = "mute"
        elif intent == "brightness" and not params.get("action"):
            params["action"] = "up" if any(w in ut for w in ["up","increase","higher","more"]) else "down"
        elif intent == "media" and not params.get("action"):
            if "next" in ut or "skip" in ut: params["action"] = "next"
            elif "prev" in ut or "previous" in ut: params["action"] = "prev"
            elif "pause" in ut or "stop" in ut: params["action"] = "pause"
            else: params["action"] = "play"
        elif intent == "web_search" and not params.get("query"):
            q = re.sub(r'.*(search|google|look up|find)\s*(for|about)?\s*', '', ut, flags=re.I).strip()
            params["query"] = q or ut
        elif intent == "schedule_add" and not params.get("event"):
            params["event"] = re.sub(r'.*(schedule|remind me to|add)\s*', '', ut, flags=re.I).strip()

        # Guard rail: force YouTube query execution when user explicitly asks play/search/watch on YouTube.
        if "youtube" in ut and any(w in ut for w in ["play", "search", "watch"]):
            intent = "play_youtube"
            params = {"query": FallbackParser._extract_youtube_query(ut) or str(params.get("query", "")).strip() or "trending"}

        # Guard rail: local models may return unrelated valid intents for resume commands.
        if any(w in ut for w in ["resume", "continue", "unpause"]) and any(w in ut for w in ["music", "song", "spotify", "track"]):
            intent = "media"
            params = {"action": "play"}

        return {"intent": intent, "params": params, "speech_response": speech.strip()}

    def generate(self, text):
        if self.local_core_only and not self._is_local():
            return FallbackParser.parse(text)

        model = self.openclaw_model if self.backend_type == "openclaw" else self.model
        logger.info(f"LLM: {self.backend_type}@{self.active_host} model={model}")

        ctx = db.get_context(limit=4)
        msgs = [{"role":"system","content": self.PROMPT}]
        for c in ctx:
            role = c.get("role","user")
            if role not in ("system","user","assistant"): role = "user"
            msgs.append({"role": role, "content": str(c.get("content",""))})
        msgs.append({"role":"user","content": text})

        # Attempt 1 — full prompt with JSON mode
        try:
            raw = self._call(msgs, model, json_mode=True)
            logger.info(f"LLM raw response: {raw[:300]}")
            d = self._extract_json(raw)
            if self._ok(d):
                d = self._norm(d, user_text=text)
                if d:
                    logger.info(f"LLM parsed: intent={d['intent']} params={d['params']}")
                    db.add_message("assistant", d["speech_response"])
                    return d
            raise ValueError(f"bad json structure: {d}")
        except requests.exceptions.HTTPError as e:
            err = (e.response.text if e.response else str(e)).lower()
            if self.backend_type == "openclaw":
                logger.warning(f"OpenClaw HTTP error, switching this session to local Ollama: {e}")
                self.active_host = self.ollama_host
                self.backend_type = "ollama"
                model = self.model
            elif "memory" in err and self.fallback_model:
                model = self.fallback_model
                logger.warning(f"Memory error, switching to fallback: {model}")
            else:
                logger.error(f"HTTP err: {e}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Request err: {e}")
            if self.backend_type == "openclaw":
                logger.warning("OpenClaw request failed; retrying with local Ollama backend.")
                self.active_host = self.ollama_host
                self.backend_type = "ollama"
                model = self.model
            elif self.fallback_model and model != self.fallback_model:
                model = self.fallback_model
        except Exception as e:
            logger.error(f"LLM err1: {e}")

        # Attempt 2 — stripped-down strict prompt
        try:
            strict_msgs = [
                {"role":"system","content":
                    'Return ONLY valid JSON. Schema: {"intent":"<type>","params":{},"speech_response":"<reply>"}\n'
                    'Intents: open_app, volume, brightness, media, screenshot, type, play_youtube, play_music, web_search, weather, time, schedule_add, schedule_view, system_info, chat'},
                {"role":"user","content": text}
            ]
            raw2 = self._call(strict_msgs, model, json_mode=True)
            logger.info(f"LLM attempt2 raw: {raw2[:300]}")
            try:
                d2 = self._extract_json(raw2)
                if self._ok(d2):
                    d2 = self._norm(d2, user_text=text)
                    if d2:
                        logger.info(f"LLM attempt2 parsed: intent={d2['intent']}")
                        db.add_message("assistant", d2["speech_response"])
                        return d2
            except Exception:
                pass
            # If JSON failed but we got some text, use it as chat
            reply = str(raw2 or "").strip()
            ret = {"intent":"chat","params":{},"speech_response": reply or "I couldn't process that."}
            db.add_message("assistant", ret["speech_response"])
            return ret
        except Exception as e:
            logger.error(f"LLM err2: {e}")

        # Attempt 3 — keyword fallback (no LLM needed)
        logger.warning("LLM failed both attempts, using FallbackParser")
        return FallbackParser.parse(text)

# ==============================================================================
# TTS  (Female — Zira)
# ==============================================================================
class TTSThread(QThread):
    finished_speaking = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.q = queue.Queue()
        self.running = True

    def run(self):
        backend = "none"
        engine = None
        sapi_voice = None
        comtypes_mod = None

        # Backend 1: pyttsx3 (needs working pywin32 on Windows)
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", CONFIG["tts"].get("rate", 175))
            engine.setProperty("volume", CONFIG["tts"].get("volume", 1.0))
            for v in engine.getProperty("voices"):
                if "zira" in getattr(v, "name", "").lower():
                    engine.setProperty("voice", v.id)
                    break
            backend = "pyttsx3"
            logger.info("TTS backend: pyttsx3")
        except Exception as e:
            logger.warning(f"TTS: pyttsx3 init failed ({e}). Falling back to Windows SAPI.")

        # Backend 2: Windows SAPI via comtypes (no pywin32)
        if backend != "pyttsx3":
            try:
                import comtypes
                from comtypes.client import CreateObject

                comtypes.CoInitialize()
                comtypes_mod = comtypes
                sapi_voice = CreateObject("SAPI.SpVoice")

                # Map pyttsx3-style config to SAPI ranges
                rate = int(CONFIG["tts"].get("rate", 175))
                vol = float(CONFIG["tts"].get("volume", 1.0))
                sapi_voice.Rate = max(-10, min(10, int(round((rate - 175) / 20))))
                sapi_voice.Volume = max(0, min(100, int(round(vol * 100))))

                try:
                    voices = sapi_voice.GetVoices()
                    for i in range(voices.Count):
                        tok = voices.Item(i)
                        if "zira" in tok.GetDescription().lower():
                            sapi_voice.Voice = tok
                            break
                except Exception:
                    pass

                backend = "sapi"
                logger.info("TTS backend: SAPI (comtypes)")
            except Exception as e:
                logger.error(f"TTS: SAPI init failed ({e}). TTS disabled.")
                backend = "none"

        while self.running:
            try:
                txt = self.q.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                if txt:
                    if backend == "pyttsx3":
                        engine.say(txt)
                        engine.runAndWait()
                    elif backend == "sapi":
                        sapi_voice.Speak(txt)
            except Exception as speak_err:
                logger.error(f"TTS speak error: {speak_err}")
            finally:
                # Even if TTS is disabled, emit so the app can continue.
                try:
                    self.finished_speaking.emit()
                except Exception:
                    pass
                try:
                    self.q.task_done()
                except Exception:
                    pass

        if backend == "sapi" and comtypes_mod is not None:
            try:
                comtypes_mod.CoUninitialize()
            except Exception:
                pass

    def speak(self, t):
        self.q.put(t)

    def stop(self):
        self.running = False

# ==============================================================================
# AUDIO LISTENER  (improved wake-word detection)
# ==============================================================================
class AudioListenerThread(QThread):
    wake_word_detected = pyqtSignal()
    command_recorded = pyqtSignal(str)
    audio_level = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.running = True
        self.pa = pyaudio.PyAudio()
        self.rate = CONFIG["audio"]["sample_rate"]
        self.chunk = CONFIG["audio"]["chunk_size"]
        self.session_active = False   # True while in a conversation session
        self.processing = False       # True while LLM is working
        self.wake_sec = CONFIG["audio"].get("wake_listen_sec", 2.5)
        self.max_rec = CONFIG["audio"].get("max_record_sec", 15)
        self.min_voice_frames = int(CONFIG["audio"].get("min_voice_frames", 5))
        logger.info("Loading Whisper…")
        self.wm = whisper.load_model(CONFIG["models"]["whisper_model"])
        logger.info("Whisper ready.")

    def run(self):
        stream = self.pa.open(format=pyaudio.paInt16, channels=1, rate=self.rate,
                              input=True, frames_per_buffer=self.chunk)
        thr = CONFIG["audio"]["silence_threshold_rms"]
        scale = max(thr * 3.0, 1.0)

        while self.running:
            if not self.session_active:
                # ── STANDBY: listen for wake word ──
                nf = int(self.rate / self.chunk * self.wake_sec)
                frames = []
                for _ in range(nf):
                    if not self.running: break
                    frames.append(stream.read(self.chunk, exception_on_overflow=False))
                raw = b''.join(frames)
                samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
                rms = np.sqrt(np.mean(np.square(samples)))
                self.audio_level.emit(min(1.0, max(0.0, rms / scale)))
                if rms > thr:
                    txt = self._transcribe(frames).lower()
                    logger.info(f"Wake check: '{txt}'")
                    if any(ww in txt for ww in CONFIG["wake_words"]):
                        logger.info("*** WAKE — SESSION START ***")
                        self.session_active = True
                        self.wake_word_detected.emit()
            else:
                # ── SESSION MODE: keep listening for commands ──
                # Wait while SARA is processing / speaking
                while self.processing and self.running:
                    d = stream.read(self.chunk, exception_on_overflow=False)
                    s = np.frombuffer(d, dtype=np.int16).astype(np.float32)
                    rms = np.sqrt(np.mean(np.square(s)))
                    self.audio_level.emit(min(1.0, max(0.0, rms / scale)))

                if not self.running or not self.session_active:
                    continue

                # Record until silence, but require enough voiced chunks.
                frames, sil, voiced = [], 0, 0
                max_sil = int(self.rate / self.chunk * CONFIG["audio"]["silence_duration_sec"])
                max_tot = int(self.rate / self.chunk * self.max_rec)

                while self.running and self.session_active:
                    d = stream.read(self.chunk, exception_on_overflow=False)
                    frames.append(d)
                    s = np.frombuffer(d, dtype=np.int16).astype(np.float32)
                    rms = np.sqrt(np.mean(np.square(s)))
                    self.audio_level.emit(min(1.0, max(0.0, rms / scale)))
                    if rms < thr:
                        sil += 1
                    else:
                        voiced += 1
                        sil = 0
                    if sil > max_sil or len(frames) > max_tot:
                        break

                if len(frames) > max_sil and voiced >= self.min_voice_frames:
                    wf = wave.open("temp_cmd.wav", 'wb')
                    wf.setnchannels(1)
                    wf.setsampwidth(self.pa.get_sample_size(pyaudio.paInt16))
                    wf.setframerate(self.rate)
                    wf.writeframes(b''.join(frames))
                    wf.close()
                    self.processing = True
                    self.command_recorded.emit("temp_cmd.wav")
                else:
                    logger.info(f"Ignored low-voice capture (voiced_chunks={voiced}, total_chunks={len(frames)})")
                # NOTE: session stays active — we do NOT exit session here

        stream.stop_stream(); stream.close(); self.pa.terminate()

    def _transcribe(self, frames):
        a = np.frombuffer(b''.join(frames), dtype=np.int16).astype(np.float32) / 32768.0
        return self.wm.transcribe(a, fp16=False, language='en')["text"].strip()

    def transcribe_file(self, fp):
        wf = wave.open(fp, 'rb')
        data = wf.readframes(wf.getnframes())
        wf.close()
        audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        return self.wm.transcribe(audio, fp16=False, language='en')["text"].strip()

    def end_session(self):
        """Called by Logic when session should end."""
        self.session_active = False
        self.processing = False
        logger.info("*** SESSION END ***")

    def stop(self): self.running = False

# ==============================================================================
# MAIN LOGIC CONTROLLER  (session-aware)
# ==============================================================================
END_SESSION_WORDS = ["goodbye", "bye sara", "bye", "that's all", "peace out",
                     "go to sleep", "standby", "stop listening", "done for now",
                     "thank you sara", "thanks sara", "you can go"]

class Logic(QThread):
    state_changed = pyqtSignal(int)
    log_updated = pyqtSignal(str, str)

    def __init__(self, audio, tts):
        super().__init__()
        self.audio, self.tts = audio, tts
        self.llm = LLMEngine()
        self.audio.wake_word_detected.connect(self.on_wake)
        self.audio.command_recorded.connect(self.on_cmd)
        self.tts.finished_speaking.connect(self._on_speak_done)
        threading.Thread(target=self.llm.prewarm, daemon=True).start()

    def _on_speak_done(self):
        """After speaking, go back to LISTENING if session active, else IDLE."""
        self.audio.processing = False
        if self.audio.session_active:
            self.state_changed.emit(S.LISTENING)
        else:
            self.state_changed.emit(S.IDLE)

    @staticmethod
    def _is_noise_transcript(text):
        t = str(text or "").strip()
        if not t:
            return True
        if re.fullmatch(r"[\W_]+", t):
            return True
        if len(re.sub(r"[^a-zA-Z0-9]+", "", t)) < 2:
            return True
        return False

    @staticmethod
    def _extract_open_and_type(text):
        m = re.search(
            r"\b(?:open|launch|start|run)\s+(.+?)\s+(?:and\s+)?(?:write|type|type out)\s+(.+)$",
            str(text or ""),
            flags=re.I,
        )
        if not m:
            return None
        app = re.sub(r"^(the\s+)", "", m.group(1).strip(" .,!?:;"), flags=re.I)
        typed = m.group(2).strip().strip('"\' ')
        if not app or not typed:
            return None
        return app, typed

    @staticmethod
    def _use_deterministic_parser(text_lower):
        control_terms = ["pause", "resume", "continue", "unpause", "next", "previous", "prev", "skip", "stop music"]
        if any(w in text_lower for w in control_terms):
            return True
        if "open spotify" in text_lower:
            return True
        if "youtube" in text_lower and any(w in text_lower for w in ["play", "search", "watch"]):
            return True
        return False

    def on_wake(self):
        self.audio.processing = True
        self.state_changed.emit(S.SPEAKING)
        self.tts.speak("Yes?")

    def on_cmd(self, fp):
        self.state_changed.emit(S.PROCESSING)
        threading.Thread(target=self._process, args=(fp,), daemon=True).start()

    def _process(self, fp):
        text = re.sub(r"\s+", " ", self.audio.transcribe_file(fp) or "").strip()
        if self._is_noise_transcript(text):
            logger.info(f"Ignored transcript noise: {text!r}")
            self.audio.processing = False
            self.state_changed.emit(S.LISTENING if self.audio.session_active else S.IDLE)
            return

        self.log_updated.emit("YOU", text)
        db.add_message("user", text)

        # Check for end-session command
        text_lower = text.lower()
        if any(w in text_lower for w in END_SESSION_WORDS):
            self.log_updated.emit("SARA", "Going to standby. Call me anytime!")
            self.state_changed.emit(S.SPEAKING)
            self.tts.speak("Going to standby. Call me anytime!")
            self.audio.end_session()
            return

        combined = self._extract_open_and_type(text)
        if combined:
            app, typed = combined
            try:
                ok, info = Sys.open_app(app)
                if not ok:
                    raise RuntimeError(info)
                time.sleep(0.8 if "notepad" in app.lower() else 0.4)
                Sys.type_text(typed)
                speech = f"Opening {app} and writing that now."
            except Exception as ex:
                logger.error(f"Combined open+type error: {ex}")
                speech = "I could not finish that open-and-write command."
                self.state_changed.emit(S.ERROR)

            self.log_updated.emit("SARA", speech)
            self.state_changed.emit(S.SPEAKING)
            self.tts.speak(speech)
            return

        if self._use_deterministic_parser(text_lower):
            data = FallbackParser.parse(text)
            logger.info(f"Deterministic parse: intent={data.get('intent')} params={data.get('params', {})}")
        else:
            data = self.llm.generate(text)
        intent, params = data.get("intent"), data.get("params", {})
        speech = data.get("speech_response", "Done.")

        try:
            if intent == "open_app":
                ok, info = Sys.open_app(params.get("app", ""))
                if not ok:
                    speech = info
            elif intent == "volume": Sys.volume(params.get("action", ""))
            elif intent == "brightness": Sys.brightness(params.get("action", ""))
            elif intent == "media": Sys.media(params.get("action", "play"))
            elif intent == "screenshot": Sys.screenshot()
            elif intent == "type": Sys.type_text(params.get("text", ""))
            elif intent == "play_youtube": Sys.play_youtube(params.get("query", ""))
            elif intent == "play_music": Sys.play_music(params.get("query", ""))
            elif intent == "web_search": Sys.web_search(params.get("query", ""))
            elif intent == "weather":
                ok, info = Sys.weather()
                if ok: speech = info
            elif intent == "time":
                _, info = Sys.get_time(); speech = info
            elif intent == "schedule_add":
                Sys.schedule_add(params.get("event", ""), params.get("event_time", ""))
            elif intent == "schedule_view":
                _, info = Sys.schedule_view(); speech = info
            elif intent == "system_info":
                _, info = Sys.sys_info(); speech = info
        except Exception as ex:
            logger.error(f"Exec error: {ex}")
            speech = "Something went wrong."
            self.state_changed.emit(S.ERROR)

        self.log_updated.emit("SARA", speech)
        self.state_changed.emit(S.SPEAKING)
        self.tts.speak(speech)

# ==============================================================================
# JARVIS HUD WIDGET
# ==============================================================================
class JarvisHUD(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(340, 340)
        self.state = S.IDLE
        self.phase = 0.0
        self.audio_level = 0.0
        self.smoothed = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(int(1000 / CONFIG["ui"].get("fps", 40)))

    def _tick(self):
        sp = {S.IDLE: 0.02, S.LISTENING: 0.08, S.PROCESSING: 0.12,
              S.SPEAKING: 0.05, S.ERROR: 0.03}
        self.phase += sp.get(self.state, 0.03)
        self.smoothed += (self.audio_level - self.smoothed) * 0.18
        self.update()

    def set_state(self, s): self.state = s
    def set_audio_level(self, v):
        try: self.audio_level = max(0.0, min(1.0, float(v)))
        except: self.audio_level = 0.0

    def _cols(self):
        return STATE_COLORS.get(self.state, STATE_COLORS[S.IDLE])

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        center = QPoint(cx, cy)
        R = min(w, h) * 0.43
        c1, c2 = self._cols()
        lv = self.smoothed
        pulse = (math.sin(self.phase) + 1.0) / 2.0

        # ── 1. Background glow ──
        gr = R * 1.3
        glow = QRadialGradient(cx, cy, gr)
        glow.setColorAt(0, QColor(c1.red(), c1.green(), c1.blue(), int(18 + 30 * lv)))
        glow.setColorAt(0.6, QColor(c2.red(), c2.green(), c2.blue(), int(6 + 10 * lv)))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), QBrush(glow))

        # ── 2. Outer arc ring  (280°, slow CW) ──
        self._arc(p, cx, cy, R * 0.93, 2.0 + lv * 3, 280, self.phase * 10, c1, lv, 22)

        # ── 3. Second arc ring  (210°, CCW) ──
        self._arc(p, cx, cy, R * 0.78, 2.5 + lv * 4, 210, -self.phase * 18, c2, lv, 16)

        # ── 4. Third arc ring  (150°, fast CW) ──
        self._arc(p, cx, cy, R * 0.62, 2.0 + lv * 3.5, 150, self.phase * 30, c1, lv, 11)

        # ── 5. Inner arc ring  (100°, very fast CCW) ──
        self._arc(p, cx, cy, R * 0.47, 1.8 + lv * 2.5, 100, -self.phase * 45, c2, lv, 7)

        # ── 6. Thin full inner circle ──
        a6 = int(30 + 30 * pulse)
        p.setPen(QPen(QColor(c1.red(), c1.green(), c1.blue(), a6), 0.8))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(center, int(R * 0.33), int(R * 0.33))

        # ── 7. Scanning radar line ──
        scan_ang = math.radians(self.phase * 50 % 360)
        sx = cx + R * 0.93 * math.cos(scan_ang)
        sy = cy - R * 0.93 * math.sin(scan_ang)
        sg = QLinearGradient(cx, cy, sx, sy)
        sg.setColorAt(0, QColor(c1.red(), c1.green(), c1.blue(), 0))
        sg.setColorAt(0.6, QColor(c1.red(), c1.green(), c1.blue(), int(15 + 35 * lv)))
        sg.setColorAt(1, QColor(c1.red(), c1.green(), c1.blue(), int(40 + 60 * lv)))
        p.setPen(QPen(QBrush(sg), 1.2))
        p.drawLine(cx, cy, int(sx), int(sy))

        # ── 8. Crosshair ──
        cl = R * 0.07
        ca = int(100 + 80 * pulse)
        p.setPen(QPen(QColor(c1.red(), c1.green(), c1.blue(), ca), 1.0))
        p.drawLine(int(cx - cl), cy, int(cx + cl), cy)
        p.drawLine(cx, int(cy - cl), cx, int(cy + cl))

        # ── 9. Center glow ──
        dr = 3.5 + lv * 5 + pulse * 2
        dg = QRadialGradient(cx, cy, dr * 3)
        dg.setColorAt(0, QColor(255, 255, 255, 220))
        dg.setColorAt(0.3, QColor(c1.red(), c1.green(), c1.blue(), 170))
        dg.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen); p.setBrush(QBrush(dg))
        p.drawEllipse(center, int(dr * 2.5), int(dr * 2.5))

        # ── 10. Small data readouts ──
        p.setFont(QFont("Consolas", 7))
        p.setPen(QColor(c1.red(), c1.green(), c1.blue(), int(90 + 60 * pulse)))
        now = datetime.datetime.now().strftime("%H:%M:%S")
        p.drawText(int(cx + R * 0.55), int(cy - R * 0.75), now)
        p.drawText(int(cx - R * 0.95), int(cy + R * 0.85),
                   f"SYS {psutil.cpu_percent(interval=0)}%")

        # ── 11. State label ──
        label = STATE_LABELS.get(self.state, "")
        if label:
            p.setFont(QFont("Consolas", 8, QFont.Bold))
            p.setPen(QColor(c1.red(), c1.green(), c1.blue(), int(140 + 80 * pulse)))
            p.drawText(QRect(0, int(cy + R * 0.6), w, 24), Qt.AlignCenter, f"[ {label} ]")

        p.end()

    def _arc(self, p, cx, cy, radius, thick, span_deg, rot_deg, color, lv, nticks):
        """Draw a rotating arc segment with tick marks."""
        p.save()
        p.translate(cx, cy)
        p.rotate(rot_deg)

        r = int(radius)
        rect = QRect(-r, -r, 2 * r, 2 * r)
        alpha = min(255, int(80 + 140 * lv))
        ac = QColor(color.red(), color.green(), color.blue(), alpha)
        p.setPen(QPen(ac, thick, Qt.SolidLine, Qt.FlatCap))
        p.setBrush(Qt.NoBrush)
        p.drawArc(rect, 0, int(span_deg * 16))

        # Tick marks
        ta = int(alpha * 0.6)
        tc = QColor(color.red(), color.green(), color.blue(), ta)
        p.setPen(QPen(tc, 0.8))
        tick_in = radius - 5 - thick / 2
        tick_out = radius + 5 + thick / 2
        for i in range(nticks + 1):
            ang = math.radians(span_deg * i / nticks)
            p.drawLine(
                int(tick_in * math.cos(ang)), int(-tick_in * math.sin(ang)),
                int(tick_out * math.cos(ang)), int(-tick_out * math.sin(ang)))

        # End caps — small bright dots at arc endpoints
        cap_r = 2.0 + lv * 1.5
        cap_c = QColor(color.red(), color.green(), color.blue(), min(255, alpha + 40))
        p.setPen(Qt.NoPen); p.setBrush(cap_c)
        p.drawEllipse(QRectF(radius - cap_r, -cap_r, cap_r * 2, cap_r * 2))
        end_ang = math.radians(span_deg)
        ex, ey = radius * math.cos(end_ang), -radius * math.sin(end_ang)
        p.drawEllipse(QRectF(ex - cap_r, ey - cap_r, cap_r * 2, cap_r * 2))

        p.restore()

# ==============================================================================
# MAIN WINDOW
# ==============================================================================
class SaraGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("S.A.R.A")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(820, 420)

        main_w = QWidget()
        main_l = QHBoxLayout(main_w)
        main_l.setContentsMargins(10, 10, 10, 10)
        main_l.setSpacing(0)
        main_w.setStyleSheet(
            "QWidget { background: qradialgradient("
            "cx:0.3, cy:0.4, radius:1.0, "
            "stop:0 rgba(8, 12, 22, 240), "
            "stop:0.5 rgba(4, 8, 16, 248), "
            "stop:1 rgba(2, 4, 8, 252)); "
            "border-radius: 16px; "
            "border: 1px solid rgba(0, 200, 255, 50); }"
        )

        # Left — JARVIS HUD
        self.hud = JarvisHUD()
        main_l.addWidget(self.hud, 4)

        # Right — chat log
        right = QWidget()
        right.setStyleSheet("QWidget{background:transparent;border:none;}")
        rl = QVBoxLayout(right); rl.setContentsMargins(8, 8, 8, 8)

        title = QLabel("S . A . R . A")
        title.setStyleSheet(
            "color:rgba(0,210,255,180);font-size:11px;font-family:'Consolas';"
            "font-weight:bold;letter-spacing:8px;")
        title.setAlignment(Qt.AlignCenter)
        rl.addWidget(title)

        sub = QLabel("Smart Autonomous Response Assistant")
        sub.setStyleSheet("color:rgba(0,180,255,80);font-size:8px;font-family:'Consolas';letter-spacing:3px;")
        sub.setAlignment(Qt.AlignCenter)
        rl.addWidget(sub)

        self.chat = QListWidget()
        self.chat.setStyleSheet(
            "QListWidget{background:rgba(0,0,0,25);border:none;"
            "border-left:1px solid rgba(0,200,255,30);"
            "color:#b0d8f0;font-family:'Consolas';font-size:12px;padding:4px;}"
            "QListWidget::item{padding:3px 6px;margin:1px 0;border-radius:3px;}")
        self.chat.setWordWrap(True)
        rl.addWidget(self.chat)
        main_l.addWidget(right, 5)
        self.setCentralWidget(main_w)

        # Threads
        self.tts = TTSThread()
        self.audio = AudioListenerThread()
        self.logic = Logic(self.audio, self.tts)

        self.logic.state_changed.connect(self.hud.set_state)
        self.logic.log_updated.connect(self.add_log)
        self.audio.audio_level.connect(self.hud.set_audio_level)

        self.tts.start(); self.audio.start()
        QTimer.singleShot(1000, lambda: self.tts.speak("Sara online. Awaiting your command."))

    def add_log(self, role, msg):
        item = QListWidgetItem(f"[{role}]  {msg}")
        item.setForeground(QColor(0, 255, 180) if role == "SARA" else QColor(160, 200, 255))
        self.chat.addItem(item); self.chat.scrollToBottom()

    def mousePressEvent(self, e):
        self.old_pos = e.globalPos() if e.button() == Qt.LeftButton else None
    def mouseMoveEvent(self, e):
        if hasattr(self, 'old_pos') and self.old_pos:
            d = e.globalPos() - self.old_pos
            self.move(self.x() + d.x(), self.y() + d.y()); self.old_pos = e.globalPos()
    def mouseReleaseEvent(self, e): self.old_pos = None
    def closeEvent(self, e):
        self.audio.stop(); self.tts.stop(); super().closeEvent(e)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Consolas", 10))
    gui = SaraGUI()
    gui.show()
    sys.exit(app.exec_())
