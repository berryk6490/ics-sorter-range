# Milestone 2: one bounded installed-core startup and no-package handshake

**PREPARED FOR OPERATOR REVIEW — NOT EXECUTED.** No guest was contacted in
preparing this procedure. No startup, reset, RPC, service change or capture
was performed. Approval must cover the entire finite procedure, its temporary
startup initialization allowances and conditional stop. A capture approval does not
authorize these writes.

## Evidence and source retained

- Selected measurement source: **f587cd6d6e5d425834d1ae35b43fd1fa1a8e69e9**,
  clean worktree `/home/kevin/vm/sorter-m2-measurement-source`.
  Selected manifest SHA256:
  `9a6b3b9c84fc1552fa73f7eb9835d30b4ab781fa52604a55dea23bd1362665ab`,
  43 destinations, declared PLC identity 24115. No manifest changes or guest
  deployment are part of this preparation.
- The October 14, 2026 Milestone 1 paper remains at `1e271ed`. Dedicated
  XLe, PLC identity 24115 and later source differences are Milestone 2 choices,
  not revisions of the paper's original claims.
- Recovery authority:
  `/home/kevin/vm/sorter-evidence/m2-prerequisite-review-20261022T002927Z/RECOVERY_REVIEW.md`.
- Capture gate: **PASS, mechanics only**, preserved from
  `/home/kevin/vm/sorter-evidence/m2-smoke-final-verification-20261022T022904Z/FINAL_DIAGNOSTIC_SMOKE_VERIFICATION.md`.
  No recapture or collector development is needed. Its connection refusals
  were not successful application communication. All package scenarios remain
  **NOT RUN**.

The current repository has canonical serial transport, recovery, typed capture
and comparison, deployment preflight and an approval-spending ledger. It has
no suitable installed-core startup guard. Historical `core_start.py` and
`initial_gate.py` have stale paths/transport/deadline defects and are not used.
The necessary **host-only** guard is `tests/m2_plc_startup_guard.py`, with the
small fixed launch/owned-RPC helper `tests/m2_owned_core.py` rendered inline
through the canonical serial transport. Neither file is deployed or installed.
It calls the existing mechanisms; it does not deploy a guest script or create
a second recovery runner. The small optional total-deadline/trace additions to
`serial_command.py` and `deployment_preflight.py` retain their default behavior.
Host tool hashes are recorded separately from selected deployment identity.

## Proposed endpoint and authorization

One successful attempt leaves **exactly one existing core and TCP502 running,
master off, modes off, empty slots and required counters/faults zero**.
This changes core availability from the original stopped state. It does not
start sorting or establish a healthy-idle operating scenario.

Approval must explicitly permit:

1. One launch of the already installed `./core/openplc`, in its verified working
   directory and reviewed existing execution identity, with one unused log file.
2. One local `start_modbus(502)` RPC.
3. Explicit startup-only QW561=1 allowance with accepted epoch exactly[0,0],
   QW564=0 and all safe-state conditions passing. Plant QW591=1 is a separate,
   optional documented initialization allowance. Both require their own flags.
4. One canonical no-package `--remote-xle --reset-run` operation.
5. One conditional local `quit()` attempt on post-start failure or loss of the
   host controller, **only with proven ownership** as specified below. Refusal
   means INCOMPLETE and operator recovery needed, without another stop method.
   Read-only exact-incarnation exit/listener/configuration verification follows.
6. The successful core-running/master-off endpoint above.

No automatic second launch, second reset, second start_modbus or second quit
is allowed. No PID kill, service/VM restart, database repair, compile/upload,
scanner ACK/Retry, ASX plan edit or configuration change is proposed.
Web UI availability and `Start_run_mode=false` are checked and preserved.

## Fresh pre-start reads and unresolved prerequisites

The new prerequisite review is
`/home/kevin/vm/sorter-evidence/m2-startup-guest-prerequisites-20261026T201608Z/REVIEW.md`.
Its observations are retained with their original time; this preparation
performs no guest reads and does not renew them. The prior host-repair PASS
and capture-mechanics PASS remain historical results, not startup approval.

| Check | October26 review result | Still required before this attempt |
| --- | --- | --- |
| Selected source/manifest and43 guest hashes; four installed PLC hashes | PASS at recorded time | Repeat against clean selected source, not dirty tooling source. |
| PLC mapping, full DB integrity, project/settings, Web UI; core/502/43628 absent | PASS at recorded time | Fresh pre-start reads; no QW249/stopped-empty Modbus claim yet. |
| Drives aliases .21–.29, units/argv, nine config templates | PASS at recorded time | Direct root-worker selected environment and socket-inode proof still needs operator access. |
| UID1000 bind502 | BLOCKED: zero capabilities, threshold1024, no setuid/file capabilities | Use the existing intended root context below; never launch1000 to rediscover this failure. |
| XLe journal/plant journal text | Readable at recorded time | Fresh bounded text reads. SQLite file is unreadable as1000; use approved root read/export below. |
| Console ownership | No competing console observed at recorded instant | Operator coordination and fresh recheck; no permanent exclusivity claim. |
| Accepted RPC descriptor | NOT RUN, core stopped | Startup-only proof on the actual connection, before Modbus bytes; repeat before conditional quit. |

The boot-ID comparison defect is repaired by validating/normalizing journald
hex32 and /proc hyphenated UUIDs to hex32. Missing/malformed IDs or genuinely
different boots still reject. Journal age, PID, exit, selected argv, retry and
all other XLe evidence requirements remain enforced. Replaying the archived
read is historical compatibility evidence only; a fresh read is mandatory.

The guard repeats source identity and all 43 guest hashes using the selected
manifest, six VM states, required units, competing-process checks, installed
configuration and full SQLite `integrity_check`. It requires no core and no
502/43628 listener before launch. The active web service/login must remain
available. It checks the active file and exact Programs row 38, installed
ST/core/POUS/openplc.py hashes from the recovery review, and preserves settings.
The core log parent must exist and be writable; the exact log must not exist.
No pre-start live QW249 or empty-slot claim is possible while port502 is closed.
That claim is established only by the first bounded successful state read.

## Existing intended execution context and operator access

