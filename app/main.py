import zipfile
import json
import os
import sys
import hashlib
import io
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google import genai

from app.config import OUTPUT_DIR, API_KEY, MODEL, VOICE
from app.script_parser import (
    read_script_file,
    parse_script_text,
    parse_course_script,
    extract_script_settings,
)
from app.tts_service import GeminiTTSService

app = FastAPI(title="Gemini Voice Generator")
app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=OUTPUT_DIR), name="audio")


def gemini_service_error(detail: str, context: str) -> HTTPException:
    detail_upper = detail.upper()
    detail_lower = detail.lower()

    if any(marker in detail_upper for marker in ("500", "503", "UNAVAILABLE", "INTERNAL", "SERVICEUNAVAILABLE")):
        return HTTPException(
            status_code=503,
            detail=f"Google Gemini is temporarily unavailable. Please try again in a few minutes. Details: {detail}",
        )
    if "429" in detail_upper or "RESOURCE_EXHAUSTED" in detail_upper:
        return HTTPException(
            status_code=429,
            detail=f"Rate limited by Google Gemini. Please wait before trying again. Details: {detail}",
        )
    if "API KEY" in detail_upper or "AUTH" in detail_upper or "FORBIDDEN" in detail_upper:
        return HTTPException(
            status_code=401,
            detail=f"Gemini authentication failed. Check the API key and project access. Details: {detail}",
        )
    if "not found" in detail_lower or "unsupported" in detail_lower:
        return HTTPException(
            status_code=400,
            detail=f"Unsupported or unavailable model/voice combination. {context}. Details: {detail}",
        )
    return HTTPException(status_code=500, detail=f"Gemini {context} failed. Details: {detail}")


