import os
from dotenv import load_dotenv
from pathlib import Path

env_file = Path.cwd() / 'api.env'
print(f'Loading from: {env_file}')
print(f'File exists: {env_file.exists()}')

load_dotenv(env_file)

gemini_key = os.getenv("GEMINI_API_KEY")
print(f'GEMINI_API_KEY in os.environ: {bool(gemini_key)}')
if gemini_key:
    print(f'Value preview: {gemini_key[:30]}...')
else:
    print('Value: None')

# Also check what config.py sees
from app.config import API_KEY
print(f'\nFrom app.config:')
print(f'API_KEY present: {bool(API_KEY)}')
if API_KEY:
    print(f'API_KEY preview: {API_KEY[:30]}...')
