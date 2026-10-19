# UnderstandVideo implementation plan

Updated: 19 October 2026

## Goal

Turn the current local-first video Q&A workspace into a trustworthy product in
which a user can upload footage, receive a grounded overview, ask follow-up
questions, and jump to cited evidence without the system fabricating details.

## Current architecture

```text
React + Vite
  ├─ local playback and bundled demo
  ├─ upload, timeline, overview, and SSE chat UI
  └─ /api proxy during local development
          │
FastAPI
  ├─ upload validation and metadata probing
  ├─ SQLite account/workspace-scoped videos, conversations, and messages
  ├─ local storage adapter (cloud implementation pending)
  ├─ request limits and public runtime config
  └─ inference adapter
       ├─ deterministic bundled demo
       └─ optional sampled-frame OpenAI vision request
```

The original `video_chatgpt/` research stack remains separate from the FastAPI
request process and is not yet connected as a production inference worker.

## Audit results and fixes completed

| Area | Finding | Resolution |
| --- | --- | --- |
| Answer integrity | Browser fallback returned alpine-demo facts for arbitrary uploads. | Custom uploads now receive a truthful model-not-connected response; deterministic facts are restricted to the bundled demo. |
| Demo identity | A custom file could impersonate the demo by using its filename. | Demo behavior now requires a SHA-256 content match with the bundled asset. |
| Conversation memory | Server history lookup mixed every conversation belonging to a video. | History is now filtered by both `video_id` and `conversation_id`; the frontend retains the server-issued conversation ID. |
| Scene presentation | Demo thumbnails and alpine labels appeared for every uploaded file. | Custom uploads use neutral timeline reference points with no fabricated imagery or scene claims. |
| Overview | The custom-upload overview contained demo-specific people, location, and mood. | Overview content now comes from API state and clearly distinguishes generated content from unavailable analysis. |
| Runtime status | UI always said systems were ready and displayed hardcoded usage. | The UI reads `/api/config`, reports API/demo state, and calculates usage from the real hourly limit. |
| Upload limits | The browser and error message assumed 100 MB even when the server was configured differently. | Public config now exposes the active limit and server errors format it dynamically. |
| Search and shortcuts | Search, recent, `⌘/`, and `⌘K` looked interactive but did nothing. | Search opens and focuses the library, filtering works, recent opens the library, and both shortcuts are implemented. |
| Input validation | Blank or excessively long chat messages could reach inference. | Pydantic validation trims messages and enforces a 4,000-character maximum. |
| Persistence | SQLite connections were committed but not reliably closed. | A transaction context manager now commits/rolls back and always closes connections. |
| Diagnostics | Cloud inference failures were silently swallowed. | Failures are logged server-side while users still receive a safe, non-sensitive error. |
| Frame extraction | Video capture could leak on early return and accepted unbounded sample counts. | Capture release is guaranteed and sampling is clamped to 1–16 frames. |
| Reproducibility | Every JavaScript dependency used the moving `latest` tag. | Direct dependencies are pinned to the versions verified by the current lockfile. |
| Legacy training | Video token length was fixed at `356` regardless of the saved feature shape. | Training now derives token length from the loaded feature tensor and closes the dataset file cleanly. |
| Regression coverage | No automated API checks protected these behaviors. | Tests now cover config, MIME rejection, configured size limits, blank input, and conversation isolation. |
| Online sources | Only local file uploads could enter the workspace. | Public links from major video platforms and direct HTTPS media can now enter the same library, playback, timeline, and chat flow with SSRF checks, limits, timeouts, and range streaming. |
| Durable timeline | Custom videos used temporary, evenly spaced text placeholders and fake browser progress. | Uploads now create persisted jobs, extract real evidence thumbnails in the background, expose saved progress/failure state, resume queued work after restart, and support retry. |
| Library management | Saved videos could not be renamed or removed. | The library now supports inline rename and a deliberate confirmation step for deletion; the API removes media, evidence frames, jobs, scenes, and messages together. |
| Conversation continuity | Conversation IDs were scoped correctly but users could not reopen or name prior threads. | Conversations are now durable records with automatic titles, selection, new-thread creation, rename, message counts, and history reopening. |
| Transcript evidence | Speech was not extracted, searchable, or available to the answer model. | An optional cloud transcription job now extracts compressed audio, persists timestamped spans, supports search and click-to-seek, exposes retry/status, and supplies transcript evidence to grounded answers. |
| Shot-aware timeline | Evidence frames were selected only at even intervals, so meaningful visual transitions could be missed. | Timeline extraction now scans for strong histogram changes, stores per-moment detection provenance, fills sparse results with distinct neutral reference frames, and automatically upgrades legacy timelines. |
| Workspace/storage foundation | Media records had no ownership boundary and filesystem operations were embedded directly in routes. | Local account, workspace, and membership records now scope every user-facing video lookup; a cross-workspace regression test prevents access, and file allocation/deletion now runs through a replaceable local storage adapter. |
| Inference isolation | Grounded cloud inference was hardwired into the API module. | Demo, cloud, and HTTP-worker adapters now share one contract; a dedicated worker service can run model calls outside the API process and restricts requests by bearer token and media root. |
| Research and review workflow | Evidence could be discussed only inside chat and one video at a time. | Users can add clickable timestamp notes, export complete Markdown/JSON research records, create expiring exact-moment links, and compare two saved timelines side by side. |
| Large link imports | Link imports held one request open and gave no measurable progress or cancellation. | Imports now run as persisted background jobs with percentage, cancel, retry, restart recovery, and browser-refresh recovery. |
| Library search | Search matched only the locally loaded video title and URL. | Server-side workspace search now includes titles, source links, transcript text, timestamped notes, conversation messages, and user-managed tags. |
| Media authenticity | Uploads trusted the declared browser MIME type. | The API now verifies container signatures and rejects files that cannot be decoded into video dimensions and duration. |
| Conversation cleanup | Threads could be created and renamed but not removed independently. | A scoped delete route and confirmation UI remove one conversation with all of its messages. |
| Frontend regression coverage | Timestamp formatting and citation parsing had no direct tests. | Node-based frontend unit tests now run inside the standard `npm run check` workflow. |
| Transcript structure | Transcript lines had no navigation above raw timestamp search. | Transcription now creates deterministic chapters, stores optional speaker labels, and supports saved quote highlights and report export. |
| Privacy controls | Retention and deletion were all-or-nothing manual actions. | Each video can receive a durable expiry timestamp; expired media and derivatives are permanently removed by API startup or the worker. |
| Malware defense | Container signatures and decoding caught disguised files but no scanner was connected. | Optional/required ClamAV scanning is now wired through `MALWARE_SCAN_MODE`; required mode fails closed. |
| Durable quotas | Rate limits disappeared on every restart and were keyed to an IP. | Hourly counters now persist in SQLite and are keyed to the active account and action. |
| Reliability | Processing depended on the API process and had no browser/CI gate. | A standalone persisted job worker, request timing/queue metrics, desktop/mobile Playwright tests, and GitHub Actions checks are included. |
| Review and delivery | Review status, comments, clips, and polished reports were absent. | Local review state/assignee/comment APIs, downloadable clips, and styled HTML reports are implemented. |

