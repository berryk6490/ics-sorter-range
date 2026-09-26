"""Temporary localhost bridge to the isolated SCADA HMI over its serial console.

Run on the hypervisor for browser checks. The bridge adds no host address or
route to the isolated networks and forwards only GET /, GET /api, and the
    scanner/XLe fault button POSTs to the guest's own localhost HMI. The
    browser test start marker is written through the same serial console.
    GET /ua invokes the canonical direct OPC UA reader on SCADA.
"""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pexpect
import threading
import time


console = pexpect.spawn("virsh", ["-c", "qemu:///system", "console", "scada"],
                        encoding="utf-8", timeout=15, maxread=100000)
console.expect("Escape character")
console.sendline("")
console.expect(r"kevin@scada:.*\$ ")
lock = threading.Lock()
cache_lock = threading.Lock()
api_cache = (0.0, None)


def guest_request(path, post=False):
    if path == "/ua" and not post:
        with lock:
            console.sendline("/home/kevin/opcua/bin/python "
                             "/home/kevin/sorter-services/read_chute_ua.py")
            console.expect(r"__UA__([A-Za-z0-9+/=]+)__END__", timeout=20)
            body = base64.b64decode(console.match.group(1))
            console.expect(r"kevin@scada:.*\$ ", timeout=15)
        return 200, body
    url = "http://127.0.0.1:8000" + path
    data = "data=bytes()" if post else "data=None"
    command = (
        "python3 -c 'import base64,urllib.request; "
        f"r=urllib.request.urlopen(urllib.request.Request(\"{url}\",{data})); "
        "print(\"__HTTP__\"+str(r.status)+\":\"+"
        "base64.b64encode(r.read()).decode()+\"__END__\",flush=True)'"
    )
    with lock:
        console.sendline(command)
        console.expect(r"__HTTP__(\d+):([A-Za-z0-9+/=]+)__END__", timeout=20)
        status = int(console.match.group(1))
        body = base64.b64decode(console.match.group(2))
        console.expect(r"kevin@scada:.*\$ ", timeout=15)
    return status, body


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, path, post=False):
        allowed = {"/", "/api", "/ua", "/chute/action/acknowledge",
                   "/chute/action/empty", "/chute/action/resume",
                   "/cmd/scanner_fault_ack/1",
                   "/cmd/scanner_retry/1", "/cmd/xle_fault_ack/1",
                   "/cmd/xle_retry/1"}
        if path not in allowed or (post != path.startswith(("/cmd/", "/chute/action/"))):
            self.send_error(404)
            return
        try:
            if path == "/api":
                global api_cache
                with cache_lock:
                    if time.monotonic() - api_cache[0] > 0.5:
                        api_cache = (time.monotonic(), guest_request(path))
                    status, body = api_cache[1]
            else:
                status, body = guest_request(path, post)
        except Exception as exc:
            self.send_error(502, str(exc))
            return
        self.send_response(status)
        self.send_header("Content-Type", "text/html" if path == "/" else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self.respond(self.path)

    def do_POST(self):
        if self.path == "/test/three_slot_run/shared_four":
            try:
                with lock:
                    console.sendline(
                        "cd /home/kevin/sorter-services; "
                        "nohup /home/kevin/opcua/bin/python live_plant_three_slots.py shared_four "
                        "--start-file /tmp/plant-test-start "
                        "> /tmp/plant-three-browser.out 2>&1 & sleep 1")
                    console.expect(r"kevin@scada:.*\$ ", timeout=15)
                self.send_response(204)
                self.end_headers()
            except Exception as exc:
                self.send_error(502, str(exc))
            return
        if self.path in ("/test/lane3_run/all", "/test/lane3_run/shared",
                         "/test/lane3_run/failure"):
            case = self.path.rsplit("/", 1)[1]
            try:
                with lock:
                    console.sendline(
                        f"nohup /home/kevin/opcua/bin/python /home/kevin/live_plant_lane3.py {case} "
                        "--stateful --start-file /tmp/plant-test-start "
                        f"> /tmp/plant-lane3-browser-{case}.out 2>&1 & sleep 1")
                    console.expect(r"kevin@scada:.*\$ ", timeout=15)
                self.send_response(204)
                self.end_headers()
            except Exception as exc:
                self.send_error(502, str(exc))
            return
        if self.path in ("/test/lane2_run/shared", "/test/lane2_run/failure"):
            case = self.path.rsplit("/", 1)[1]
            try:
                with lock:
                    console.sendline(
                        "cd /home/kevin/sorter-services; "
                        f"nohup /home/kevin/opcua/bin/python live_plant_lanes.py {case} "
                        "--start-file /tmp/plant-test-start "
                        f"> /tmp/plant-lanes-browser-{case}.out 2>&1 & sleep 1")
                    console.expect(r"kevin@scada:.*\$ ", timeout=15)
                self.send_response(204)
                self.end_headers()
            except Exception as exc:
                self.send_error(502, str(exc))
            return
        if self.path in ("/test/plant_run/two", "/test/plant_run/failure"):
            case = self.path.rsplit("/", 1)[1]
            try:
                with lock:
                    console.sendline(
                        "cd /home/kevin/sorter-services; "
                        f"nohup /home/kevin/opcua/bin/python live_plant.py {case} "
                        "--speed 120 --terminal-hold 4 "
                        "--start-file /tmp/plant-test-start "
                        f"> /tmp/plant-browser-{case}.out 2>&1 & sleep 1")
                    console.expect(r"kevin@scada:.*\$ ", timeout=15)
                self.send_response(204)
                self.end_headers()
            except Exception as exc:
                self.send_error(502, str(exc))
            return
        if self.path == "/test/plant_start":
            try:
                with lock:
                    console.sendline("touch /tmp/plant-test-start")
                    console.expect(r"kevin@scada:.*\$ ", timeout=15)
                self.send_response(204)
                self.end_headers()
            except Exception as exc:
                self.send_error(502, str(exc))
            return
        self.respond(self.path, True)


try:
    ThreadingHTTPServer(("127.0.0.1", 18000), Handler).serve_forever()
finally:
    console.send("\x1d")
    console.close()
