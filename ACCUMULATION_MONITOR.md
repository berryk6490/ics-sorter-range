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

The canonical read-only deployment preflight requires each serial command to
finish with its nonce-bound completion marker and the existing guest prompt.
It then detaches and reattaches to the same guest for a separate `printf`
liveness probe with a new nonce. A command or probe failure stops preflight at
once; its JSON output distinguishes command timeout, missing marker, missing
prompt, liveness failure, and pager-like output. The probe never logs the guest
shell out. Service reads use exactly
`env SYSTEMD_PAGER=cat SYSTEMD_COLORS=0 /usr/bin/systemctl --no-pager is-active UNIT`.
The host wrapper has a 30-second bound, each serial operation has a 20-second
bound, and a fresh probe follows every guest hash, PLC identity, and service
read. The preflight also requires manifest guest VMs running and compares PLC
holding register 249 with the manifest identity. The tool makes no guest writes.
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

## Detached SCADA scenario coordination (2026-09-25)

The earlier `scenario` subcommand runs the SCADA case in the foreground and
therefore monopolizes the one SCADA serial console. It remains for the normal
three-lane regression. For live hold screenshots use
`tests/accumulation_scenario_control.py` and the canonical guest
`/home/kevin/sorter-services/live_accumulation_detached.py`. A repository
launcher starts a new session with stdin closed and output in a unique guest
log, then returns the shell promptly. There is no inline background shell
program. The worker imports the runner, verifies its manifest hash and PLC
identity 24113, and reads stopped/empty preconditions before writing a
`ready.json` marker. It cannot call the scenario runner until the host first
proves both this marker and the independent drives monitor ready, writes an
identity-bound `authorize.json`, and then writes `begin.json`.

All lifecycle JSON files contain `run_id`, `scenario`, `pid`, and Linux
`start_ticks`. `pid.json` adds repository commit, runner hash and timestamp;
`ready.json` adds the read-only preflight and `mutation_started=false`;
`checkpoint.json` adds the PLC-validated slot/zone/hold/dwell/ready/block,
commit/counter/fault sample; `release.json` records the host's explicit
release. `terminal.json` keeps `status`, `original_error`, `cleanup`, and
`cleanup_failed` separately. Files are created durably and never overwritten.
`inspect`, `authorize`, `begin`, `release`, and `abort` all verify PID,
start ticks, command line, script, user, run ID, and case. `abort` signals
only that matching PID. A wrong or stale release fails the worker. Missing
release times out after at most 60 seconds; the live runner's `finally`
stops the sorter, XLe and ASX, resets occupied slots through the documented
operator reset, and restores the recorded operator settings.

For lane and merge cases, the separate drives helper
`/home/kevin/live_accumulation_fixture.py` is a bounded supervisor. It
accepts only the fixed lane-1 premerge or shared-merge plant fixture. Hermes
must obtain **one fresh operator approval immediately before fixture-start**.
The approval must identify the unique run ID and scenario and explicitly
cover both exact guest commands:

```text
sudo -n /usr/bin/systemctl stop sorter-plant.service
sudo -n /usr/bin/systemctl start sorter-plant.service
```

The first command starts the bounded fixture; the second restores the
canonical service during normal completion, abort, failure, or timeout.
The approval covers only these two transitions for this one run. Hermes
passes its approval receipt ID to `fixture-authorize`; that command records
the run ID, scenario, unit, both argument vectors, approval ID, guest
authorization timestamp, expiry (30–180 seconds), and initial active
service/process state in a unique guest `authorization.json`. The helper
checks that record again before the first stop and atomically claims it
once. A missing, stale, mismatched, reused, or malformed record blocks the
stop. The guest also stores the receipt's SHA256 in
`/home/kevin/.local/state/sorter-fixture-approvals.jsonl` under a file lock;
the same receipt cannot authorize another run or scenario. A receipt claimed
before a failed record write is spent and requires a new operator approval.
The expiry gates **starting** the fixture; the previously approved
restorative start remains available after expiry so emergency cleanup never
waits for a second interactive approval. The helper attempts that exact
start in `finally`, then verifies the service active and exactly one
unflagged plant process. Terminal evidence records the original failure
and restoration errors separately. The host's `wait` also requests fixture
stop after the scenario terminal; on host disconnect the guest supervisor
restores independently at its bounded deadline.

The approval receipt is an operator attestation supplied by Hermes; the
script validates its scope and lifetime, not the identity of the approving
person. Do not grant generic sudo, wildcard sudoers rules, Hermes YOLO mode,
or a broader command allowlist. The no-package smoke and drive-stop case
never invoke this helper.

### Canonical Hermes command order

Deploy only test tooling and verify exact hashes:

```sh
cd /home/kevin/vm/sorter
python3 tests/serial_copy.py scada tests/live_accumulation.py:/home/kevin/sorter-services/live_accumulation.py tests/live_accumulation_detached.py:/home/kevin/sorter-services/live_accumulation_detached.py
python3 tests/serial_copy.py drives tests/live_accumulation_detached.py:/home/kevin/live_accumulation_detached.py tests/live_accumulation_fixture.py:/home/kevin/live_accumulation_fixture.py
python3 tests/deployment_preflight.py --live
```

First run the monitor `start` and `probe` commands above. Substitute its
printed control path for `$MONITOR_CONTROL`. The scenario controller prints
its own control path for `$SCENARIO_CONTROL`:

