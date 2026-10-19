from __future__ import annotations

import os
import subprocess
import wave
from functools import lru_cache
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
    speaker: str | None = None


def _field(value, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


@lru_cache(maxsize=1)
def _local_whisper_model():
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionUnavailable("Install faster-whisper to use free local transcription") from exc
    return WhisperModel(
        os.getenv("LOCAL_WHISPER_MODEL", "small"),
        device=os.getenv("LOCAL_WHISPER_DEVICE", "cpu"),
        compute_type=os.getenv("LOCAL_WHISPER_COMPUTE_TYPE", "int8"),
    )


@lru_cache(maxsize=1)
def _local_diarization_pipeline():
    try:
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise TranscriptionUnavailable("Install pyannote.audio to identify speakers locally") from exc
    token = os.getenv("HUGGINGFACE_TOKEN") or os.getenv("HF_TOKEN") or True
    try:
        return Pipeline.from_pretrained(
            os.getenv("LOCAL_DIARIZATION_MODEL", "pyannote/speaker-diarization-community-1"),
            token=token,
        )
    except Exception as exc:
        raise TranscriptionUnavailable(
            "Log in to Hugging Face locally and accept access to the Community-1 speaker model"
        ) from exc


def _speaker_turns(audio_path: Path) -> list[tuple[float, float, str]]:
    # Passing an in-memory waveform avoids TorchCodec's dependency on a separate
    # system FFmpeg installation. transcribe_video always creates mono PCM16 WAV.
    try:
        import torch
    except ImportError as exc:
        raise TranscriptionUnavailable("Install PyTorch to identify speakers locally") from exc
    with wave.open(str(audio_path), "rb") as audio:
        sample_rate = audio.getframerate()
        channel_count = audio.getnchannels()
        frames = audio.readframes(audio.getnframes())
    waveform = torch.frombuffer(bytearray(frames), dtype=torch.int16).reshape(-1, channel_count).T
    waveform = waveform.to(torch.float32) / 32768.0
    output = _local_diarization_pipeline()({"waveform": waveform, "sample_rate": sample_rate})
    annotation = getattr(output, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = getattr(output, "speaker_diarization", output)
    turns: list[tuple[float, float, str]] = []
    if hasattr(annotation, "itertracks"):
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            turns.append((float(turn.start), float(turn.end), str(speaker)))
    else:
        for item in annotation:
            if len(item) == 2:
                turn, speaker = item
            else:
                turn, _, speaker = item
            turns.append((float(turn.start), float(turn.end), str(speaker)))
    return turns


def _speaker_for_segment(start: float, end: float, turns: list[tuple[float, float, str]]) -> str | None:
    best_label = None
    best_overlap = 0.0
    for turn_start, turn_end, label in turns:
        overlap = max(0.0, min(end, turn_end) - max(start, turn_start))
        if overlap > best_overlap:
            best_overlap = overlap
            best_label = label
    return best_label


def _friendly_speaker_labels(turns: list[tuple[float, float, str]]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for _, _, label in sorted(turns, key=lambda item: item[0]):
        if label not in labels:
            labels[label] = f"Speaker {len(labels) + 1}"
    return labels


def _transcribe_local(audio_path: Path) -> list[TranscriptSegment]:
    try:
        raw_segments, _ = _local_whisper_model().transcribe(
            str(audio_path),
            beam_size=int(os.getenv("LOCAL_WHISPER_BEAM_SIZE", "5")),
            vad_filter=True,
            word_timestamps=True,
        )
        raw_segments = list(raw_segments)
        turns = _speaker_turns(audio_path) if os.getenv("LOCAL_SPEAKER_DIARIZATION", "true").lower() not in {"0", "false", "off"} else []
    except TranscriptionError:
        raise
    except Exception as exc:
        raise TranscriptionError("The local speech models could not process this audio") from exc
    friendly_labels = _friendly_speaker_labels(turns)
    segments: list[TranscriptSegment] = []
    for raw in raw_segments:
        text = str(raw.text).strip()
        if not text:
            continue
        start = max(0.0, float(raw.start))
        end = max(start, float(raw.end))
        raw_speaker = _speaker_for_segment(start, end, turns)
        segments.append(
            TranscriptSegment(
                round(start, 2),
                round(end, 2),
                text,
                friendly_labels.get(raw_speaker) if raw_speaker else None,
            )
        )
    return segments


def _transcribe_openai(audio_path: Path) -> list[TranscriptSegment]:
    if not os.getenv("OPENAI_API_KEY"):
        raise TranscriptionUnavailable("Configure OPENAI_API_KEY or use TRANSCRIPTION_PROVIDER=local")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise TranscriptionUnavailable("Cloud transcription dependencies are not installed") from exc

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


def transcribe_video(video_path: Path, work_root: Path, video_id: str) -> list[TranscriptSegment]:
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise TranscriptionUnavailable("Audio extraction dependencies are not installed") from exc

    derived_dir = work_root / "derived" / video_id
    derived_dir.mkdir(parents=True, exist_ok=True)
    audio_path = derived_dir / "transcript-audio.wav"
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
        "-c:a",
        "pcm_s16le",
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

        provider = os.getenv("TRANSCRIPTION_PROVIDER", "local").strip().lower()
        if provider == "local":
            return _transcribe_local(audio_path)
        if provider == "openai":
            return _transcribe_openai(audio_path)
        raise TranscriptionUnavailable("TRANSCRIPTION_PROVIDER must be local or openai")
    except subprocess.TimeoutExpired as exc:
        raise TranscriptionError("Audio extraction timed out") from exc
    finally:
        audio_path.unlink(missing_ok=True)
