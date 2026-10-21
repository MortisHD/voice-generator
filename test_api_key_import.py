#!/usr/bin/env python
"""Direct test of whether API_KEY is loaded in the app module."""
import sys
sys.path.insert(0, '.')

print("Importing app.main...")
from app.main import API_KEY, app
print(f"API_KEY in app.main: {bool(API_KEY)}")
print(f"API_KEY value: {API_KEY}")

if not API_KEY:
    print("\n⚠️  API_KEY is None or empty!")
    print("This is the problem - API_KEY is not loaded when main.py imports it")
else:
    print("\n✅ API_KEY is loaded correctly")