def course_output_dir(course_name: str | None) -> Path:
    course_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(course_name or "unsorted").stem).strip("_")
    output_dir = OUTPUT_DIR / (course_slug or "unsorted")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def load_manifest(output_dir: Path) -> dict:
    try:
        return json.loads((output_dir / "generation_manifest.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"scenes": {}}


def save_manifest(output_dir: Path, manifest: dict) -> None:
    manifest_path = output_dir / "generation_manifest.json"
    temporary_path = manifest_path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary_path.replace(manifest_path)


def scene_fingerprint(scene_text: str, model: str, voice: str, system_instruction: str | None) -> str:
    effective_input = json.dumps(
        {
            "text": scene_text,
            "model": model,
            "voice": voice,
            "system_instruction": system_instruction or "",
        },
        sort_keys=True,
    )
    return hashlib.sha256(effective_input.encode("utf-8")).hexdigest()


def generate_scene_file(
    service: GeminiTTSService,
    scene_name: str,
    scene_text: str,
    output_dir: Path,
    *,
    scene_voice: str | None = None,
    scene_system_instruction: str | None = None,
    scene_group: str | None = None,
    force_regenerate: bool = False,
) -> tuple[str, bool]:
    effective_voice = scene_voice or service.voice
    effective_system_instruction = scene_system_instruction if scene_system_instruction is not None else service.system_instruction
    fingerprint = scene_fingerprint(scene_text, service.model, effective_voice, effective_system_instruction)
    manifest = load_manifest(output_dir)
    record = manifest.setdefault("scenes", {}).get(scene_name, {})
    target_dir = (output_dir / scene_group) if scene_group else output_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    output_path = target_dir / f"{scene_name}_{fingerprint[:12]}.wav"

    if not force_regenerate and record.get("fingerprint") == fingerprint and output_path.is_file():
        return str(output_path), True

    generated_path = service.generate_wav(
        scene_text,
        output_path,
        voice_override=scene_voice,
        system_instruction_override=scene_system_instruction,
    )
    manifest["scenes"][scene_name] = {
        "file": Path(generated_path).relative_to(output_dir).as_posix(),
        "fingerprint": fingerprint,
        "model": service.model,
        "voice": effective_voice,
        "word_count": len(scene_text.split()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_manifest(output_dir, manifest)
    return generated_path, False


def resolve_scene_voice(scene: tuple, scene_voice_overrides: dict, default_voice: str | None) -> str | None:
    """Resolve the effective voice for a scene: manual override > embedded script voice > default."""
    scene_name = scene[0]
    embedded_voice = scene[2] if len(scene) > 2 else None
    return (scene_voice_overrides or {}).get(scene_name) or embedded_voice or default_voice


_CLIP_SCENE_GROUP_PATTERN = re.compile(r"^(.+)_[A-Za-z]\d+$")


def derive_scene_group(scene_name: str) -> str | None:
    """For two-voice clip names like 'screen_1_A1', return the shared screen group 'screen_1'."""
    match = _CLIP_SCENE_GROUP_PATTERN.match(scene_name)
    return match.group(1) if match else None


def select_scenes(
    scenes: list[tuple[str, str]],
    scene_name: str | None,
    scene_index: object,
    scene_names: list[str] | None = None,
) -> list[tuple[str, str]]:
    if scene_names:
        name_set = set(scene_names)
        scenes = [scene for scene in scenes if scene[0] in name_set]
    elif scene_name:
        scenes = [scene for scene in scenes if scene[0] == scene_name]
    elif scene_index is not None:
        try:
            index = int(scene_index)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="scene_index must be an integer.")
        if 0 <= index < len(scenes):
            scenes = [scenes[index]]
        else:
            raise HTTPException(status_code=400, detail="scene_index is out of range.")
    if not scenes:
        raise HTTPException(status_code=400, detail="No matching scene was found.")
    return scenes


def resolve_generation_settings(payload: dict) -> tuple[str, str, str | None, object]:
    script_settings = extract_script_settings(payload["text"])
    model = payload.get("model") or script_settings.get("model") or MODEL
    voice = payload.get("voice") or script_settings.get("voice") or VOICE
    system_instruction = payload.get("system_instruction") or script_settings.get("system_instruction")
    temperature = payload.get("temperature")
    if temperature is None:
        temperature = script_settings.get("temperature")
    return model, voice, system_instruction, temperature


def read_zip_script_members(raw_bytes: bytes) -> list[tuple[str, str]]:
    """Read every .md/.markdown/.txt/.json entry from an uploaded zip.

    Returns a list of (course_name, text) pairs, one per script file, named
    after the file's stem and ordered as they appear in the archive.
    """
    courses = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if "__MACOSX" in name or Path(name).name.startswith("."):
                    continue
                if not name.lower().endswith((".md", ".markdown", ".txt", ".json")):
                    continue
                with zf.open(info) as fh:
                    try:
                        text = fh.read().decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                if text.strip():
                    courses.append((Path(name).stem, text))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid zip archive.")

    if not courses:
        raise HTTPException(status_code=400, detail="No .md/.txt/.json script files were found inside the zip.")

    return courses


def collect_course_wav_files(output_dir: Path) -> list[Path]:
    manifest = load_manifest(output_dir).get("scenes", {})
    wav_files = []
    for record in manifest.values():
        filename = record.get("file") if isinstance(record, dict) else None
        wav_file = output_dir / filename if filename else None
        if wav_file and wav_file.is_file() and wav_file.suffix.lower() == ".wav":
            wav_files.append(wav_file)

    if not wav_files:
        wav_files = sorted(wav_file for wav_file in output_dir.rglob("*.wav") if wav_file.is_file())

    return wav_files


def build_zip_archive(wav_files: list[Path], base_dir: Path | None = None) -> io.BytesIO:
    """Zip the given wav files, preserving their folder structure relative to base_dir."""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for wav_file in wav_files:
            arcname = wav_file.relative_to(base_dir) if base_dir else wav_file.name
            zf.write(wav_file, arcname=str(arcname))
    archive.seek(0)
    return archive


@app.get("/test-api-key")
def test_api_key():
    return {
        "api_key_present": bool(API_KEY),
        "api_key_value": API_KEY,
        "api_key_preview": f"{API_KEY[:20]}..." if API_KEY else None
    }


@app.get("/debug-config")
def debug_config():
    return {
        "api_key_present": bool(API_KEY),
        "api_key_preview": f"{API_KEY[:20]}..." if API_KEY else None,
        "model": MODEL,
        "voice": VOICE,
        "output_dir": str(OUTPUT_DIR)
    }


@app.post("/restart")
def restart_server():
    """Restart the server by re-executing the Python process."""
    try:
        # Return success response first
        response = {"status": "ok", "message": "Server restarting..."}
        
        # Restart the process in background
        import threading
        def restart():
            import time
            time.sleep(0.5)  # Give time for response to send
            os.execv(sys.executable, [sys.executable] + sys.argv)
        
        thread = threading.Thread(target=restart, daemon=True)
        thread.start()
        
        return response
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Server restart failed: {exc}")



@app.get("/")
def root():
    return FileResponse(Path(__file__).resolve().parent / "static" / "index.html")


@app.get("/list-audio")
def list_audio(course_name: str | None = None):
    output_dir = course_output_dir(course_name)
    wav_files = sorted(output_dir.rglob("*.wav"))
    return {"files": [wav_file.relative_to(OUTPUT_DIR).as_posix() for wav_file in wav_files]}


@app.get("/list-courses")
def list_courses():
    courses = sorted(
        directory.name
        for directory in OUTPUT_DIR.iterdir()
        if directory.is_dir() and any(directory.rglob("*.wav"))
    )
    return {"courses": courses}


@app.post("/clear-course-audio")
def clear_course_audio(payload: dict):
    output_dir = course_output_dir(payload.get("course_name"))
    deleted = 0
    for wav_file in output_dir.rglob("*.wav"):
        wav_file.unlink()
        deleted += 1
    # Remove now-empty scene-group subfolders left behind by two-voice courses.
    for subdir in sorted(output_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if subdir.is_dir() and not any(subdir.iterdir()):
            subdir.rmdir()
    manifest_path = output_dir / "generation_manifest.json"
    if manifest_path.exists():
        manifest_path.unlink()
    return {"status": "ok", "deleted": deleted, "course": output_dir.name}


@app.post("/parse-script")
def parse_script_endpoint(payload: dict):
    text = payload.get("text", "")
    scenes = parse_script_text(text)
    settings = extract_script_settings(text)
    return {
        "status": "ok",
        "scenes": [s[0] for s in scenes],
        "scene_voices": [(s[2] if len(s) > 2 else None) for s in scenes],
        "scene_count": len(scenes),
        "settings": settings,
    }


@app.post("/preflight")
def preflight(payload: dict):
    text = payload.get("text")
    if not text or not str(text).strip():
        raise HTTPException(status_code=400, detail="Text is required.")

    model, voice, system_instruction, _ = resolve_generation_settings(payload)
    scene_voice_overrides = payload.get("scene_voices") or {}
    scenes = select_scenes(
        parse_script_text(text),
        payload.get("scene_name"),
        payload.get("scene_index"),
        payload.get("scene_names"),
    )
    output_dir = course_output_dir(payload.get("course_name"))
    manifest = load_manifest(output_dir).get("scenes", {})
    scene_summaries = []
    for scene in scenes:
        scene_name, scene_text = scene[0], scene[1]
        effective_voice = resolve_scene_voice(scene, scene_voice_overrides, voice)
        fingerprint = scene_fingerprint(scene_text, model, effective_voice, system_instruction)
        output_path = output_dir / f"{scene_name}_{fingerprint[:12]}.wav"
        scene_summaries.append(
            {
                "name": scene_name,
                "voice": effective_voice,
                "words": len(scene_text.split()),
                "estimated_seconds": round(len(scene_text.split()) / 150 * 60),
                "cached": manifest.get(scene_name, {}).get("fingerprint") == fingerprint and output_path.is_file(),
                "will_chunk": len(scene_text.split()) > 400,
            }
        )
    return {
        "status": "ok",
        "model": model,
        "voice": voice,
        "scene_count": len(scene_summaries),
        "total_words": sum(scene["words"] for scene in scene_summaries),
        "estimated_seconds": sum(scene["estimated_seconds"] for scene in scene_summaries),
        "cached_scene_count": sum(scene["cached"] for scene in scene_summaries),
        "scenes": scene_summaries,
    }


@app.post("/generate-from-text")
def generate_from_text(payload: dict):
    text = payload.get("text")
    if not text or not str(text).strip():
        raise HTTPException(status_code=400, detail="Text is required.")

    api_key = payload.get("api_key") or API_KEY
    model = payload.get("model") or MODEL
    voice = payload.get("voice") or VOICE
    scene_name = payload.get("scene_name")
    scene_index = payload.get("scene_index")
    system_instruction = payload.get("system_instruction")
    temperature = payload.get("temperature")

    script_settings = extract_script_settings(text)
    if not payload.get("model"):
        model = script_settings.get("model") or model
    if not payload.get("voice"):
        voice = script_settings.get("voice") or voice
    if not system_instruction:
        system_instruction = script_settings.get("system_instruction")
    if temperature is None:
        temperature = script_settings.get("temperature")

    try:
        service = GeminiTTSService(
            api_key=api_key,
            model=model,
            voice=voice,
            temperature=temperature,
            system_instruction=system_instruction,
        )
        scenes = parse_script_text(text)
        if not scenes:
            raise HTTPException(status_code=400, detail="No scenes were found in the transcript.")

        scenes = select_scenes(scenes, scene_name, scene_index, payload.get("scene_names"))

        scene_voice_overrides = payload.get("scene_voices") or {}
        scenes = [
            (scene[0], scene[1], resolve_scene_voice(scene, scene_voice_overrides, None))
            for scene in scenes
        ]

        generated = service.generate_scene_files(scenes)
        return {"status": "ok", "files": generated, "output_dir": str(OUTPUT_DIR)}
    except HTTPException:
        raise
    except Exception as exc:
        raise gemini_service_error(str(exc), f"generation error for model={model} voice={voice}")


@app.post("/generate-from-text-stream")
def generate_from_text_stream(payload: dict):
    """Streaming endpoint that returns progress updates as it generates files."""
    text = payload.get("text")
    if not text or not str(text).strip():
        raise HTTPException(status_code=400, detail="Text is required.")

    api_key = payload.get("api_key") or API_KEY
    scene_name_filter = payload.get("scene_name")
    scene_index_filter = payload.get("scene_index")
    scene_names_filter = payload.get("scene_names")
    force_regenerate = bool(payload.get("force_regenerate"))
    scene_voice_overrides = payload.get("scene_voices") or {}
    output_dir = course_output_dir(payload.get("course_name"))
    model, voice, system_instruction, temperature = resolve_generation_settings(payload)

    def progress_generator():
        try:
            service = GeminiTTSService(
                api_key=api_key,
                model=model,
                voice=voice,
                temperature=temperature,
                system_instruction=system_instruction,
            )
            scenes = parse_script_text(text)
            if not scenes:
                yield json.dumps({"error": "No scenes were found in the transcript."}) + "\n"
                return

            scenes = select_scenes(scenes, scene_name_filter, scene_index_filter, scene_names_filter)

            total = len(scenes)
            generated = []

            for idx, scene in enumerate(scenes, start=1):
                scene_name_item, scene_text = scene[0], scene[1]
                effective_voice = resolve_scene_voice(scene, scene_voice_overrides, voice)
                yield json.dumps({
                    "status": "generating",
                    "current": idx,
                    "total": total,
                    "scene": scene_name_item,
                    "voice": effective_voice,
                    "message": f"Generating {scene_name_item} ({idx} of {total}) with voice {effective_voice}..."
                }) + "\n"
                
                try:
                    result, reused = generate_scene_file(
                        service,
                        scene_name_item,
                        scene_text,
                        output_dir,
                        scene_voice=effective_voice,
                        force_regenerate=force_regenerate,
                    )
                    relative_result = Path(result).relative_to(OUTPUT_DIR).as_posix()
                    generated.append(relative_result)
                    
                    yield json.dumps({
                        "status": "completed",
                        "current": idx,
                        "total": total,
                        "scene": scene_name_item,
                        "voice": effective_voice,
                        "file": relative_result,
                        "reused": reused,
                        "message": f"Reused {scene_name_item}" if reused else f"Completed {scene_name_item}",
                    }) + "\n"
                except Exception as e:
                    error_str = str(e)
                    friendly_message = error_str
                    
                    # Provide helpful error messages
                    if "500" in error_str or "INTERNAL" in error_str:
                        friendly_message = f"Google API server error (500). The API is experiencing issues. Waiting longer before retry may help. Try again in a moment."
                    elif "503" in error_str or "UNAVAILABLE" in error_str:
                        friendly_message = f"Google API is temporarily unavailable (503). Try again in a moment."
                    elif "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                        friendly_message = f"Rate limited. Too many requests to Google API. Wait a moment before trying again."
                    
                    yield json.dumps({
                        "status": "error",
                        "current": idx,
                        "total": total,
                        "scene": scene_name_item,
                        "error": friendly_message,
                        "message": f"Failed to generate {scene_name_item}"
                    }) + "\n"
                    return
            
            yield json.dumps({
                "status": "done",
                "files": generated,
                "output_dir": str(output_dir)
            }) + "\n"
        except Exception as exc:
            detail = str(exc)
            error_msg = detail
            if "API key" in detail or "auth" in detail.lower():
                error_msg = f"Gemini authentication failed. Check your API key."
            elif "not found" in detail.lower() or "unsupported" in detail.lower():
                error_msg = f"Unsupported model/voice combination."
            elif "500" in detail or "INTERNAL" in detail:
                error_msg = f"Google API returned a 500 error (server issue). This is temporary. Please wait a moment and try again."
            elif "503" in detail or "UNAVAILABLE" in detail:
                error_msg = f"Google API is temporarily unavailable (503). Please wait and try again."
            elif "429" in detail or "RESOURCE_EXHAUSTED" in detail:
                error_msg = f"Rate limited by Google API. Please wait a few moments before trying again."
            
            yield json.dumps({
                "status": "error",
                "error": error_msg,
                "details": detail
            }) + "\n"

    return StreamingResponse(progress_generator(), media_type="text/event-stream")


@app.post("/parse-course-bundle")
async def parse_course_bundle(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="A .zip file is required.")

    raw_bytes = await file.read()
    course_texts = read_zip_script_members(raw_bytes)

    courses = []
    for course_name, text in course_texts:
        records, voice_roles = parse_course_script(text)
        if not records:
            continue
        script_settings = extract_script_settings(text)
        courses.append(
            {
                "name": course_name,
                "format": "two_voice" if voice_roles else "standard",
                "voice_roles": {letter: persona for letter, (persona, _instruction) in voice_roles.items()},
                "record_count": len(records),
                "records": [
                    {"name": record[0], "voice": record[2] if len(record) > 2 else None}
                    for record in records
                ],
                "settings": script_settings,
            }
        )

    if not courses:
        raise HTTPException(status_code=400, detail="No renderable scenes were found in any script file inside the zip.")

    return {"status": "ok", "course_count": len(courses), "courses": courses}


@app.post("/generate-course-bundle-stream")
async def generate_course_bundle_stream(
    file: UploadFile = File(...),
    api_key: str | None = Form(default=None),
    model: str | None = Form(default=None),
    voice: str | None = Form(default=None),
    temperature: float | None = Form(default=None),
    system_instruction: str | None = Form(default=None),
    voice_map: str | None = Form(default=None),
    selection: str | None = Form(default=None),
    force_regenerate: bool = Form(default=False),
):
    """Batch-render every script file found in an uploaded zip, one output folder per course.

    'selection' is an optional JSON object mapping course_name -> list of scene/clip
    names to render for that course. Courses missing from the map are skipped entirely,
    letting the caller deselect whole courses or individual scenes before rendering.
    """
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="A .zip file is required.")

    raw_bytes = await file.read()
    course_texts = read_zip_script_members(raw_bytes)

    try:
        voice_map_dict = json.loads(voice_map) if voice_map else {}
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail='voice_map must be a JSON object, e.g. {"A": "Kore", "B": "Puck"}.')

    try:
        selection_dict = json.loads(selection) if selection else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="selection must be a JSON object mapping course names to lists of scene names.")

    resolved_api_key = api_key or API_KEY

    def progress_generator():
        try:
            courses = []
            for course_name, text in course_texts:
                if selection_dict is not None and course_name not in selection_dict:
                    continue

                records, voice_roles = parse_course_script(text)
                if not records:
                    continue

                if selection_dict is not None:
                    selected_names = set(selection_dict.get(course_name) or [])
                    records = [record for record in records if record[0] in selected_names]
                if not records:
                    continue

                course_model, course_voice, course_system_instruction, course_temperature = resolve_generation_settings(
                    {"text": text, "model": model, "voice": voice, "temperature": temperature, "system_instruction": system_instruction}
                )
                courses.append((course_name, records, voice_roles, course_model, course_voice, course_system_instruction, course_temperature))

            if not courses:
                yield json.dumps({"error": "No renderable scenes were found in any script file inside the zip."}) + "\n"
                return

            total_scenes = sum(len(records) for _, records, *_ in courses)
            done_scenes = 0
            generated_courses = {}

            for course_name, records, voice_roles, course_model, course_voice, course_system_instruction, course_temperature in courses:
                output_dir = course_output_dir(course_name)
                generated_courses[course_name] = []

                try:
                    service = GeminiTTSService(
                        api_key=resolved_api_key,
                        model=course_model,
                        voice=course_voice,
                        temperature=course_temperature,
                        system_instruction=course_system_instruction,
                    )
                except Exception as exc:
                    yield json.dumps({
                        "status": "error",
                        "course": course_name,
                        "error": str(exc),
                        "message": f"[{course_name}] Could not start generation: {exc}",
                    }) + "\n"
                    return

                for record in records:
                    scene_name, scene_text = record[0], record[1]
                    role_or_voice = record[2] if len(record) > 2 else None
                    embedded_instruction = record[3] if len(record) > 3 else None

                    if voice_roles and role_or_voice in voice_roles:
                        effective_voice = voice_map_dict.get(role_or_voice) or course_voice
                    else:
                        effective_voice = role_or_voice or course_voice
                    effective_instruction = embedded_instruction if embedded_instruction is not None else course_system_instruction
                    scene_group = derive_scene_group(scene_name) if voice_roles else None

                    done_scenes += 1
                    yield json.dumps({
                        "status": "generating",
                        "current": done_scenes,
                        "total": total_scenes,
                        "course": course_name,
                        "scene": scene_name,
                        "voice": effective_voice,
                        "message": f"[{course_name}] Generating {scene_name} ({done_scenes} of {total_scenes}) with voice {effective_voice}...",
                    }) + "\n"

                    try:
                        result, reused = generate_scene_file(
                            service,
                            scene_name,
                            scene_text,
                            output_dir,
                            scene_voice=effective_voice,
                            scene_system_instruction=effective_instruction,
                            scene_group=scene_group,
                            force_regenerate=force_regenerate,
                        )
                        relative_result = Path(result).relative_to(OUTPUT_DIR).as_posix()
                        generated_courses[course_name].append(relative_result)

                        yield json.dumps({
                            "status": "completed",
                            "current": done_scenes,
                            "total": total_scenes,
                            "course": course_name,
                            "scene": scene_name,
                            "voice": effective_voice,
                            "file": relative_result,
                            "reused": reused,
                            "message": f"[{course_name}] {'Reused' if reused else 'Completed'} {scene_name}",
                        }) + "\n"
                    except Exception as e:
                        error_str = str(e)
                        friendly_message = error_str
                        if "500" in error_str or "INTERNAL" in error_str:
                            friendly_message = "Google API server error (500). The API is experiencing issues. Try again in a moment."
                        elif "503" in error_str or "UNAVAILABLE" in error_str:
                            friendly_message = "Google API is temporarily unavailable (503). Try again in a moment."
                        elif "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                            friendly_message = "Rate limited. Too many requests to Google API. Wait a moment before trying again."

                        yield json.dumps({
                            "status": "error",
                            "current": done_scenes,
                            "total": total_scenes,
                            "course": course_name,
                            "scene": scene_name,
                            "error": friendly_message,
                            "message": f"[{course_name}] Failed to generate {scene_name}",
                        }) + "\n"
                        return

            yield json.dumps({
                "status": "done",
                "courses": list(generated_courses.keys()),
                "files_by_course": generated_courses,
            }) + "\n"
        except Exception as exc:
            detail = str(exc)
            error_msg = detail
            if "API key" in detail or "auth" in detail.lower():
                error_msg = "Gemini authentication failed. Check your API key."
            elif "500" in detail or "INTERNAL" in detail:
                error_msg = "Google API returned a 500 error (server issue). Please wait a moment and try again."
            elif "503" in detail or "UNAVAILABLE" in detail:
                error_msg = "Google API is temporarily unavailable (503). Please wait and try again."
            elif "429" in detail or "RESOURCE_EXHAUSTED" in detail:
                error_msg = "Rate limited by Google API. Please wait a few moments before trying again."

            yield json.dumps({"status": "error", "error": error_msg, "details": detail}) + "\n"

    return StreamingResponse(progress_generator(), media_type="text/event-stream")


@app.post("/generate-from-file")
async def generate_from_file(
    file: UploadFile = File(...),
    api_key: str | None = Form(default=None),
    model: str | None = Form(default=None),
    voice: str | None = Form(default=None),
    scene_name: str | None = Form(default=None),
    scene_index: str | None = Form(default=None),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="A file is required.")

    try:
        contents = await file.read()
        text = contents.decode("utf-8")
        scenes = parse_script_text(text)
        if not scenes:
            raise HTTPException(status_code=400, detail="No scenes were found in the uploaded file.")

        if scene_name:
            scenes = [scene for scene in scenes if scene[0] == scene_name]
        elif scene_index is not None:
            try:
                scene_index = int(scene_index)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="scene_index must be an integer.")
            if 0 <= scene_index < len(scenes):
                scenes = [scenes[scene_index]]
            else:
                raise HTTPException(status_code=400, detail="scene_index is out of range.")

        if not scenes:
            raise HTTPException(status_code=400, detail="No matching scene was found.")

        service = GeminiTTSService(api_key=api_key, model=model, voice=voice)
        generated = service.generate_scene_files(scenes)
        return {"status": "ok", "files": generated, "output_dir": str(OUTPUT_DIR)}
    except HTTPException:
        raise
    except Exception as exc:
        raise gemini_service_error(str(exc), f"file generation error for model={model} voice={voice}")


