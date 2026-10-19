#!/bin/sh
set -eu

if [ -x "./.venv/bin/python" ]; then
  exec ./.venv/bin/python "$@"
fi

exec python3 "$@"
