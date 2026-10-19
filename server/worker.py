from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from server.inference import DemoInferenceAdapter, InferenceRequest, OpenAIInferenceAdapter


MEDIA_ROOT = Path(os.getenv("WORKER_MEDIA_ROOT", Path(__file__).resolve().parent / "uploads")).resolve()
WORKER_TOKEN = os.getenv("VIDEO_WORKER_TOKEN")

app = FastAPI(title="UnderstandVideo grounded-answer worker", version="0.1.0")


class WorkerAnswerRequest(BaseModel):
    video_path: str
    question: str = Field(min_length=1, max_length=4_000)
    history: list[dict] = Field(default_factory=list, max_length=8)
    duration: float = Field(gt=0, le=86_400)
    transcript: list[dict] = Field(default_factory=list, max_length=80)
    demo_fallback: str | None = Field(default=None, max_length=4_000)


def authorized(authorization: str | None) -> None:
    if WORKER_TOKEN and authorization != f"Bearer {WORKER_TOKEN}":
        raise HTTPException(status_code=401, detail="Worker authorization failed")


def media_path(value: str) -> Path:
    path = Path(value).resolve()
    if not path.is_relative_to(MEDIA_ROOT) or not path.is_file():
        raise HTTPException(status_code=404, detail="Worker media was not found")
    return path


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "provider": "openai" if os.getenv("OPENAI_API_KEY") else "demo"}


@app.post("/answer")
async def answer(payload: WorkerAnswerRequest, authorization: str | None = Header(default=None)) -> dict[str, str]:
    authorized(authorization)
    path = media_path(payload.video_path)
    fallback = lambda _question, _duration: payload.demo_fallback or "The worker needs a configured vision model."
    request = InferenceRequest(
        path=path,
        question=payload.question,
        history=payload.history,
        duration=payload.duration,
        fallback=fallback,
        transcript=payload.transcript,
    )
    adapter = OpenAIInferenceAdapter() if os.getenv("OPENAI_API_KEY") else DemoInferenceAdapter()
    return {"answer": await asyncio.to_thread(adapter.answer, request)}
