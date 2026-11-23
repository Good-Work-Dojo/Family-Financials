#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
# One data refresh, run by the scheduler at the household's hours (its crontab is written from household.yaml as it
# starts: deploy/make_crontab.py) and by the refresher when the dashboard asks (deploy/refresh_runner.py), as the
# install's user.
#
# Unlike `make refresh` (fetch -> ingest, which aborts on the first failure), this fetches the bank feed NON-fatally
# and then ALWAYS ingests, so the dashboard reflects the latest snapshots and statement files even if the feed is down.
# Its steps, in order: the fetch; the AI categorization step (categorize_step: nothing at all unless an AI key is
# stored, and never fatal); the ingest.
# A household with no bank feed (nothing at the feed credential's name: deploy/feed_check.py) skips the fetch and
# ingests its files alone: no failure streak and no alert about the feed, and the status says the run read the files
# only. Logs to its own directory (fetch.log, moved aside to fetch.log.1 once it passes LOG_MAX_BYTES) and stdout
# (docker logs); on the important failures it sends an alert (deploy/alert.py: Telegram and/or a webhook, set up as
# files in the alert secrets, prefixed with the household's name), one per failure streak.
#
# Usage: refresh.sh             # run the refresh
#        refresh.sh ingest      # re-ingest only, with no feed fetch (deploy/refresh_runner.py runs it when the
#                               # ingest marker asks): the feed's budget, the failure streak and the re-auth
#                               # state are left alone, and so are the last refresh's own status fields; with
#                               # FF_AI_RESET / FF_AI_RUN set (the refresher's, from the AI markers) it first
#                               # forgets the AI step's answers / runs the AI step
#        refresh.sh test-alert  # send a test alert and exit (verify the alert pipe)
#
# Where its files are: each location's own variable, else its place under FF_HOME, else its default place (under
# FF_ROOT: /app, moved only by tests). The store and the worker resolve the same directories (deploy/refresh_state.py
# locate and engine_dir), the ETL by etl/config.py Paths.
#
#   what                                       variable               under FF_HOME   default place
#   the mailbox, which the store writes too:   FF_STATE_REFRESH_DIR   state/refresh/  $FF_ROOT/var/refresh
#     request, status, ingest marker, the
#     run's liveness lock, the schedule
#   the engine's own: the refresh lock, the    FF_STATE_ENGINE_DIR    state/engine/   the lock in the mailbox; the rest
#     failure streak, the re-auth set, the                                            in the feed store,
#     ingest alert streak, the alert-channel                                          ${FF_STORE_DIR:-$FF_ROOT}/simplefin
#     note and the log
#     (a mailbox named alone holds these too)
#   the code it runs (the ETL,                 FF_APP (tests only)    -               the folder above this script
#     refresh_state.py, feed_check.py,
#     alert.py)
#
# The store writes the mailbox too, so this script reads and writes it only through refresh_state.py, which never
# follows a link there. Its own files are read only when the name holds a regular file (get), written as a new file
# renamed over the name (put), and the log is appended to only when it is a regular file (open_log).
set -u

ROOT="${FF_ROOT:-/app}"
APP="${FF_APP:-$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)}"
abs() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s/%s' "$PWD" "$1" ;; esac; }   # from the starting dir
STATE_NAMED=""                                               # set: a variable names the mailbox, made when absent
if [ -n "${FF_STATE_REFRESH_DIR:-}${FF_HOME:-}" ]; then
  STATE_DIR="$(abs "${FF_STATE_REFRESH_DIR:-${FF_HOME:-}/state/refresh}")"
  STATE_NAMED=1
else
  STATE_DIR="$ROOT/var/refresh"
fi
if [ -n "${FF_STATE_ENGINE_DIR:-}${FF_HOME:-}" ]; then
  ENGINE_DIR="$(abs "${FF_STATE_ENGINE_DIR:-${FF_HOME:-}/state/engine}")"
  OWN_DIR="$ENGINE_DIR"
