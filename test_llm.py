"""Test the improved SARA LLM prompt against multiple command types."""
import requests, json, time, sys

sys.path.insert(0, ".")
# Import the prompt from sara.py by extracting it
from sara import LLMEngine, FallbackParser

engine = LLMEngine()
print(f"Backend: {engine.backend_type} @ {engine.active_host}")
print(f"Model: {engine.model}")
print("="*60)

TEST_COMMANDS = [
    "play lo-fi music on youtube",
    "open vs code",
    "increase volume",
    "play next song",
    "play Shape of You on Spotify",
    "take a screenshot",
    "what time is it",
    "search how to make pasta",
    "reduce brightness",
    "remind me to call mom at 5pm",
    "open chrome",
    "how are you doing today",
    "what's the weather like",
    "show my schedule",
    "check battery status",
]

passed = 0
failed = 0

for cmd in TEST_COMMANDS:
    print(f"\n>>> USER: {cmd}")
    try:
        result = engine.generate(cmd)
        intent = result.get("intent", "?")
        params = result.get("params", {})
        speech = result.get("speech_response", "?")
        print(f"    INTENT: {intent}")
        print(f"    PARAMS: {params}")
        print(f"    SPEECH: {speech}")
        
        # Basic validation
        is_chat_only = (intent == "chat")
        if cmd == "how are you doing today":
            ok = True  # chat is fine for this
        elif "youtube" in cmd and intent != "play_youtube":
            ok = False
        elif "vs code" in cmd and intent != "open_app":
            ok = False
        elif "volume" in cmd and intent != "volume":
            ok = False
        elif "next song" in cmd and intent != "media":
            ok = False
        elif "spotify" in cmd.lower() and intent != "play_music":
            ok = False
        elif "screenshot" in cmd and intent != "screenshot":
            ok = False
        elif "time" in cmd and intent != "time":
            ok = False
        elif "search" in cmd and intent != "web_search":
            ok = False
        elif "brightness" in cmd and intent != "brightness":
            ok = False
        elif "remind" in cmd and intent != "schedule_add":
            ok = False
        elif "chrome" in cmd and intent != "open_app":
            ok = False
        elif "weather" in cmd and intent != "weather":
            ok = False
        elif "schedule" in cmd and intent not in ("schedule_view", "schedule_add"):
            ok = False
        elif "battery" in cmd and intent != "system_info":
            ok = False
        else:
            ok = True
        
        status = "PASS" if ok else "FAIL"
        print(f"    [{status}]")
        if ok:
            passed += 1
        else:
            failed += 1
    except Exception as e:
        print(f"    ERROR: {e}")
        failed += 1

print(f"\n{'='*60}")
print(f"RESULTS: {passed} passed, {failed} failed out of {len(TEST_COMMANDS)}")
print(f"{'='*60}")
