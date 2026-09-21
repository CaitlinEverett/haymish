#!/usr/bin/env bash
# Host-side caption/embed catch-up — not a Cursor babysitting job.
# See docs/plans/overnight-full-library-reindex.md
set -euo pipefail

REPO="${HAYMISH_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
HAY="${HOME}/.haymish"
LOG_DIR="${HAY}/logs"
JOB_DIR="${HAY}/jobs"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="${LOG_DIR}/reindex-catchup-${STAMP}.log"
STATUS="${JOB_DIR}/reindex-status.json"
PIDFILE="${JOB_DIR}/reindex-catchup.pid"

mkdir -p "$LOG_DIR" "$JOB_DIR"
cd "$REPO"

MODE="${1:---catch-up-captions}"
case "$MODE" in
  --catch-up-captions|--no-captions) ;;
  *)
    echo "usage: $0 [--catch-up-captions|--no-captions]" >&2
    exit 2
    ;;
esac

if [[ -f "$PIDFILE" ]]; then
  old="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "${old}" ]] && kill -0 "$old" 2>/dev/null; then
    echo "catch-up already running as pid $old" >&2
    echo "log=$(python3 -c 'import json; print(json.load(open("'"$STATUS"'")).get("log",""))' 2>/dev/null || true)"
    exit 0
  fi
  rm -f "$PIDFILE"
fi

# Double-fork into a new session so Cursor/agent shell teardown cannot reap us.
# Plain `nohup … &` is not enough when the parent process group is killed.
PID="$(
  REPO="$REPO" LOG="$LOG" PIDFILE="$PIDFILE" STATUS="$STATUS" MODE="$MODE" \
  python3 - <<'PY'
import json, os, subprocess, time
from pathlib import Path

repo = Path(os.environ["REPO"])
log = Path(os.environ["LOG"])
pidfile = Path(os.environ["PIDFILE"])
status = Path(os.environ["STATUS"])
mode = os.environ["MODE"]

def write_status(state: str, pid: int | None, msg: str) -> None:
    status.write_text(json.dumps({
        "state": state,
        "pid": pid,
        "log": str(log),
        "msg": msg,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }) + "\n")

# First fork: return control to the caller immediately.
if os.fork() > 0:
    # Parent of first fork — wait briefly for pidfile, then print pid.
    for _ in range(50):
        if pidfile.exists():
            print(pidfile.read_text().strip())
            raise SystemExit(0)
        time.sleep(0.05)
    print("0")
    raise SystemExit(1)

os.setsid()
# Second fork: ensure we are not a session leader that can reacquire a TTY.
if os.fork() > 0:
    raise SystemExit(0)

os.chdir(repo)
os.environ["PYTHONUNBUFFERED"] = "1"
# Close inherited stdio; child logs only to the job log.
devnull = os.open(os.devnull, os.O_RDWR)
os.dup2(devnull, 0)
os.dup2(devnull, 1)
os.dup2(devnull, 2)
os.close(devnull)

log.parent.mkdir(parents=True, exist_ok=True)
lf = open(log, "a", buffering=1)
lf.write(f"---- start {time.strftime('%Y-%m-%dT%H:%M:%S%z')} mode={mode} ----\n")
lf.flush()

cmd = ["caffeinate", "-i", "uv", "run", "haymish", "index", mode]
proc = subprocess.Popen(
    cmd,
    cwd=str(repo),
    stdin=subprocess.DEVNULL,
    stdout=lf,
    stderr=subprocess.STDOUT,
    start_new_session=True,
    env={**os.environ, "PYTHONUNBUFFERED": "1"},
)
pidfile.write_text(str(proc.pid) + "\n")
write_status("running", proc.pid, f"haymish index {mode}")
rc = proc.wait()
write_status("exited", proc.pid, f"exit={rc}")
lf.write(f"---- exit {rc} {time.strftime('%Y-%m-%dT%H:%M:%S%z')} ----\n")
lf.close()
try:
    pidfile.unlink()
except FileNotFoundError:
    pass
raise SystemExit(rc)
PY
)"

if [[ -z "${PID}" || "${PID}" == "0" ]]; then
  echo "failed to detach catch-up job" >&2
  exit 1
fi

echo "started pid=$PID"
echo "log=$LOG"
echo "status=$STATUS"
echo "watch: tail -f \"$LOG\""
