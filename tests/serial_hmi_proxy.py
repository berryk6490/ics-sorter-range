"""Temporary localhost bridge to the isolated SCADA HMI over its serial console.

Run on the hypervisor for browser checks. The bridge adds no host address or
route to the isolated networks and forwards only GET /, GET /api, and the two
scanner fault button POSTs to the guest's own localhost HMI.
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
        allowed = {"/", "/api", "/cmd/scanner_fault_ack/1",
                   "/cmd/scanner_retry/1"}
        if path not in allowed or (post != path.startswith("/cmd/")):
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
        self.respond(self.path, True)


try:
    ThreadingHTTPServer(("127.0.0.1", 18000), Handler).serve_forever()
finally:
    console.send("\x1d")
    console.close()
