"""Small simulated ASX: an editable barcode to trailer sort plan.

This process has no PLC client. Bind it to loopback on the SCADA guest.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import time


def decide(request, plan):
    package_id = request["package_id"]
    request_id = request["request_id"]
    barcode = str(request["barcode"])
    # A parcel-specific override can distinguish two parcels bearing one label.
    # The default barcode plan remains editable and backward compatible.
    serial_key = f"{barcode}:{request.get('package_serial', 0)}"
    destination = plan.get("barcode_serial", {}).get(serial_key,
                   plan.get("barcodes", plan).get(barcode))
    if type(destination) is int and 1 <= destination <= 9:
        return {"request_id": request_id, "package_id": package_id,
                "decision": "route", "destination": destination}
    return {"request_id": request_id, "package_id": package_id,
            "decision": "no_decision", "destination": None}


def serve(plan_path, port, scenario="normal", delay=0.0):
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
                print(json.dumps({"event": "asx_request", "event_ns": time.monotonic_ns(),
                                  "package_id": request["package_id"],
                                  "request_id": request["request_id"],
                                  "barcode": request["barcode"]}), flush=True)
                plan = json.loads(Path(plan_path).read_text())
                response = decide(request, plan)
                serial_key = f"{request['barcode']}:{request.get('package_serial', 0)}"
                request_delay = plan.get("delays_ms", {}).get(serial_key, 0) / 1000
                if scenario == "delay" or request_delay:
                    time.sleep(delay if scenario == "delay" else request_delay)
                elif scenario == "mismatch":
                    response["request_id"] = "stale-" + response["request_id"]
                print(json.dumps({"event": "asx_response", "event_ns": time.monotonic_ns(),
                                  "package_id": request["package_id"],
                                  "request_id": request["request_id"],
                                  "response": response, "scenario": scenario}), flush=True)
                body = json.dumps(response).encode()
            except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
                self.send_error(400, str(exc))
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                print(json.dumps({"event": "asx_late_delivery",
                                  "event_ns": time.monotonic_ns(),
                                  "package_id": request["package_id"],
                                  "request_id": request["request_id"]}), flush=True)

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", default=str(Path(__file__).with_name("sort_plan.json")))
    parser.add_argument("--port", type=int, default=8089)
    parser.add_argument("--scenario", choices=("normal", "delay", "mismatch"),
                        default="normal")
    parser.add_argument("--delay", type=float, default=1.2)
    args = parser.parse_args()
    serve(args.plan, args.port, args.scenario, args.delay)
