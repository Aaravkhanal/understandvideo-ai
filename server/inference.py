from __future__ import annotations

import base64
import hashlib
import logging
import os
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Protocol


logger = logging.getLogger(__name__)
DEMO_VIDEO = Path(__file__).resolve().parents[1] / "public" / "demo" / "trail-runner-demo.webm"


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def demo_video_digest() -> str | None:
    return file_digest(DEMO_VIDEO) if DEMO_VIDEO.exists() else None


def is_demo_video(path: Path) -> bool:
    expected = demo_video_digest()
    if not expected or not path.exists() or path.stat().st_size != DEMO_VIDEO.stat().st_size:
        return False
    return file_digest(path) == expected


def inference_mode() -> str:
    if os.getenv("VIDEO_WORKER_URL"):
        return "video-worker"
    if os.getenv("OLLAMA_VISION_MODEL"):
        return "local-vision"
    if os.getenv("OPENAI_API_KEY"):
        return "cloud-vision"
    return os.getenv("INFERENCE_MODE", "demo")


def sample_video_frames(path: Path, count: int = 8, max_dimension: int = 960) -> list[tuple[float, str]]:
    try:
        import cv2
    except ImportError:
        return []

    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            return []
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = capture.get(cv2.CAP_PROP_FPS) or 1
        if frame_count <= 0:
            return []

        sample_count = min(16, max(1, count))
        indexes = sorted({round(i * (frame_count - 1) / max(1, sample_count - 1)) for i in range(sample_count)})
        sampled: list[tuple[float, str]] = []
        for index in indexes:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            scale = min(1.0, max(224, max_dimension) / max(width, height))
            if scale < 1:
                frame = cv2.resize(frame, (int(width * scale), int(height * scale)))
            encoded, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
            if encoded:
                sampled.append((index / fps, base64.b64encode(buffer).decode("ascii")))
        return sampled
    finally:
        capture.release()


@dataclass(frozen=True)
class InferenceRequest:
    path: Path
    question: str
    history: list[dict]
    duration: float
    fallback: Callable[[str, float], str]
    transcript: list[dict] | None = None


class InferenceAdapter(Protocol):
    def answer(self, request: InferenceRequest) -> str: ...


class DemoInferenceAdapter:
    def answer(self, request: InferenceRequest) -> str:
        if not is_demo_video(request.path):
            return (
                "Your video is uploaded and ready, but a semantic vision model is not configured on this server yet. "
                "Set OPENAI_API_KEY (or connect the local Video-ChatGPT weights) and restart the app to answer questions from real frames."
            )
        return request.fallback(request.question, request.duration)


class OpenAIInferenceAdapter:
    def answer(self, request: InferenceRequest) -> str:
        frames = sample_video_frames(request.path)
        if not frames:
            return "I couldn’t extract readable frames from this video. Try an MP4 encoded with H.264 or a WebM file."

        try:
            from openai import OpenAI

            history_text = "\n".join(f"{item['role']}: {item['content']}" for item in request.history[-6:])
            frame_labels = ", ".join(f"frame {index + 1} = {seconds:.1f}s" for index, (seconds, _) in enumerate(frames))
            transcript_text = "\n".join(
                f"[{float(item['start_seconds']):.1f}s–{float(item['end_seconds']):.1f}s] {item['text']}"
                for item in (request.transcript or [])[:80]
            )
            prompt = (
                "You are a video-understanding assistant. Answer only from the supplied sampled frames and conversation. "
                "A timestamped speech transcript may also be supplied. Never claim details outside this evidence. "
                "Cite supporting moments using exactly [[MM:SS]]. "
                "If the evidence is insufficient, say so clearly. Keep the answer concise and conversational.\n\n"
                f"Video duration: {request.duration:.1f}s. Frame timing: {frame_labels}.\n"
                f"Transcript evidence:\n{transcript_text or '(none)'}\n"
                f"Conversation so far:\n{history_text or '(none)'}\n\nUser question: {request.question}"
            )
            content = [{"type": "input_text", "text": prompt}]
            content.extend(
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{image}", "detail": "low"}
                for _, image in frames
            )
            response = OpenAI(timeout=float(os.getenv("OPENAI_REQUEST_TIMEOUT", "60"))).responses.create(
                model=os.getenv("OPENAI_VISION_MODEL", "gpt-4.1-mini"),
                input=[{"role": "user", "content": content}],
            )
            return response.output_text.strip() or "I couldn’t form an answer from the sampled frames."
        except Exception:
            logger.exception("Vision inference failed for video %s", request.path.name)
            if is_demo_video(request.path):
                return request.fallback(request.question, request.duration)
            return "I couldn’t reach the configured vision model. Your upload is safe; retry the question after checking the server connection."


