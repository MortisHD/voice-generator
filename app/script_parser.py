import json
import re
from pathlib import Path


def normalize_scene_name(scene_ref: str):
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(scene_ref)).strip("_")
    if not cleaned:
        return "Scene_1"
    if re.fullmatch(r"\d+", cleaned):
        return f"Scene_{cleaned}"
    return f"Scene_{cleaned}"


def normalize_course_name(course_ref: str, index: int = 1):
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(course_ref)).strip("_")
    return cleaned or f"Course_{index}"


def _strip_audio_extension(name: str) -> str:
    return re.sub(r"\.(wav|mp3|m4a|ogg)$", "", str(name), flags=re.IGNORECASE)


def _extract_save_as_name(text: str):
    match = re.search(r"save\s+as[:\s]+\*{0,2}`?([A-Za-z0-9_\-.]+)`?\*{0,2}", text, re.IGNORECASE)
    return _strip_audio_extension(match.group(1)) if match else None


def clean_speech_text(text: str) -> str:
    """Clean text for TTS generation by removing markdown code fences, annotations, etc."""
    if not text:
        return ""

    # Remove code fences (e.g. ``` or ```text)
    cleaned = re.sub(r"```[a-zA-Z]*", "", text)
    cleaned = cleaned.replace("```", "")

    # Remove metadata lines like *156 words - target ~62 seconds* or *100 words*
    cleaned = re.sub(r"^\s*\*+\s*\d+\s*words.*?\*+\s*$", "", cleaned, flags=re.IGNORECASE | re.MULTILINE)
    cleaned = re.sub(r"\*\d+\s*words[^*]*\*", "", cleaned, flags=re.IGNORECASE)

    # Remove notes like -> save as **Scene_1** or → **save as screen-1**
    cleaned = re.sub(r"^\s*(?:->|\u2192).*$", "", cleaned, flags=re.MULTILINE)

    # Remove residual markdown headers inside scene body
    cleaned = re.sub(r"^\s*#+\s*.*$", "", cleaned, flags=re.MULTILINE)

    # Join non-empty lines cleanly
    lines = [line.strip() for line in cleaned.splitlines()]
    non_empty = [line for line in lines if line]

    return " ".join(non_empty)


def _json_scene_records(scenes):
    """Turn a list of raw JSON scene dicts into (scene_name, text, voice) tuples."""
    records = []
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue

        scene_name = scene.get("save_as") or scene.get("title") or scene.get("name") or f"Scene_{index}"
        scene_text = scene.get("text") or scene.get("prompt") or scene.get("content") or ""
        if not scene_text:
            continue

        if scene.get("save_as"):
            normalized_name = normalize_scene_name(scene["save_as"])
        else:
            normalized_name = normalize_scene_name(scene_name)
        scene_voice = scene.get("voice")
        records.append((normalized_name, clean_speech_text(scene_text), str(scene_voice) if scene_voice else None))

    return records


def _extract_json_scenes(raw_text: str):
    """Parse a JSON scene array payload into (scene_name, text, voice) tuples."""
    try:
        parsed = json.loads(raw_text)
    except (TypeError, ValueError):
        return []

    if isinstance(parsed, dict):
        scenes = parsed.get("scenes")
        if scenes is None and isinstance(parsed.get("text"), str):
            return [("Scene_1", parsed["text"], parsed.get("voice"))]
        if not isinstance(scenes, list):
            return []
    elif isinstance(parsed, list):
        scenes = parsed
    else:
        return []

    return _json_scene_records(scenes)


def _extract_json_courses(raw_text: str):
    """Parse a JSON payload with a top-level 'courses' array into (course_name, scenes) groups."""
    try:
        parsed = json.loads(raw_text)
    except (TypeError, ValueError):
        return []

    if not isinstance(parsed, dict):
        return []

    courses = parsed.get("courses")
    if not isinstance(courses, list):
        return []

    results = []
    for index, course in enumerate(courses, start=1):
        if not isinstance(course, dict):
            continue
        scenes_list = course.get("scenes")
        if not isinstance(scenes_list, list):
            continue
        records = _json_scene_records(scenes_list)
        if not records:
            continue
        name = course.get("name") or course.get("title") or course.get("save_as") or f"Course_{index}"
        results.append((normalize_course_name(name, index), records))

    return results


