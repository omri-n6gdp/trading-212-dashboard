#!/bin/bash
# Trading 212 Dashboard launcher.
# Starts the FastAPI server (if not running) and opens the dashboard in Safari,
# forcing a fresh load so an updated dashboard never shows a stale cached tab.

set -e

# Resolve the repo relative to this script, so the dashboard works wherever this
# folder lives (it now lives in the ~/code hub).
REPO="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/backend"
PORT=8000
LOG="$HOME/.jarvis/launcher.log"
PID_FILE="$HOME/.jarvis/server.pid"
UV="$HOME/.local/bin/uv"
CONFIG="$HOME/.jarvis/jarvis.config.json"

mkdir -p "$HOME/.jarvis"

# --- 1. Start the server if it is not already responding ---
if curl -sf "http://localhost:$PORT/api/health" >/dev/null 2>&1; then
  echo "$(date) · server already up" >> "$LOG"
else
  echo "$(date) · starting server" >> "$LOG"
  cd "$REPO"
  # `python -m uvicorn` (not the console script) avoids a stale shebang path.
  nohup "$UV" run python -m uvicorn src.main:app --port "$PORT" >>"$LOG" 2>&1 &
  echo $! > "$PID_FILE"
  for i in $(seq 1 60); do
    curl -sf "http://localhost:$PORT/api/health" >/dev/null 2>&1 && break
    sleep 0.5
  done
fi

# --- 2. Decide which page to open ---
# Open the design you last chose (a|b|c); until you've picked one, open the
# launcher so you can choose. A cache-busting query guarantees a fresh load.
THEME=$(python3 -c "import json,os;p=os.path.expanduser('~/.jarvis/jarvis.config.json');print(json.load(open(p)).get('theme','') if os.path.exists(p) else '')" 2>/dev/null || echo "")
case "$THEME" in
  a|b|c) SUB="/$THEME" ;;
  *)     SUB="/" ;;
esac
URL="http://localhost:$PORT$SUB?v=$(date +%s)"

# --- 3. Open Safari and force a fresh navigation ---
# Reuse an existing localhost tab and re-point it (which reloads); otherwise
# open a new tab. Plain `open -a Safari` only focuses a stale tab, which is why
# an old dashboard could linger after an update.
osascript <<OSA 2>/dev/null || open "$URL"
set theURL to "$URL"
tell application "Safari"
  activate
  set didIt to false
  repeat with w in windows
    repeat with t in tabs of w
      if (URL of t) contains "localhost:$PORT" then
        set URL of t to theURL
        set current tab of w to t
        set index of w to 1
        set didIt to true
        exit repeat
      end if
    end repeat
    if didIt then exit repeat
  end repeat
  if not didIt then
    if (count of windows) is 0 then
      make new document with properties {URL:theURL}
    else
      tell window 1 to set current tab to (make new tab with properties {URL:theURL})
    end if
  end if
end tell
OSA
