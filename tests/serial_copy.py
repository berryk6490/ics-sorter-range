"""Copy repository files to a running isolated guest over its serial console.

Use only for test deployment. No bridge address or guest network route is added.
The guest shell must already be at a kevin prompt. Log in yourself with
`virsh console` first if it is not; this utility never handles a password.
"""
import argparse
import base64
import hashlib
from pathlib import Path
import pexpect
import shlex


def copy(vm, pairs):
    tty = pexpect.spawn("virsh", ["-c", "qemu:///system", "console", vm],
                        encoding="utf-8", timeout=30, maxread=200000)
    tty.expect("Escape character")
    tty.sendline("")
    result = tty.expect([rf"kevin@{vm}:.*\$ ", "login: "])
    if result == 1:
        tty.send("\x1d")
        tty.close()
        raise RuntimeError(f"Log in on the {vm} serial console yourself, then retry")
    prompt = rf"kevin@{vm}:.*\$ "
    tty.sendline("stty -echo")
    tty.expect(prompt)
    for source, destination in pairs:
        payload = Path(source).read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        quoted = shlex.quote(destination)
        tty.sendline(f"mkdir -p {shlex.quote(str(Path(destination).parent))}")
        tty.expect(prompt)
        tty.sendline(f"base64 -d > {quoted} <<'SORTER_FILE_END'")
        for offset in range(0, len(payload), 384):
            tty.sendline(base64.b64encode(payload[offset:offset + 384]).decode())
        tty.sendline("SORTER_FILE_END")
        tty.expect(prompt, timeout=120)
        tty.sendline(f"sha256sum {quoted}")
        tty.expect(digest, timeout=30)
        tty.expect(prompt)
        print(f"{vm}: {source} -> {destination} sha256 {digest}", flush=True)
    tty.sendline("stty echo")
    tty.expect(prompt)
    tty.send("\x1d")
    tty.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("vm", choices=("plc", "scada"))
    parser.add_argument("paths", nargs="+", help="source:guest-destination")
    args = parser.parse_args()
    copy(args.vm, [item.split(":", 1) for item in args.paths])
