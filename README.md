# Gemini Voice Generator

This project creates WAV files from a training script using Gemini TTS. It is designed for slide-deck narration, with each scene kept as a separate audio file.

## Features

- Accepts a transcript as plain text or a markdown file
- Parses scene-based scripts automatically
- Calls the Gemini TTS API with a configurable voice
- Saves one WAV file per scene in a generated folder
- Includes a lightweight FastAPI server for local use

## Quick start

1. Create a virtual environment:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy the environment file and set your API key:
   ```bash
   copy api.env.example api.env
   ```
4. Update `GEMINI_API_KEY` in `api.env` with your Gemini API key. This file is excluded from Git, so create it separately on each machine. Adjust the model or voice there if needed.
5. Run the app:
   ```bash
   uvicorn app.main:app --reload
   ```
6. Open the docs at http://localhost:8000/docs

## Example request

Send a markdown transcript containing headings like `Scene 1`, `Scene 2`, etc.

The API will create files such as:

- `generated_audio/Scene_1.wav`
- `generated_audio/Scene_2.wav`

## Notes

- Gemini model names change over time. If your API uses a newer TTS model than the default, update `GEMINI_MODEL` in `api.env`.
- The project assumes a voice name such as `Zephyr`.
- The example script in `examples/training_script.md` is a sample only.
