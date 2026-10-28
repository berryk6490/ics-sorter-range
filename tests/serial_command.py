"""Run one command at an existing serial shell with explicit completion evidence."""
import argparse
import json
import re
import secrets
import time

import pexpect


class SerialFailure(Exception):
    def __init__(self, kind, detail=""):
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}")


def execute(vm, command, timeout=120, nonce=None, spawn=pexpect.spawn, *, total_timeout=None,
            shell_user='kevin'):
    if shell_user not in ('kevin','root') or (shell_user=='root' and vm not in ('plc','drives','xle','scada')):
        raise ValueError('unsupported explicit console context')
    nonce = nonce or secrets.token_hex(16)
    if not re.fullmatch(r"[0-9a-f]{24,64}", nonce):
        raise ValueError("nonce must be 24-64 lowercase hex characters")
    prompt = (rf"root@{re.escape(vm)}:.*\# " if shell_user=='root'
              else rf"kevin@{re.escape(vm)}:.*\$ ")
    # A terminal mode escape may precede the marker when a command emits no
    # output. The echoed command contains %s, not digits, so cannot satisfy it.
    marker = rf"__SORTER_RC_{nonce}__(\d+)"
    deadline = None if total_timeout is None else time.monotonic() + total_timeout
    def remaining():
        budget = timeout if deadline is None else min(timeout, deadline - time.monotonic())
        if budget <= 0:
            raise pexpect.TIMEOUT('total serial deadline expired')
        return budget
    tty = spawn("virsh", ["-c", "qemu:///system", "console", vm],
                encoding="utf-8", timeout=timeout, maxread=200000)
    stage = "attach"
    try:
        tty.expect("Escape character", timeout=remaining())
        tty.sendline("")
        stage = "initial_prompt"
        if tty.expect([prompt, "login: "], timeout=remaining()):
            raise SerialFailure("login_required", vm)
        if shell_user=='root':
            # This attaches only to an already authenticated operator shell.
            # No intended command is sent until fresh nonce-bound UID proof.
            stage='identity'
            uid_marker=rf'__SORTER_UID_{nonce}__(\d+)'
            remaining()
            tty.sendline(f'''printf '__SORTER_UID_{nonce}__%s\\n' "$(/usr/bin/id -u)"''')
            tty.expect(uid_marker,timeout=remaining())
            if tty.match.group(1)!='0':raise SerialFailure('context_uid_mismatch',vm)
            tty.expect(prompt,timeout=remaining())
        stage = "completion"
        remaining()  # Never send a command after the total attachment budget.
        tty.sendline(command + f"; printf '__SORTER_RC_{nonce}__%s\\n' \"$?\"")
        if tty.expect([marker, prompt], timeout=remaining()):
            raise SerialFailure("missing_completion_marker", str(tty.before)[-400:])
        # Bash bracketed-paste toggles can precede the first output byte.
        output = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", tty.before).replace("\r", "").strip()
        code = int(tty.match.group(1))
        stage = "final_prompt"
        tty.expect(prompt, timeout=remaining())
        return {"nonce": nonce, "output": output, "command_rc": code,
                "completion_marker": True, "prompt": True,
                **({'shell_user':'root','context_uid':0} if shell_user=='root' else {})}
    except pexpect.TIMEOUT as exc:
        buffer = str(getattr(tty, "before", ""))
        pager = re.search(r"\(END\)|--More--|lines \d+-\d+|Press q to quit",
                          buffer, re.IGNORECASE)
        kind = "pager_like" if pager else {
            "attach": "attach_timeout", "initial_prompt": "missing_prompt",
            "identity": "context_identity_timeout",
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
    parser.add_argument('--shell-user',choices=('kevin','root'),default='kevin',
                        help='already authenticated console only; never logs in or elevates')
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--total-timeout", type=float,
                        help="total attach/command/prompt budget, excluding bounded detach overhead")
    args = parser.parse_args()
    try:
        result = execute(args.vm, args.command, args.timeout, args.nonce,
                         total_timeout=args.total_timeout,shell_user=args.shell_user)
        print(json.dumps(result, sort_keys=True) if args.json else result["output"], flush=True)
        raise SystemExit(result["command_rc"])
    except SerialFailure as exc:
        print(json.dumps({"nonce": args.nonce, "error": exc.kind,
                          "detail": exc.detail}, sort_keys=True) if args.json else str(exc), flush=True)
        raise SystemExit(124)
