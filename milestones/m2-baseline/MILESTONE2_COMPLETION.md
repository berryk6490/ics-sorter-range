# Milestone 2 completion checklist and reviewed execution order

Preparation only, October27,2026. Selected measurement source **f587cd6d6e5d425834d1ae35b43fd1fa1a8e69e9**;
manifest SHA256 **9a6b3b9c84fc1552fa73f7eb9835d30b4ab781fa52604a55dea23bd1362665ab**.
Host changes are undeployed. No new attempt, capture, guest contact or authority
is created here. All six operating records remain **NOT RUN**.

Hermes's final narrow review accepted the startup repair and four scenario
corrections; its remaining healthy-idle sample-span defect is corrected in
IMPLEMENT-001. The same packet contains a host-prepared adaptation of the
existing collector for pinned guest execution/export and selected log sinks.
CORRECTION-002 addresses only the four independently reproduced A-01/B1/B2/B3
evidence findings; idle remains closed. **The corrected bytes await Hermes
review; no guest provisioning or execution is claimed.** Startup verification270/watchdog600/abort30 remain unchanged.

## Evidence already available; reuse its actual scope

| Requirement/gate | Existing evidence and disposition |
| --- | --- |
| Capture mechanics/integrity/cleanup | October22 `m2-smoke-final-verification-20261022T022904Z/FINAL_DIAGNOSTIC_SMOKE_VERIFICATION.md`: PASS. Two host PCAPs show SYN/refusal traffic, not successful application traffic. Reuse this closed gate; no diagnostic recapture. |
| Selected deployment/installed identity | Latest `m2-startup-20261027T162715081114Z`:43/43 guest destinations and program24115 verified then; startup/owned listener/initialization/handshake passed. Historical observations need freshness at the next operation, not another source migration. |
| Typed stopped restoration | Latest typed comparison245 records, zero differences. Partial startup evidence only. |
| XLe executable/parser | Accepted exact path `/opt/sorter-xle/venv/bin/python` and argv; latest first nine-field sample passed. Second sample absent: no two-sample stability claim. |
| Abort | Latest independently verified PASS/CORE_STOPPED, log export COMPLETE in7.667s. Original startup remains INCOMPLETE/SPENT. Prior successful aborts/recovery remain separately valid. |
| Source/paper reconciliation | October14 Milestone1 paper at1e271ed remains unchanged. Selected M2 dedicated-XLe, three-slot/24115/43-entry deployment differs from the paper's SCADA-local XLe/ASX,24113/33 entries. |

## Execution blockers

1. Hermes must review **one consolidated change packet**: final-verification
   batching/endpoint/timing and the dedicated scenario extensions. Verification270
   is proposed, watchdog600/abort30/initialization280 unchanged. Old approval
   is SPENT and cannot authorize different source or timing bytes.
2. A new independently checked prerequisite receipt and fresh explicit one-use
   startup authorization are required. Root consoles on plc/drives/xle and kevin
   on scada/fw are existing operator contexts; observe and recheck exclusivity.
   Support/read permissions are not permanent exclusivity guarantees. Templates
   stay NOT REVIEWED. Core must be independently observed stopped before launch.
3. Final startup still needs two separated richer XLe samples, actual UA/HMI
   freshness and accepted watchdog result. Latest attempt supplies none of the
   missing gates. Only an actually accepted startup can open scenario execution.
4. Required local traffic remains invisible at host taps. IMPLEMENT-001 now
   packages the existing collector/supervisor and bounded canonical read-only
   export (`tests/m2_guest_capture.py`); the timeout-only renderer is UNSUPPORTED.
   This is host fixture evidence only: independent review, separate provisioning
   approval, exact existing execution contexts/destinations and real capture-open
   support remain blockers. Historical October22 local routes and October27 settings were verified; current
   route/interface/tool/context evidence is pending and drives-local remains
   disabled. Do not substitute firewall PCAPs for loopback.
5. Operator must supply exact capture-open rights, readable exports, tap/MAC
   mapping, storage budget, clock uncertainty and contemporaneous unit/PID/socket
   attribution. Saved file hashes do not attest loaded policy, scanner env files
   or interface bindings. Do not fill these from a manifest.
6. Each package/recovery scenario requires its own new scope and approval;
   no scenario follows an unresolved canonical restoration gate. Normal accepted
   startup does not authorize package writes, captures or a later core stop.

## Exact runner path and six-case matrix