elif [ -n "${FF_STATE_REFRESH_DIR:-}" ]; then
  ENGINE_DIR="$STATE_DIR"
  OWN_DIR="$STATE_DIR"
else
  ENGINE_DIR="$STATE_DIR"
  OWN_DIR="$(abs "${FF_STORE_DIR:-$ROOT}")/simplefin"
fi
LOG="$OWN_DIR/fetch.log"
LOG_SHOWN="state/engine/fetch.log"                           # the log as a person finds it in the install directory
LOG_MAX_BYTES=1048576                                        # past this the log is moved aside (one older log kept)
STATE="$OWN_DIR/.refresh_state"                              # consecutive-failure counter
REAUTH_STATE="$OWN_DIR/.reauth_state"
INGEST_ALERT_STATE="$OWN_DIR/.ingest_alert_state"
ALERT_NOTE_STATE="$OWN_DIR/.alert_channel_state"             # non-empty: "no alert channel" has been logged

# Serialize EVERY refresh against every other one — the scheduled runs, a run the dashboard
# button queued (deploy/refresh_runner.py), an ingest-only run, and a hand-run copy. Two at once each burn one
# of SimpleFIN's 24 daily requests for the same data, and their ingests fight over a
# shared data/finance.db.tmp. Re-exec under the lock (refresh_state.py hold-lock: the engine's refresh lock, and the
# mailbox's liveness file the store probes, neither opened through a link) so everything below runs unchanged; it
# exits 75 when the lock stayed held for FF_LOCK_WAIT seconds, apart from this script's own 0/1/2 exit codes.
# Placed ABOVE the mktemp below on purpose: exec replaces this process, so an EXIT trap
# registered first would never fire and would leak the temp file.
# Skipped when the state dir isn't mounted (Mac dev, `make refresh`) or for test-alert.
if [ -z "${FF_LOCK_HELD:-}" ] && [ "${1:-}" != "test-alert" ] && [ -d "$STATE_DIR" ]; then
  export FF_LOCK_HELD=1
  exec python3 "$APP/deploy/refresh_state.py" hold-lock --wait "${FF_LOCK_WAIT:-600}" \
    --lock "$ENGINE_DIR/refresh.lock" --liveness "$STATE_DIR/refresh.lock" -- "$0" "$@"
fi

RUN_OUT="$(mktemp 2>/dev/null || echo "/tmp/ff-refresh.$$")" # THIS run's output only
trap 'rm -f "$RUN_OUT"' EXIT

# The script's own files: read only when the name holds a regular file; written as a new file (mktemp makes it, never
# through a link) renamed over the name (mv -T replaces whatever the name holds, a link included, never writing
# through it), with the mode a plain `>` gives. A folder at the name makes the write fail, which is never fatal.
get() { [ -f "$1" ] && [ ! -L "$1" ] && cat -- "$1"; }
put() {   # put <file> [<line>]: the line and a newline, or nothing
  local tmp
  tmp="$(mktemp "$(dirname -- "$1")/.$(basename -- "$1").XXXXXX" 2>/dev/null)" || return 1
  chmod "$(printf '%o' "$(( 0666 & ~$(umask) ))")" "$tmp"
  if [ "$#" -ge 2 ]; then printf '%s\n' "$2" > "$tmp"; else : > "$tmp"; fi
  mv -f -T -- "$tmp" "$1" 2>/dev/null || { rm -f -- "$tmp"; return 1; }
}
# The log, appended through one descriptor opened once the name is known to hold a regular file (or nothing yet);
# anything else there leaves the log to standard output alone. A log past LOG_MAX_BYTES is first renamed to fetch.log.1
# (replacing whatever that name held, never writing through it), so the two never hold much more than twice that.
LOG_FD=""
open_log() {
  if [ -L "$LOG" ] || { [ -e "$LOG" ] && [ ! -f "$LOG" ]; }; then
    echo "!!! $LOG is not a regular file: this run's log goes to standard output only"
    return 0
  fi
  if [ -f "$LOG" ] && [ "$(stat -c %s -- "$LOG" 2>/dev/null || echo 0)" -gt "$LOG_MAX_BYTES" ]; then
    mv -f -T -- "$LOG" "$LOG.1" 2>/dev/null || true
  fi
  exec {LOG_FD}>>"$LOG" || LOG_FD=""
}
[ -d "$OWN_DIR" ] && open_log

