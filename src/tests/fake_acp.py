"""Fake `kiro-cli acp` for tests: a JSON-RPC agent over stdio, steered by environment variables.

- FAKE_LOG: file that receives one JSON line per event (process start, every request).
- FAKE_MODE: normal | refusal | empty | hang | crash_on_prompt | permission.
  hang never answers a prompt. permission asks the client for permission first.
"""

import json
import os
import sys

LOG = os.environ.get("FAKE_LOG")
MODE = os.environ.get("FAKE_MODE", "normal")


def log(event):
    if LOG:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def send(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    log({"event": "start", "pid": os.getpid(), "cwd": os.getcwd(), "home": os.environ.get("HOME")})
    counter = 0
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        log({"event": "request", "method": method, "params": params})
        if request_id is None:
            continue  # notifications such as session/cancel need no answer
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}})
        elif method == "session/new":
            counter += 1
            send({"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": f"sess_{counter:08x}", "modes": {}}})
        elif method == "session/load":
            if str(params.get("sessionId", "")).startswith("sess_"):
                send({"jsonrpc": "2.0", "id": request_id, "result": {}})
            else:
                send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "unknown session"}})
        elif method == "session/delete":
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        elif method == "session/prompt":
            session_id = params["sessionId"]
            if MODE == "hang":
                continue
            if MODE == "crash_on_prompt":
                sys.exit(0)
            if MODE == "permission":
                send({"jsonrpc": "2.0", "id": 900, "method": "session/request_permission",
                      "params": {"sessionId": session_id, "toolCall": {"toolCallId": "t1"}, "options": []}})
                reply = json.loads(sys.stdin.readline())
                log({"event": "permission_reply", "reply": reply})
            text = params["prompt"][0]["text"]
            send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": {
                "sessionUpdate": "session_info_update",
                "_meta": {"kiro": {"kind": "context_usage", "usagePercentage": 1.25}}}}})
            if MODE != "empty":
                send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": {
                    "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": f"Ответ: {text}"}}}})
            stop = "refusal" if MODE == "refusal" else "end_turn"
            send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": stop}})
        else:
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}})


if __name__ == "__main__":
    main()
