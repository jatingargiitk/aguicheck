#!/usr/bin/env bash
# Regenerate the outputs shown in README.md. CI runs this and fails on any diff.
set -u
cd "$(dirname "$0")/.."
python examples/demo_server.py 8765 >/dev/null &
SERVER=$!
trap 'kill $SERVER' EXIT
for _ in $(seq 50); do python -c "import socket; socket.create_connection(('127.0.0.1', 8765))" 2>/dev/null && break; sleep 0.1; done

run() {  # name, command...
  local name=$1; shift
  { echo "\$ $*"; "$@"; echo "[exit $?]"; } > "examples/expected/$name.txt" 2>&1
}
run good aguicheck run http://127.0.0.1:8765/agent -m Pune --run-id r1
run buggy aguicheck run http://127.0.0.1:8765/agent-buggy -m Pune --run-id r1
run cut aguicheck run http://127.0.0.1:8765/agent-cut -m Pune --run-id r1 --no-transcript
run sdk aguicheck check examples/sdk-0.1.22-thinking.sse
run sdk-strict aguicheck check examples/sdk-0.1.22-thinking.sse --strict --no-transcript
