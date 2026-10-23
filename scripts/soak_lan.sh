#!/usr/bin/env bash
# LAN bot soak, over real TCP (127.0.0.1, real sockets, two processes):
#   host   = headless bot co-pilot ("steady"), auto-starts the run
#   client = "weave" bot, headless, auto-quits after AUTOQUIT seconds
# Success = client exits cleanly, both logs are exception-free, screenshots land.
set -uo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-7747}"
AUTOQUIT="${AUTOQUIT:-60}"
mkdir -p tmp
HOST_LOG=tmp/soak_host.log
CLIENT_LOG=tmp/soak_client.log
rm -f "$HOST_LOG" "$CLIENT_LOG" tmp/soak_host.png tmp/soak_client.png

SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
  .venv/bin/python main.py --host --port "$PORT" --bot steady --mute --no-log \
  --auto-start --screenshot tmp/soak_host.png >"$HOST_LOG" 2>&1 &
HOST_PID=$!
cleanup() {
  kill "$HOST_PID" 2>/dev/null || true
  wait "$HOST_PID" 2>/dev/null || true
}
trap cleanup EXIT

# wait for the listen socket
up=0
for _ in $(seq 1 50); do
  if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
    exec 3>&- 3<&-
    up=1
    break
  fi
  sleep 0.1
done
[ "$up" = 1 ] || { echo "HOST: never came up"; cat "$HOST_LOG"; exit 1; }
echo "host listening on :$PORT (pid $HOST_PID)"

SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
  .venv/bin/python main.py --client 127.0.0.1 --port "$PORT" --bot weave \
  --name Weaver --mute --auto-quit "$AUTOQUIT" --screenshot tmp/soak_client.png \
  >"$CLIENT_LOG" 2>&1
RC=$?
echo "client exit code: $RC"

sleep 0.5
echo "--- host log (tail) ---"
tail -6 "$HOST_LOG"
echo "--- client log (tail) ---"
tail -6 "$CLIENT_LOG"

fail=0
for log in "$HOST_LOG" "$CLIENT_LOG"; do
  if grep -nE "Traceback|Error|assert" "$log"; then
    echo "SOAK FAIL: exception in $log"
    fail=1
  fi
done
[ -f tmp/soak_host.png ]    || { echo "SOAK FAIL: no host screenshot";    fail=1; }
[ -f tmp/soak_client.png ]  || { echo "SOAK FAIL: no client screenshot";  fail=1; }
[ $RC -eq 0 ] || { echo "SOAK FAIL: client rc=$RC"; fail=1; }
if [ $fail -eq 0 ]; then
  echo "SOAK PASS: ${AUTOQUIT}s loopback co-op, no exceptions, screenshots ok"
fi
exit $fail