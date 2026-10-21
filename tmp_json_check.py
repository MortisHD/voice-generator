from app.script_parser import parse_script_text, extract_script_settings

sample = '''
{
  "module": "AI Essentials 101",
  "voice": "Zephyr",
  "temperature": 0.3,
  "scenes": [
    {
      "save_as": "screen-1",
      "title": "Welcome",
      "system_instructions": "Narrate this calmly.",
      "text": "Welcome to AI Essentials."
    },
    {
      "save_as": "screen-2",
      "title": "Why this matters",
      "system_instructions": "Narrate this calmly.",
      "text": "This matters because you need safe tools."
    }
  ]
}
'''

scenes = parse_script_text(sample)
settings = extract_script_settings(sample)
print('scene_count=', len(scenes))
print('scene_names=', [s[0] for s in scenes])
print('voice=', settings['voice'])
print('temperature=', settings['temperature'])
print('system_instruction=', settings['system_instruction'])