ts()      { date -u '+%Y-%m-%d %H:%M:%SZ'; }
logline() { echo "$*"; [ -z "$LOG_FD" ] || echo "$*" >&"$LOG_FD"; }

# Publish run state for the dashboard's refresh button. Shells out to the shared helper
# rather than hand-rolling JSON here: the failed-step list and the re-auth warnings carry
# spaces, quotes and emoji, and the feed's errors are read from this run's output. Best-effort — a broken button must
# never break the refresh.
status() {   # status <state> [rc] [failed-steps] [pipe-joined warnings] [the fetch: ok|failed|none]
  [ -d "$STATE_DIR" ] || return 0
  local feed=()
  [ -z "${5:-}" ] || feed=(--fetched "$5" --feed-lines "$RUN_OUT")
  python3 "$APP/deploy/refresh_state.py" write-status --dir "$STATE_DIR" \
    --state "$1" --trigger "${FF_TRIGGER:-schedule}" --nonce "${FF_NONCE:-}" \
    --rc "${2:-}" --failed "${3:-}" --warnings "${4:-}" "${feed[@]}" >/dev/null 2>&1 || true
}
# The ingest step's own fields (status.json ingest_*: its start, end and exit code), written by every ingest, an
# ingest-only run's or a refresh's, and nothing else: an ingest the dashboard asked for is answered by any ingest
# that starts after it asked. Best-effort, like status().
ingest_status() {   # ingest_status start [--only --at <epoch>] | ingest_status done --rc <code>
  [ -d "$STATE_DIR" ] || return 0
  python3 "$APP/deploy/refresh_state.py" write-status --dir "$STATE_DIR" --ingest "$@" >/dev/null 2>&1 || true
}
MODE="${1:-}"
INGEST_OPEN=""   # set while an ingest step runs, so a signal records its end too
# `docker compose up -d` mid-run SIGTERMs us; leave a terminal status behind so the
# dashboard doesn't sit spinning on a run that is already gone. An ingest-only run ends its ingest and leaves the
# last refresh's state as it was.
on_signal() {
  [ -n "$INGEST_OPEN" ] && ingest_status done --rc 143
  [ "$MODE" = ingest ] || status interrupted 143
  exit 143
}
trap on_signal TERM INT

# Best-effort notification — never fails the refresh. deploy/alert.py sends it through each channel the alert secrets
# set up and says what failed; the log says whether it went out. With no channel set up nothing is sent, and the log
# says so once (ALERT_NOTE_STATE, cleared by the next alert that goes out), not at every alert that would have been.
alert() {
  local out code
  out="$(python3 "$APP/deploy/alert.py" "$1" 2>&1)"; code=$?
  [ -z "$out" ] || logline "$out"
  case "$code" in
    0) logline "--- alert delivered: $1"; put "$ALERT_NOTE_STATE" || true ;;
    3) if [ -z "$(get "$ALERT_NOTE_STATE" 2>/dev/null)" ]; then
         logline "--- alerts are not sent: no alert channel is set up (a Telegram bot or a webhook, in the alert secrets)"
         put "$ALERT_NOTE_STATE" noted || true
       fi ;;
    *) logline "!!! alert NOT delivered: $1" ;;
  esac
}