The existing installation used a **detached root core**, independent of
`openplc.service`: see [ACCUMULATION_BACKPRESSURE.md](../../docs/design/ACCUMULATION_BACKPRESSURE.md), line234,
and the preserved root launch receipt
`/home/kevin/vm/sorter-evidence/xle-recirc-deployment-prep-20260926T062200Z/plc-core-launch.json`
(`__SORTER_ROOT_RC_...`), not an authorization for a new launch.
The recorded installed startup script
`/home/kevin/vm/sorter-evidence/chute-preflight-PpZ2fWYc/plc-start-script.txt`
runs the Web UI from `/home/kevin/OpenPLC_v3/webserver`; the October22
`plc-services.json` records `/home/kevin/OpenPLC_v3/start_openplc.sh` as the
active service ExecStart. Starting that service is not the proposed core launch.
The October26 record confirms the selected generated core hash is unchanged,
root-owned0755 with no setuid/file capabilities. File ownership alone does
not grant bind502 to1000. No current root session or fresh loaded service UID
was established by this preparation; read the exact service context below.

Proposed context: ordinary kevin **host** Python, already authorized/root
operator consoles on **plc, drives and xle**, existing kevin consoles on scada
and fw. The operator must supply those authenticated sessions using existing
rights before any timer is armed. If that access is unavailable, remain
BLOCKED. No password handling, sudo/su, account or policy change is automated.
The operator authenticates through their existing approved access, then detaches
any virsh console with Ctrl+] while preserving the shell; no competing attached
console may be displaced. This preparation supplies neither authentication nor
authorization to acquire those sessions.
The canonical serial tool's explicit `--shell-user root` profile accepts only
root@plc/drives/xle prompts and proves a fresh nonce-bound UID0 before sending
each intended command, including the separate liveness command. Default kevin
behavior is unchanged. UID/prompt failure detaches and blocks; it never tries
to authenticate or elevate. This host implementation needs focused review.

Before launch, `PLC_LAUNCH_CONTEXT` requires UID/EUID0 in the initial user/net
namespaces and existing effective/bounding CAP_NET_BIND_SERVICE when the
unchanged port threshold exceeds502. No capability or sysctl write is proposed.
The future guard uses only the hash-pinned fixed helper, executable, cwd,
attempt token, log and two fixed RPCs. These are individually approved commands
inside existing operator contexts, **not a privileged Python permission grant**.
The host controller is never run as root, and no Python/script sudoers rule,
setcap, setuid, port-threshold change or deployment is part of this procedure.

## Verified initialization policy at the selected commit

The October26 instruction authorizes this **host policy change only**. It
supersedes the earlier plant-only policy decision; it does not authorize live
startup or recovery. Old verification packets remain unchanged historical
evidence. The source was read with
`git show f587cd6d6e5d425834d1ae35b43fd1fa1a8e69e9:Sorter.st` and compared
byte-for-byte with the clean measurement worktree. SHA256:
`952ff58580a2d1a0444b6b0e91f745ff74a923484607645173909c5ab4be3dc6`.