Extend the existing host `tests/run_accumulation_attempt.py`, using its canonical
approval ledger/restoration gate, `m2_plc_startup_guard.LiveIO` authenticated serial
receipts/separate liveness, selected-source deployment preflight, typed snapshots,
and the installed remote-XLe recovery. No second service/proxy/monitor lifecycle.
The reviewed host copy of `live_xle_vm_three_lane.py` is sent as an inline command;
the installed original file and manifest remain unchanged. This is a proposed
guest command, not a deployment claim. It imports the existing selected
`/home/kevin/live_xle_multi.py`. No ASX plan changes or local XLe/ASX processes.

| CLI case | Exact worker/control path | Assertions/writes beyond read-only guards | Current result |
| --- | --- | --- | --- |
| `healthy_idle` | >=2 safe observations spanning10 seconds between successful samples | None; typed/current core/final state and UA/HMI required | NOT RUN |
| `normal_sorting` | worker `--case normal`, seed137 | Three packages, lanes1/2/3, trailers2/5/8; journal identity/outcomes | NOT RUN |
| `recirculation` | worker `--case recirculation`, seed1 | One lane1 GOOD barcode1001, unchanged ASX no_decision, recirc reason1/actual0, zero trailer confirmations, journal | NOT RUN |
| `backpressure` | worker `--case backpressure`, seed137 | Three packages; outbound coils885–887 off then on; all three validated drive-off holds, no load during hold; then trailers2/5/8 | NOT RUN |
| `drive_stop_resume` | worker `--case drive_stop`, seed137 | One package;882 off then on, validated drive-off hold, VFD10.10.1.21 RPM0→positive, one trailer2 confirmation | NOT RUN |
| `documented_recovery` | installed `recover_accumulation_state.py --remote-xle --reset-run` | Stopped no-package canonical reset only; fresh nonce/epoch/plant identity, journal, ACK and heartbeat | NOT RUN |

Package workers temporarily set seed247, enabled lanes882–884/outbounds885–887,
modes914/915/918/919/920, belt setpoints200–205 and rates207–209, master880,
and scanner reset910. Existing finally restores original operator settings,
master off; then the separately approved canonical reset clears dynamic results.
Do not infer full restoration from finally: typed comparison, safe state, live
hash/service checks, readable journal and fresh UA/HMI must independently pass.
Backpressure is deliberately defined above as outbound-drive backpressure; no
legacy lane_hold/merge_hold service fixture or unmeasured FIFO/spacing claim.

Each host scenario has a fixed540-second total operation deadline, enforced by
a host alarm as well as the shared monotonic serial deadline. The package
worker's independent GNU timeout is300 seconds, TERM then KILL after3; host
transport allows305 seconds plus separate liveness, clamped to remaining total.
Its normal internal waits are30-second reset +20-second readiness +190-second
workload, leaving60 seconds before TERM for I/O/finally. Catchable TERM runs
finally; KILL cannot. Unknown completion never authorizes another reset while
the worker might still run: result/restoration stay INCOMPLETE, block the next
case, retain receipts and require operator diagnosis after the independent bound.
No fallback signals to a PLC/core, service restart or automatic core quit exists
in this scenario path. A hung host/kernel or failed guest supervisor is outside
userspace timing guarantees and must remain an explicit incomplete result.

## Inert future commands (NOT authorization)

All paths below are placeholders ending REVIEWED, not existing or newly created
authority. Use a different immutable receipt/approval/evidence path for each case.
The sealed packet contains NOT-REVIEWED templates and a Bash-syntax-checked
command sheet. Do not execute this section during preparation.

```sh
cd /home/kevin/vm/sorter-milestone2-baseline
/usr/bin/python3 -B tests/m2_plc_startup_guard.py plan --core-log /home/kevin/m2-core-REVIEWED.log --allow-initial-epoch-fault-1 --allow-initial-plant-fault-1 --allow-launch-failure-child-cleanup
# AFTER independent review and separate explicit startup approval:
/usr/bin/python3 -B tests/m2_plc_startup_guard.py run --evidence /home/kevin/vm/sorter-evidence/m2-startup-REVIEWED --core-log /home/kevin/m2-core-REVIEWED.log --expected-core-uid 0 --execution-context operator-root-consoles --approval-id m2-startup-REVIEWED --reviewed-prerequisites /home/kevin/vm/sorter-evidence/m2-startup-REVIEWED-prerequisites.json --allow-initial-epoch-fault-1 --allow-initial-plant-fault-1 --allow-launch-failure-child-cleanup
# AFTER complete accepted startup, execute ONE separately approved case:
/usr/bin/python3 -B tests/run_accumulation_attempt.py --dedicated-prerequisites /home/kevin/vm/sorter-evidence/m2-scenario-REVIEWED-prerequisites.json --dedicated-case healthy_idle --evidence /home/kevin/vm/sorter-evidence/m2-healthy-idle-REVIEWED --approval-id m2-healthy-idle-REVIEWED --approved-at REVIEWED_ISO_UTC
```