# Verify the alert pipe on demand, without waiting for a real failure: it always says how it went.
if [ "${1:-}" = "test-alert" ]; then
  out="$(python3 "$APP/deploy/alert.py" "✅ test alert — if you got this, failure alerts are working." 2>&1)"; code=$?
  [ -z "$out" ] || logline "$out"
  case "$code" in
    0) logline "--- test alert delivered" ;;
    3) logline "--- test alert not sent: no alert channel is set up (a Telegram bot or a webhook, in the alert secrets)" ;;
    *) logline "!!! test alert NOT delivered" ;;
  esac
  exit 0
fi

cd "$APP" || { logline "!!! cannot cd to $APP"; exit 1; }
mkdir -p "$OWN_DIR"
[ -z "$STATE_NAMED" ] || mkdir -p "$STATE_DIR"
[ -n "$LOG_FD" ] || open_log
: > "$RUN_OUT"

# Run one ETL step: its combined output to the durable log AND this run's buffer,
# and return the command's own exit code.
run_step() {  # run_step <label> <cmd...>
  local label="$1"; shift
  local out; out="$("$@" 2>&1)"; local code=$?
  printf '%s\n' "$out" >> "$RUN_OUT"
  [ -z "$LOG_FD" ] || printf '%s\n' "$out" >&"$LOG_FD"
  [ "$code" -ne 0 ] && logline "!!! $label failed (exit $code): its lines are in $LOG_SHOWN in the install; ./ff validate lists config problems"
  return "$code"
}

# Where the AI step's key would be (etl/config.py Paths.secrets_ai_dir, then its api_key): FF_SECRETS_AI_DIR, else
# FF_HOME's secrets/ai, else secrets/ai beside the config directory FF_CONFIG_DIR names, else beside the code's (the
# developer layout). Called after the cd into the code, so a relative variable reads as the ETL reads it.
ai_key_path() {
  local dir
  if [ -n "${FF_SECRETS_AI_DIR:-}" ]; then dir="$(abs "$FF_SECRETS_AI_DIR")"
  elif [ -n "${FF_HOME:-}" ]; then dir="$(abs "$FF_HOME")/secrets/ai"
  elif [ -n "${FF_CONFIG_DIR:-}" ]; then dir="$(dirname -- "$(abs "$FF_CONFIG_DIR")")/secrets/ai"
  else dir="$APP/secrets/ai"; fi
  printf '%s/api_key' "$dir"
}

# The categorization step, between the fetch and the ingest (which reads the answers it stores): the AI step. With
# nothing at the key's name there is no step at all — nothing is started, not even Python. With a key, `python -m etl
# ai run --from-refresh` decides the rest (it sends nothing unless the household switched the step on in the app for
# what is sent now, and nothing past its caps), bounded by AI_TIMEOUT_S, and never fatal: whatever it ends in, the
# ingest runs and the refresh's own status, streak and alerts are not touched. The provider refusing the key (exit 3)
# alerts once per streak of such runs (AI_ALERT_STATE); every other failure is logged with how many runs in a row have
# failed (AI_FAIL_STATE); a run that ends well clears both. Its output is counts only, never a vendor or the key.
AI_TIMEOUT_S=120
AI_ALERT_STATE="$OWN_DIR/.ai_alert_state"
AI_FAIL_STATE="$OWN_DIR/.ai_fail_state"
categorize_step() {
  local key code prev
  key="$(ai_key_path)"
  [ -e "$key" ] || [ -L "$key" ] || return 0
  run_step "AI step" timeout -k 10 "$AI_TIMEOUT_S" python -m etl ai run --from-refresh; code=$?
  if [ "$code" -eq 0 ]; then
    put "$AI_ALERT_STATE" || true
    put "$AI_FAIL_STATE" || true
    return 0
  fi
  prev=$(get "$AI_FAIL_STATE" 2>/dev/null || echo 0)
  case "$prev" in ''|*[!0-9]*) prev=0 ;; esac
  put "$AI_FAIL_STATE" "$((prev + 1))" || true
  case "$code" in
    124|137) logline "!!! the AI step was stopped after ${AI_TIMEOUT_S}s" ;;
  esac
  logline "!!! the AI step has failed $((prev + 1)) run(s) in a row; the ingest goes on"
  if [ "$code" -eq 3 ]; then
    if [ -n "$(get "$AI_ALERT_STATE" 2>/dev/null)" ]; then
      logline "!!! the AI provider refused the key again: alert suppressed as a repeat of this streak's"
    else
      alert "🟠 AI categorization is paused: its provider refused the stored key, so nothing new is categorized until a working key is stored. Everything else refreshed as usual. (Repeat alerts suppressed until the AI step works again.)"
      put "$AI_ALERT_STATE" alerted || true
    fi
  fi
  return 0
}

