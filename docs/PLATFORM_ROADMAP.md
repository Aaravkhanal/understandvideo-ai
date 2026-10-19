# UnderstandVideo AI — Product Roadmap

This roadmap turns the current local-first workspace into a reliable, useful,
and commercially deployable video intelligence platform. Each milestone should
ship behind measurable acceptance criteria instead of accumulating unfinished
features.

For the code-level audit, completed fixes, acceptance criteria, and recommended
implementation order, see [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

## Product north star

Users should be able to upload an existing video, receive a trustworthy visual
overview, ask follow-up questions about its contents, and jump to supporting
evidence in under two minutes.
The experience should feel as immediate as search and as grounded as a video
editor's timeline.

Video generation and video editing are explicit non-goals. The platform exists
to understand supplied footage and support evidence-grounded conversation.

## Milestone 0 — Modern product foundation (shipped)

- Responsive dashboard, library, upload workspace, video player, and AI panel.
- Built-in sample footage and scene thumbnails for zero-setup onboarding.
- Validated 100 MB uploads, metadata extraction, SQLite records, SSE chat, and
  per-client request quotas.
- Timeline scene markers and clickable timestamps in answers.
- Dedicated local ports and a single command that launches the web app and API.
- Browser-only fallback when the API or model is unavailable.
- Runtime-sourced limits and API status, conversation-scoped history, truthful
  non-demo fallbacks, and regression tests for core API behavior.
- Public-link import with guarded platform extraction, direct HTTPS media,
  persistent library playback, and HTTP byte-range delivery.
- Persistent background link-import jobs with real progress, cancel, retry,
  restart recovery, and browser-refresh recovery.
- Durable local timeline jobs with saved progress, retry, real evidence-frame
  thumbnails, poster reuse, and restart recovery.
- Inline video rename and cascading deletion across media, derived evidence,
  processing state, and conversation data.
- Durable per-video conversations with history reopening, automatic titles,
  new-thread creation, message counts, inline rename, and thread deletion.
- Optional cloud transcription with saved timestamp spans, text search,
  click-to-seek results, retry state, and answer grounding.
- Shot-aware timelines with visual-cut detection, neutral fallback frames,
  stored detection provenance, and automatic regeneration of legacy timelines.
- Account, workspace, and membership schema; workspace-scoped local video
  access; and a replaceable local storage adapter with isolation coverage.
- Pluggable inference providers and an optional dedicated grounded-answer
  service with bearer-token and media-root enforcement.
- Timestamped notes, portable Markdown/JSON exports, expiring exact-moment
  links, and side-by-side saved-timeline comparison.
- Verified upload container signatures and decodability, plus workspace search
  across titles, URLs, transcripts, notes, conversation messages, and
  user-managed tags.

Success check: a first-time contributor can clone, install, launch, and complete
the sample-video conversation without model weights.

## Milestone 1 — Real understanding engine (next)

1. Connect the shipped worker interface to the local Video-ChatGPT driver.
2. Connect the local Video-ChatGPT/CLIP pipeline without loading model state in
   the request process.
3. Add a cloud multimodal driver for machines without CUDA.
4. Extend the shipped frame/transcript jobs with speaker diarization.
5. Store scene-level evidence and require every answer to cite time ranges.
6. Add cancel, retry, and visible failure states for processing jobs.

Success check: evaluation questions over a 5-minute test set return grounded
answers with valid timestamps at least 90% of the time.

## Milestone 2 — Accounts, storage, and history

1. Add email magic-link and Google OAuth login.
2. Move metadata to PostgreSQL and media to S3-compatible object storage.
3. Replace the local development identity with authenticated workspace membership.
4. Extend shipped durable conversation history, rename, delete, and full-video
   export with conversation-specific exports and cross-device sync.
5. Add signed upload URLs, malware scanning, retention controls,
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

- Extend shipped full-library text search with semantic and tag search.
- Extend shipped structural two-video comparison with grounded semantic changes.
- Speaker-aware transcripts, chapters, highlights, and shareable clips.
- Extend shipped timestamped notes and exact-moment links with team assignments.
- Extend shipped Markdown/JSON exports with support-ticket and content-brief templates.
- API keys and webhooks for programmatic ingestion and completed analyses.

## Release discipline

Every product change should include its smallest relevant automated test, a
manual browser pass at desktop and mobile widths, and a concise changelog entry.
Large model changes should additionally include a repeatable evaluation run and
cost/latency measurements before release.