class OllamaInferenceAdapter:
    def answer(self, request: InferenceRequest) -> str:
        frames = sample_video_frames(
            request.path,
            count=int(os.getenv("OLLAMA_FRAME_COUNT", "4")),
            max_dimension=int(os.getenv("OLLAMA_MAX_FRAME_DIMENSION", "448")),
        )
        if not frames:
            return "I couldn’t extract readable frames from this video. Try an MP4 encoded with H.264 or a WebM file."
        history_text = "\n".join(f"{item['role']}: {item['content']}" for item in request.history[-6:])
        frame_labels = ", ".join(f"image {index + 1} = {seconds:.1f}s" for index, (seconds, _) in enumerate(frames))
        transcript_text = "\n".join(
            f"[{float(item['start_seconds']):.1f}s–{float(item['end_seconds']):.1f}s] {item['text']}"
            for item in (request.transcript or [])[:80]
        )
        prompt = (
            "Answer only from the supplied video frames and transcript. Never invent details. "
            "Cite supporting moments using exactly [[MM:SS]]. If evidence is insufficient, say so. "
            "Keep the answer concise and conversational.\n\n"
            f"Duration: {request.duration:.1f}s. Image timing: {frame_labels}.\n"
            f"Transcript:\n{transcript_text or '(none)'}\n"
            f"Conversation:\n{history_text or '(none)'}\n\nQuestion: {request.question}"
        )
        body = json.dumps({
            "model": os.getenv("OLLAMA_VISION_MODEL", "qwen3-vl:2b-instruct"),
            "stream": False,
            "think": False,
            "messages": [{"role": "user", "content": prompt, "images": [image for _, image in frames]}],
            "options": {"temperature": 0.1, "num_predict": 160},
        }).encode("utf-8")
        endpoint = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/") + "/api/chat"
        try:
            ollama_request = urllib.request.Request(endpoint, data=body, method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(ollama_request, timeout=float(os.getenv("OLLAMA_REQUEST_TIMEOUT", "300"))) as response:
                payload = json.loads(response.read().decode("utf-8"))
            answer = str(payload.get("message", {}).get("content", "")).strip()
            if not answer:
                raise ValueError("Ollama returned an empty answer")
            return answer
        except Exception:
            logger.exception("Local Ollama vision inference failed for %s", request.path.name)
            if is_demo_video(request.path):
                return request.fallback(request.question, request.duration)
            return "The local vision model is temporarily unavailable. Your video is safe; retry after the model finishes loading."


class HttpWorkerInferenceAdapter:
    def answer(self, request: InferenceRequest) -> str:
        url = os.environ["VIDEO_WORKER_URL"].rstrip("/") + "/answer"
        body = json.dumps(
            {
                "video_path": str(request.path.resolve()),
                "question": request.question,
                "history": request.history[-8:],
                "duration": request.duration,
                "transcript": (request.transcript or [])[:80],
                "demo_fallback": request.fallback(request.question, request.duration)
                if is_demo_video(request.path)
                else None,
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token := os.getenv("VIDEO_WORKER_TOKEN"):
            headers["Authorization"] = f"Bearer {token}"
        worker_request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(worker_request, timeout=float(os.getenv("VIDEO_WORKER_TIMEOUT", "90"))) as response:
                payload = json.loads(response.read().decode("utf-8"))
            answer = str(payload.get("answer", "")).strip()
            if not answer:
                raise ValueError("Worker returned an empty answer")
            return answer
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.HTTPError):
            logger.exception("Grounded-answer worker request failed")
            return "The video worker is temporarily unavailable. Your upload is safe; retry this question shortly."


def inference_adapter() -> InferenceAdapter:
    if os.getenv("VIDEO_WORKER_URL"):
        return HttpWorkerInferenceAdapter()
    if os.getenv("OLLAMA_VISION_MODEL"):
        return OllamaInferenceAdapter()
    if os.getenv("OPENAI_API_KEY"):
        return OpenAIInferenceAdapter()
    return DemoInferenceAdapter()


def answer_video_question(
    path: Path,
    question: str,
    history: list[dict],
    duration: float,
    fallback: Callable[[str, float], str],
    transcript: list[dict] | None = None,
) -> str:
    request = InferenceRequest(path, question, history, duration, fallback, transcript)
    return inference_adapter().answer(request)