# The household asked, from the app, for the AI step's stored answers to be forgotten (deploy/refresh_runner.py says
# so in FF_AI_RESET for the ingest-only run it starts): `python -m etl ai reset`, before that run's ingest. Never fatal.
ai_reset_step() {
  run_step "AI reset" python -m etl ai reset || true
}

# One alert per streak of failed ingest-only runs: a broken config would otherwise alert at every settled save (up to
# MAX_INGEST_PER_DAY a day). Non-empty = the streak has alerted; truncated, never removed, by any successful ingest,
# of either mode (INGEST_ALERT_STATE). A refresh's own failed ingest alerts every run, as before, and leaves the
# streak as it is.

# The ingest alone. Nothing below this block applies to it: there is no fetch, so the failure streak and the re-auth
# state (both about the feed) and the last refresh's status fields (what the dashboard shows of that refresh) stay as
# they are. Run by the refresher, it is told the start the refresher already recorded (FF_INGEST_STARTED_AT), so the
# run is counted once. The first failed ingest-only run alerts, as a refresh's failed ingest does; the next ones, while
# none succeeds in between, are logged as repeats. The exit code is the ingest's.
if [ "$MODE" = ingest ]; then
  logline "=== $(ts) ingest start (no feed fetch) ==="
  # what the household asked for from the app's AI settings, which the refresher passes on: forget the stored answers,
  # then run the AI step, each before this run's ingest
  [ -z "${FF_AI_RESET:-}" ] || ai_reset_step
  [ -z "${FF_AI_RUN:-}" ] || categorize_step
  INGEST_OPEN=1
  ingest_status start --only --at "${FF_INGEST_STARTED_AT:-}"
  run_step "ingest" python -m etl ingest; rc=$?
  ingest_status done --rc "$rc"
  INGEST_OPEN=""
  if [ "$rc" -eq 0 ]; then
    put "$INGEST_ALERT_STATE" || true
  elif [ -n "$(get "$INGEST_ALERT_STATE" 2>/dev/null)" ]; then
    logline "!!! an ingest without a feed fetch failed again (rc=$rc): alert suppressed as a repeat of this failure streak's"
  else
    alert "🔴 data refresh FAILED (an ingest without a feed fetch errored). Dashboard data may be stale. Its lines are in $LOG_SHOWN in the install (./ff logs refresher shows the run); ./ff validate lists config problems. (Repeat alerts suppressed until an ingest succeeds.)"
    put "$INGEST_ALERT_STATE" alerted || true
  fi
  logline "=== $(ts) ingest done (rc=$rc) ==="
  exit "$rc"
fi

logline "=== $(ts) refresh start ==="
status running

# The fetch, when the household has a bank feed (feed_check.py: 3 = nothing at the credential's name; any other answer,
# its own failure included, is a feed). `fetched` is what the status is told: ok, failed, or none (no feed).
rc=0; failed=""; sf_rc=0; fetched=ok
check="$(python3 "$APP/deploy/feed_check.py" 2>&1)"; feed=$?
[ -z "$check" ] || logline "$check"
if [ "$feed" -eq 3 ]; then
  fetched=none
  logline "--- no bank feed is set up (nothing at the feed credential's place): this refresh reads the files alone"