For the other five commands substitute exactly the corresponding CLI case,
case-specific evidence, approval ID and reviewed prerequisite JSON from the table.
The host gate checks source/runner/guard/preflight pins, exact scenario and
approval time, accepted startup core identity and unresolved restoration gate
**before any guest command**. Fresh startup receipt is not reconstructed from
partial readiness. The canonical approved-ID ledger is spent once before reads.

It additionally pins `scenario_control_sha256`, requires each of the six named
`checks` to be PASS with a nonempty list of exact `{path,sha256}` `check_evidence`
references, and verifies those bytes before spending. Hashes prove retained
bytes, not human approval: the operator must independently judge the referenced
facts. No outer PASS label can replace a missing detailed check.

The canonical stable operation lock is held from restoration-gate inspection
through approval spending, operation and sealing. A competing dedicated call
fails before claim or guest contact. Local terminal PASS is sealed before shared
gate PASS is published. Unknown completion or failed sealing leaves the next-run
gate closed. Legacy SCADA-local runners remain excluded from this procedure;
the lock is not a claim that arbitrary external guest operators are prevented.

During idle, at least two successful safe PLC samples span at least10 seconds
between observation timestamps; first-read latency does not count. They preserve exact
epoch/nonce/plant identity and show advancing heartbeat; journal inventory must
remain unchanged. Final identity/journal are checked again. During drive/hold
cases, every loop sample after hold establishment must retain validated motion,
reason, dwell/quality, zero trailer counters and disabled targeted coils (plus
RPM0 for induct stop). At least two retained samples span3 seconds before resume;
any intervening sampled loss fails. These are sampled observations, not a claim
about unobserved analog intervals. Run identity/master must remain consistent.

