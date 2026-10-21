from app.script_parser import parse_script_text, extract_script_settings

SAMPLE_JSON = '''
{
  "module": "AI Essentials 101",
  "voice": "Zephyr",
  "temperature": 0.3,
  "scenes": [
    {
      "save_as": "screen-1",
      "title": "Welcome",
      "voice": "Zephyr",
      "temperature": 0.3,
      "text": "Welcome to AI Essentials.",
      "system_instructions": "Narrate this calmly."
    },
    {
      "save_as": "screen-2",
      "title": "Why this matters",
      "voice": "Zephyr",
      "temperature": 0.3,
      "text": "This matters because you need safe tools.",
      "system_instructions": "Narrate this calmly."
    }
  ]
}
'''


def test_parse_script_text_accepts_json_scene_array():
    scenes = parse_script_text(SAMPLE_JSON)
    assert len(scenes) == 2
    assert scenes[0][0] == "Scene_screen_1"
    assert scenes[1][0] == "Scene_screen_2"
    assert "Welcome to AI Essentials." in scenes[0][1]
    assert scenes[0][2] == "Zephyr"


def test_parse_script_text_supports_per_scene_voice_json():
    payload = '''
    {
      "scenes": [
        {"save_as": "scene-1", "text": "Line one.", "voice": "Kore"},
        {"save_as": "scene-2", "text": "Line two.", "voice": "Puck"}
      ]
    }
    '''
    scenes = parse_script_text(payload)
    assert scenes[0][2] == "Kore"
    assert scenes[1][2] == "Puck"


def test_parse_script_text_supports_voice_directive_in_markdown():
    markdown = (
        "Scene 1 - Welcome\n"
        "Voice: Kore\n"
        "Hello there, welcome to the course.\n"
        "\n"
        "Scene 2 - Next\n"
        "Voice: Puck\n"
        "This is the second scene.\n"
    )
    scenes = parse_script_text(markdown)
    assert len(scenes) == 2
    assert scenes[0][2] == "Kore"
    assert "Voice:" not in scenes[0][1]
    assert scenes[1][2] == "Puck"


def test_extract_script_settings_accepts_json_scene_payload():
    settings = extract_script_settings(SAMPLE_JSON)
    assert settings["voice"] == "Zephyr"
    assert settings["temperature"] == 0.3
    assert "Narrate this calmly." in settings["system_instruction"]