def extract_inline_voice(text: str):
    """Pull a standalone 'Voice: Name' directive line out of scene body text.

    Returns (voice_or_None, remaining_text).
    """
    if not text:
        return None, text

    voice = None
    kept_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        match = re.match(r"^(?:>>\s*)?Voice\s*[:=]\s*([A-Za-z0-9_\- ]+?)\s*$", stripped, re.IGNORECASE)
        if match and voice is None:
            voice = match.group(1).strip()
            continue
        kept_lines.append(line)

    return voice, "\n".join(kept_lines)


def parse_script_text(raw_text: str):
    """Return a list of (scene_name, text, voice) tuples from transcript text.

    ``voice`` is ``None`` unless the scene explicitly declares one (via JSON
    ``voice``/``save_as`` fields or a ``Voice: Name`` directive line), allowing
    different scenes in the same script to be rendered with different voices.
    """
    if not isinstance(raw_text, str):
        raw_text = str(raw_text or "")

    cleaned = raw_text.strip()
    if not cleaned:
        return []

    json_records = _extract_json_scenes(cleaned)
    if json_records:
        return json_records

    blocks = [block.strip() for block in re.split(r"\n\s*---\s*\n", cleaned) if block.strip()]
    if len(blocks) > 1:
        records = []
        for block in blocks:
            scene_name, body = extract_scene_title_and_body(block)
            if scene_name == "NON_SCENE_HEADER":
                continue
            scene_voice, body_no_voice = extract_inline_voice(body)
            body_clean = clean_speech_text(body_no_voice)
            # Skip non-scene metadata blocks like "STYLE", "SYSTEM INSTRUCTIONS", etc.
            if body_clean and not any(k in scene_name.upper() for k in ["STYLE", "SYSTEM", "INSTRUCTION", "SETTING", "HEADER", "NON_SCENE"]):
                records.append((scene_name, body_clean, scene_voice))
        if records:
            return records

    lines = [line.rstrip() for line in cleaned.splitlines()]
    records = []
    current_name = "Scene_1"
    current_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_lines:
                current_lines.append("")
            continue

        match = re.match(r"^(?:#+[ \t]*)?(?:Scene|SCENE)[ \t]*[-:]?[ \t]*(\d+|[A-Za-z0-9_\-]+)(?:[ \t]*[-:][ \t]*(.*))?[ \t]*$", stripped)
        if match:
            if current_lines:
                scene_voice, joined_no_voice = extract_inline_voice("\n".join(current_lines))
                body_clean = clean_speech_text(joined_no_voice)
                if body_clean and not any(k in current_name.upper() for k in ["STYLE", "SYSTEM", "INSTRUCTION", "SETTING", "HEADER", "NON_SCENE"]):
                    records.append((current_name, body_clean, scene_voice))
            current_name = normalize_scene_name(match.group(1))
            current_lines = []
            if match.group(2):
                current_lines.append(match.group(2).strip())
            continue

        current_lines.append(stripped)

    if current_lines:
        scene_voice, joined_no_voice = extract_inline_voice("\n".join(current_lines))
        body_clean = clean_speech_text(joined_no_voice)
        if body_clean and not any(k in current_name.upper() for k in ["STYLE", "SYSTEM", "INSTRUCTION", "SETTING", "HEADER", "NON_SCENE"]):
            records.append((current_name, body_clean, scene_voice))

    if not records:
        scene_voice, cleaned_no_voice = extract_inline_voice(cleaned)
        cleaned_body = clean_speech_text(cleaned_no_voice)
        if cleaned_body:
            records = [("Scene_1", cleaned_body, scene_voice)]

    return records


_COURSE_KEYWORD_PATTERN = re.compile(r"^#{1,3}[ \t]*(?:Course|Module)\b[ \t]*[-:#]*[ \t]*(.*)$", re.IGNORECASE)
_H1_HEADER_PATTERN = re.compile(r"^#(?!#)[ \t]*(.+)$")
_SCENE_HEADER_PATTERN = re.compile(r"^(?:Scene|SCENE)\b")


