#!/usr/bin/env bash
# Kill whatever is listening on a TCP port. `make run`/`make dev` call this
# first, so a reload worker orphaned by a previous Ctrl+C never blocks the
# next start with "Address already in use".
#
#   scripts/free-port.sh 8000
#
# Looks for the listening pid with whichever of lsof / fuser / ss is on PATH
# (lsof covers macOS and most Linux; fuser and ss cover Linux boxes that don't
# have lsof installed). If none of the three are available — a minimal
# container image, or a plain Windows shell with no WSL/Git Bash tooling —
# this is a no-op rather than a failure: set SS_SKIP_FREE_PORT=1 to skip it
# outright and silence the notice.
set -euo pipefail
PORT="${1:?usage: free-port.sh <port>}"

[ -n "${SS_SKIP_FREE_PORT:-}" ] && exit 0

find_pids() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true
  elif command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "$PORT" 2>/dev/null | tr -s ' \t' '\n' | grep -E '^[0-9]+$' || true
  elif command -v ss >/dev/null 2>&1; then
    # ss -ltnp prints e.g. users:(("uvicorn",pid=1234,fd=7)) on the matching line.
    ss -ltnp 2>/dev/null | awk -v p=":$PORT " '$4 ~ p' | grep -oE 'pid=[0-9]+' | cut -d= -f2 || true
  fi
}

if ! command -v lsof >/dev/null 2>&1 && ! command -v fuser >/dev/null 2>&1 \
  && ! command -v ss >/dev/null 2>&1; then
  echo "▸ Can't check port $PORT (no lsof, fuser or ss on PATH) — skipping" >&2
  exit 0
fi

pids="$(find_pids)"
[ -z "$pids" ] && exit 0

echo "▸ Port $PORT is in use (pid $(echo "$pids" | xargs | tr ' ' ',')) — freeing it" >&2
kill $pids 2>/dev/null || true

for _ in 1 2 3 4 5; do
  sleep 0.2
  pids="$(find_pids)"
  [ -z "$pids" ] && exit 0
done

kill -9 $pids 2>/dev/null || true
