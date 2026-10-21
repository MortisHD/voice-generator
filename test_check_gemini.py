import requests
import json

url = "http://localhost:8000/check-gemini"
payload = {}  # Empty payload to test the API_KEY fallback

try:
    response = requests.post(url, json=payload)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")
