# Production launch runbook

This runbook is the safe path from local development to a small multi-user beta.
Use a separate staging host and, ideally, a separate Supabase project before
pointing the public domain at the stack.

## 1. Provision the host

The host needs Docker Engine with Compose, persistent disk, HTTPS termination,
and enough compute for the selected local models. Keep ports 8787, 6379, and
3310 private. Expose only the HTTPS proxy in front of port 8080.

The checked-in stack runs:

- a static Nginx web frontend and same-origin `/api` proxy;
- two API workers without in-process media jobs;
- one durable job worker with a Redis lease;
- Redis with append-only persistence;
- ClamAV with persistent signatures;
- a shared private media volume; and
- an on-demand PostgreSQL backup job.

## 2. Configure secrets

Copy `.env.production.example` to a secret manager or a local untracked
`.env.production`. Replace every placeholder. Never use the development database
password, service-role key, worker token, or publishable key in production.

Before launch, rotate the Supabase database password and service-role key, then
update the secret manager. The browser receives only the Supabase publishable
key; it must never receive the service-role key.

Run:

```sh
npm run validate:production
docker compose -f compose.production.yml config --quiet
```

Both commands must succeed before deployment.

## 3. Configure Supabase Auth

In Supabase Auth URL Configuration, set the Site URL to `APP_PUBLIC_URL` and add
the same HTTPS origin to the redirect allow-list. Keep localhost redirects only
for local development. Confirm that the private Storage policies and all SQL
migrations are applied.

## 4. Start staging

```sh
docker compose -f compose.production.yml up -d --build
docker compose -f compose.production.yml ps
curl -fsS https://staging.example.com/api/ready
```

The readiness response must be `ready`. Verify sign-in, upload, link import,
playback, transcript, chat, deletion, invitation, and API-key revocation with
non-sensitive test footage.

## 5. Exercise concurrency

Start with a bounded read test:

```sh
npm run test:load -- https://staging.example.com --requests 200 --concurrency 20
```

For an authenticated endpoint, add `--path /api/videos --token uv_...` using a
temporary API key, then revoke the key after the test. Increase concurrency only
after the failure count stays at zero and p95 latency remains acceptable.

Test several simultaneous uploads separately with disposable media. Watch API,
worker, Redis, database, disk, ClamAV, and model-host resource usage.

## 6. Backups and restore drills

Run a database backup manually or from the host scheduler:

```sh
docker compose -f compose.production.yml --profile maintenance run --rm backup
```

The job creates and verifies a compressed `pg_dump` in the private
`database-backups` volume and removes dumps older than `BACKUP_RETENTION_DAYS`.
Supabase Storage and host media volumes need their own provider snapshots. Test a
restore into staging before public launch; an untested backup is not a recovery
plan.

## 7. Monitoring

Monitor `/api/ready`, container health, restart counts, disk usage, database
connections, Redis memory, ClamAV freshness, queue depth from `/api/metrics`, and
model-host availability. Forward container logs to the host's log service and
alert on repeated 5xx responses, failed imports, failed transcripts, or growing
queue depth.

## 8. Launch gate

Launch to a small invited beta only when:

- production validation, unit tests, and browser tests pass;
- database and object-storage credentials have been rotated;
- HTTPS, CORS, and Supabase redirects use the final domain;
- malware scanning fails closed;
- a backup and restore drill succeeds;
- concurrency tests have no request failures;
- privacy and terms pages show the operator's real support contact; and
- monitoring alerts reach a person who can respond.

Expand access gradually and keep upload limits conservative until real workload
measurements justify increasing them.
