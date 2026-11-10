"""Offline tests for recorded voice note validation and Groq Whisper transcription."""
import base64
import io
import os
import unittest
import wave
from unittest.mock import AsyncMock, patch

import httpx

from core.services.voice_transcription import (
    GROQ_MODEL, GROQ_TRANSCRIPTIONS_URL, decode_recording,
    transcribe_pcm, transcribe_recording,
)


def recording(channels=1, rate=16000, width=2, frames=1600):
    data = io.BytesIO()
    with wave.open(data, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(b"\x01\x00" * frames * channels if width == 2 else b"\x01" * frames * channels)
    return base64.b64encode(data.getvalue()).decode()


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    def test_pcm_validation(self):
        self.assertEqual(len(decode_recording(recording())), 3200)
        for bad in ("not base64", recording(channels=2), recording(rate=8000),
                    recording(width=1), recording(frames=0), recording(frames=16000*91)):
            with self.subTest(bad=bad[:20]), self.assertRaises(ValueError):
                decode_recording(bad)

    async def test_invalid_audio_never_reaches_provider(self):
        with patch("core.services.voice_transcription.transcribe_pcm", new_callable=AsyncMock) as provider:
            with self.assertRaises(ValueError):
                await transcribe_recording("invalid")
            provider.assert_not_called()

    async def test_groq_receives_wav_and_returns_final_text(self):
        def handler(request: httpx.Request):
            self.assertEqual(str(request.url), GROQ_TRANSCRIPTIONS_URL)
            self.assertEqual(request.headers.get("authorization"), "Bearer fictional-groq-key")
            body = request.content
            self.assertIn(GROQ_MODEL.encode(), body)
            self.assertIn(b'name="language"', body)
            self.assertIn(b'\r\n\r\nen\r\n', body)
            self.assertIn(b'filename="voice-note.wav"', body)
            start = body.index(b"RIFF")
            with wave.open(io.BytesIO(body[start:]), "rb") as audio:
                self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, 16000))
                self.assertEqual(audio.readframes(audio.getnframes()), b"\x01\x00" * 1600)
            return httpx.Response(200, json={"text": "  I want one jollof rice.  "})

        original_client = httpx.AsyncClient
        def fake_client(*args, **kwargs):
            return original_client(*args, **kwargs, transport=httpx.MockTransport(handler))

        with patch.dict(os.environ, {"GROQ_API_KEY": "fictional-groq-key"}), patch(
            "core.services.voice_transcription.httpx.AsyncClient", side_effect=fake_client
        ):
            result = await transcribe_recording(recording())
        self.assertEqual(result, "I want one jollof rice.")

    async def test_missing_api_key_fails_closed(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}), patch(
            "core.services.voice_transcription.httpx.AsyncClient"
        ) as client:
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                await transcribe_pcm(b"\x00" * 3200)
            client.assert_not_called()

    async def test_provider_failure_is_not_silent_audio(self):
        original_client = httpx.AsyncClient
        def fake_client(*args, **kwargs):
            return original_client(*args, **kwargs, transport=httpx.MockTransport(
                lambda request: httpx.Response(429, json={"error": "fictional limit"})
            ))
        with patch.dict(os.environ, {"GROQ_API_KEY": "fictional-groq-key"}), patch(
            "core.services.voice_transcription.httpx.AsyncClient", side_effect=fake_client
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                await transcribe_pcm(b"\x00" * 3200)

    async def test_invalid_provider_payload_is_not_sent_to_agent(self):
        original_client = httpx.AsyncClient
        def fake_client(*args, **kwargs):
            return original_client(*args, **kwargs, transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"wrong_field": "hi"})
            ))
        with patch.dict(os.environ, {"GROQ_API_KEY": "fictional-groq-key"}), patch(
            "core.services.voice_transcription.httpx.AsyncClient", side_effect=fake_client
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid transcript"):
                await transcribe_pcm(b"\x00" * 3200)

    async def test_provider_timeout_is_retryable(self):
        def handler(request):
            raise httpx.ReadTimeout("fictional timeout")
        original_client = httpx.AsyncClient
        def fake_client(*args, **kwargs):
            return original_client(*args, **kwargs, transport=httpx.MockTransport(handler))
        with patch.dict(os.environ, {"GROQ_API_KEY": "fictional-groq-key"}), patch(
            "core.services.voice_transcription.httpx.AsyncClient", side_effect=fake_client
        ):
            with self.assertRaises(TimeoutError):
                await transcribe_pcm(b"\x00" * 3200)


if __name__ == "__main__":
    unittest.main()
