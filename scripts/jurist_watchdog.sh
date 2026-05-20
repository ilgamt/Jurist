#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$ROOT/logs"
WATCHDOG_LOG="$LOG_DIR/watchdog.log"
USER_DOMAIN="gui/$(id -u)"

mkdir -p "$LOG_DIR"

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log() {
  printf '%s %s\n' "$(timestamp)" "$*" >> "$WATCHDOG_LOG"
}

ensure_launch_agent() {
  local label="$1"
  local plist="$2"

  if ! launchctl print "$USER_DOMAIN/$label" >/dev/null 2>&1; then
    log "WARN $label is not loaded; bootstrapping from $plist"
    if ! launchctl bootstrap "$USER_DOMAIN" "$plist" >> "$WATCHDOG_LOG" 2>&1; then
      log "ERROR failed to bootstrap $label"
      return 1
    fi
  fi

  launchctl enable "$USER_DOMAIN/$label" >> "$WATCHDOG_LOG" 2>&1 || true

  if ! launchctl print "$USER_DOMAIN/$label" 2>/dev/null | /usr/bin/grep -q "state = running"; then
    log "WARN $label is not running; kickstarting"
    if ! launchctl kickstart -k "$USER_DOMAIN/$label" >> "$WATCHDOG_LOG" 2>&1; then
      log "ERROR failed to kickstart $label"
      return 1
    fi
  fi
}

check_file() {
  local path="$1"
  if [ ! -f "$path" ]; then
    log "ERROR missing file: $path"
    return 1
  fi
}

check_sqlite() {
  JURIST_ROOT="$ROOT" "$ROOT/.venv/bin/python" - <<'PY' >> "$WATCHDOG_LOG" 2>&1
import os
from pathlib import Path
import sqlite3
import sys

db_path = Path(os.environ["JURIST_ROOT"]) / "storage" / "jurist.db"
try:
    with sqlite3.connect(db_path) as connection:
        connection.execute("SELECT COUNT(*) FROM telegram_users").fetchone()
        connection.execute("SELECT COUNT(*) FROM telegram_requests").fetchone()
except Exception as exc:
    print(f"ERROR sqlite health check failed: {exc}")
    sys.exit(1)
PY
}

check_recent_log_errors() {
  local path="$1"
  local name="$2"

  [ -f "$path" ] || return 0
  [ -s "$path" ] || return 0

  if /usr/bin/find "$path" -mmin -10 -print | /usr/bin/grep -q .; then
    if /usr/bin/tail -n 200 "$path" | /usr/bin/grep -Eiq 'Traceback|ERROR|CRITICAL|invalid_api_key|Unauthorized|401|403|429|rate limit'; then
      log "WARN recent suspicious entries found in $name: $path"
    fi
  fi
}

main() {
  log "INFO watchdog tick started"

  check_file "$ROOT/.env" || true
  check_file "$ROOT/credentials/google_oauth_client.json" || true
  check_file "$ROOT/credentials/google_token.json" || true
  check_file "$ROOT/storage/jurist.db" || true

  if ! check_sqlite; then
    log "ERROR sqlite check failed"
  fi

  ensure_launch_agent "com.jurist.telegram-service" "/Users/a1/Library/LaunchAgents/com.jurist.telegram-service.plist" || true
  ensure_launch_agent "com.jurist.telegram-worker" "/Users/a1/Library/LaunchAgents/com.jurist.telegram-worker.plist" || true
  ensure_launch_agent "com.jurist.dashboard-admin" "/Users/a1/Library/LaunchAgents/com.jurist.dashboard-admin.plist" || true

  check_recent_log_errors "$LOG_DIR/telegram-service.err.log" "telegram-service"
  check_recent_log_errors "$LOG_DIR/telegram-worker.err.log" "telegram-worker"
  check_recent_log_errors "$LOG_DIR/dashboard-admin.err.log" "dashboard-admin"

  log "INFO watchdog tick completed"
}

main "$@"
