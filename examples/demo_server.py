"""A tiny AG-UI endpoint for trying aguicheck, standard library only.

    python examples/demo_server.py 8765

POST /agent        a well-formed 1.0 run: text, a tool call, its result, state
POST /agent-buggy  the same run with four mistakes real servers make
POST /agent-cut    a run whose stream stops half way
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def good_run(thread_id, run_id, text):
    city = text.strip() or "Paris"
    return [
        {"type": "RUN_STARTED", "threadId": thread_id, "runId": run_id},
        {"type": "STATE_SNAPSHOT", "snapshot": {"lookups": 0}},
        {"type": "TEXT_MESSAGE_START", "messageId": "m1", "role": "assistant"},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "Checking the weather in "},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": city + "."},
        {"type": "TEXT_MESSAGE_END", "messageId": "m1"},
        {"type": "TOOL_CALL_START", "toolCallId": "call1", "toolCallName": "get_weather", "parentMessageId": "m1"},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "call1", "delta": json.dumps({"city": city})},
        {"type": "TOOL_CALL_END", "toolCallId": "call1"},
        {"type": "TOOL_CALL_RESULT", "messageId": "m2", "toolCallId": "call1", "content": "18C, clear"},
        {"type": "STATE_DELTA", "delta": [{"op": "replace", "path": "/lookups", "value": 1}]},
        {"type": "RUN_FINISHED", "threadId": thread_id, "runId": run_id},
    ]


def buggy_run(thread_id, run_id, text):
    events = good_run(thread_id, run_id, text)
    # 1. Tool arguments sent as an object instead of a JSON string.
    events[7] = {"type": "TOOL_CALL_ARGS", "toolCallId": "call1", "delta": {"city": text.strip() or "Paris"}}
    # 2. The text message is never closed before the run finishes.
    del events[5]
    # 3. A pre-1.0 optional null.
    events[-1]["outcome"] = None
    # 4. A misspelt field the client strips: toolCallID instead of toolCallId on the result.
    events[8]["toolCallID"] = events[8]["toolCallId"]
    return events


def cut_run(thread_id, run_id, text):
    return good_run(thread_id, run_id, text)[:8]


ROUTES = {"/agent": good_run, "/agent-buggy": buggy_run, "/agent-cut": cut_run}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        route = ROUTES.get(self.path)
        if route is None:
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        messages = [m for m in body.get("messages", []) if m.get("role") == "user"]
        text = messages[-1]["content"] if messages and isinstance(messages[-1].get("content"), str) else ""
        events = route(body.get("threadId", "t"), body.get("runId", "r"), text)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for ev in events:
            self.wfile.write(("data: %s\n\n" % json.dumps(ev)).encode("utf-8"))
            self.wfile.flush()

    def log_message(self, *args):
        pass


def serve(port=8765):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    server = serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
    print("demo AG-UI agent on http://127.0.0.1:%d (routes: %s)" % (server.server_port, ", ".join(ROUTES)))
    server.serve_forever()