## Phase 1 — Make analysis real and durable

Priority: highest. This converts the visual shell into a reliable understanding
product.

1. Connect the shipped HTTP worker adapter to the local Video-ChatGPT model.
2. Extend the shipped local frame/transcript jobs into a separate worker for
   shot detection, speaker diarization, and embeddings.
3. Add cancellation, idempotency keys, and bounded automatic retries to the
   shipped durable job table.
4. Extend persisted evidence with real shot boundaries, speaker labels, and
   model provenance.
5. Make the overview and answer citations consume only persisted evidence.
6. Reject or qualify every claim that cannot be supported by a stored time range.

Acceptance criteria:

- A five-minute upload never blocks an API worker while analysis runs.
- Refreshing the browser preserves processing state and the completed result.
- At least 90% of answers in a fixed evaluation set contain a valid citation to
  supporting evidence.
- Failed extraction and inference are retryable and visible to the user.

## Phase 2 — Complete the library workflow

1. Add authenticated ownership checks to the existing HTTP range playback route.
2. Extend the shipped conversation deletion and full-video export with conversation-specific export.
3. Extend shipped durable public-link imports with distributed-worker cancellation.
4. Extend shipped title, transcript, note, and message search with tags and semantic search.

Acceptance criteria:

- A user can reload, reopen a prior upload, seek, and continue the correct
  conversation.
