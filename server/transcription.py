from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class TranscriptionError(Exception):
    pass


class TranscriptionUnavailable(TranscriptionError):
    pass


class NoAudioTrack(TranscriptionError):
    pass


@dataclass(frozen=True)
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str


def _field(value, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def transcribe_video(video_path: Path, work_root: Path, video_id: str) -> list[TranscriptSegment]:
    if not os.getenv("OPENAI_API_KEY"):
        raise TranscriptionUnavailable("Configure OPENAI_API_KEY to generate searchable transcripts")

    try:
        import imageio_ffmpeg
        from openai import OpenAI
    except ImportError as exc:
        raise TranscriptionUnavailable("Cloud transcription dependencies are not installed") from exc

    derived_dir = work_root / "derived" / video_id
    derived_dir.mkdir(parents=True, exist_ok=True)
    audio_path = derived_dir / "transcript-audio.mp3"
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "48k",
        "-y",
        str(audio_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if result.returncode != 0 or not audio_path.is_file() or audio_path.stat().st_size == 0:
            detail = (result.stderr or "").lower()
            if "does not contain any stream" in detail or "matches no streams" in detail:
                raise NoAudioTrack("No spoken-audio track was found")
            raise TranscriptionError("The audio track could not be prepared for transcription")

        with audio_path.open("rb") as audio:
            response = OpenAI(timeout=float(os.getenv("OPENAI_TRANSCRIPTION_TIMEOUT", "180"))).audio.transcriptions.create(
                model=os.getenv("OPENAI_TRANSCRIPTION_MODEL", "whisper-1"),
                file=audio,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        raw_segments = _field(response, "segments", []) or []
        segments = []
        for raw in raw_segments:
            text = str(_field(raw, "text", "")).strip()
            if not text:
                continue
            start = max(0.0, float(_field(raw, "start", 0) or 0))
            end = max(start, float(_field(raw, "end", start) or start))
            segments.append(TranscriptSegment(round(start, 2), round(end, 2), text))
        if not segments:
            full_text = str(_field(response, "text", "")).strip()
            if full_text:
                segments.append(TranscriptSegment(0, 0, full_text))
        return segments
    except subprocess.TimeoutExpired as exc:
        raise TranscriptionError("Audio extraction timed out") from exc
    finally:
        audio_path.unlink(missing_ok=True)
