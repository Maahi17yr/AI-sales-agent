"""Transcribe a short voice recording through a dedicated Bedrock speech model."""
from contextlib import closing
import logging
import os

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

AUDIO_FORMATS = {
    "audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4",
    "audio/x-m4a": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav",
    "audio/x-wav": "wav",
}
MAX_AUDIO_BYTES = 5 * 1024 * 1024


def transcribe(audio: bytes, content_type: str) -> str:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if not audio or len(audio) > MAX_AUDIO_BYTES or media_type not in AUDIO_FORMATS:
        raise ValueError("Record a voice note of up to 90 seconds, then try again.")
    model_id = os.getenv("BEDROCK_TRANSCRIPTION_MODEL_ID", "mistral.voxtral-mini-3b-2507")
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    try:
        with closing(boto3.client("bedrock-runtime", region_name=region,
                          config=Config(connect_timeout=5, read_timeout=35,
                                        retries={"total_max_attempts": 1}))) as client:
            result = client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [
                    {"text": "Transcribe the speech verbatim. Return only the words spoken, "
                             "without commentary. If there is no intelligible speech, return an empty string."},
                    {"audio": {"format": AUDIO_FORMATS[media_type], "source": {"bytes": audio}}},
                ]}],
                inferenceConfig={"maxTokens": 2048, "temperature": 0},
            )
    except (ClientError, BotoCoreError) as exc:
        logging.getLogger(__name__).warning("voice_transcription_failed type=%s", type(exc).__name__)
        raise RuntimeError("Could not transcribe your recording. Please try sending it again.") from exc
    text = " ".join(block.get("text", "") for block in
                    result.get("output", {}).get("message", {}).get("content", [])).strip()
    if not text:
        raise ValueError("I couldn't hear any words. Please record the voice note again.")
    return text
