"""Transcribe short mono WAV voice notes with Amazon Transcribe Streaming."""
import asyncio
import io
import logging
import os
import wave

from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler

AUDIO_FORMATS = {"audio/wav": "wav", "audio/x-wav": "wav"}
MAX_AUDIO_BYTES = 3 * 1024 * 1024


class TranscriptCollector(TranscriptResultStreamHandler):
    def __init__(self, output_stream):
        super().__init__(output_stream)
        self.parts = {}

    async def handle_transcript_event(self, event):
        for result in event.transcript.results:
            if not result.is_partial and result.alternatives:
                self.parts[result.result_id] = result.alternatives[0].transcript


async def _transcribe_pcm(pcm: bytes, region: str) -> str:
    client = TranscribeStreamingClient(region=region)
    stream = await client.start_stream_transcription(
        language_code="en-US", media_sample_rate_hz=16000, media_encoding="pcm",
    )
    collector = TranscriptCollector(stream.output_stream)

    async def upload():
        try:
            for offset in range(0, len(pcm), 8192):
                await stream.input_stream.send_audio_event(audio_chunk=pcm[offset:offset + 8192])
        finally:
            await stream.input_stream.end_stream()

    await asyncio.gather(upload(), collector.handle_events())
    return " ".join(collector.parts.values()).strip()


def transcribe(audio: bytes, content_type: str) -> str:
    kind = content_type.split(";", 1)[0].strip().lower()
    if not audio or len(audio) > MAX_AUDIO_BYTES or kind not in AUDIO_FORMATS:
        raise ValueError("Record a voice note of up to 90 seconds, then try again.")
    try:
        with wave.open(io.BytesIO(audio), "rb") as recording:
            if (recording.getnchannels() != 1 or recording.getsampwidth() != 2
                    or recording.getframerate() != 16000 or recording.getnframes() > 90 * 16000):
                raise ValueError("This recording format is not supported. Please record it again.")
            pcm = recording.readframes(recording.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError("This recording could not be read. Please record it again.") from exc
    if not pcm:
        raise ValueError("No audio was captured. Please record your note again.")
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    try:
        text = asyncio.run(asyncio.wait_for(_transcribe_pcm(pcm, region), timeout=35))
    except Exception as exc:
        logging.getLogger(__name__).warning("voice_transcription_failed type=%s", type(exc).__name__)
        raise RuntimeError("Could not transcribe your recording. Please try sending it again.") from exc
    if not text:
        raise ValueError("I couldn't hear any words. Please record your note again.")
    return text