def _find_course_boundaries(lines):
    """Return [(title, line_index), ...] marking where each course starts."""
    keyword_matches = [
        (match.group(1).strip(), index)
        for index, line in enumerate(lines)
        if (match := _COURSE_KEYWORD_PATTERN.match(line.strip()))
    ]
    if keyword_matches:
        return keyword_matches

    # Fall back to treating each top-level (single '#') non-scene heading as a course boundary.
    h1_matches = [
        (match.group(1).strip(), index)
        for index, line in enumerate(lines)
        if (match := _H1_HEADER_PATTERN.match(line.strip())) and not _SCENE_HEADER_PATTERN.match(match.group(1).strip())
    ]
    return h1_matches if len(h1_matches) > 1 else []


def parse_multi_course_script(raw_text: str):
    """Split a script into (course_name, scenes) groups, where each scenes list
    is the same (scene_name, text, voice) format returned by parse_script_text.

    Course boundaries are '# Course: Name' / '## Module - Name' style headings, a
    JSON payload's top-level 'courses' array, or (when neither is present) multiple
    top-level '# Title' headings. A script with no detectable boundaries is
    returned as a single default course.
    """
    if not isinstance(raw_text, str):
        raw_text = str(raw_text or "")

    cleaned = raw_text.strip()
    if not cleaned:
        return []

    json_courses = _extract_json_courses(cleaned)
    if json_courses:
        return json_courses

    lines = cleaned.splitlines()
    boundaries = _find_course_boundaries(lines)

    if not boundaries:
        scenes = parse_script_text(cleaned)
        return [("Course_1", scenes)] if scenes else []

    courses = []
    for index, (title, start) in enumerate(boundaries):
        end = boundaries[index + 1][1] if index + 1 < len(boundaries) else len(lines)
        body = "\n".join(lines[start + 1 : end]).strip()
        scenes = parse_script_text(body)
        if scenes:
            courses.append((normalize_course_name(title, index + 1), scenes))

    return courses


def normalize_clip_name(name_ref: str):
    """Sanitize an explicit save-as filename (e.g. 'screen-1-A1.wav') into a safe stem."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(Path(name_ref).stem)).strip("_")
    return cleaned or "Clip_1"


_VOICE_INSTRUCTION_HEADER_PATTERN = re.compile(
    r"^#{1,3}[ \t]*SYSTEM INSTRUCTIONS[ \t]*[\u2014\u2013-]*[ \t]*VOICE[ \t]+([A-Za-z])\b[ \t]*(?:\(([^)]*)\))?",
    re.IGNORECASE,
)
_CLIP_LINE_PATTERN = re.compile(
    r"^\*\*([A-Za-z]\d*)\*\*[ \t]*[\u2014\u2013-][ \t]*save as[ \t]*`?([^`\n]+?)`?[ \t]*$",
    re.IGNORECASE,
)


def _read_fenced_code_block(lines, start_index):
    """Starting at/after start_index, find the next ``` fenced block and return (text, index_after_block)."""
    index = start_index
    while index < len(lines) and not lines[index].strip().startswith("```"):
        index += 1
    if index >= len(lines):
        return "", start_index
    index += 1
    body_lines = []
    while index < len(lines) and not lines[index].strip().startswith("```"):
        body_lines.append(lines[index])
        index += 1
    return "\n".join(body_lines).strip(), index + 1


def parse_two_voice_script(raw_text: str):
    """Parse the 'two-voice dialogue' transcript format (each turn is its own
    clip, tagged **A1**/**B1**/... with an explicit save-as filename, and voiced
    per persona-specific 'SYSTEM INSTRUCTIONS — VOICE A/B' blocks).

    Returns (clips, voice_roles):
      clips: list of (clip_name, text, voice_role, system_instruction) tuples,
             one per narration line, in document order.
      voice_roles: {"A": (persona_label, system_instruction), ...}
    Returns ([], {}) if the text doesn't match this format.
    """
    if not isinstance(raw_text, str):
        raw_text = str(raw_text or "")

    lines = raw_text.splitlines()

    voice_roles = {}
    index = 0
    while index < len(lines):
        match = _VOICE_INSTRUCTION_HEADER_PATTERN.match(lines[index].strip())
        if match:
            letter = match.group(1).upper()
            persona = (match.group(2) or "").strip()
            instruction_text, next_index = _read_fenced_code_block(lines, index + 1)
            voice_roles[letter] = (persona, instruction_text)
            index = next_index
            continue
        index += 1

    if not voice_roles:
        return [], {}

    clips = []
    index = 0
    while index < len(lines):
        match = _CLIP_LINE_PATTERN.match(lines[index].strip())
        if match:
            letter = match.group(1)[0].upper()
            save_as = match.group(2).strip()
            clip_text, next_index = _read_fenced_code_block(lines, index + 1)
            clip_text = clean_speech_text(clip_text)
            if clip_text:
                _, instruction_text = voice_roles.get(letter, ("", None))
                clips.append((normalize_clip_name(save_as), clip_text, letter, instruction_text))
            index = next_index
            continue
        index += 1

    return clips, voice_roles


