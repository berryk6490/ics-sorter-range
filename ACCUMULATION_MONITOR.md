# Phase 2A readiness monitor for independent validation

This is **test infrastructure only**. `tests/live_accumulation_monitor.py`
runs on the existing drives VM, reads PLC Modbus, and writes files in one
unique `/tmp/sorter-accumulation-monitor-<run-id>/` directory. It never
writes a PLC value, starts the sorter, enables a mode, or reads the plant
process. `tests/accumulation_monitor_control.py` runs on the host and uses
the already logged-in drives serial shell. Neither tool changes networking.

The failed 2026-09-24 inline monitor under
`sorter-evidence/phase2a-reapproved-20260924T164912Z/readiness-monitor-investigation/`
never reached the guest: Bash exited 2 with an unmatched quote. It had no
guest PID or output. Even a corrected inline command through
`tests/serial_command.py` would buffer its stdout until completion, so it
could not prove readiness before the package runner began. The canonical
monitor uses persistent guest files and an explicit ready gate instead.

## Files, ownership, and ordering

The controller creates a unique run ID and guest directory, then launches
the monitor as a bounded background process with one `shlex.join` layer.
It immediately records the guest PID in local `control.json`. The guest
creates `pid.json`, writes `monitor_starting`, opens PLC Modbus, reads a
complete valid sample, writes and `fsync`s `initial_sample`, writes and
`fsync`s `monitor_ready`, then atomically creates `ready.json`. No ready
marker exists after a connection or first-read failure. The sample includes
its run ID, wall and monotonic timestamps, PLC identity/epoch/nonce, plant
identity/fault, master and mode coils, raw and PLC-validated zone rows,
QW784 raw readiness, QW785 commit sequence, QW786 validated readiness,
QW787 age, QW788 fault, slot identities/states, lane enables, and observed
hold reasons. `block_state` is inferred from PLC-validated holds; it is
not plant fixture ground truth.

The host `probe` succeeds only while the exact PID/start time/command line
is live, the terminal marker is absent, the ready marker run ID matches,
and the first durable JSONL sample matches the SHA256 in that marker.
Mode-disabled `validated_ready_mask=0` is valid. A stale marker, changed
run ID, wrong PID, early exit, missing record, or timeout blocks scenario
launch. The `scenario` command repeats that proof immediately before
launching the canonical SCADA runner; it saves a local launch timestamp and
always records scenario and monitor cleanup results separately. The guest
monitor emits `sample` or `change` on every bounded interval and finishes
with `monitor_stopping` plus `monitor_complete`, or `monitor_error` on a
failure. JSONL lines are flushed immediately; the initial and terminal
records are also synced to disk.

`pid.json` is `{"run_id": ..., "pid": ..., "start_ticks": ...}`. `ready.json`
adds `initial_sample_sha256`, `ready_monotonic_ns`, and
`ready_wall_time_ns`. `terminal.json` includes the same identity,
`status` (`complete` or `error`), `reason` (`duration`, `signal`, or
`error`), error text if any, sample count, and timestamps. Marker creation
uses a temporary file plus an atomic link that refuses an existing target.
`inspect` and `signal` verify the PID file, Linux process start ticks, user,
canonical monitor script path, and run ID in the process command line before
reporting it live or sending SIGTERM. A PID mismatch is never signalled.
`collect` waits for process exit, retrieves complete JSONL and terminal
records, checks every run ID and lifecycle ending, and records orphan and
cleanup status in `cleanup.json`. It can be repeated safely.

## Exact deployment and preflight

The only new guest deployment is the read-only monitor script on drives:

```sh
python3 tests/serial_copy.py drives \
  tests/live_accumulation_monitor.py:/home/kevin/live_accumulation_monitor.py
python3 tests/deployment_preflight.py --live
```

The host controller stays at
`/home/kevin/vm/sorter/tests/accumulation_monitor_control.py`.
The source and guest SHA256 must match `deploy/deployment_manifest.json`.
No service unit is installed or restarted. The serial shell must already be
logged in as `kevin`; the tools never handle a password.

## Exact commands for Hermes

Choose an evidence directory outside Git. `start` prints a JSON object
containing `control`, `run_id`, and the **guest** PID. Use the printed
`control` path verbatim in subsequent commands:

```sh
cd /home/kevin/vm/sorter
python3 tests/accumulation_monitor_control.py start \
  --evidence-dir /home/kevin/vm/sorter-evidence/phase2a-hermes \
  --plc-host 10.10.1.10 --plc-port 502 --duration 300 --interval 0.12
python3 tests/accumulation_monitor_control.py probe \
  --control /path/printed/by/start/control.json --timeout 15
python3 tests/accumulation_monitor_control.py scenario \
  --control /path/printed/by/start/control.json --case normal
```

`scenario` performs its own fresh readiness proof before any accumulation
enablement or induction by the runner. For an abandoned validation, call:

```sh
python3 tests/accumulation_monitor_control.py stop \
  --control /path/printed/by/start/control.json --timeout 20
```

`collect` waits for natural completion without signalling and has the same
control/timeout arguments. Save the controller's `control.json`,
`ready-proof.json`, `samples.jsonl`, `terminal.json`, `monitor.log`,
`cleanup.json`, and, when a scenario runs, `scenario-launch.json`,
`scenario-output.txt`, and `scenario-result.json`. Generate `SHA256SUMS` in
the external evidence directory. An independent validation must show the
ready proof's observation before `scenario_launch_monotonic_ns`, a live
PID at that proof, and `monitor_complete` only after the scenario. The
monitor's timestamps are observational; the host proof and launch times
are the ordering authority because guest and host monotonic clocks need
not share an epoch.

The authorized smoke check for this tooling launches only the monitor
while master and accumulation mode stay off. It probes readiness before
completion, then calls `stop`. It does **not** run a package scenario.

## Monitor-only live smoke, 2026-09-24 UTC

The deployed monitor source SHA256 is
`7c3da1a183a261e3a9a3d3cf23d646f0b839493b2432a0e55008594f7984b0db`.
The complete evidence is outside Git at
`/home/kevin/vm/sorter-evidence/phase2a-monitor-20260924T1745Z/`.
The final run ID was
`20260924T174912_91fe01f8af124da2848d48ca877b182a`, guest PID 4594.
The host saved `ready-proof.json` at **17:49:35.903986 UTC** while the PID
was live and no terminal marker existed. The guest terminal record was
written at **17:49:50.744079 UTC**, after the controller sent only that
PID SIGTERM. The initial sample read PLC identity 24113, epoch `[1,0]`,
nonce 12, plant identity `[1,0,12]`, plant fault 0, master off,
accumulation mode off, validated ready mask **0**, commit sequence 21656,
zone age 0, and zone fault 0. Collection saved 40 JSONL events including
start, initial sample, ready, samples, stopping, and complete. Terminal
status was `complete`, reason `signal`; `cleanup.json` reported no errors
and `orphan=false`.

The first smoke run identified two host transport bugs while the monitor
itself remained read-only: the JSONL file grew between `stat` and `base64`,
and an empty guest log carried a terminal control escape before the payload.
Both are covered by focused tests and fixed in the host controller. Its
records were collected after natural completion. The final smoke above
observed readiness **before** completion and stopped the monitor by its
validated PID. No package scenario was run by Codex in this milestone.
