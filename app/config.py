import os
from pathlib import Path
from dotenv import load_dotenv

# Load api.env from parent directory (project root)
env_file = Path(__file__).parent.parent / "api.env"
load_dotenv(env_file)

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro-preview-tts")  # Pro: works reliably. Try flash (5-10x cheaper) if available on your account
VOICE = os.getenv("GEMINI_VOICE", "Zephyr")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "generated_audio")).resolve()

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
