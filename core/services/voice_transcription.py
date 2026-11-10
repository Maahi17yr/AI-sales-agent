"""Bounded, in-memory transcription of completed voice notes via Groq Whisper.

Only the finished recording goes to the speech provider. The ordering agent
receives the resulting text, never the audio or the provider API key.
"""
import asyncio
import base64
import binascii
import io
import os
import wave

import httpx

MAX_SECONDS = 90
SAMPLE_RATE = 16000
MAX_WAV_BYTES = MAX_SECONDS * SAMPLE_RATE * 2 + 4096
GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"


def decode_recording(encoded: str) -> bytes:
    if len(encoded) > ((MAX_WAV_BYTES + 2) // 3) * 4:
        raise ValueError("Voice note is too large. Keep recordings under 90 seconds.")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_WAV_BYTES:
            raise ValueError("Voice note is too large.")
        with wave.open(io.BytesIO(raw), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
                    audio.getcomptype()) != (1, 2, SAMPLE_RATE, "NONE"):
                raise ValueError("Voice note must be mono 16-bit PCM at 16 kHz.")
            frames = audio.getnframes()
            if not 0 < frames <= MAX_SECONDS * SAMPLE_RATE:
                raise ValueError("Voice note must be between 0 and 90 seconds.")
            pcm = audio.readframes(frames)
            if len(pcm) != frames * 2:
                raise ValueError("Voice note audio is incomplete.")
            return pcm
    except (binascii.Error, wave.Error, EOFError) as exc:
        raise ValueError("Voice note audio is invalid.") from exc


def encode_wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(pcm)
    return output.getvalue()


async def transcribe_pcm(pcm: bytes) -> str:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Voice transcription is not configured.")
    # No retries: avoid unintended duplicate provider charges. The frontend
    # retains the playable recording and offers an explicit retry.
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(24.0, connect=5.0), follow_redirects=False
        ) as client:
            response = await client.post(
                GROQ_TRANSCRIPTIONS_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                data={
                    "model": GROQ_MODEL,
                    "language": "en",
                    "response_format": "json",
                    "temperature": "0",
                },
                files={"file": ("voice-note.wav", encode_wav(pcm), "audio/wav")},
            )
    except httpx.TimeoutException as exc:
        raise TimeoutError("Speech transcription timed out.") from exc
    except httpx.RequestError as exc:
        raise RuntimeError("Speech transcription provider unavailable.") from exc
    # Provider response bodies can contain sensitive detail: never log or return them.
    if response.status_code != 200:
        raise RuntimeError(f"Speech transcription provider returned HTTP {response.status_code}.")
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("Speech transcription provider returned invalid JSON.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise RuntimeError("Speech transcription provider returned an invalid transcript.")
    transcript = data["text"].strip()
    if len(transcript) > 8000:
        raise RuntimeError("Speech transcription provider returned an oversized transcript.")
    return transcript


async def transcribe_recording(encoded: str) -> str:
    pcm = decode_recording(encoded)
    async with asyncio.timeout(25):
        return await transcribe_pcm(pcm)
