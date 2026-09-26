"""Run one command at an existing serial shell with explicit completion evidence."""
import argparse
import json
import re
import secrets

import pexpect


class SerialFailure(Exception):
    def __init__(self, kind, detail=""):
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}")


def execute(vm, command, timeout=120, nonce=None, spawn=pexpect.spawn):
    nonce = nonce or secrets.token_hex(16)
    if not re.fullmatch(r"[0-9a-f]{24,64}", nonce):
        raise ValueError("nonce must be 24-64 lowercase hex characters")
    prompt = rf"kevin@{re.escape(vm)}:.*\$ "
    # A terminal mode escape may precede the marker when a command emits no
    # output. The echoed command contains %s, not digits, so cannot satisfy it.
    marker = rf"__SORTER_RC_{nonce}__(\d+)"
    tty = spawn("virsh", ["-c", "qemu:///system", "console", vm],
                encoding="utf-8", timeout=timeout, maxread=200000)
    stage = "attach"
    try:
        tty.expect("Escape character", timeout=timeout)
        tty.sendline("")
        stage = "initial_prompt"
        if tty.expect([prompt, "login: "], timeout=timeout):
            raise SerialFailure("login_required", vm)
        tty.sendline(command + f"; printf '__SORTER_RC_{nonce}__%s\\n' \"$?\"")
        stage = "completion"
        if tty.expect([marker, prompt], timeout=timeout):
            raise SerialFailure("missing_completion_marker", str(tty.before)[-400:])
        # Bash bracketed-paste toggles can precede the first output byte.
        output = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", tty.before).replace("\r", "").strip()
        code = int(tty.match.group(1))
        stage = "final_prompt"
        tty.expect(prompt, timeout=timeout)
        return {"nonce": nonce, "output": output, "command_rc": code,
                "completion_marker": True, "prompt": True}
    except pexpect.TIMEOUT as exc:
        buffer = str(getattr(tty, "before", ""))
        pager = re.search(r"\(END\)|--More--|lines \d+-\d+|Press q to quit",
                          buffer, re.IGNORECASE)
        kind = "pager_like" if pager else {
            "attach": "attach_timeout", "initial_prompt": "missing_prompt",
            "completion": "command_timeout", "final_prompt": "missing_prompt"}[stage]
        raise SerialFailure(kind, buffer[-400:]) from exc
    except pexpect.EOF as exc:
        raise SerialFailure("console_closed", stage) from exc
    finally:
        # Ctrl+] detaches virsh, preserving the guest's persistent shell.
        try:
            tty.send("\x1d")
        except (OSError, pexpect.EOF):
            pass
        tty.close()


def run(vm, command, timeout=120):
    result = execute(vm, command, timeout)
    print(result["output"], flush=True)
    if result["command_rc"]:
        raise SystemExit(result["command_rc"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("vm", choices=("plc", "drives", "scada", "xle", "fw", "analyst"))
    parser.add_argument("command")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--nonce")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = execute(args.vm, args.command, args.timeout, args.nonce)
        print(json.dumps(result, sort_keys=True) if args.json else result["output"], flush=True)
        raise SystemExit(result["command_rc"])
    except SerialFailure as exc:
        print(json.dumps({"nonce": args.nonce, "error": exc.kind,
                          "detail": exc.detail}, sort_keys=True) if args.json else str(exc), flush=True)
        raise SystemExit(124)
