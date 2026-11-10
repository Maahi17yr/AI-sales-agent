"""Bounded, in-memory transcription of completed voice notes."""
import asyncio
import base64
import binascii
import io
import logging
import os
import wave

MAX_SECONDS = 90
SAMPLE_RATE = 16000
MAX_WAV_BYTES = MAX_SECONDS * SAMPLE_RATE * 2 + 4096
logger = logging.getLogger(__name__)


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


async def transcribe_pcm(pcm: bytes) -> str:
    # Lazy imports let disabled deployments run without constructing an AWS client.
    from aws_sdk_transcribe_streaming.client import AsyncTranscribeStreamingClient
    from aws_sdk_transcribe_streaming.config import AsyncTranscribeStreamingConfig
    from aws_sdk_transcribe_streaming.models import (
        StartStreamTranscriptionInput, AudioStreamAudioEvent, AudioEvent, TranscriptEvent,
    )
    from smithy_http.aio.crt import AWSCRTHTTPClient
    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    config = await AsyncTranscribeStreamingConfig.resolve(
        region=region, transport=AWSCRTHTTPClient(),
    )
    async with AsyncTranscribeStreamingClient(config=config) as client:
        stream = await client.start_stream_transcription(input=StartStreamTranscriptionInput(
            language_code="en-US", media_sample_rate_hertz=SAMPLE_RATE, media_encoding="pcm",
        ))
        _, output = await stream.await_output()
        final = {}

        async def send():
            try:
                for offset in range(0, len(pcm), 3200):
                    await stream.input_stream.send(AudioStreamAudioEvent(
                        value=AudioEvent(audio_chunk=pcm[offset:offset + 3200])))
            finally:
                await stream.input_stream.close()

        async def receive():
            async for event in output:
                if isinstance(event.value, TranscriptEvent) and event.value.transcript:
                    for result in event.value.transcript.results or []:
                        if not result.is_partial and result.alternatives:
                            text = result.alternatives[0].transcript
                            if text:
                                final[result.result_id] = text

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(send())
            tasks.create_task(receive())
        return " ".join(final.values()).strip()


async def transcribe_recording(encoded: str) -> str:
    pcm = decode_recording(encoded)
    async with asyncio.timeout(25):
        return await transcribe_pcm(pcm)