@app.post("/generate-from-script-path")
def generate_from_script_path(payload: dict):
    path = payload.get("path")
    if not path:
        raise HTTPException(status_code=400, detail="A file path is required.")

    api_key = payload.get("api_key") or API_KEY
    model = payload.get("model") or MODEL
    voice = payload.get("voice") or VOICE
    scene_name = payload.get("scene_name")
    scene_index = payload.get("scene_index")

    try:
        scenes = read_script_file(path)
        if not scenes:
            raise HTTPException(status_code=400, detail="No scenes found in the script file.")

        if scene_name:
            scenes = [scene for scene in scenes if scene[0] == scene_name]
        elif scene_index is not None:
            try:
                scene_index = int(scene_index)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="scene_index must be an integer.")
            if 0 <= scene_index < len(scenes):
                scenes = [scenes[scene_index]]
            else:
                raise HTTPException(status_code=400, detail="scene_index is out of range.")

        if not scenes:
            raise HTTPException(status_code=400, detail="No matching scene was found.")

        service = GeminiTTSService(api_key=api_key, model=model, voice=voice)
        generated = service.generate_scene_files(scenes)
        return {"status": "ok", "files": generated, "output_dir": str(OUTPUT_DIR)}
    except HTTPException:
        raise
    except Exception as exc:
        raise gemini_service_error(str(exc), f"script-path generation error for model={model} voice={voice}")


