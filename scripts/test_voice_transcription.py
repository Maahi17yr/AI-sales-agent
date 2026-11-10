"""No-network tests for completed voice-note validation and speech streaming."""
import base64
import io
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.voice_transcription import decode_recording, transcribe_pcm, transcribe_recording


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
            with self.assertRaises(ValueError):
                decode_recording(bad)

    async def test_invalid_audio_never_reaches_provider(self):
        with patch("core.services.voice_transcription.transcribe_pcm", new_callable=AsyncMock) as provider:
            with self.assertRaises(ValueError):
                await transcribe_recording("invalid")
            provider.assert_not_called()

    async def test_final_results_and_cleanup(self):
        from aws_sdk_transcribe_streaming.models import (
            TranscriptEvent, Transcript, Result, Alternative, TranscriptResultStreamTranscriptEvent,
        )
        async def events():
            for partial, text in [(True, "wrong"), (False, "one rice"), (False, "one rice")]:
                yield TranscriptResultStreamTranscriptEvent(value=TranscriptEvent(transcript=Transcript(
                    results=[Result(result_id="same", is_partial=partial,
                                    alternatives=[Alternative(transcript=text)])])))
        input_stream = SimpleNamespace(send=AsyncMock(), close=AsyncMock())
        stream = SimpleNamespace(input_stream=input_stream, await_output=AsyncMock(
            return_value=(None, events())))
        client = SimpleNamespace(start_stream_transcription=AsyncMock(return_value=stream))
        context = AsyncMock()
        context.__aenter__.return_value = client
        with patch("aws_sdk_transcribe_streaming.config.AsyncTranscribeStreamingConfig.resolve",
                   new_callable=AsyncMock), patch(
                   "aws_sdk_transcribe_streaming.client.AsyncTranscribeStreamingClient",
                   return_value=context):
            self.assertEqual(await transcribe_pcm(b"\x00" * 6400), "one rice")
        self.assertEqual(input_stream.send.await_count, 2)
        input_stream.close.assert_awaited_once()
        context.__aexit__.assert_awaited_once()

    async def test_provider_failure_is_not_an_empty_transcript(self):
        with patch("core.services.voice_transcription.transcribe_pcm", new_callable=AsyncMock,
                   side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                await transcribe_recording(recording())


if __name__ == "__main__":
    unittest.main()