def parse_course_script(raw_text: str):
    """Unified entry point for a single course's script text.

    Auto-detects the two-voice dialogue format; otherwise falls back to the
    standard single/multi-voice scene parser. Always returns
    (records, voice_roles) where records is a list of
    (name, text, voice, system_instruction) 4-tuples and voice_roles is
    {"A": (persona_label, system_instruction), ...} for two-voice scripts,
    or {} otherwise.
    """
    clips, voice_roles = parse_two_voice_script(raw_text)
    if clips:
        return clips, voice_roles

    scenes = parse_script_text(raw_text)
    records = [(scene[0], scene[1], scene[2] if len(scene) > 2 else None, None) for scene in scenes]
    return records, {}


def extract_scene_title_and_body(block: str):
    lines = [line.rstrip() for line in block.splitlines() if line.strip()]
    if not lines:
        return "Scene_1", ""

    first_line = lines[0].strip()
    scene_match = re.match(r"^(?:#+[ \t]*)?(?:Scene|SCENE)[ \t]*[-:]?[ \t]*(\d+|[A-Za-z0-9_\-]+)(?:[ \t]*[-:][ \t]*(.*))?[ \t]*$", first_line)
    if scene_match:
        title = normalize_scene_name(scene_match.group(1))
        body_lines = lines[1:]
        if scene_match.group(2):
            body_lines = [scene_match.group(2).strip(), *body_lines]
        body = "\n".join(body_lines).strip()
        return title, body

    title_match = re.match(r"^(#+)\s*(.+)$", first_line)
    if title_match:
        header_text = title_match.group(2).strip()
        body = "\n".join(lines[1:]).strip()
        if re.search(r"\b(?:Scene|SCENE)\b", header_text):
            return normalize_scene_name(header_text), body
        if any(k in header_text.upper() for k in ["STYLE", "SYSTEM", "INSTRUCTION", "SETTING"]):
            return "NON_SCENE_HEADER", body
        # A plain (non-"Scene") heading that still wraps narration text counts as a
        # scene, named after its explicit 'save as <name>' directive when present.
        if "```" in body:
            save_as_name = _extract_save_as_name(body)
            scene_name = normalize_scene_name(save_as_name) if save_as_name else normalize_scene_name(header_text)
            return scene_name, body
        return "NON_SCENE_HEADER", body

    return "Scene_1", block.strip()


def read_script_file(path: str):
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    return parse_script_text(text)