else
  run_step "SimpleFIN fetch" python -m etl simplefin fetch || { rc=1; sf_rc=1; failed="$failed SimpleFIN-fetch"; fetched=failed; }
fi
categorize_step
INGEST_OPEN=1
ingest_status start
run_step "ingest" python -m etl ingest; irc=$?
ingest_status done --rc "$irc"
INGEST_OPEN=""
[ "$irc" -ne 0 ] && { rc=2; failed="$failed ingest"; }
[ "$irc" -eq 0 ] && { put "$INGEST_ALERT_STATE" || true; }   # a successful ingest ends that streak
failed="${failed# }"   # trim leading space

# Consecutive-failure streak, persisted in the durable store (guards against a corrupt file).
if [ "$rc" -eq 0 ]; then
  streak=0
else
  prev=$(get "$STATE" 2>/dev/null || echo 0)
  case "$prev" in ''|*[!0-9]*) prev=0 ;; esac
  streak=$((prev + 1))
fi
put "$STATE" "$streak" || true

# --- Alerts: only the actionable ones, de-duplicated to avoid per-run noise ------------
# (1) Bank connection(s) need re-auth — NAMED, scoped to THIS run, and alerted only when the
#     SET of warning connections changes (a persistent known issue must not ping every run; REAUTH_STATE).
warns=$(grep "WARN simplefin" "$RUN_OUT" 2>/dev/null \
        | sed -n 's/.*Connection to \(.*\) may need attention.*/\1/p' | sort -u | paste -sd'|' -)
[ -z "$warns" ] && grep -q "WARN simplefin" "$RUN_OUT" 2>/dev/null && warns="unknown (see fetch.log)"
if [ "$fetched" = none ]; then
  put "$REAUTH_STATE" || true   # no feed, so no connection: one that comes back with the feed alerts again
elif [ "$sf_rc" -ne 0 ]; then
  : # Fetch failed, so this run learned NOTHING about connection health. Leaving the
    # state untouched matters: clearing it would make an already-known connection look
    # new on the next success and re-alert for something unchanged.
elif [ -n "$warns" ]; then
  if [ "$warns" != "$(get "$REAUTH_STATE" 2>/dev/null || true)" ]; then
    names=$(printf '%s' "$warns" | tr '|' ',' | sed 's/,/, /g')
    alert "⚠️ SimpleFIN connection(s) need re-auth: ${names}. Reconnect in SimpleFIN or those balances go stale. (Repeat alerts suppressed until this set changes.)"
  fi
  put "$REAUTH_STATE" "$warns" || true
else
  put "$REAUTH_STATE" || true   # cleared — a re-appearance will alert again
fi
# (2) Ingest failed — the dashboard data is broken/stale until fixed (alert every run).
if [ "$rc" -eq 2 ]; then
  alert "🔴 data refresh FAILED (ingest errored; steps: ${failed}). Dashboard data may be stale. Its lines are in $LOG_SHOWN in the install (./ff logs scheduler shows the run); ./ff validate lists config problems."
# (3) A feed has failed 2 runs in a row — persistent (revoked credential / extended outage), not a one-off blip, so
#     worth one ping even though ingest self-healed; the runs after it in the same streak are logged only.
elif [ "$rc" -ne 0 ] && [ "$streak" -eq 2 ]; then
  alert "🟠 data refresh has failed ${streak} runs in a row (steps: ${failed}). Balances are going stale. See $LOG_SHOWN in the install. (Repeat alerts suppressed until a refresh succeeds.)"
elif [ "$rc" -ne 0 ] && [ "$streak" -gt 2 ]; then
  logline "!!! the refresh has failed ${streak} runs in a row: alert suppressed as a repeat of this failure streak's"
fi
# A single transient feed blip (rc=1, streak=1) that still re-ingested is logged only.

status done "$rc" "$failed" "$warns" "$fetched"
logline "=== $(ts) refresh done (rc=$rc, streak=$streak) ==="
exit "$rc"
