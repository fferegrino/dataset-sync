#!/usr/bin/env bash
# Ping a healthchecks.io-style dead man's switch: ping.sh <url> [success|fail|start].
# Never fails: a missed ping alerts by itself, and failing a run that published fine over a monitoring hiccup would
# be perverse.
set -uo pipefail

url="${1:-}"
status="${2:-success}"

if [ -z "$url" ]; then
  echo "::warning::No healthcheck URL; skipping ping."
  exit 0
fi

case "$status" in
  success) ;;
  fail | start) url="${url%/}/$status" ;;
  *)
    echo "::warning::Unknown healthcheck status '$status'; expected success, fail or start. Skipping ping."
    exit 0
    ;;
esac

curl -fsS -m 10 --retry 3 -o /dev/null "$url" || echo "::warning::Failed to ping the healthcheck."