def extract_script_settings(raw_text: str) -> dict:
    """Extract settings like system_instruction / STYLE, temperature, voice, model from transcript text."""
    settings = {
        "system_instruction": None,
        "temperature": None,
        "voice": None,
        "model": None,
    }
    if not raw_text or not raw_text.strip():
        return settings

    try:
        parsed = json.loads(raw_text)
    except (TypeError, ValueError):
        parsed = None

    if isinstance(parsed, dict):
        top_level_voice = parsed.get("voice")
        top_level_temperature = parsed.get("temperature")
        top_level_system_instruction = parsed.get("system_instructions") or parsed.get("system_instruction")

        if top_level_voice:
            settings["voice"] = str(top_level_voice)
        if top_level_temperature is not None:
            try:
                settings["temperature"] = float(top_level_temperature)
            except (TypeError, ValueError):
                pass
        if top_level_system_instruction:
            settings["system_instruction"] = str(top_level_system_instruction)

        scenes = parsed.get("scenes")
        if isinstance(scenes, list):
            for scene in scenes:
                if not isinstance(scene, dict):
                    continue
                scene_system_instruction = scene.get("system_instructions") or scene.get("system_instruction")
                if scene_system_instruction and not settings["system_instruction"]:
                    settings["system_instruction"] = str(scene_system_instruction)
                if not settings["voice"]:
                    scene_voice = scene.get("voice")
                    if scene_voice:
                        settings["voice"] = str(scene_voice)
                if settings["temperature"] is None:
                    scene_temperature = scene.get("temperature")
                    try:
                        settings["temperature"] = float(scene_temperature)
                    except (TypeError, ValueError):
                        pass
        return settings

    # 1. Extract Temperature
    temp_match = re.search(r"temperature[\s\*:]+([0-9\.]+)", raw_text, re.IGNORECASE)
    if temp_match:
        try:
            settings["temperature"] = float(temp_match.group(1))
        except ValueError:
            pass

    # 2. Extract Voice (strictly validate against Gemini prebuilt voices)
    allowed_voices = {
        "achernar", "achird", "algenib", "algieba", "alnilam", "aoede", "autonoe", "callirrhoe",
        "charon", "despina", "enceladus", "erinome", "fenrir", "gacrux", "iapetus", "kore",
        "laomedeia", "leda", "orus", "puck", "pulcherrima", "rasalgethi", "sadachbia", "sadaltager",
        "schedar", "sulafat", "umbriel", "vindemiatrix", "zephyr", "zubenelgenubi"
    }

    voice_match = re.search(r"(?:voice|Voice)\s*[\*:]+\s*\*?\*?([A-Za-z0-9_\-]+)\*?\*?", raw_text)
    if voice_match:
        val = voice_match.group(1).strip()
        if val.lower() in allowed_voices:
            settings["voice"] = val.capitalize()

    if not settings["voice"]:
        for known_voice in ["Zephyr", "Kore", "Puck", "Luna"]:
            if re.search(rf"\b{known_voice}\b", raw_text, re.IGNORECASE):
                settings["voice"] = known_voice
                break

    # 3. Extract Model if explicitly mentioned
    model_match = re.search(r"(?:model|Model)[\s\*:]+\*?\*?([A-Za-z0-9_\-\.]+)\*?\*?", raw_text)
    if model_match:
        val = model_match.group(1).strip()
        if "gemini" in val.lower():
            settings["model"] = val

    # 4. Extract STYLE / System Instructions block
    style_header_match = re.search(
        r"^\s*#+\s*.*?(?:STYLE|System\s*Instructions?).*$",
        raw_text,
        re.IGNORECASE | re.MULTILINE
    )
    if style_header_match:
        start_pos = style_header_match.end()
        end_match = re.search(r"^\s*---|\n\s*#+\s*Scene", raw_text[start_pos:], re.IGNORECASE | re.MULTILINE)
        section_text = raw_text[start_pos : start_pos + end_match.start()] if end_match else raw_text[start_pos:]

        code_match = re.search(r"```[a-zA-Z]*\s*\n?(.*?)\n?```", section_text, re.DOTALL)
        if code_match:
            sys_inst = code_match.group(1).strip()
        else:
            sys_inst = re.sub(r"```[a-zA-Z]*", "", section_text).replace("```", "").strip()

        if sys_inst:
            settings["system_instruction"] = sys_inst
    else:
        style_section_match = re.search(
            r"(?:STYLE|System\s*Instructions?)(?:[^\n]*\n)+(.*?)(?=\n\s*---|\n\s*##\s*Scene|\n\s*Scene\s*\d+|\Z)",
            raw_text,
            re.IGNORECASE | re.DOTALL,
        )
        if style_section_match:
            section_text = style_section_match.group(1).strip()
            code_block_match = re.search(r"```[a-zA-Z]*\s*\n?(.*?)\n?```", section_text, re.DOTALL)
            if code_block_match:
                sys_inst = code_block_match.group(1).strip()
            else:
                sys_inst = re.sub(r"```[a-zA-Z]*", "", section_text).replace("```", "").strip()

            if sys_inst:
                settings["system_instruction"] = sys_inst

    return settings