@app.post("/test-model")
def test_model(payload: dict):
    api_key = payload.get("api_key") or API_KEY
    model_name = payload.get("model") or MODEL
    voice_name = payload.get("voice") or VOICE
    system_instruction = payload.get("system_instruction")
    temperature = payload.get("temperature")

    if not model_name:
        raise HTTPException(status_code=400, detail="Model is required.")

    if not api_key:
        raise HTTPException(status_code=400, detail="API key is required for Gemini checks. Add it in the UI or a .env file.")

    try:
        service = GeminiTTSService(
            api_key=api_key,
            model=model_name,
            voice=voice_name,
            temperature=temperature,
            system_instruction=system_instruction,
        )
        output_path = OUTPUT_DIR / "_model_test.wav"
        # is_test=True uses fewer retries (3 instead of 5) to fail faster if API is down
        result = service.generate_wav("This is a test of the selected Gemini voice model.", output_path, is_test=True)
        return {"status": "ok", "file": result, "output_dir": str(OUTPUT_DIR)}
    except Exception as exc:
        detail = str(exc)
        if "500" in detail or "INTERNAL" in detail:
            raise HTTPException(status_code=503, detail=f"Google Gemini server returned 500 INTERNAL error. This is temporary. The API appears to be down. Try again in a few minutes.")
        if "503" in detail or "UNAVAILABLE" in detail:
            raise HTTPException(status_code=503, detail=f"Google Gemini API is temporarily unavailable (503). Please try again in a moment.")
        if "429" in detail or "RESOURCE_EXHAUSTED" in detail:
            raise HTTPException(status_code=429, detail=f"Rate limited by Google API. Please wait before trying again.")
        if "API key" in detail or "auth" in detail.lower() or "forbidden" in detail.lower():
            raise HTTPException(status_code=401, detail=f"Gemini authentication failed for model={model_name}. Check the API key and project access. Details: {detail}")
        if "not found" in detail.lower() or "unsupported" in detail.lower() or "model" in detail.lower() or "voice" in detail.lower():
            raise HTTPException(status_code=400, detail=f"Unsupported or unavailable model/voice combination. model={model_name} voice={voice_name}. Details: {detail}")
        raise HTTPException(status_code=500, detail=f"Gemini model test failed for model={model_name} voice={voice_name}. Details: {detail}")


