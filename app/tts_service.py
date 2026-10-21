import base64
import io
import re
import tempfile
import time
import wave
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types

from app.config import API_KEY, MODEL, OUTPUT_DIR, VOICE


class GeminiTTSService:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        temperature: float | None = None,
        system_instruction: str | None = None,
    ):
        self.api_key = api_key or API_KEY
        self.model = model or MODEL
        self.voice = voice or VOICE
        try:
            requested_temperature = float(temperature) if temperature is not None else 0.3
        except (TypeError, ValueError):
            raise ValueError("Temperature must be a number greater than 0.")
        self.temperature = requested_temperature if requested_temperature > 0 else 0.3
        
        self.system_instruction = system_instruction

        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is required. Set it in .env or environment variables.")

        self.client = genai.Client(api_key=self.api_key)

    def _generate_wav_once(self, text: str, output_path: str | Path, max_retries: int = 5, initial_delay: float = 5.0, is_test: bool = False, voice_override: str | None = None, system_instruction_override: str | None = None):
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        effective_voice = voice_override or self.voice
        effective_system_instruction = system_instruction_override if system_instruction_override is not None else self.system_instruction

        response = None
        delay = initial_delay
        # Use fewer retries for tests to fail faster
        if is_test:
            max_retries = 3

        prompt = "TTS the following transcript word for word.\n\n"
        if effective_system_instruction:
            prompt += f"Performance directions:\n{effective_system_instruction}\n\n"
        prompt += f"Transcript:\n{text}"

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                print(f"[Attempt {attempt}/{max_retries}] Calling Gemini API with model={self.model}, voice={effective_voice}...")
                response = self.client.interactions.create(
                    model=self.model,
                    input=prompt,
                    response_format={"type": "audio"},
                    generation_config={"speech_config": [{"voice": effective_voice}]},
                )
                print(f"[Attempt {attempt}] API call succeeded!")
                break
            except Exception as exc:
                last_error = exc
                err_str = str(exc)
                print(f"[Attempt {attempt}] API error: {err_str[:150]}")
                # Retry on server errors (500, 503), rate limits (429), and unavailable (UNAVAILABLE)
                if ("503" in err_str or "500" in err_str or "UNAVAILABLE" in err_str or "INTERNAL" in err_str or "high demand" in err_str or "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "ServiceUnavailable" in err_str) and attempt < max_retries:
                    # Exponential backoff with jitter to avoid thundering herd
                    wait_time = delay * (2 ** (attempt - 1))
                    print(f"[Attempt {attempt}] Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"[Attempt {attempt}] Not retrying - giving up.")
                    raise exc
        
        if response is None:
            raise RuntimeError(f"Failed to generate audio after {max_retries} attempts. Last error: {last_error}")

        audio_data = self._extract_audio_data(response)
        if not audio_data:
            raise ValueError(f"No audio data returned from Gemini TTS API. Response structure: {type(response).__name__}")

        # If audio_data is raw PCM (doesn't start with RIFF/ID3/OggS/MP3 header), wrap it in a valid WAV container
        if not (audio_data.startswith(b'RIFF') or audio_data.startswith(b'ID3') or audio_data.startswith(b'\xff\xfb') or audio_data.startswith(b'OggS')):
            out = io.BytesIO()
            with wave.open(out, 'wb') as wf:
                wf.setnchannels(1)       # Mono
                wf.setsampwidth(2)        # 16-bit PCM
                wf.setframerate(24000)    # 24kHz sample rate
                wf.writeframes(audio_data)
            audio_data = out.getvalue()

        path.write_bytes(audio_data)
        return str(path)

    @staticmethod
    def _extract_audio_data(response) -> bytes:
        steps = getattr(response, "steps", None) or []
        last_user_input = max(
            (index for index, step in enumerate(steps) if getattr(step, "type", None) == "user_input"),
            default=-1,
        )
        audio_chunks = []
        for step in steps[last_user_input + 1:]:
            if getattr(step, "type", None) != "model_output":
                continue
            for item in getattr(step, "content", None) or []:
                if getattr(item, "type", None) != "audio":
                    continue
                encoded_audio = getattr(item, "data", None)
                if encoded_audio:
                    audio_chunks.append(base64.b64decode(encoded_audio))

        if audio_chunks:
            return b"".join(audio_chunks)

        output_audio = getattr(response, "output_audio", None)
        encoded_audio = getattr(output_audio, "data", None)
        return base64.b64decode(encoded_audio) if encoded_audio else b""

    def generate_wav(self, text: str, output_path: str | Path, max_retries: int = 5, initial_delay: float = 5.0, is_test: bool = False, voice_override: str | None = None, system_instruction_override: str | None = None):
        """Generate a complete WAV, splitting only unusually long scenes."""
        words = text.split()
        max_words_per_chunk = 400
        if len(words) <= max_words_per_chunk:
            return self._generate_wav_once(text, output_path, max_retries, initial_delay, is_test, voice_override, system_instruction_override)

        chunks = []
        current_chunk = []
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
            sentence_words = sentence.split()
            if not sentence_words:
                continue

            if current_chunk and len(current_chunk) + len(sentence_words) > max_words_per_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = []

            while len(sentence_words) > max_words_per_chunk:
                chunks.append(" ".join(sentence_words[:max_words_per_chunk]))
                sentence_words = sentence_words[max_words_per_chunk:]

            current_chunk.extend(sentence_words)
        if current_chunk:
            chunks.append(" ".join(current_chunk))

        print(f"Splitting long scene into {len(chunks)} audio chunks.")
        with tempfile.TemporaryDirectory() as temp_dir:
            chunk_paths = []
            for index, chunk in enumerate(chunks, start=1):
                chunk_path = Path(temp_dir) / f"chunk_{index}.wav"
                self._generate_wav_once(chunk, chunk_path, max_retries, initial_delay, is_test, voice_override, system_instruction_override)
                chunk_paths.append(chunk_path)

            with wave.open(str(output_path), "wb") as combined:
                with wave.open(str(chunk_paths[0]), "rb") as first:
                    combined.setparams(first.getparams())
                for chunk_path in chunk_paths:
                    with wave.open(str(chunk_path), "rb") as chunk_file:
                        combined.writeframes(chunk_file.readframes(chunk_file.getnframes()))

        return str(output_path)

    def generate_scene_files(self, scene_texts):
        """Generate WAVs for (name, text), (name, text, voice) or (name, text, voice, system_instruction) tuples."""
        generated = []
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        for scene in scene_texts:
            scene_name, text = scene[0], scene[1]
            voice_override = scene[2] if len(scene) > 2 else None
            system_instruction_override = scene[3] if len(scene) > 3 else None
            filename = f"{scene_name}_{timestamp}.wav"
            output_path = OUTPUT_DIR / filename
            result = self.generate_wav(text, output_path, voice_override=voice_override, system_instruction_override=system_instruction_override)
            generated.append(result)
        return generated