Each scenario collects bounded pre/post evidence on drives/scada/xle: selected
systemd service/incarnation fields and `ss -H -antp` PID/socket ownership, plus
**selected installed log sinks**. Manifest-pinned service templates declare
SCADA `/home/kevin/opcua.log` and `/home/kevin/hmi.log`, scanner/VFD
`/var/log/{scanner,vfd}-<instance>.log`, and XLe/ASX/plant journald. File reads
are O_RDONLY/O_NOFOLLOW, <=8192 bytes/unit with dev/inode/range/hash/cursor;
actual MainPID stdout/stderr FDs must match the selected file, with stable
startticks. Post reads require the same device/inode and a matching <=256-byte
previous boundary anchor before the old offset. Missing anchor, changed content,
rotation or short reads fail closed. Same-inode copytruncate/regrowth is detected
when the anchor changes; matching anchors cannot prove arbitrary untouched file
history. File events are BOUNDED_EVENT_BYTES and history remains UNPROVEN. Exact old/new
witness and returned-suffix ranges/lengths/digests/types are enforced. A supplied
prior observation requires its cursor; missing legacy evidence cannot silently
reset to an initial snapshot. INCOMPLETE cursor state persists across failed,
unstable or exhausted intervals; later append cannot repair that interval.
ACCESS-LOG-005 introduces an explicit initial-position phase: stable long/empty
files may establish BASELINE_VALID cursors while historical event attribution
remains INCOMPLETE. Initial request/payload phases must match; prior cursors
cannot be passed to initial mode or discarded automatically. A stable quiet
actual interval retains positioning but reports NO_EVENTS/INCOMPLETE attribution.
Pre-positioning/journal readiness and actual post-interval attribution are
aggregated separately without overwriting a failed axis. A
consumer requiring lossless history remains INCOMPLETE; sink binding is not
exclusive authorship.
Journal evidence is <=200 JSON entries/unit, from guest pre-observation UTC;
only current unit/MainPID/boot/**InvocationID** matching events count. Missing or
contradictory invocation identity does not count; manager messages remain separate.
Each subprocess has3 seconds and1MiB checked output, inside canonical
command/liveness/shared540-second limits. Raw service fields and log bytes/
metadata are preserved; MainPID/start/invocation/restart identity must stay stable.
No environment values are exported.

Missing/denied or mismatched FD evidence blocks before the worker. Empty,
truncated, unstable or manager-only evidence cannot claim application attribution:
`application_attribution` remains independently INCOMPLETE; bounded matching
events support `SUPPORTED_WITH_LIMITS`, never a complete log-stream claim.
Transport success or an empty journal is not application evidence. Actual sockets
and packet content still determine attribution; IP alone is insufficient.

Fresh support for those exact installed commands is required **before package
execution**. Reuse existing root drives/xle and kevin SCADA profiles. First request
SCADA kevin log readability/socket evidence; systemctl reads and root reads stay
separate approval scopes. Do not grant journal rights or root-SCADA. The sealed
packet contains exact inert commands; the older access-check probes remain
historical inputs and do not supersede the corrected selected-sink command.

Capture is independent. For every approved scenario, plan these exact host
points using its same scenario ID, then review the emitted commands:

```sh
/usr/bin/python3 -B tests/baseline_capture.py plan --point fw-l2 --point plc-l1 --output /home/kevin/vm/sorter-evidence/m2-capture-REVIEWED --scenario-id m2-scenario-REVIEWED --seconds 600 --packets 200000 --max-bytes 67108864 --grace 3
# Separately approved capture context ONLY, before the scenario; separate terminal:
/usr/bin/python3 -B tests/baseline_capture.py collect --point fw-l2 --point plc-l1 --output /home/kevin/vm/sorter-evidence/m2-capture-REVIEWED --scenario-id m2-scenario-REVIEWED --seconds 600 --packets 200000 --max-bytes 67108864 --grace 3 --clock-note 'REVIEWED clock brackets and uncertainty; one host clock'
```

This is a measurement window, not a repetition of the passed diagnostic smoke.
Both capture points must be RUNNING with validated attachment before the scenario
starts; reserve600 seconds so a540-second scenario plus launch offset fits.
If the capture fails or reaches packet/file caps early, capture_completeness is
INCOMPLETE even if functional outcome passes. Leave captures to their supervisor;
do not stop PLC/services to clean up a capture. Use the existing fail-fast
verify/analyze/seal workflow in MILESTONE2_BASELINE.md, preserving failed-stage
status and separate sealing errors. No scenario is automatically started by it.

The sealed IMPLEMENT-001 packet's `COMMANDS.sh` gives local archive build/plan
and separately held future remote-collect/export/verify-export commands. Guest
plans are NOT REVIEWED, NOT PROVISIONED and NOT EXECUTED. They reuse the existing
supervisor; no new lifecycle is installed. Source/runtime/contract pins and exact
root-owned XLe versus kevin SCADA paths must be independently checked first.
Drives-local planning remains rejected by unchanged capture configuration.
The same600-second/200000-packet/64MiB per-point ceilings apply. Capture command
transport is640/shared660 seconds; retrieval is a separate fixed-budget read-only
stage (20/shared30 seconds per inventory/chunk). Neither extends the capture bound.
Missing canonical collection receipt can only yield failure-evidence retrieval,
not acceptance. Every export inventory/chunk/final-inventory requires its own
exact canonical intended+separate-liveness pair. Offline verification replays
required order/chunk coverage, nonces, request/result and fixed budgets, and binds
both namespace postflights to canonical supervision identities. Missing receipts
or altered expected UID/boot cannot be repaired by rehashing. Verification uses
the recorded explicit outer host interpreter; Hermes execute_code must launch
that external process, not call guest_read in its own interpreter. Slow serial throughput may require a smaller planned capture;
full-size retrieval is not assumed feasible until measured/approved.

Minimum rights: ordinary host Python/files and existing libvirt console access;
operator-provided existing root contexts for PLC bind502/held-socket proof and
root-owned drives/XLe evidence. Capture needs packet-socket permission (normally
CAP_NET_RAW/root) and whatever the pinned interface/drop-user options require
in the already approved context, not blanket
passwordless Python. No sudoers, capabilities, permissions, port threshold or
VM/network configuration changes are proposed. Each capture, startup, reset,
package/drive action and any later owned stop is a separate reviewed state change.

## Deliverable acceptance and report finishing

- Preserve PCAP start/end, interface/filter, process/exit/drop/terminal/seal
  records, selected deployment, actual host tools, scenario/approval IDs and
  clock bounds. Export readable bytes without changing original evidence.
- Produce per-point offline summaries with the existing analyzer: observed
  endpoints/initiators, Modbus functions/ranges/rates/intervals/exceptions and
  supported TCP resets/retransmissions; unknown parser fields stay UNAVAILABLE.
  Never count TCP refusal as a Modbus exception. Do not sum duplicate routed taps.
- Join service logs, current PID/incarnation/socket observations and epoch/nonce/
  package journal identity for attribution. IP alone cannot identify a process.
  Build a map of observed edges, distinguishing it from expected topology.
- Fill every independent results axis (functional, capture, services, PLC
  recovery, typed postflight, orphan/plan checks and application attribution) from its own evidence. Missing
  axes or unexecuted cases remain NOT RUN/INCOMPLETE, never promoted by a final
  shell success. Record explicit evidence-backed NOT REQUIRED separately.
- Finish MILESTONE2_REPORT.md only after those records exist. Remaining report
  prose/formatting is a documentation task; missing measurements/authorizations
  are execution blockers. No attack, detection or hardening extension is needed.
