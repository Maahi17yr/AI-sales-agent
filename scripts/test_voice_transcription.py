"""Recorded-audio validation and safe AWS transcription failures without provider traffic."""
import io
import unittest
import wave
from unittest.mock import AsyncMock, patch

from core.services import voice_transcription


def wav_audio(rate=16000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(rate)
        recording.writeframes(b"\0\0" * 1600)
    return buffer.getvalue()


class VoiceTranscriptionTests(unittest.TestCase):
    @patch("core.services.voice_transcription._transcribe_pcm", new_callable=AsyncMock)
    def test_valid_wav_reaches_provider_as_pcm(self, provider):
        provider.return_value = "One rice please"
        self.assertEqual(voice_transcription.transcribe(wav_audio(), "audio/wav"), "One rice please")
        self.assertEqual(provider.call_args.args[0], b"\0\0" * 1600)

    @patch("core.services.voice_transcription._transcribe_pcm", new_callable=AsyncMock)
    def test_invalid_audio_never_reaches_provider(self, provider):
        for audio, kind in [(b"", "audio/wav"), (b"audio", "text/plain"), (b"garbage", "audio/wav"),
                            (wav_audio(8000), "audio/wav"),
                            (b"x" * (voice_transcription.MAX_AUDIO_BYTES + 1), "audio/wav")]:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                voice_transcription.transcribe(audio, kind)
        provider.assert_not_called()

    @patch("core.services.voice_transcription._transcribe_pcm", new_callable=AsyncMock)
    def test_silent_result_is_retryable(self, provider):
        provider.return_value = ""
        with self.assertRaisesRegex(ValueError, "hear any words"):
            voice_transcription.transcribe(wav_audio(), "audio/wav")

    @patch("core.services.voice_transcription._transcribe_pcm", new_callable=AsyncMock)
    def test_provider_failure_is_sanitized(self, provider):
        provider.side_effect = RuntimeError("private provider detail")
        with self.assertRaisesRegex(RuntimeError, "try sending"):
            voice_transcription.transcribe(wav_audio(), "audio/wav")


if __name__ == "__main__":
    unittest.main()
