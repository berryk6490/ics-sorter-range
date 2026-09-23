"""Run a supplied shell command at an already logged-in guest serial prompt."""
import argparse
import pexpect


def run(vm, command, timeout=120):
    tty = pexpect.spawn("virsh", ["-c", "qemu:///system", "console", vm],
                        encoding="utf-8", timeout=timeout, maxread=200000)
    tty.expect("Escape character")
    tty.sendline("")
    outcome = tty.expect([rf"kevin@{vm}:.*\$ ", "login: "])
    if outcome:
        tty.send("\x1d")
        tty.close()
        raise RuntimeError(f"Log in yourself on {vm} serial console first")
    tty.sendline(command)
    tty.expect(r"__SORTER_RC__(\d+)", timeout=timeout)
    # The command passed in must print the marker after it exits. This avoids
    # mistaking a transient shell prompt in compiler or service output.
    output = tty.before
    code = int(tty.match.group(1))
    tty.expect(rf"kevin@{vm}:.*\$ ")
    tty.send("\x1d")
    tty.close()
    print(output.strip(), flush=True)
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("vm", choices=("plc", "drives", "scada", "fw", "analyst"))
    parser.add_argument("command")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    run(args.vm, args.command + "; printf '__SORTER_RC__%s\\n' \"$?\"", args.timeout)
