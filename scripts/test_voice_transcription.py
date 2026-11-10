"""Recorded-audio transcription validation without provider traffic."""
import unittest
from unittest.mock import patch, MagicMock

from botocore.exceptions import ClientError
from core.services import voice_transcription


class VoiceTranscriptionTests(unittest.TestCase):
    @patch("core.services.voice_transcription.boto3.client")
    def test_valid_audio_is_transcribed_and_client_closed(self, factory):
        client = factory.return_value
        client.converse.return_value = {"output": {"message": {"content": [{"text": "One rice please"}]}}}
        self.assertEqual(voice_transcription.transcribe(b"audio", "audio/webm;codecs=opus"), "One rice please")
        request = client.converse.call_args.kwargs
        self.assertEqual(request["messages"][0]["content"][1]["audio"],
                         {"format": "webm", "source": {"bytes": b"audio"}})
        client.close.assert_called_once()

    @patch("core.services.voice_transcription.boto3.client")
    def test_invalid_audio_never_calls_provider(self, factory):
        for audio, kind in [(b"", "audio/wav"), (b"audio", "text/plain"),
                            (b"x" * (voice_transcription.MAX_AUDIO_BYTES + 1), "audio/wav")]:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                voice_transcription.transcribe(audio, kind)
        factory.assert_not_called()

    @patch("core.services.voice_transcription.boto3.client")
    def test_silent_result_is_retryable(self, factory):
        factory.return_value.converse.return_value = {"output": {"message": {"content": [{"text": ""}]}}}
        with self.assertRaisesRegex(ValueError, "hear any words"):
            voice_transcription.transcribe(b"audio", "audio/wav")

    @patch("core.services.voice_transcription.boto3.client")
    def test_provider_error_is_sanitized(self, factory):
        factory.return_value.converse.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "private provider detail"}}, "Converse")
        with self.assertRaisesRegex(RuntimeError, "try sending"):
            voice_transcription.transcribe(b"audio", "audio/wav")
        factory.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