@app.post("/check-gemini")
def check_gemini(payload: dict):
    api_key = payload.get("api_key") or API_KEY
    if not api_key:
        raise HTTPException(status_code=400, detail="API key is required for Gemini checks.")

    try:
        client = genai.Client(api_key=api_key)
        models = list(client.models.list(config={"page_size": 20}).page)
        names = []
        for item in models:
            if hasattr(item, "name"):
                names.append(str(item.name).split("/")[-1])
            elif isinstance(item, dict):
                names.append(str(item.get("name", "")).split("/")[-1])
        return {"status": "ok", "credential_status": "valid", "available_models": names[:20]}
    except Exception as exc:
        detail = str(exc)
        if "500" in detail or "INTERNAL" in detail:
            raise HTTPException(status_code=503, detail=f"Google Gemini API is currently experiencing temporary issues. Please wait and try again. Details: {detail}")
        if "503" in detail or "UNAVAILABLE" in detail:
            raise HTTPException(status_code=503, detail=f"Google Gemini API is temporarily unavailable. Please try again in a moment.")
        if "429" in detail or "RESOURCE_EXHAUSTED" in detail:
            raise HTTPException(status_code=429, detail=f"Rate limited by Google API. Please wait before trying again.")
        if "API key" in detail or "auth" in detail.lower() or "forbidden" in detail.lower():
            raise HTTPException(status_code=401, detail=f"Gemini API key is invalid or rejected. Details: {detail}")
        raise HTTPException(status_code=500, detail=f"Gemini API check failed. Details: {detail}")


