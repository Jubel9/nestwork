#!/usr/bin/env bash
# Nestwork server control: a small menu to start, stop and restart the server and change its port.
#
#   ./run.sh                  the menu
#   ./run.sh start | stop | restart | status | open | log
#   ./run.sh port 9000        change the port (restarts the server if it's running)
#   ./run.sh check            lint (ruff) and run every test before committing
#
# The port is saved in data/server.env (default 8834). The server runs in the background; its process
# id is in data/server.pid and its output in data/server.log.

set -u
cd "$(dirname "$0")"

DATA="data"
CONF="$DATA/server.env"
PIDFILE="$DATA/server.pid"
LOG="$DATA/server.log"
DEFAULT_PORT=8834
mkdir -p "$DATA"

if [ -t 1 ]; then
  C=$'\033[36m'; M=$'\033[35m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; D=$'\033[2m'; B=$'\033[1m'; N=$'\033[0m'
else
  C=; M=; G=; R=; Y=; D=; B=; N=
fi

port() {
  local p=""
  [ -f "$CONF" ] && p=$(sed -n 's/^PORT=\([0-9]\{1,5\}\)$/\1/p' "$CONF" | head -1)
  echo "${p:-$DEFAULT_PORT}"
}
url() { echo "http://localhost:$(port)"; }

# The pid of our server if it's running (from the pid file, checked to really be uvicorn for Nestwork).
server_pid() {
  [ -f "$PIDFILE" ] || return 1
  local pid; pid=$(cat "$PIDFILE" 2>/dev/null)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && ps -p "$pid" -o command= 2>/dev/null | grep -q "nestwork.main:app"; then
    echo "$pid"; return 0
  fi
  rm -f "$PIDFILE"; return 1
}
# Whatever is listening on a port (pid), if anything.
listener() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1; }

ensure_venv() {
  if [ ! -x ".venv/bin/uvicorn" ]; then
    echo "${Y}No virtual environment yet: creating .venv and installing requirements…${N}"
    python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || { echo "${R}Install failed.${N}"; return 1; }
  fi
}

start() {
  local p; p=$(port)
  if pid=$(server_pid); then echo "${G}Already running${N} on $(url) (pid $pid)."; return 0; fi
  local other; other=$(listener "$p")
  if [ -n "$other" ]; then
    echo "${R}Port $p is already in use${N} by: $(ps -p "$other" -o command= 2>/dev/null | cut -c1-80)"
    echo "Pick another port (menu option 4, or ./run.sh port <number>)."
    return 1
  fi
  ensure_venv || return 1
  echo "Starting Nestwork on port ${p}…"
  nohup .venv/bin/uvicorn nestwork.main:app --host 127.0.0.1 --port "$p" >>"$LOG" 2>&1 &
  echo $! >"$PIDFILE"
  for _ in $(seq 1 40); do
    if curl -fsS -o /dev/null "$(url)/api/version" 2>/dev/null; then
      echo "${G}Running${N} at ${B}$(url)${N} (pid $(cat "$PIDFILE"))."
      return 0
    fi
    if ! server_pid >/dev/null; then break; fi
    sleep 0.25
  done
  echo "${R}The server didn't start.${N} Last lines of $LOG:"
  tail -n 15 "$LOG"
  return 1
}

stop() {
  local pid
  if ! pid=$(server_pid); then echo "${D}Not running.${N}"; return 0; fi
  echo "Stopping the server (pid $pid)…"
  kill "$pid" 2>/dev/null
  for _ in $(seq 1 40); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
  rm -f "$PIDFILE"
  echo "${G}Stopped.${N}"
}

restart() { stop; start; }

status() {
  if pid=$(server_pid); then
    echo "Server: ${G}● running${N} at ${B}$(url)${N} (pid $pid)"
  else
    echo "Server: ${R}○ stopped${N} · port $(port)"
  fi
}

change_port() {
  local new="${1:-}"
  if [ -z "$new" ]; then
    printf "New port (now %s, default %s): " "$(port)" "$DEFAULT_PORT"
    read -r new
  fi
  if ! [[ "$new" =~ ^[0-9]+$ ]] || [ "$new" -lt 1024 ] || [ "$new" -gt 65535 ]; then
    echo "${R}A port is a number from 1024 to 65535.${N}"; return 1
  fi
  [ "$new" = "$(port)" ] && { echo "Already on port $new."; return 0; }
  local other; other=$(listener "$new")
  if [ -n "$other" ] && [ "$other" != "$(server_pid 2>/dev/null)" ]; then
    echo "${R}Port $new is already in use${N} by: $(ps -p "$other" -o command= 2>/dev/null | cut -c1-80)"; return 1
  fi
  local was_running=0; server_pid >/dev/null && was_running=1
  [ $was_running = 1 ] && stop
  echo "PORT=$new" >"$CONF"
  echo "${G}Port set to $new.${N}"
  [ $was_running = 1 ] && start
  return 0
}

open_browser() {
  server_pid >/dev/null || { echo "${Y}The server isn't running: starting it first.${N}"; start || return 1; }
  if command -v open >/dev/null; then open "$(url)"; elif command -v xdg-open >/dev/null; then xdg-open "$(url)"; else echo "Open $(url)"; fi
}

# Lint and test: what to run before committing. Installs the dev tools into .venv the first time.
check() {
  [ -x ".venv/bin/python" ] || { echo "${Y}No .venv yet: run ./run.sh start once first.${N}"; return 1; }
  [ -x ".venv/bin/ruff" ] || .venv/bin/pip install -q -r requirements-dev.txt || return 1
  echo "${C}ruff${N}"; .venv/bin/ruff check nestwork tests || return 1
  echo "${C}pytest${N}"; .venv/bin/python -m pytest -q || return 1
  echo "${G}All good.${N}"
}

show_log() { [ -f "$LOG" ] && tail -n 30 "$LOG" || echo "${D}No log yet.${N}"; }

menu() {
  while true; do
    echo
    echo "${C}${B}NESTWORK${N} ${M}▪${N} server"
    status
    echo
    echo "  ${C}1${N}  Start server"
    echo "  ${C}2${N}  Stop server"
    echo "  ${C}3${N}  Restart server"
    echo "  ${C}4${N}  Change port ${D}(now $(port))${N}"
    echo "  ${C}5${N}  Open in browser"
    echo "  ${C}6${N}  Show the last log lines"
    echo "  ${C}q${N}  Quit ${D}(the server keeps running)${N}"
    printf "\nChoose: "
    read -r choice || exit 0
    echo
    case "$choice" in
      1) start ;;
      2) stop ;;
      3) restart ;;
      4) change_port ;;
      5) open_browser ;;
      6) show_log ;;
      q|Q|"") exit 0 ;;
      *) echo "${Y}Pick 1–6 or q.${N}" ;;
    esac
  done
}

case "${1:-}" in
  "") menu ;;
  start) start ;;
  stop) stop ;;
  restart) restart ;;
  status) status ;;
  port) change_port "${2:-}" ;;
  open) open_browser ;;
  log) show_log ;;
  check) check ;;
  -h|--help|help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) echo "Unknown command: $1 (try ./run.sh help)"; exit 1 ;;
esac