- Deleted media, thumbnails, evidence, and messages are removed by one auditable
  workflow.
- Search results are stable across devices for the same account.

## Phase 3 — Security, privacy, and accounts

1. Replace the shipped local identity with verified email/OAuth sessions and membership checks.
2. Implement an S3-compatible version of the shipped storage adapter with signed upload/playback URLs.
3. Verify file signatures, decode media in a sandbox, scan uploads, and enforce
   duration, resolution, and codec limits.
4. Add configurable retention, deletion, download, and privacy controls.
5. Replace in-memory IP quotas with an account-aware token bucket in Redis.
6. Apply trusted proxy configuration before using forwarded client addresses.

Acceptance criteria:

- Automated tests prove that one account cannot read another account's videos or
  messages.
- Upload and playback URLs expire and cannot be reused outside their intended
  scope.
- A deletion request removes all derived data within a documented time limit.

## Phase 4 — Quality and operations

1. Extend shipped frontend timestamp/citation unit tests with fallback, search,
   and state-transition coverage.
2. Add Playwright tests for demo, real upload, API loss, processing failure, and
   mobile navigation.
3. Add structured logs, traces, latency histograms, queue depth, model cost, and
   answer-quality sampling.
4. Pin JavaScript dependency versions and add automated dependency/security
   updates.
5. Add CI for build, Python compilation, API tests, browser tests, and a small
   deterministic model evaluation.
6. Add database migrations instead of runtime `CREATE TABLE` statements.

Acceptance criteria:

- Every pull request runs the same `npm run check` command used locally.
- Production alerts distinguish upload, extraction, model, and streaming
  failures.
- Releases include measured latency, answer quality, and model cost deltas.

## Phase 5 — Differentiating product features

- Compare two videos and explain changes with paired timestamps.
- Speaker-aware transcript navigation and quote search.
- Shareable links that open at an exact cited moment.
- Structured exports for research notes, support tickets, and content briefs.
- Team annotations, assignments, and review states.
- API keys and webhooks for programmatic ingestion.

## Still hardcoded by design

The following values are intentionally deterministic because they belong only to
the bundled onboarding demo:

- demo filename, duration label, poster, scene stills, and alpine description;
- three suggested starter questions;
- demo fallback answers and timestamps.

They must remain isolated behind demo identity checks. Product limits, API
status, usage, custom scene labels, conversation identity, and upload validation
must continue to come from runtime state or configuration.

## Recommended next implementation slice

Replace the local development identity with verified authentication, then add an
S3-compatible storage implementation. Then connect the shipped worker contract
to local Video-ChatGPT and extend structural comparison with grounded semantic
change explanations.

## What Aarav needs to provide for transcript quality and production security

For item 1, local Whisper plus pyannote is connected. The schema stores a
`speaker` per segment, chapters and highlights work, and workspace search covers
every saved transcript. A representative set of 10–20 videos with known speakers
is still needed to measure diarization and chapter quality before production tuning.

For item 2, use the checked-in `compose.production.yml` stack and set
`MALWARE_SCAN_MODE=required`. It starts a private ClamAV service and the standalone
locked-down worker only after signature loading and its health check succeed, with
CPU/memory/process/file-size limits, non-root identity, read-only root filesystem,
and no worker Linux capabilities. A one-shot initializer owns only the two data
volumes and then exits. New videos inherit `DEFAULT_RETENTION_DAYS` (90 by
default), and CI rejects unsafe production configuration. Supabase Auth, verified
JWTs, private Storage policies, server-side mirroring, and five-minute signed
playback URLs are connected. The remaining cutover work needs PostgreSQL/Redis,
deployment-managed encryption secrets, and a final retention policy. The
checked-in PostgreSQL baseline records migration intent, but application queries
remain SQLite-first until `DATABASE_URL` is integrated.