@app.get("/download-output-zip")
def download_output_zip(course_name: str | None = None):
    output_dir = course_output_dir(course_name)
    wav_files = collect_course_wav_files(output_dir)
    if not wav_files:
        raise HTTPException(status_code=404, detail="No WAV files have been generated yet.")

    archive = build_zip_archive(wav_files, output_dir)
    return StreamingResponse(
        archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{output_dir.name}.zip"'},
    )


@app.get("/download-all-course-zips")
def download_all_course_zips(course_names: str):
    """course_names is a comma-separated list of course folder names, each bundled into its own zip entry."""
    names = [name.strip() for name in course_names.split(",") if name.strip()]
    if not names:
        raise HTTPException(status_code=400, detail="At least one course_name is required.")

    outer_archive = io.BytesIO()
    with zipfile.ZipFile(outer_archive, "w", compression=zipfile.ZIP_DEFLATED) as outer_zf:
        for name in names:
            output_dir = course_output_dir(name)
            wav_files = collect_course_wav_files(output_dir)
            if not wav_files:
                continue
            inner_archive = build_zip_archive(wav_files, output_dir)
            outer_zf.writestr(f"{output_dir.name}.zip", inner_archive.read())

    outer_archive.seek(0)
    return StreamingResponse(
        outer_archive,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="all_courses_bundle.zip"'},
    )
