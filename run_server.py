import os
from pathlib import Path
from dotenv import load_dotenv
import uvicorn

# Load environment variables BEFORE anything else
env_file = Path(__file__).parent / "api.env"
load_dotenv(env_file)
print(f"[run_server] Loaded env from: {env_file}")
print(f"[run_server] GEMINI_API_KEY present: {bool(os.getenv('GEMINI_API_KEY'))}")

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
