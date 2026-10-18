from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Callable


def inference_mode() -> str:
    if os.getenv("OPENAI_API_KEY"):
        return "cloud-vision"
    return os.getenv("INFERENCE_MODE", "demo")


def sample_video_frames(path: Path, count: int = 8) -> list[tuple[float, str]]:
    try:
        import cv2
    except ImportError:
        return []

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return []
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = capture.get(cv2.CAP_PROP_FPS) or 1
    if frame_count <= 0:
        capture.release()
        return []

    indexes = sorted({round(i * (frame_count - 1) / max(1, count - 1)) for i in range(count)})
    sampled: list[tuple[float, str]] = []
    for index in indexes:
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if not ok:
            continue
        height, width = frame.shape[:2]
        scale = min(1.0, 960 / max(width, height))
        if scale < 1:
            frame = cv2.resize(frame, (int(width * scale), int(height * scale)))
        encoded, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
        if encoded:
            sampled.append((index / fps, base64.b64encode(buffer).decode("ascii")))
    capture.release()
    return sampled


def answer_video_question(
    path: Path,
    question: str,
    history: list[dict],
    duration: float,
    fallback: Callable[[str, float], str],
) -> str:
    is_demo_video = "trail-runner-demo" in path.name.lower()
    if not os.getenv("OPENAI_API_KEY"):
        if not is_demo_video:
            return (
                "Your video is uploaded and ready, but a semantic vision model is not configured on this server yet. "
                "Set OPENAI_API_KEY (or connect the local Video-ChatGPT weights) and restart the app to answer questions from real frames."
            )
        return fallback(question, duration)

    frames = sample_video_frames(path)
    if not frames:
        return "I couldn’t extract readable frames from this video. Try an MP4 encoded with H.264 or a WebM file."

    try:
        from openai import OpenAI

        history_text = "\n".join(f"{item['role']}: {item['content']}" for item in history[-6:])
        frame_labels = ", ".join(f"frame {index + 1} = {seconds:.1f}s" for index, (seconds, _) in enumerate(frames))
        prompt = (
            "You are a video-understanding assistant. Answer only from the supplied sampled frames and conversation. "
            "Never claim details that are not visible. Cite supporting moments using exactly [[MM:SS]]. "
            "If the evidence is insufficient, say so clearly. Keep the answer concise and conversational.\n\n"
            f"Video duration: {duration:.1f}s. Frame timing: {frame_labels}.\n"
            f"Conversation so far:\n{history_text or '(none)'}\n\nUser question: {question}"
        )
        content = [{"type": "input_text", "text": prompt}]
        content.extend(
            {"type": "input_image", "image_url": f"data:image/jpeg;base64,{image}", "detail": "low"}
            for _, image in frames
        )
        response = OpenAI().responses.create(
            model=os.getenv("OPENAI_VISION_MODEL", "gpt-4.1-mini"),
            input=[{"role": "user", "content": content}],
        )
        return response.output_text.strip() or "I couldn’t form an answer from the sampled frames."
    except Exception:
        if is_demo_video:
            return fallback(question, duration)
        return "I couldn’t reach the configured vision model. Your upload is safe; retry the question after checking the server connection."