```sh
python3 tests/accumulation_scenario_control.py launch --evidence-dir /home/kevin/vm/sorter-evidence/phase2a-hermes --case lane_hold --monitor-control "$MONITOR_CONTROL" --startup-timeout 30 --hold-timeout 30
python3 tests/accumulation_scenario_control.py probe-ready --control "$SCENARIO_CONTROL"
```

Run a **fresh preflight** now (controller ready/monitor proof, manifest
hashes, stopped/empty PLC, normal plant service). Hermes then requests one
operator approval naming this run ID, `lane_hold`, and the exact stop and
start commands above. Only after approval, record its unique receipt ID and
continue. If the bounded worker startup wait expires while approval is
pending, launch a new run and obtain a fresh run-bound approval; never reuse
the old receipt:

```sh
python3 tests/accumulation_scenario_control.py authorize --control "$SCENARIO_CONTROL"
python3 tests/accumulation_scenario_control.py fixture-authorize --control "$SCENARIO_CONTROL" --approval-id "$APPROVAL_RECEIPT_ID" --valid-for 180
python3 tests/accumulation_scenario_control.py fixture-start --control "$SCENARIO_CONTROL" --duration 150
python3 tests/accumulation_scenario_control.py begin --control "$SCENARIO_CONTROL"
python3 tests/accumulation_scenario_control.py probe-checkpoint --control "$SCENARIO_CONTROL" --timeout 90
```

For `merge_hold`, substitute that case. For `drive_stop`, omit
`fixture-start`. For a harmless no-package `smoke`, also omit it. The
`probe-checkpoint` reads current independent monitor Modbus samples and
calls the canonical `check_accumulation_views.py --motion {2,3,4}` through a
short SCADA serial attachment to compare OPC UA and HMI with PLC validated
state. After it returns, run `python3 tests/serial_hmi_proxy.py` on the host in a separate
terminal, collect `GET http://127.0.0.1:18000/api`, and run
`python3 tests/browser_accumulation.py {lane_hold,merge_hold} --output-dir
$EVIDENCE_DIR` for a genuine rendered screenshot. The `stale` browser case
uses an injected fixture and must not be labeled live. Stop the proxy
before using the SCADA serial console again. Save the screenshot and HMI
API response, then run:

```sh
python3 tests/accumulation_scenario_control.py evidence --control "$SCENARIO_CONTROL" --screenshot "$EVIDENCE_DIR/lane_hold.png"
python3 tests/accumulation_scenario_control.py release --control "$SCENARIO_CONTROL"
python3 tests/accumulation_scenario_control.py wait --control "$SCENARIO_CONTROL" --timeout 240
python3 tests/accumulation_scenario_control.py collect --control "$SCENARIO_CONTROL" --timeout 240
python3 tests/accumulation_scenario_control.py fixture-stop --control "$SCENARIO_CONTROL" --timeout 25
python3 tests/accumulation_monitor_control.py stop --control "$MONITOR_CONTROL" --timeout 20
python3 tests/accumulation_scenario_control.py verify-clean --control "$SCENARIO_CONTROL"
```

For `smoke` and `drive_stop`, omit `fixture-authorize`, `fixture-start`, and
`fixture-stop`. On browser/proxy failure, stop the proxy, call `abort`, then
`wait` (which requests restorative fixture stop), `fixture-stop` for
collection, monitor `stop`, and `verify-clean`. No second operator approval
is requested during cleanup. If the host disconnects, the scenario evidence
hold and fixture supervisor expire independently. The scenario's terminal status,
fixture terminal, monitor cleanup, and final-state proof must all be saved,
including failures. Hash the outside-Git evidence directory with
`sha256sum` after collection. The checkpoint is a trigger for evidence,
not evidence of HMI rendering by itself. Accept live HMI evidence only when
its PLC-backed zone rows and identity agree with the direct PLC and OPC UA
read within 15 dwell scans; the API does not expose the raw QW785 commit
sequence, so record that limitation rather than claiming an API commit match.

### Detached no-package smoke result, 2026-09-25 UTC

Outside-Git evidence:
`/home/kevin/vm/sorter-evidence/phase2a-detached-smoke-20260925T0210Z/`
(`SHA256SUMS`, 22 hashed files). The drives monitor run
`20260925T021043_19de24a245974ab7b63bac9e5d6b2460` was independently
ready at 02:11:01.672629 UTC, with PLC 24113, master off, accumulation
mode off, ready mask 0, three empty slots, and no zone/plant fault. The
SCADA detached smoke run
`20260925T021107_4cbc987cf5fe4ba8b378bd036ea8cfc4` returned its shell,
passed read-only preflight, and reached its checkpoint at 02:11:38.282006
UTC. While that PID was waiting, the existing `serial_hmi_proxy.py`
successfully attached to SCADA and returned a real HMI `/api` response at
02:11:59.055652 UTC: connected, sorter stopped, accumulation mode off,
zone fault 0, and all nine trailer counters 0. The proxy exited before the
explicit release. The guest terminal was `complete` at 02:12:07.275964
UTC. Collection found no detached-runner orphan; monitor terminal was
`complete` with 576 JSONL records and no orphan. Final PLC state matched
all saved operator coils, setpoints, seed, slots, counters, and faults;
`sorter-plant.service` remained active. No package was inducted, no plant
service was stopped, and no fixture was launched. Lane, merge, and drive
checkpoint evidence remain for independent Hermes validation.
