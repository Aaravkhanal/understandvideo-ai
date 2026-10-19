#!/usr/bin/env sh
set -eu

if [ -f .env.local ]; then
  set -a
  # .env.local is intentionally ignored and contains local deployment secrets.
  . ./.env.local
  set +a
fi

exec "$@"
