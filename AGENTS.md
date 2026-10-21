# Project Context

## Purpose

This is a local web app that turns training scripts into per-scene WAV narration using the Google Gemini TTS API. The FastAPI backend is in `app/`, and the browser UI is in `app/static/index.html`.

## Architecture

- `app/main.py` defines the FastAPI routes, coordinates parsing and audio generation, and writes generated files and manifests.
- `app/script_parser.py` handles script parsing and normalization. Preserve support for both Markdown-style scene scripts and JSON scene/course payloads.
- `app/tts_service.py` calls Gemini and writes WAV audio. Long scenes are split into chunks.
- `app/config.py` reads `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_VOICE`, and `OUTPUT_DIR` from `api.env` or the process environment. Defaults are defined there.
- Generated audio and per-course generation manifests live under `generated_audio/`; this directory is ignored by Git.

## Setup and Validation

- Follow the setup instructions in `README.md`. Copy `api.env.example` to `api.env` and put the API key only in `api.env`.
- Start the development server with `uvicorn app.main:app --reload`.
- Run the focused parser tests with `python -m pytest tests/test_json_scene_parser.py`.
- `run_server.py` and `start_app.bat` are alternate launchers; check their host and port settings before changing them.

## Working Conventions

- Never commit API keys, `api.env`, generated audio, or generated manifests.
- Preserve existing scene naming and per-scene voice behavior when changing parsing or generation.
- Add or update focused tests when changing script parsing or scene-generation behavior.
- Keep generated files and large source transcripts/bundles out of unrelated code changes.
