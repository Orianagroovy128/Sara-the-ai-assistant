import os

def create_file(filename, content):
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content.strip() + '\n')
    print(f"[+] Created file: {filename}")

print("==================================================")
print(" S.A.R.A Workspace Builder - Initializing...")
print("==================================================")

# 1. Create Directories
os.makedirs("sara_screenshots", exist_ok=True)
print("[+] Created folder: sara_screenshots")

# 2. Define File Contents
reqs = r'''
PyQt5==5.15.9
openai-whisper==20231117
pyaudio==0.2.14
numpy==1.26.2
pyttsx3==2.90
pycaw==20240210
comtypes==1.4.8
psutil==5.9.6
PyAutoGUI==0.9.54
requests==2.31.0
torch==2.1.2
soundfile==0.12.1
'''

config = r'''{
  "assistant_name": "Sara",
  "wake_words": ["hey sara", "sara", "sarah", "hey sarah", "ok sara", "ok sarah", "hello sara", "hello sarah", "hi sara", "hi sarah", "sara wake up", "sarah wake up", "sara are you there", "sarah are you there", "hey sara","thank you", "peace out"],
  "audio": {
    "sample_rate": 16000,
        "chunk_size": 512,
        "silence_threshold_rms": 30,
        "silence_duration_sec": 1.2,
    "wake_word_timeout_sec": 5.0
  },
  "models": {
        "whisper_model": "small.en",
    "ollama_host": "http://localhost:11434",
    "openclaw_host": "http://127.0.0.1:18789",
        "ollama_model": "llama3.2:latest",
        "ollama_fallback_model": "llama3:latest",
        "num_ctx": 4096,
        "num_predict": 256
    },
    "privacy": {
        "local_core_only": true
  },
  "tts": {
    "rate": 165,
    "volume": 1.0,
    "fallback_tld": "com"
  },
  "ui": {
        "fps": 30,
    "opacity": 0.95
  }
}'''

setup_bat = r'''@echo off
echo [DevOps] Initializing S.A.R.A installation for Windows...

:: Check Python
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo Python could not be found. Please install Python 3.9+ and add to PATH.
    pause
    exit /b
)

:: Create Virtual Environment
echo [DevOps] Creating virtual environment...
python -m venv sara_env
call sara_env\Scripts\activate.bat

:: Install Dependencies
echo [DevOps] Installing Python dependencies...
python -m pip install --upgrade pip
pip install pipwin
pipwin install pyaudio
pip install -r requirements.txt

:: Check Ollama
ollama --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [DevOps] WARNING: Ollama is not installed. Please install from https://ollama.com
) ELSE (
    echo [DevOps] Local model check... make sure to run 'ollama run gemma4:e4b' manually first.
)

echo.
echo =======================================================
echo [DevOps] Setup Complete! 
echo To run S.A.R.A, type the following two commands:
echo 1. sara_env\Scripts\activate
echo 2. python sara.py
echo =======================================================
pause
'''