- [Sorter.st:855](https://github.com/berryk6490/ics-sorter-range/blob/b38b48f3d049e89b96116176758cc5e9db7b4053/Sorter.st#L855):
  initialization clears the offer ACK, accepted epoch=[0,0], sets QW561=1,
  clears QW564, and resets XLe heartbeat/liveness state. Plant fault is
  initialized to1 at [line842](https://github.com/berryk6490/ics-sorter-range/blob/b38b48f3d049e89b96116176758cc5e9db7b4053/Sorter.st#L842).
- [Sorter.st:949](https://github.com/berryk6490/ics-sorter-range/blob/b38b48f3d049e89b96116176758cc5e9db7b4053/Sorter.st#L949):
  a fresh nonzero offer commit is acknowledged at560 and accepted only while
  stopped, scanner-ready and unprovisioned, with a nonzero base30000 epoch.
  At963, QW564=2 takes precedence and produces fault2; otherwise zero epoch
  produces fault1 and a valid accepted epoch produces fault0.
- [Sorter.st:974](https://github.com/berryk6490/ics-sorter-range/blob/b38b48f3d049e89b96116176758cc5e9db7b4053/Sorter.st#L974):
  XLe heartbeat acknowledgement requires fault0 and a matching heartbeat epoch.
- [Sorter.st:2112](https://github.com/berryk6490/ics-sorter-range/blob/b38b48f3d049e89b96116176758cc5e9db7b4053/Sorter.st#L2112):
  package commands still require fault0 plus exact epoch, nonce, occupied-slot,
  token, serial, sequence and barcode identity. Induction also requires master,
  plant/epoch/liveness/heartbeat gates at
  [line2159](https://github.com/berryk6490/ics-sorter-range/blob/b38b48f3d049e89b96116176758cc5e9db7b4053/Sorter.st#L2159).
  These operational checks are unchanged.

With `--allow-initial-epoch-fault-1`, the guard accepts QW561=1 **only** with
accepted epoch exactly[0,0], a fresh QW564=0 read, correct identity, master/modes
off, empty slots/lanes, zero required counters and all other required safety
conditions. QW561=2, QW564=2 (even if the fault read still says1), any unexpected
QW564 value, nonzero epoch with fault1, and zero epoch with fault0 fail closed.
Only separately enabled plant591=1 may coexist; no other fault is waived.

This is **INITIALIZING**, never healthy-baseline PASS. Both the initial and
typed-before stage receipts retain that status, and the original canonical
self-comparison remains FAIL for its named initialization fields. No raw typed
snapshot or comparator rule is normalized to manufacture PASS.

The allowance is valid only before synchronization during initial35s,
typed-before150s and handshake95s, with an additional nonrenewable **280-second
startup ceiling** anchored at the initial stage. It is disabled after readiness;
the runtime flags cannot relax final checks or allow recurrence. Every policy
read uses the smaller stage/initialization deadline.

The source's very first reset scan also starts the automatic scanner prepare/
reset protocol at [line925](https://github.com/berryk6490/ics-sorter-range/blob/b38b48f3d049e89b96116176758cc5e9db7b4053/Sorter.st#L925).
The cold fixture represents the first safe readable state **after those L1
ACKs**, while XLe epoch and plant identity remain unprovisioned. It does not
pretend that scanner state1/2 is an accepted safe sample. Scanner fault/state
guards remain intact; the later canonical stopped reset deliberately creates
the fresh nonce and journal-backed epoch required for final acceptance.

Before marking the prerequisite receipt PASS, the operator must separately
obtain and review the following **fresh, bounded read-only evidence** through
`deployment_preflight.guest_read` (completion, prompt, separate liveness):

| Receipt key | Evidence required |
| --- | --- |
| `drives_bindings_and_environment` | Canonical drives shell accessible. Nine actual aliases/listeners .21–.29; scanner argv and environment .27/.28/.29, six VFD argv/environment .21–.26; one canonical plant, six VFDs, three scanners, no workers/legacy XLe. Compare /etc/scanner/tunnel{1,2,3}.conf and /etc/vfd/{induct,outbnd}{1,2,3}.conf with selected templates, retain bytes/hashes and process/socket ownership evidence. These files and interface configuration are **outside the 43-entry manifest**. |
| `plc_slave_mapping` | Fresh `mbconfig.cfg` bytes/hash and read-only SQLite slave rows, register lengths/offsets, nine .21–.29 destinations; no mapping edits. Compare the reviewed mapping, not merely address suffixes. |
| `launch_privilege_and_nc_timeout` | Retained key: operator-provided UID/EUID0 PLC context and initial namespaces, existing bind502 rights, current service/startup context, selected core hash and unused writable log; Python3/pidfd support. The SCADA timeout probe already passed historically; do not repeat solely for reassurance. Missing current support blocks; no permission change. nc is not the RPC transport. |
| `console_exclusivity` | Record UTC/host process and guest who observations, operator coordination and immediate pre-start recheck. PASS means those observations and coordination were reviewed; it is not a permanent guarantee. The host PLC lock coordinates only controller/watchdog, cannot evict or exclude other operators. |
| `readable_evidence_export` | Host evidence parent writable/readable by kevin; guest core log readable in launch identity, XLe/plant journals readable, XLe SQLite journal readable via mode=ro. Establish the normal-user export path before startup. |
| `owned_rpc_prestart_support` | Pre-start support only: reviewed fixed helper/source and installed binary/source hashes, root/self pidfd and /proc stat/status/environ/exe/fd/TCP visibility, explicit no-hostile-root/FD-transfer assumptions. Static excerpts/symbols support review, not a claim about an actual accepted FD. The stopped core has no descriptor to prove. Actual ownership is a startup-only check below, never a prerequisite PASS invented before launch. |

For those reads, use this exact transport pattern with each reviewed command,
an unused label, and one deadline of at most 30 seconds for the command plus
separate liveness. This example does **not** authorize arbitrary guest writes:

```sh
cd /home/kevin/vm/sorter-milestone2-baseline
/usr/bin/python3 -B -c 'import sys,time; sys.path.insert(0,"tests"); import m2_plc_startup_guard as g; from deployment_preflight import guest_read; print(guest_read("drives",g.DRIVES_ROOT_READ,"startup-root-workers-REVIEWED",shell_user="root",deadline=time.monotonic()+30))'
```

Other concrete read commands to pass through that same mechanism:

- drives: `sha256sum -- /etc/scanner/tunnel1.conf /etc/scanner/tunnel2.conf /etc/scanner/tunnel3.conf /etc/vfd/induct1.conf /etc/vfd/induct2.conf /etc/vfd/induct3.conf /etc/vfd/outbnd1.conf /etc/vfd/outbnd2.conf /etc/vfd/outbnd3.conf`
- plc: `cat /home/kevin/OpenPLC_v3/webserver/mbconfig.cfg`; the same
  read-only SQLite connection used by `PLC_INSPECT` can list
  `PRAGMA table_info(Slave_dev)` and `SELECT * FROM Slave_dev`.
- plc: `id; stat -c "%a %U %G %n" /home/kevin/OpenPLC_v3/webserver/core/openplc; getcap /home/kevin/OpenPLC_v3/webserver/core/openplc; cat /proc/sys/net/ipv4/ip_unprivileged_port_start; readlink -f /usr/bin/nc; /usr/bin/nc -h`.
- scada: `readlink -f /usr/bin/timeout; /usr/bin/timeout --version`.
  Version output alone does not prove signal behavior; use a separately
  reviewed inert-process test if existing evidence does not prove it.
- xle: readonly journal integrity/epochs via the exact `JOURNAL` command in
  the exported guard plan; recent `journalctl --no-pager -u sorter-xle.service -n 30 -o cat`.

The worker probe has an independent guest20+2-second timeout inside the
host30-second command/completion/liveness budget. It pins each of nine current
systemd MainPIDs with a pidfd; records PID/start/UID/EUID; validates only ADDR
and scanner LANE; checks the exact canonical argv; matches each listener's
TCP inode to that worker's FD set; rechecks incarnation/liveness/MainPID.
R/S scheduling changes are informational. Any permission/ownership/race error
fails the read. All other environment values are discarded without printing
or hashing; even unexpected ADDR/LANE values are withheld on failure. Do not
substitute a full `/proc/*/environ` or `ps e` dump in a report.

After the operator provides the approved root consoles, these additional
**read-only, review-only** host commands have one30-second total deadline
(including separate serial liveness), with20 seconds maximum per command:

```sh
cd /home/kevin/vm/sorter-milestone2-baseline
# PLC: existing launch rights, no bind attempt or privilege changes:
/usr/bin/python3 -B -c 'import json,sys,time; sys.path.insert(0,"tests"); import m2_plc_startup_guard as g; from deployment_preflight import guest_read; r=guest_read("plc",g.PLC_LAUNCH_CONTEXT,"startup-root-context-REVIEWED",shell_user="root",deadline=time.monotonic()+30); e=json.loads(r.splitlines()[-1]); print(json.dumps(e)); g.launch_context_guard(e)'
# PLC: installed service context/static source; this does not start it:
/usr/bin/python3 -B -c 'import sys,time; sys.path.insert(0,"tests"); from deployment_preflight import guest_read; print(guest_read("plc","env SYSTEMD_PAGER=cat SYSTEMD_COLORS=0 /usr/bin/systemctl --no-pager show openplc.service -p User -p Group -p MainPID -p ExecStart -p WorkingDirectory -p FragmentPath -p DropInPaths && /usr/bin/sha256sum /lib/systemd/system/openplc.service /home/kevin/OpenPLC_v3/start_openplc.sh","startup-installed-context-REVIEWED",shell_user="root",deadline=time.monotonic()+30))'
# XLe: one consistent read-only SQLite export; no permission change or DB copy:
/usr/bin/python3 -B -c 'import sys,time; sys.path.insert(0,"tests"); import m2_plc_startup_guard as g; from deployment_preflight import guest_read; print(guest_read("xle",g.JOURNAL,"startup-root-journal-REVIEWED",shell_user="root",deadline=time.monotonic()+30))'
# XLe: fresh structured retry evidence with UUID normalization in the host gate:
/usr/bin/python3 -B -c 'import json,sys,time; sys.path.insert(0,"tests"); import m2_plc_startup_guard as g; from deployment_preflight import guest_read; r=guest_read("xle",g.XLE_PRESTART,"startup-root-xle-gate-REVIEWED",shell_user="root",deadline=time.monotonic()+30); e=json.loads(r.splitlines()[-1]); v=g.xle_prestart_gate(e); print(json.dumps({"evidence":e,"decision":v})); raise SystemExit(0 if v["accepted"] else 2)'
```

Use unique labels and new normal-user host output files, preserve rc and UTC
trace receipts, and separately hash each export. No stdout pipeline may hide
a failed read. A previously unreadable DB remains BLOCKED until such a read
actually succeeds. The consistent export uses `mode=ro`, `query_only=ON`,
one explicit BEGIN transaction for integrity/epochs/outcomes, a2-second lock
timeout and3-second SQLite progress ceiling, and closes the connection.
It observes a single committed snapshot, including WAL data through SQLite.
Never chmod/chown the database, use immutable=1 on a live DB, or copy only the
live database file and assume it is consistent. An operator may provide the
same fresh read-only JSON export and trace through an already authorized
context for prerequisite review; a raw DB copy is not the export protocol.
Runtime journal-before/after/final reads must still be fresh inside their
existing stages through the reviewed root context. There is no pause for a
manual export inside a live handshake timer; absent access means do not launch.

Before approval, record host `ps -eo pid=,etimes=,args=` observations of virsh
console/startup/recovery owners and guest `who` through the same bounded
transport. Repeat immediately before arming; coordinate all operators for the
attempt and abort diagnostics. The observations do not guarantee that another
operator cannot attach later. Do not displace an observed owner automatically.
The guard repeats guest who and a bounded host owner scan at the end of
pre-start, retains the UTC/redacted PID-role observation, and blocks an observed
competing console/startup/recovery owner. It still cannot guarantee future
exclusivity; the PLC flock covers only this controller/watchdog.

Minimum rights: normal kevin host file/ledger/libvirt-console access; existing
operator-authenticated root PLC execution/bind502/process/socket/log reads;
root drives selected environment/FD reads; root XLe read-only SQLite/process
and journal reads; existing kevin SCADA Python and documented operator-coil
rights; remaining normal kevin read contexts. All inline guest Python is fixed,
hash-pinned, action-specific review content, not an unrestricted privileged
interpreter rule. No host-root Python or passwordless sudo recommendation.
No packet socket privilege or capture operation is needed.

**XLe ordering:** before 502, require the selected unit/configuration, ASX
active, at most one canonical XLe and a bounded journal showing the expected
PLC-unavailable retry. Its on-failure restart every two seconds is expected.
The automated gate reads structured `systemctl show` properties, current
process argv when running, and same-boot JSON journal entries from the last
10 seconds (81-entry read, rejected if more than80). Only a same-PID
connection refusal to10.10.1.10:502 plus the selected XLe's exact
`RuntimeError: PLC Modbus unavailable` proves the expected retry. An
auto-restart sample must corroborate ExecMainPID, exit-code1 and MainPID0;
a running retry must have canonical argv, a nonzero matching MainPID and
NRestarts>0. The retained decision explicitly distinguishes EXPECTED_PLC_UNAVAILABLE_RETRY,
START_LIMIT_FAILURE, CONFIGURATION_ERROR, UNEXPECTED_FAILURE and
INSUFFICIENT_EVIDENCE. Start-limit, unrelated exceptions/signals, execution
errors, stale/truncated/restricted/missing evidence block **before launch**.
Manager rows must be attributed to PID1/the XLe unit and match the recognized
exit-code1/restart/started/stopped messages; unknown transitions also block.
Successful serial/status transport alone cannot pass this gate.
Do not require XLe to be stably active before enabling 502. After 502, typed
pre-handshake requires the canonical active XLe. Final verification requires
active/running, nonzero MainPID and unchanged MainPID/NRestarts over two
samples separated by two seconds, plus journal/PLC acknowledgement proof.
Unexpected retry causes are not waived and no manual XLe restart is permitted.

## Exact planned control primitives

The host guard exports its installed/state/freshness/journal read commands,
expected hashes and these fixed writes using `plan`. The manifest supplies
the exact43 destination hash and unit reads. No command below was executed
during preparation.

The inert `plan` command below emits the exact inline Python launch/helper
source and fixed handshake command for review. Do not copy an old shell PID
launch or naked nc quit into this workflow. The launch helper runs in
`/home/kevin/OpenPLC_v3/webserver`, opens the unused log with `xb`, and uses
`Popen(['nohup','./core/openplc'])` with stdin=/dev/null and stdout/stderr=log.
It adds only `SORTER_CORE_ATTEMPT=<fresh 32-hex attempt token>` to the inherited
environment. No Web UI or Start_run_mode setting changes. While its direct
child is still unreaped, it pins a pidfd and records child PID, start ticks,
UID/EUID, boot ID, parent PID, attempt marker and guest monotonic issue time.
The launcher flushes `child-created` and `birth-before-validation` JSON events
before validation can raise. Each birth includes the raw process observation,
Popen spawn PID, parent PID and original monotonic issue time. Every failed
validation predicate reports its name, observed value and type, and expected
value. UID/EUID0 are valid integers; false, floating-point or string substitutes
are rejected. No historical rejected field is reconstructed.

Canonical `serial-launch-once-*-command.json` retains stdout/stderr before the
transport can raise for nonzero command exit. `launch-diagnostics.json` extracts
the untrusted events, terminal/cleanup result and extraction errors even on that
failure path; missing terminal evidence is incomplete. On success the controller
also retains raw output in `launch-observation.json` and inspection in
`started-core-observation.json`, explicitly untrusted.
The plan's zero token is an inert placeholder; `run` generates a fresh token
and substitutes the independently reviewed UID and log path.

**Launch lifecycle and separately approved failure cleanup:** the launcher
requires the default SIGCHLD disposition and exclusive control of its child
handle (no other waiter/reaper). It retains the unreaped Popen child, pins its
pidfd, and checks its PID/start/boot/UID/EUID/direct PPID against the unchanged
parent incarnation. A pidfd alone does not establish ownership. `nohup` may
exec the selected core in the same incarnation; two matching selected executable
path/hash samples must appear within2 seconds and at most32 observations.
Polling never renews the original issue time. A mismatched birth predicate
fails; no marker/UID/ancestry exception or alternate target is accepted.

The future operator scope must explicitly include
`--allow-launch-failure-child-cleanup` and the prerequisite receipt field
`launch_failure_cleanup:"owned-direct-child-pidfd-term-kill-v1"`, bound to the
exact reviewed helper/guard hashes and a fresh startup approval. Missing either
blocks the run before guest contact or spending authority. This is a new scope
for a future launch, not use of the spent startup or recovery authority.
Preparation does not approve these signals or any guest operation.

Only on launcher failure, while it still holds the child/pidfd, this scope
permits `signal.pidfd_send_signal(fd,SIGTERM)` once, wait up to0.5 seconds,
then recheck the same PID/start/boot/UID/EUID/direct parent and parent incarnation
before `SIGKILL` on the same pidfd if necessary. Cleanup has at most2 seconds,
clamped to the original4-second observe/cleanup envelope inside startup10;
observation and cleanup bounds are not added to the stage/watchdog/abort limits.
Existing UID/EUID0 suffices; no capability, sudoers or permission change is
proposed. Popen's wait/reap independently confirms this child exited, including
an exit during validation. A confirmed `CHILD_EXITED` is not a global
`CORE_STOPPED` or listener-closure proof. No process-name kill, numeric PID signal,
retargeting or unconditional RPC fallback exists. Missing pidfd/anchor, changed
identity/parent/UID, exhausted time or failed wait is INCOMPLETE with operator
recovery needed; a still-running child is never labeled stopped.

The final receipt is flushed while both handles are still retained so a
publication error also reaches this cleanup. Primary validation/launch errors,
cleanup results, evidence-write errors and resource errors are separate.
Catchable launcher SIGTERM reaches this path. SIGKILL, host failure, loss of the
launcher before pinning, a transport failure after successful handoff, or
uninterruptible fork/exec/I/O cannot be repaired or proved safe by this path.
No new supervisor or cleanup authority for an already orphaned core is added.
Missing terminal evidence remains INCOMPLETE. The existing independent watchdog
still refuses RPC without its complete validated private/durable authorization.

**Preserved history:** startup `m2-startup-20261026T230506869193Z` remains
INCOMPLETE; its grouped error does not identify the historical failed field.
The separately authorized recovery
`m2-existing-core-stop-20261027T022354886020Z` recorded CORE_STOPPED with independent
exit/listener verification. Its evidence and both spent claims are preserved;
that recorded endpoint is not a new current observation or launch authority.
New final records label an incomplete abort's PLC state
`UNVERIFIED_ABORT_INCOMPLETE`; only independently confirmed stop proof may
produce `UNAVAILABLE_AFTER_CORE_STOP`. Original raw results are not rewritten.

**Publication:** only an observation matching that direct-child receipt,
reviewed UID/EUID, attempt token, boot ID, start ticks, selected executable
path/hash, installed configuration and no early502 can create
`started-core-identity.json`. A private pipe carries the validated authorization
to the watchdog. Neither a raw observation nor a launch PID alone is authority.
Launch88/observed77, UID mismatch, missing or malformed proof never authorizes77.

**RPC ownership and replacement race:** the installed interface is an
unauthenticated TCP listener, so a PID check followed by a new nc connection
is insufficient. For either fixed `start_modbus(502)` or conditional `quit()`,
the inline helper validates the durable/private authorization, guest receipt
age0–630 seconds, exact PID/start/boot/UID/EUID/attempt/executable/hash, a live
pidfd, and absence of other cores. It requires the sole43628 listening socket
inode in that core's FD set. It then connects **without sending bytes** and
proves the server-side ESTABLISHED tuple's inode belongs to that same core.
Immediately before sending it rechecks incarnation, pidfd, core set, listener
inode and accepted-socket inode. It sends the single fixed command on that
**same established connection**, never reconnecting or retrying. A listener
replacement before the check is refused; one after the check cannot retarget
an already-established TCP connection to the replacement listener. A send
ambiguity is retained as unconfirmed, without retry.

This proof assumes the reviewed core/kernel do not transfer accepted FDs to
another process; hostile root/ptrace or descriptor passing is outside its
scope. That installed-interface/visibility prerequisite remains NOT REVIEWED.
If it cannot be established with existing rights, refuse the RPC, report
INCOMPLETE/operator recovery needed, and stop here. There is no process-name
kill, PID signal, alternate RPC or unconditional fallback.

**Phase boundary:** pre-start support checks never connect to an absent core
or require an accepted descriptor. Startup10 validates/publishes only the
launched incarnation. In listener10, the existing helper opens the actual
connection and proves its accepted FD **before sending start_modbus(502)**;
the proof/command result is retained in listener `rpc_ownership` and the serial
receipt. There is no extra test RPC or automatic retry. Initial-state reads
and the no-package handshake depend on that success. Conditional abort makes
its own fresh same-connection proof immediately before quit; a former success
does not authorize a replacement listener. This dynamic proof stays NOT RUN
in the prerequisite template and cannot be promoted by static source review.

If startup ownership cannot be proved: stop dependent actions, retain the
launch/observations/token and original error, and allow only the existing
watchdog's ownership-conditional abort within30 seconds. If it also refuses,
record abort/final-state INCOMPLETE and operator recovery needed; listener
absence alone cannot change that outcome. The operator retains normal-user
exported logs/receipts, coordinates consoles again, and performs this bounded
**read-only** exact-incarnation diagnostic using the PID from the validated
authorization (never a guessed process name):

```sh
# REVIEW ONLY: replace REVIEWED_PID with the validated authorization's decimal PID.
cd /home/kevin/vm/sorter-milestone2-baseline
/usr/bin/python3 -B -c 'import sys,time; sys.path.insert(0,"tests"); import m2_plc_startup_guard as g; from deployment_preflight import guest_read; pid=int(sys.argv[1]); assert pid>0; print(guest_read("plc",g.PLC_INSPECT+" "+str(pid),"startup-incomplete-diagnostic-REVIEWED",shell_user="root",deadline=time.monotonic()+30))' REVIEWED_PID
```

With missing/corrupt authorization, use this exact read without a PID;
do not assign another core as this attempt's target:

```sh
cd /home/kevin/vm/sorter-milestone2-baseline
/usr/bin/python3 -B -c 'import sys,time; sys.path.insert(0,"tests"); import m2_plc_startup_guard as g; from deployment_preflight import guest_read; print(guest_read("plc",g.PLC_INSPECT,"startup-missing-identity-diagnostic-REVIEWED",shell_user="root",deadline=time.monotonic()+30))'
```

Preserve the incomplete attempt unchanged. Resolve
the access/identity ambiguity with the operator before a separately approved,
exactly identified recovery action. This procedure authorizes no manual quit,
kill, service restart, second launch/reset or permission change as a workaround.

The single existing SCADA handshake remains:

```sh
cd /home/kevin/sorter-services && /usr/bin/timeout --signal=TERM --kill-after=3s 75s /home/kevin/opcua/bin/python -B recover_accumulation_state.py --remote-xle --reset-run
```

The helper writes coil910 true once; it enables only914/915/918/919. Its
teardown writes921,918,919,920,914,915,916,917 false, then880 false.
The PLC reset changes scanner nonce and clears state/counters; the dedicated
XLe remains the sole journal writer and allocates the new epoch. Normal
XLe/plant/scanner acknowledgements, heartbeats and PLC polling follow their
existing contracts. No direct internal register/epoch/journal writes are added.
The helper's finally can write even on guard failure, so the safe state must
be established independently before invoking it.

## Bounds and transitions

| Stage | Maximum seconds | Required result |
| --- | ---: | --- |
| Fresh automated pre-start | 240 | Clean selected source, 43/43 hashes, expected services/processes/VMs, exact installed files/DB/settings, no core/502/RPC. |
| Startup | 10 | One direct-child launch receipt; one matching live executable hash/PID/start/UID/EUID/boot/attempt, local43628, no early502. Only validated proof is published. Missing/ambiguous result spends the launch and requests conditional abort. |
| Listener | 10 | One ownership-proven established-socket start_modbus RPC returning OK, same surviving core, 502 present. |
| First readable initial state | 35 | First successful complete schema3 read must immediately meet all safe gates. Only unavailable connection may be retried inside this bound. Malformed/short/unsafe reads fail immediately. |
| Typed pre-handshake | 150 | Raw canonical capture retained plus fresh policy sidecar including564; consistent run identity required; self-comparison failures restricted to the explicit plant/epoch initialization fields. No operational synchronization prerequisite. Journal integrity/epoch inventory saved. |
| Canonical reset/handshake | 95 | Guest timeout75+3 escalation; canonical command timeout85, remaining host/liveness budget capped by95. Two PLC samples and journal-backed synchronization proof must also finish inside95. No retry on ambiguous completion, incomplete proof or failed cleanup. |
| Verification | **270 proposed; review required** | Complete unmodified typed stopped-state comparison, full43-entry live preflight, stable XLe, unchanged core/configuration and stopped-empty state, readable export, then UA/HMI freshness with the same synchronized run identity. |

The independent host watchdog has an additional **600-second total ceiling**
after arming, per-stage leases that are not refreshed by polling, and private
pipe EOF on controller death. These are userspace bounds under normal scheduling.
On abort it allows up to20 seconds for PLC-console lock, ownership proof,
fixed quit transport and liveness, then **10 seconds** for exact-incarnation
exit/listener/config checks. The owned RPC proof has an internal6-second
ceiling, with connect/response socket waits at most3 seconds each and always
clamped to that6. Log export runs **after cleanup**, at most5 seconds using
only time remaining inside the same30-second abort budget. No budget is renewed.
The controller waits at most32 seconds for the watchdog terminal record.
Explicit root UID probes consume these existing stage/overall budgets; none
is extended for authentication, manual export or slow transport.
The first-state35-second gate begins when the listener is confirmed.
The guard does not wait for human approval inside any live timer.
The consolidated preparation proposes changing **only verification180→270**.
Post-arming maxima total570 seconds (10+10+35+150+95+270), leaving30 seconds
inside the unchanged600 ceiling for stage transitions/watchdog arming overhead.
The existing owned-RPC launch-age limit remains630 seconds, covering the
600-second ceiling plus30-second abort; raising the overall ceiling would
need its own ownership-age review. Initialization remains280 seconds;
handshake95 and abort30 are unchanged. The prerequisite receipt must contain
the exact `timing_policy` emitted by the inert plan; older approvals are not
valid for these bytes or the changed stage budget. This proposal is not an
executed extension or a renewal of a spent attempt.
The guest75+3 bound leaves17 seconds of the95-second handshake stage for the
pre-reset read, transport/liveness and synchronization proof. These budgets
overlap; they are not additive permissions to exceed95. An85-second logical
helper fixture plus1-second pre-reset read, two2-second PLC reads,0.5-second
spacing and2-second journal read succeeds in92.5 seconds. A96-second helper
fixture fails and requests abort. Slow real serial/guest reads may exhaust
the remaining budget and must abort; no live latency feasibility is claimed.

The initial safe subset requires QW249=24115; master880 false; reset910,
ACK/Retry912/913/916/917 false; external/plant/photoeye/accumulation/chute modes
914/915/918/919/920/921 false; three empty slots and lane assignments; all
required process214–242 counters except informational serial-next221 zero;
scanner250–254 and trailer222–230 counts zero; scanner255/256, confirmation592,
photoeye748–750, zone788, ready786, XLe569 and chute faults/occupancy clear;
six VFDs ready, fault-free and settled0–40RPM. No zero-filled fallback is used.
Plant591=1 is allowed only with its explicit flag and every other gate passing.
Epoch561=1 is allowed only under the explicit startup policy above, never with
a nonzero epoch or an XLe fail-closed request. Plant2, epoch2, occupancy, master-on
and all other faults fail immediately. Neither allowance is healthy/run-ready.

Before the handshake stage can finish, both allowances end. Require new nonzero journal-backed epoch
558/559 not in the pre-handshake journal inventory; changed nonce509;
scanner255/256=0 and ACKmask257=7; plant587–589=[epoch_lo,epoch_hi,new_nonce],
different from before; plant heartbeat590 changing over two fresh samples,
age593<=30; plant591/epoch561/XLe569=0 and QW564=0; nonzero573==567; matching
555/556 accepted epoch, nonzero offer557==ACK560, matching heartbeat565/566;
no package outcomes
for the new epoch:nonce in the journal and no occupancy/counter change.
The two samples must retain the same handshake identity. That identity is
recorded as SYNCHRONIZED (baseline_result NOT_EVALUATED), then must remain
unchanged in typed-after and the final fresh state. Persistence of fault1 beyond
the handshake bound, recurrence after readiness (including a new zero epoch),
or any inconsistent sample fails and invokes the reviewed conditional quit.
The final fresh state/journal read also rechecks the acknowledgements, plant
identity, heartbeat advance and journal membership; initial synchronization
does not excuse a later lost ACK or invalid journal.

The canonical comparator is used unchanged: preserve all current EXACT fields,
zero required RESET_ZERO fields, safe environment/processes/VMs and six settled
VFDs. Do not substitute a historical field count. Initialization self-compare
FAIL remains recorded; only the explicit bounded initialization policy allows
the named plant/epoch pre-handshake fields. QW564 is read by the host's inline
observer because the selected guest reader does not expose it. A separate
fresh sidecar supplies it, and must match the raw typed snapshot's run identity;
missing/failed/inconsistent data is rejected. No guest reader is modified.
Final compare must PASS with **zero differences**, including seed, setpoints,
enables, photoeye/chute configuration and VFD commands/references.
Full live preflight is run only after502; requiring it before startup would
impose an impossible live-identity dependency.

Final XLe stability reads one canonical `systemctl show sorter-xle.service`
payload twice, separated by2 seconds, inside the proposed verification270
deadline. The selected properties are Id, ActiveState, SubState, MainPID,
NRestarts, ExecMainPID, ExecMainStartTimestampMonotonic, InvocationID and
ExecStart. Require the selected unit, active/running, positive matching main/
execution PIDs, a positive execution-start identity, a nonzero32-hex invocation
identity and exactly one unambiguous canonical ExecStart entry. Its string
`path=` must exactly equal `/opt/sorter-xle/venv/bin/python`, selected by
`deploy/systemd/sorter-xle.service:12` at the measurement commit and corroborated
by preserved installed-service evidence. Its argv must be the selected string-only
argument list. Matching argv cannot authorize a different executable path;
missing, duplicate, relative or ambiguous paths fail. The raw ExecStart path and
argv remain intact in the receipts and service views. Both complete service
views must match. Invocation/start identity detects changes even if a numeric
PID is reused; any restart-count or execution-property difference fails.
Availability of these properties on the installed service must be established
in a separately reviewed operation; missing fields fail, with no four-field
fallback. The reader remains read-only, using the existing canonical transport.

The host parser accepts exactly the selected property block, optionally preceded
by the exact intended command's canonical echo/completion-marker syntax.
Duplicate, missing, unknown, malformed or additional output fails closed. It
never treats `env SYSTEMD_PAGER=...`, completion/liveness nonces, receipt UTC or
transport deadlines as service properties, and never normalizes whole receipts.
The execution-start monotonic property is deliberately part of service incarnation
identity; it is distinct from transport timestamps. Canonical nonce, root UID,
completion, prompt, exit and separate liveness checks still run before parsing.
Raw command/receipt evidence stays intact. Parsed views and their successful
comparison are saved separately as xle-stability-observations.json and
xle-stability.json. These records authorize no additional lifecycle action.

The preserved attempt `m2-startup-20261027T042934779982Z` recorded only the older
four service properties. Correct extraction makes that recorded subset equal;
its differing command nonces do not prove a service restart. The missing richer
incarnation/execution properties cannot be invented from later or pre-start
records. Offline subset equality does not establish the new complete gate or a
final startup PASS. UA/HMI and later acceptance were NOT RUN. The historical
attempt remains INCOMPLETE and its conditional abort remains PASS / CORE_STOPPED.

Freshness is checked on SCADA's own monotonic clock: two good-quality UA
PlcPollMonotonicMs values advancing, each age0–1500ms, ProgramHash24115;
two HMI GET /api replies connected with poll_fresh true, run false,
scanner clear/ACK7, plant/zone fault0 and inducted0. No HMI POST or proxy.
Inactive historical slot/zone payload stays informational under the canonical
contract; it is not reclassified as current occupancy.

## Concrete host invocation for later approval

Consolidated preparation for attempt `m2-startup-20261027T162715081114Z` is in
[MILESTONE2_COMPLETION.md](MILESTONE2_COMPLETION.md). That attempt remains
INCOMPLETE/SPENT; its owned conditional abort remains separately PASS/CORE_STOPPED.
Its verification stage consumed179.692 seconds: typed capture about43,
full per-entry deployment preflight130.625, first XLe sample plus liveness
about2.3 and the required2-second separation. Only0.189 seconds remained for
the second serial command. The600-second overall ceiling was not exhausted.

Final verification now batches compatible read-only hashes and service
ActiveState observations **per guest** through the canonical authenticated
command and separate liveness receipts. All43 selected hashes, exact unit sets
and PLC identity are still checked; source files and manifest remain selected
f587cd6. Typed comparison, both richer XLe observations/separation, installed
identity, stopped state, journal, logs/export and acceptance remain required.
No arbitrary receipt normalization or ignored service fields were added.

The UA client now uses the selected server's actual bind endpoint,
`opc.tcp://10.10.2.10:4840/sorter/` (`scada/opcua_server.py:ENDPOINT`), rather
than127.0.0.1. Namespace `urn:sorter:level2`, Process/Status/PlcPollMonotonicMs
and Run/ProgramHash paths match selected source; HMI `/api` remains local
HTTP127.0.0.1:8000. Execute two UA/HMI observations separated by0.5 seconds;
require good UA quality, identity24115, advancing poll stamp, measured age0..1500ms,
connected/fresh HMI and the full required stopped values. These are ages at
guest observation, with the authenticated transport bracket retained, not a
claim of zero transport latency. This last remote gate follows export so export
cannot age it out. No actual UA/HMI output exists in the latest attempt: these
interfaces are source/installed-hash supported and fixture tested, still NOT RUN.

At the observed~2.32 seconds per command/liveness pair, batching the61-pair
130.625-second preflight to five guest reads plus the unchanged PLC identity
read budgets about14–20 seconds. A planning envelope allocates typed60,
preflight25, XLe10 (including2-second separation), final core/state/journal20,
logs/export20, UA/HMI10 and acceptance/export bookkeeping10:155 seconds. Proposed270 leaves
115 seconds of explicit stage margin; these are forecasts, not measured live
durations or permission to exceed either deadline. A missing final gate still
fails and invokes the existing independently bounded ownership-safe abort.

Inert plan (host-only, safe to inspect):

```sh
cd /home/kevin/vm/sorter-milestone2-baseline
/usr/bin/python3 -B tests/m2_plc_startup_guard.py plan --core-log /home/kevin/m2-core-REVIEWED.log --allow-initial-epoch-fault-1 --allow-initial-plant-fault-1 --allow-launch-failure-child-cleanup
```

The operator supplies a fresh normal-user readable prerequisite JSON, with
`status:"PASS"`, `selected_source` set to the full selected commit,
`success_endpoint:"CORE_RUNNING_MASTER_OFF"`, `approval_id`,
`reviewed_utc` (at most600 seconds old), exact `core_log`, verified `core_uid`,
`schema_version:2`, `host_guard_sha256`, `owned_core_helper_sha256`, the current
host `serial_command_sha256`/`deployment_preflight_sha256`,
`core_uid:0`, `execution_context:"operator-root-consoles"`,
`console_shell_users:{"plc":"root","drives":"root","xle":"root"}` and the
six **pre-start** receipt keys above each PASS. Separately approve
`launch_failure_cleanup:"owned-direct-child-pidfd-term-kill-v1"` as described
above. Set
`runtime_checks:{"modbus_accepted_socket":"NOT RUN","conditional_abort_recheck":"NOT RUN"}`.
These cannot be prefilled PASS to manufacture readiness. Retain exact
read evidence paths/hashes/review findings in that JSON too. A label alone is
not evidence. It is not generated with PASS by preparation.

Only after explicit approval, replace REVIEWED everywhere and use an unused
approval ID/evidence path. The following proposed command independently opts
into the separately reviewed **epoch fault1** and **plant fault1** startup
allowances; remove any flag not approved for that future attempt. The present
instruction approves host changes only. UID1000 startup is now blocked;
the root-console profile and access require independent review. Prerequisite
templates remain NOT REVIEWED, including support keys that need operator access.

```sh
cd /home/kevin/vm/sorter-milestone2-baseline
/usr/bin/python3 -B tests/m2_plc_startup_guard.py run --evidence /home/kevin/vm/sorter-evidence/m2-startup-REVIEWED --core-log /home/kevin/m2-core-REVIEWED.log --expected-core-uid 0 --execution-context operator-root-consoles --approval-id m2-startup-REVIEWED --reviewed-prerequisites /home/kevin/vm/sorter-evidence/m2-startup-REVIEWED-prerequisites.json --allow-initial-epoch-fault-1 --allow-initial-plant-fault-1 --allow-launch-failure-child-cleanup > /home/kevin/vm/sorter-evidence/m2-startup-REVIEWED-controller.stdout 2> /home/kevin/vm/sorter-evidence/m2-startup-REVIEWED-controller.stderr
```

The existing host approval ledger spends the ID before the attempt. Existing
evidence paths or a used approval ID cannot be retried. Do not wrap the command
in an outer timeout that kills its watchdog. Catchable controller termination
requests abort; SIGKILL cannot create a controller terminal receipt, while the
surviving watchdog still attempts ownership-conditional quit and writes its own record. Losing both
processes, host failure, an inaccessible/occupied console or unavailable RPC
cannot be repaired by this host-only procedure. Missing stop proof means
INCOMPLETE and immediate operator escalation, without a kill/restart/reset.
If controller death prevents validation, durable publication or private receipt
delivery, the watchdog refuses quit. Missing, corrupt, mismatched or stale
identity cannot authorize another core. Listener/name absence alone cannot
establish exact-incarnation exit; abort/final-state evidence stays INCOMPLETE
and operator recovery is required. No process can write its own terminal
receipt after SIGKILL.

The fresh prerequisite receipt and host operation stdout/stderr should be
preserved beside the new packet; retain the guard's numeric exit status before
running any export command. No startup command is retried to repair a missing
transport marker or unreadable receipt. Unexpected XLe errors, any unsafe read,
wrong identity, deadline, failed helper teardown, typed difference, stale
UA/HMI or missing evidence requests the watchdog's single ownership-conditional
stop attempt; an unprovable target is refused, never guessed.

## Evidence, failure and normal-user export

`results.json` records startup, listener, initial policy, typed-before,
handshake, verification, abort and final-state results independently. Unexecuted
stages stay NOT RUN. Abort PASS never turns startup/handshake failure into PASS.
The watchdog's final core-stopped proof is distinct from whether quit transport
succeeded. Successful verification leaves the explicit running/master-off
endpoint; a later deliberate stop would need a separately approved action.
After abort closes502, live PLC state and a new typed postflight are unavailable.
A final core-stopped PASS attests only core/listener closure plus unchanged
installed configuration/Web UI. It does not attest empty slots, clear PLC faults
or PLC recovery after the stop; those remain unverified. The attempt remains
INCOMPLETE even when conditional stopping succeeds.

Every canonical serial command and separate liveness probe retains nonce,
completion/prompt/exit, stdout/stderr, command, UTC and remaining deadline.
Repeated attempts have distinct trace files. Typed snapshots/comparison,
43-entry report, first-state exception record, journal inventories/selected
logs, two heartbeat samples, UA/HMI values, core incarnation, core log export
and host source identity are retained. Host results are created as normal kevin,
not in the old privileged capture directory. No mode/owner changes are made.
The core log export is capped at64KiB, decoded into readable host core.log and
checked against its guest byte count/hash. A larger/unreadable/changing log
fails verification; no silent truncation is called complete.

On abort, early startup failure, watchdog timeout or controller loss, the
watchdog attempts the same bounded best-effort export into `abort-core.log`
after cleanup. `abort-log-export.json` and watchdog `log_export` retain errors
separately from the primary controller/watchdog error, `ownership_errors`, and
cleanup `errors`/abort result. Export failure cannot override an original
failure or prevent owned-target cleanup; successful export cannot make an
incomplete stop PASS. A nonexistent early log, unavailable console, >64KiB,
changing/unreadable log, storage failure or exhausted remaining abort budget
is explicitly INCOMPLETE. The controller-loss fixture uses a fake core log;
it does not attest guest log access or real serial latency.

For both success and failure, retain stdout/stderr and hash the new host packet
using checked output redirection (outside the guest). Record guard and hashing
statuses separately. For example, after the guard finishes, use Bash:

```sh
# REVIEW ONLY. Set these to the completed new startup packet; do not run startup here.
EVIDENCE=/home/kevin/vm/sorter-evidence/m2-startup-REVIEWED
(
set -euo pipefail
cd "$EVIDENCE"
find . -maxdepth 1 -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum -- > SHA256SUMS
)
```

Any nonzero result or failure to open SHA256SUMS means export/sealing INCOMPLETE.
It cannot override the original guard failure. Readonly copies/exports must
preserve bytes/hashes; do not chmod/chown the raw packet or restore archived
root ownership. If a guest log/journal is unreadable, retain the failure and
request a separately reviewed bounded read/export; do not elevate the controller.

Preparation fixture evidence is separate from all live results. Kernel
scheduling/serial latency, guest timeout behavior, startup privileges and actual
deployment/bindings remain execution prerequisites, not fixture attestations.

Focused ownership/log/XLe repair evidence and superseding inert templates:
`/home/kevin/vm/sorter-evidence/m2-startup-ownership-repair-20261026T185724Z/`.
Use its `PROPOSED_PLAN_REVIEW_ONLY.json`,
`PROPOSED_COMMANDS_REVIEW_ONLY.sh` (unconditional exit before proposed actions),
and `prerequisites-NOT-REVIEWED.json` for Hermes's focused re-review.
Prior preparation/review evidence remains intact. Neither these fixtures nor
the accepted capture-mechanics gate approves startup or fills prerequisite PASS.

The superseding UUID/access/context preparation and inert templates are in
`/home/kevin/vm/sorter-evidence/m2-startup-prerequisite-preparation-20261026T204936Z/`.
Use that packet's new hashes, schema2 prerequisite template (NOT REVIEWED),
read-only command sheet and guarded proposal for Hermes's review. The earlier
UID1000 command/receipt is superseded and blocked by this guard. Live startup,
Modbus enablement, handshake and conditional abort remain NOT RUN.
