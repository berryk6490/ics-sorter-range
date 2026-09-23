"""Small simulated ASX: an editable barcode to trailer sort plan.

This process has no PLC client. Bind it to loopback on the SCADA guest.
"""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path


def decide(request, plan):
    package_id = request["package_id"]
    request_id = request["request_id"]
    barcode = str(request["barcode"])
    destination = plan.get(barcode)
    if type(destination) is int and 1 <= destination <= 9:
        return {"request_id": request_id, "package_id": package_id,
                "decision": "route", "destination": destination}
    return {"request_id": request_id, "package_id": package_id,
            "decision": "no_decision", "destination": None}


def serve(plan_path, port):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/sort-plan":
                self.send_error(404)
                return
            try:
                length = int(self.headers["Content-Length"])
                if length > 4096:
                    raise ValueError("request too large")
                request = json.loads(self.rfile.read(length))
                plan = json.loads(Path(plan_path).read_text())["barcodes"]
                response = decide(request, plan)
                body = json.dumps(response).encode()
            except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
                self.send_error(400, str(exc))
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", default=str(Path(__file__).with_name("sort_plan.json")))
    parser.add_argument("--port", type=int, default=8089)
    args = parser.parse_args()
    serve(args.plan, args.port)