sara_code = r'''
# ==============================================================================
# S.A.R.A (Smart Autonomous Response Assistant)
# Elite Engineering Team Collaboration
# ==============================================================================

import sys
import os
import time
import json
import sqlite3
import threading
import queue
import subprocess
import re
import math
import logging
import datetime
import shutil
import platform
import wave
import struct

# Setting up standard logging for rotating logs and console output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(threadName)s: %(message)s',
    handlers=[
        logging.FileHandler("sara_system.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("SARA")

try:
    from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                                 QHBoxLayout, QLabel, QListWidget, QListWidgetItem)
    from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QPoint
    from PyQt5.QtGui import QPainter, QColor, QPen, QFont, QBrush, QLinearGradient
    
    import whisper
    import pyaudio
    import numpy as np
    import pyttsx3
    import psutil
    import pyautogui
    import requests
except ImportError as e:
    logger.critical(f"Missing dependency: {e}. Please run the setup.bat script.")
    sys.exit(1)

# ==============================================================================
# ENUMS & CONSTANTS
# ==============================================================================
class SaraState:
    IDLE = 0
    LISTENING = 1
    PROCESSING = 2
    SPEAKING = 3
    ERROR = 4

COLORS = {
    SaraState.IDLE: QColor(0, 150, 255, 100),
    SaraState.LISTENING: QColor(0, 243, 255, 255),
    SaraState.PROCESSING: QColor(255, 170, 0, 255),
    SaraState.SPEAKING: QColor(0, 255, 100, 255),
    SaraState.ERROR: QColor(255, 50, 50, 255)
}

# ==============================================================================
# CONFIG & DATABASE
# ==============================================================================
class DatabaseManager:
    def __init__(self, db_path="sara_memory.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._create_tables()

    def _create_tables(self):
        self.cursor.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, role TEXT, content TEXT)")
        self.conn.commit()

    def add_message(self, role, content):
        timestamp = datetime.datetime.now().isoformat()
        self.cursor.execute("INSERT INTO history (timestamp, role, content) VALUES (?, ?, ?)",
                            (timestamp, role, content))
        self.conn.commit()

    def get_context(self, limit=10):
        self.cursor.execute("SELECT role, content FROM history ORDER BY id DESC LIMIT ?", (limit,))
        rows = self.cursor.fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

try:
    with open("sara_config.json", "r") as f:
        CONFIG = json.load(f)
except Exception:
    CONFIG = {
        "wake_words": ["hey sara", "sara", "sarah", "hey sarah"],
        "audio": {
            "sample_rate": 16000,
            "chunk_size": 512,
            "silence_threshold_rms": 30,
            "silence_duration_sec": 1.2
        },
        "models": {
            "whisper_model": "small.en",
            "ollama_host": "http://localhost:11434",
            "openclaw_host": "http://127.0.0.1:18789",
            "ollama_model": "llama3.2:latest",
            "ollama_fallback_model": "llama3:latest",
            "num_ctx": 4096,
            "num_predict": 256
        },
        "privacy": {
            "local_core_only": True
        },
        "tts": {"rate": 165, "volume": 1.0},
        "ui": {"fps": 30}
    }

db = DatabaseManager()

# ==============================================================================
# SYSTEM AUTOMATION
# ==============================================================================
class SystemController:
    @staticmethod
    def open_app(app_name):
        logger.info(f"Opening application: {app_name}")
        try:
            subprocess.Popen(["cmd", "/c", "start", app_name])
            return True, f"Opened {app_name}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def control_volume(action):
        try:
            if action == "mute": pyautogui.press("volumemute")
            elif action == "up": 
                for _ in range(5): pyautogui.press("volumeup")
            elif action == "down": 
                for _ in range(5): pyautogui.press("volumedown")
            return True, "Volume adjusted."
        except Exception as e:
            return False, str(e)

    @staticmethod
    def media_control(action):
        actions = {"play": "playpause", "pause": "playpause", "next": "nexttrack", "prev": "prevtrack"}
        key = actions.get(action, "playpause")
        pyautogui.press(key)
        return True, f"Media {action} executed."

    @staticmethod
    def take_screenshot():
        filename = f"sara_screenshots/screenshot_{int(time.time())}.png"
        pyautogui.screenshot(filename)
        return True, "Screenshot saved."

    @staticmethod
    def type_text(text):
        pyautogui.write(text, interval=0.02)
        return True, "Text typed."

# ==============================================================================
# AI & ML ENGINE
# ==============================================================================
class FallbackParser:
    @staticmethod
    def parse(text):
        text = text.lower()
        if "open" in text: return {"intent": "open_app", "params": {"app": text.split("open")[-1].strip()}, "speech_response": "Opening application."}
        elif "screenshot" in text: return {"intent": "screenshot", "params": {}, "speech_response": "Taking screenshot."}
        return {"intent": "chat", "params": {}, "speech_response": "I heard you, but my neural network is offline."}

class LLMEngine:
    def __init__(self):
        self.host = CONFIG["models"]["ollama_host"]
        self.model = CONFIG["models"]["ollama_model"]
        self.fallback_model = CONFIG["models"].get("ollama_fallback_model", "")
    self.local_core_only = bool(CONFIG.get("privacy", {}).get("local_core_only", True))
                self.system_prompt = """You are S.A.R.A, a locally running, privacy-first assistant with a JARVIS-like voice.
Tone: calm, precise, supportive, lightly witty. Keep responses short (1-2 sentences).
You must ALWAYS respond in valid JSON format.
Format:
{
    "intent": "open_app" | "volume" | "media" | "type" | "screenshot" | "chat",
    "params": {"app": "name", "action": "up/down/mute/play/pause", "text": "text to type"},
    "speech_response": "A short, natural, emotionally aware response to speak to the user."
}"""

    def _is_local_host(self):
        host = (self.host or "").lower()
        return "localhost" in host or "127.0.0.1" in host

    def _extract_json_payload(self, message_text):
        payload_text = (message_text or "").strip()
        try:
            return json.loads(payload_text)
        except Exception:
            m = re.search(r"\{[\s\S]*\}", payload_text)
            if not m:
                raise ValueError("No JSON object found in model response")
            return json.loads(m.group(0))

    def _has_required_keys(self, data):
        req = {"intent", "params", "speech_response"}
        return isinstance(data, dict) and req.issubset(set(data.keys()))

    def _normalize_response(self, data):
        if not isinstance(data, dict):
            return None
        intent = data.get("intent", "chat")
        allowed = {"open_app", "volume", "media", "type", "screenshot", "chat"}
        if intent not in allowed:
            intent = "chat"
        params = data.get("params") if isinstance(data.get("params"), dict) else {}
        speech = data.get("speech_response")
        if isinstance(speech, dict):
            if isinstance(speech.get("text"), str):
                speech = speech.get("text")
            elif isinstance(speech.get("outputSpeech", {}).get("text"), str):
                speech = speech.get("outputSpeech", {}).get("text")
        if not isinstance(speech, str):
            speech = json.dumps(speech) if speech is not None else ""
        speech = speech.strip()
        if not speech:
            return None
        return {"intent": intent, "params": params, "speech_response": speech}

    def _memory_error_message(self):
        if self.fallback_model:
            return {
                "intent": "chat",
                "params": {},
                "speech_response": f"Your selected model needs more RAM. Switching to local fallback model {self.fallback_model}."
            }
        return {
            "intent": "chat",
            "params": {},
            "speech_response": "Your selected model needs more RAM than available. Please use a smaller local model."
        }

    def generate_response(self, user_text):
        if self.local_core_only and not self._is_local_host():
            logger.error(f"Privacy mode blocked non-local host: {self.host}")
            return FallbackParser.parse(user_text)

        logger.info(f"Local core mode: {'ON' if self.local_core_only else 'OFF'} | Ollama host: {self.host} | model: {self.model}")
        context = db.get_context(limit=6)
        messages = [{"role": "system", "content": self.system_prompt}]
        for c in context:
            role = str(c.get("role", "user")).lower()
            if role not in ("system", "user", "assistant"):
                role = "user"
            messages.append({"role": role, "content": str(c.get("content", ""))})
        messages.append({"role": "user", "content": user_text})

        active_model = self.model

        try:
            response = requests.post(
                f"{self.host}/api/chat",
                json={"model": active_model, "messages": messages, "stream": False, "format": "json"},
                timeout=(3, 30)
            )
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "{}")
            data = self._extract_json_payload(content)
            if not self._has_required_keys(data):
                raise ValueError("Model JSON missing required keys")
            data = self._normalize_response(data)
            if not data:
                raise ValueError("Model JSON missing required keys")
            db.add_message("assistant", data.get("speech_response", "Done."))
            return data
        except requests.exceptions.HTTPError as e:
            err_text = (e.response.text if e.response is not None else str(e)).lower()
            if "requires more system memory" in err_text:
                logger.error(f"LLM memory error for model {active_model}: {err_text}")
                if self.fallback_model:
                    logger.info(f"Trying fallback model: {self.fallback_model}")
                    active_model = self.fallback_model
                else:
                    return self._memory_error_message()
            else:
                logger.error(f"LLM Error (attempt 1): {e}")
        except requests.exceptions.RequestException as e:
            logger.error(f"LLM request error (attempt 1): {e}")
            if self.fallback_model and active_model != self.fallback_model:
                logger.info(f"Retrying with fallback model after request failure: {self.fallback_model}")
                active_model = self.fallback_model
        except Exception as e:
            logger.error(f"LLM Error (attempt 1): {e}")

        strict_prompt = (
            "You are S.A.R.A, a local assistant. "
            "Return ONLY a valid JSON object with keys: intent, params, speech_response. "
            "Use intent 'chat' when unsure. "
            "speech_response must be a short plain string."
        )
        strict_messages = [
            {"role": "system", "content": strict_prompt},
            {"role": "user", "content": user_text}
        ]

        try:
            retry_response = requests.post(
                f"{self.host}/api/chat",
                json={"model": active_model, "messages": strict_messages, "stream": False},
                timeout=(3, 30)
            )
            retry_response.raise_for_status()
            retry_content = retry_response.json().get("message", {}).get("content", "{}")
            try:
                retry_data = self._extract_json_payload(retry_content)
                if not self._has_required_keys(retry_data):
                    raise ValueError("Retry JSON missing required keys")
                retry_data = self._normalize_response(retry_data)
                if not retry_data:
                    raise ValueError("Retry JSON missing required keys")
            except Exception:
                text_reply = str(retry_content or "").strip()
                if text_reply:
                    retry_data = {"intent": "chat", "params": {}, "speech_response": text_reply}
                else:
                    retry_data = {"intent": "chat", "params": {}, "speech_response": "I did not receive a usable reply from the local model."}
            db.add_message("assistant", retry_data.get("speech_response", "Done."))
            return retry_data
        except Exception as e:
            logger.error(f"LLM Error (attempt 2): {e}")
            return FallbackParser.parse(user_text)

# ==============================================================================
# BACKGROUND THREADS
# ==============================================================================
class TTSThread(QThread):
    finished_speaking = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self.queue = queue.Queue()
        self.running = True

    def run(self):
        engine = pyttsx3.init()
        engine.setProperty('rate', CONFIG["tts"]["rate"])
        for voice in engine.getProperty('voices'):
            if "david" in voice.name.lower() or "zira" in voice.name.lower():
                engine.setProperty('voice', voice.id)
                break

        while self.running:
            try:
                text = self.queue.get(timeout=0.5)
                if text:
                    engine.say(text)
                    engine.runAndWait()
                    self.finished_speaking.emit()
                    self.queue.task_done()
            except queue.Empty: continue

    def speak(self, text): self.queue.put(text)
    def stop(self): self.running = False

class AudioListenerThread(QThread):
    wake_word_detected = pyqtSignal()
    command_recorded = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.running = True
        self.pa = pyaudio.PyAudio()
        self.rate = CONFIG["audio"]["sample_rate"]
        self.chunk = CONFIG["audio"]["chunk_size"]
        self.active_listening = False
        logger.info("Loading Whisper Model... This may take a moment.")
        self.whisper_model = whisper.load_model(CONFIG["models"]["whisper_model"])
        logger.info("Whisper Ready.")

    def run(self):
        stream = self.pa.open(format=pyaudio.paInt16, channels=1, rate=self.rate, input=True, frames_per_buffer=self.chunk)
        
        while self.running:
            if not self.active_listening:
                frames = [stream.read(self.chunk, exception_on_overflow=False) for _ in range(int(self.rate / self.chunk * 1.5))]
                rms = np.sqrt(np.mean(np.square(np.frombuffer(b''.join(frames), dtype=np.int16).astype(np.float32))))
                
                if rms > CONFIG["audio"]["silence_threshold_rms"]:
                    text = self._transcribe_frames(frames).lower()
                    if any(ww in text for ww in CONFIG["wake_words"]):
                        self.wake_word_detected.emit()
                        self.active_listening = True
            else:
                frames, silence_frames = [], 0
                max_silence = int(self.rate / self.chunk * CONFIG["audio"]["silence_duration_sec"])
                
                while self.running and self.active_listening:
                    data = stream.read(self.chunk, exception_on_overflow=False)
                    frames.append(data)
                    rms = np.sqrt(np.mean(np.square(np.frombuffer(data, dtype=np.int16).astype(np.float32))))
                    
                    silence_frames = silence_frames + 1 if rms < CONFIG["audio"]["silence_threshold_rms"] else 0
                    if silence_frames > max_silence: break
                
                if len(frames) > max_silence:
                    wf = wave.open("temp_cmd.wav", 'wb')
                    wf.setnchannels(1)
                    wf.setsampwidth(self.pa.get_sample_size(pyaudio.paInt16))
                    wf.setframerate(self.rate)
                    wf.writeframes(b''.join(frames))
                    wf.close()
                    self.command_recorded.emit("temp_cmd.wav")
                self.active_listening = False

        stream.stop_stream()
        stream.close()
        self.pa.terminate()

    def _transcribe_frames(self, frames):
        audio_np = np.frombuffer(b''.join(frames), dtype=np.int16).astype(np.float32) / 32768.0
        return self.whisper_model.transcribe(audio_np, fp16=False, language='en')["text"].strip()
        
    def transcribe_file(self, filepath):
        return self.whisper_model.transcribe(filepath, fp16=False, language='en')["text"].strip()

    def stop(self): self.running = False

class MainLogicController(QThread):
    state_changed = pyqtSignal(int)
    log_updated = pyqtSignal(str, str)
    
    def __init__(self, audio, tts):
        super().__init__()
        self.audio, self.tts, self.llm = audio, tts, LLMEngine()
        self.audio.wake_word_detected.connect(self.on_wake_word)
        self.audio.command_recorded.connect(self.on_command)
        self.tts.finished_speaking.connect(lambda: self.state_changed.emit(SaraState.IDLE))
        
    def on_wake_word(self):
        self.state_changed.emit(SaraState.LISTENING)
        self.tts.speak("Yes?")

    def on_command(self, filepath):
        self.state_changed.emit(SaraState.PROCESSING)
        threading.Thread(target=self._process, args=(filepath,)).start()

    def _process(self, filepath):
        text = self.audio.transcribe_file(filepath)
        if not text:
            self.state_changed.emit(SaraState.IDLE)
            return
            
        self.log_updated.emit("USER", text)
        db.add_message("user", text)
        data = self.llm.generate_response(text)
        
        intent, params = data.get("intent"), data.get("params", {})
        speech = data.get("speech_response", "Command executed.")
        
        try:
            if intent == "open_app": SystemController.open_app(params.get("app", ""))
            elif intent == "volume": SystemController.control_volume(params.get("action", ""))
            elif intent == "media": SystemController.media_control(params.get("action", "play"))
            elif intent == "screenshot": SystemController.take_screenshot()
            elif intent == "type": SystemController.type_text(params.get("text", ""))
        except Exception:
            speech = "I encountered an error."
            self.state_changed.emit(SaraState.ERROR)
            
        self.log_updated.emit("SARA", speech)
        self.state_changed.emit(SaraState.SPEAKING)
        self.tts.speak(speech)

# ==============================================================================
# FRONTEND / GUI
# ==============================================================================
class ArcReactorWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(300, 300)
        self.state = SaraState.IDLE
        self.phase = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_anim)
        self.timer.start(int(1000 / CONFIG["ui"]["fps"]))

    def update_anim(self):
        speed = {SaraState.IDLE: 0.05, SaraState.LISTENING: 0.15, SaraState.PROCESSING: 0.2, SaraState.SPEAKING: 0.1, SaraState.ERROR: 0.05}
        self.phase += speed.get(self.state, 0.05)
        self.update()

    def set_state(self, state): self.state = state

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center, max_r = self.rect().center(), min(self.rect().width(), self.rect().height()) / 2 - 10
        base_col = COLORS.get(self.state, COLORS[SaraState.IDLE])
        pulse = (math.sin(self.phase) + 1) / 2 
        
        # Glow
        glow_r = max_r * (0.8 + 0.2 * pulse)
        grad = QLinearGradient(center.x() - glow_r, center.y() - glow_r, center.x() + glow_r, center.y() + glow_r)
        c1 = QColor(base_col)
        c1.setAlpha(int(100 * pulse))
        grad.setColorAt(0, c1)
        grad.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(center, int(glow_r), int(glow_r))
        
        # Rings
        pen = QPen(base_col, 3)
        pen.setStyle(Qt.DashLine if self.state == SaraState.PROCESSING else Qt.SolidLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, int(max_r * 0.7), int(max_r * 0.7))
        
        # Core
        core_r = max_r * 0.4 * (0.9 + 0.1 * pulse)
        core_c = QColor(base_col)
        core_c.setAlpha(200)
        painter.setBrush(QBrush(core_c))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(center, int(core_r), int(core_r))

class SaraGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("S.A.R.A")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(700, 350)
        
        main_w = QWidget()
        main_l = QHBoxLayout(main_w)
        main_w.setStyleSheet("QWidget { background-color: rgba(13, 13, 13, 240); border-radius: 15px; border: 1px solid #00f3ff; }")
        
        self.arc = ArcReactorWidget()
        main_l.addWidget(self.arc, 1)
        
        self.chat = QListWidget()
        self.chat.setStyleSheet("QListWidget { background: transparent; border: none; color: #00f3ff; font-family: 'Consolas'; font-size: 13px; }")
        main_l.addWidget(self.chat, 1)
        self.setCentralWidget(main_w)
        
        self.tts, self.audio = TTSThread(), AudioListenerThread()
        self.logic = MainLogicController(self.audio, self.tts)
        
        self.logic.state_changed.connect(self.arc.set_state)
        self.logic.log_updated.connect(self.add_log)
        
        self.tts.start(); self.audio.start()
        QTimer.singleShot(1500, lambda: self.tts.speak("Hello. I am Sara. Ready."))

    def add_log(self, role, msg):
        item = QListWidgetItem(f"[{role}] {msg}")
        item.setForeground(QColor(0, 255, 100) if role == "SARA" else QColor(255, 255, 255))
        self.chat.addItem(item)
        self.chat.scrollToBottom()

    def mousePressEvent(self, e): self.old_pos = e.globalPos() if e.button() == Qt.LeftButton else None
    def mouseMoveEvent(self, e):
        if hasattr(self, 'old_pos') and self.old_pos:
            self.move(self.x() + (e.globalPos() - self.old_pos).x(), self.y() + (e.globalPos() - self.old_pos).y())
            self.old_pos = e.globalPos()
    def mouseReleaseEvent(self, e): self.old_pos = None

    def closeEvent(self, e):
        self.audio.stop(); self.tts.stop()
        super().closeEvent(e)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    gui = SaraGUI()
    gui.show()
    sys.exit(app.exec_())
'''

# 3. Write all files
create_file("requirements.txt", reqs)
create_file("sara_config.json", config)
create_file("setup.bat", setup_bat)
create_file("sara.py", sara_code)

print("==================================================")
print(" Success! All files generated in this folder.")
print("==================================================")
print("NEXT STEPS:")
print("1. In your VS Code terminal, run the setup script by typing:")
print("   .\\setup.bat")
print("2. Wait for it to finish installing dependencies.")
print("3. Launch S.A.R.A with:")
print("   python sara.py")