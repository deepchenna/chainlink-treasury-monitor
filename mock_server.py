import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

AGENT_CARD = {
    "name": "Chainlink Data Streams Prices Agent",
    "description": "Official Chainlink Data Streams A2A Agent providing low-latency spot prices and charts.",
    "url": "http://127.0.0.1:8099/",
    "version": "1.0.0",
    "protocolVersion": "0.3.0",
    "preferredTransport": "JSONRPC",
    "capabilities": {"streaming": False, "pushNotifications": False},
    "defaultInputModes": ["application/json", "text/plain"],
    "defaultOutputModes": ["application/json", "text/plain"],
    "skills": [
        {
            "id": "stream_prices",
            "name": "Stream Prices",
            "description": "Returns sub-second spot prices for BTC/USD, ETH/USD, SOL/USD, and LINK/USD.",
            "tags": ["oracle", "chainlink", "prices"],
        }
    ],
}


class MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/.well-known/agent.json", "/.well-known/agent-card.json"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(AGENT_CARD).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(body)

        if self.path == "/slack-webhook":
            print("\n[MOCK SLACK WEBHOOK RECEIVED]:", flush=True)
            print(json.dumps(data, indent=2), flush=True)
            with open("/tmp/last_slack_alert.json", "w") as f:
                json.dump(data, f, indent=2)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
            return

        req_id = data.get("id", "req-1")
        now_ts = int(time.time())
        feeds = [
            {"pair": "BTC/USD", "price": 62500.00, "timestamp": now_ts, "stale": False},
            {"pair": "ETH/USD", "price": 2745.10, "timestamp": now_ts, "stale": False},
            {"pair": "SOL/USD", "price": 131.40, "timestamp": now_ts, "stale": False},
            {"pair": "LINK/USD", "price": 13.50, "timestamp": now_ts, "stale": False},
        ]
        resp = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "task": {
                    "id": "task-oracle-1",
                    "status": {"state": "completed"},
                    "artifacts": [
                        {
                            "name": "chainlink_feeds",
                            "data": {"feeds": feeds},
                        }
                    ],
                }
            },
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resp).encode("utf-8"))

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 8099), MockHandler)
    print("Mock A2A + Slack Server listening on http://127.0.0.1:8099", flush=True)
    server.serve_forever()
