# UnderstandVideo AI — Product Roadmap

This roadmap turns the current local-first workspace into a reliable, useful,
and commercially deployable video intelligence platform. Each milestone should
ship behind measurable acceptance criteria instead of accumulating unfinished
features.

## Product north star

Users should be able to upload a video, receive a trustworthy visual overview,
ask follow-up questions, and jump to supporting evidence in under two minutes.
The experience should feel as immediate as search and as grounded as a video
editor's timeline.

## Milestone 0 — Modern product foundation (shipped)

- Responsive dashboard, library, upload workspace, video player, and AI panel.
- Built-in sample film and generated scene imagery for zero-setup onboarding.
- Validated 100 MB uploads, metadata extraction, SQLite records, SSE chat, and
  per-client request quotas.
- Timeline scene markers and clickable timestamps in answers.
- Dedicated local ports and a single command that launches the web app and API.
- Browser-only fallback when the API or model is unavailable.

Success check: a first-time contributor can clone, install, launch, and complete
the sample-video conversation without model weights.

## Milestone 1 — Real understanding engine (next)

1. Add an inference adapter interface with `demo`, `local`, and `cloud` drivers.
2. Connect the local Video-ChatGPT/CLIP pipeline without loading model state in
   the request process.
3. Add a cloud multimodal driver for machines without CUDA.
4. Extract real frames, shots, audio, and transcript segments in background jobs.
5. Store scene-level evidence and require every answer to cite time ranges.
6. Add cancel, retry, and visible failure states for processing jobs.

Success check: evaluation questions over a 5-minute test set return grounded
answers with valid timestamps at least 90% of the time.

## Milestone 2 — Accounts, storage, and history

1. Add email magic-link and Google OAuth login.
2. Move metadata to PostgreSQL and media to S3-compatible object storage.
3. Scope every video, transcript, and conversation to its owner or workspace.
4. Implement durable conversation history, rename, delete, and export actions.
5. Add signed upload URLs, MIME sniffing, malware scanning, retention controls,
   and user-visible privacy settings.

Success check: users can securely return on another device and continue an
existing video conversation without cross-account data exposure.

## Milestone 3 — Production scale and quality

1. Move analysis to Redis-backed workers with idempotent jobs and retries.
2. Add token-bucket limits by account tier, file duration, and model cost.
3. Instrument latency, model failures, queue depth, and answer quality.
4. Add unit tests for validation and quotas, integration tests for upload/SSE,
   and browser tests for the complete happy path and recovery paths.
5. Add CDN delivery, range requests, thumbnail generation, and cleanup jobs.

Success check: 95th-percentile chat response begins within two seconds after a
video is ready, with no lost jobs during worker restarts.

## Milestone 4 — Differentiating workflows

- Search across a full video library, not just the open video.
- Compare two recordings and explain what changed.
- Speaker-aware transcripts, chapters, highlights, and shareable clips.
- Team annotations and links that open at an exact cited moment.
- Structured exports for research notes, support tickets, and content briefs.
- API keys and webhooks for programmatic ingestion and completed analyses.

## Release discipline

Every product change should include its smallest relevant automated test, a
manual browser pass at desktop and mobile widths, and a concise changelog entry.
Large model changes should additionally include a repeatable evaluation run and
cost/latency measurements before release.
