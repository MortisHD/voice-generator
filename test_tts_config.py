import os
import sys
from dotenv import load_dotenv
load_dotenv()

out_file = open("test_output.txt", "w", encoding="utf-8")
def log(*args):
    msg = " ".join(str(a) for a in args)
    out_file.write(msg + "\n")
    out_file.flush()

log("Starting tests...")

from app.config import API_KEY
from google import genai
from google.genai import types

log("API_KEY present:", bool(API_KEY))

client = genai.Client(api_key=API_KEY)

log("--- Test 1: Minimal config ---")
try:
    resp = client.models.generate_content(
        model="gemini-2.5-pro-preview-tts",
        contents="Test audio.",
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Zephyr")
                )
            )
        )
    )
    log("Test 1 OK! Candidate length:", len(resp.candidates))
except Exception as e:
    log("Test 1 FAIL:", e)

log("\n--- Test 2: With system_instruction ---")
try:
    resp = client.models.generate_content(
        model="gemini-2.5-pro-preview-tts",
        contents="Test audio.",
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            system_instruction="You are a narrator.",
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Zephyr")
                )
            )
        )
    )
    log("Test 2 OK!")
except Exception as e:
    log("Test 2 FAIL:", e)

log("\n--- Test 3: With temperature=0.3 ---")
try:
    resp = client.models.generate_content(
        model="gemini-2.5-pro-preview-tts",
        contents="Test audio.",
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            temperature=0.3,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Zephyr")
                )
            )
        )
    )
    log("Test 3 OK!")
except Exception as e:
    log("Test 3 FAIL:", e)

log("Finished tests!")
out_file.close()

