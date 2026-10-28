# Milestone 2 baseline collection — host preparation

Status: **PREPARED / LIVE COLLECTION NOT RUN**. This document and the synthetic
host tests are preparation evidence. No new guest observation, deployment,
packet capture, operating result, service action or PLC recovery is claimed.
The revised deliverables are **packet captures, an observed communication map,
operating results, and written baseline analysis** (October 14, 2026 paper,
p. 3). The original proposal's several-hour capture and Zeek deliverables on
p. 2 are historical proposal text; they are not added requirements here.
Attack, detection and hardening remain outside this workflow.

Current completion preparation is [MILESTONE2_COMPLETION.md](MILESTONE2_COMPLETION.md)
and the evidence-backed draft is [MILESTONE2_REPORT.md](MILESTONE2_REPORT.md).
The dated inventory/access blockers below are historical: the latest retained
startup attempt verified43/43 deployed destinations and synchronized the stopped
handshake, but final verification failed on its stage deadline. It remains
INCOMPLETE/SPENT and was conditionally aborted PASS/CORE_STOPPED. No accepted
healthy baseline or operating scenario follows from those partial successes.
The October22 capture mechanics gate remains PASS and will **not be repeated**.
IMPLEMENT-001 adds host-prepared guest packaging/export of the existing collector;
it is **NOT REVIEWED / NOT PROVISIONED / NOT EXECUTED**, not a guest mechanics PASS.

## Authority and configuration selection

The authority for current inventory is the operator-specified
`/home/kevin/vm/sorter-evidence/milestone2-inventory-20261021T235743Z/HANDOFF.md`,
SHA-256 `c31297e8ebf9d836b8afb915e00b8eb3ae75c56af6a6d41d774c9eef76cdcbfe`.
It observes October 21–22 UTC and explicitly retains the older
`milestone2-inventory-20261021T232146Z/HANDOFF.md` for the paper/runner audit.
Only that referenced audit is used from the older report; its powered-off
inventory is superseded. The refreshed packet's SHA256SUMS must verify before
use. Inventory is historical evidence, never fresh run clearance. The later
prerequisite review at
`/home/kevin/vm/sorter-evidence/m2-prerequisite-review-20261022T002927Z/REVIEW.md`,
with `SMOKE_REVIEW.md` and `RECOVERY_REVIEW.md` in that same directory, supersedes
inventory claims below where explicitly corrected. All three reports were
read in full for this collector repair; their recovery proposal is not executed.

| Layer | Identity and claims | Interpretation |
| --- | --- | --- |
| Paper baseline | `/home/kevin/Downloads/Kevin_Berry_Milestone_1_Paper.pdf`, October 14, 2026; SHA-256 `9324826d82b63f525a095f44b25e9148099e869823a9cbe69c4aecd1c978b409`; cited commit `1e271ed10bb3f3f90b669aadc30e7698911cfe83` | SCADA-local XLe/ASX; identity 24113; 33-entry manifest; 36 sweep probes (30 allow, six deny). These original claims stay dated. |
| Source inventoried by Hermes | Dirty main at `6b49eee90ea0890798f9025ef61327195714e0fd`; local 45-entry manifest, identity 24115 | Three tracked edits plus untracked observer/research files; not a clean selected release. Manifest `baseline_commit` is historical metadata, not Git HEAD or live identity. |
| Proposed measurement source | Already-local `origin/main` at `f587cd6d6e5d425834d1ae35b43fd1fa1a8e69e9`, isolated worktree `/home/kevin/vm/sorter-milestone2-baseline`, branch `milestone2/host-baseline-preparation` | Dedicated XLe, three PLC slots, declared identity 24115; **43** exact manifest entries, all locally consistent. Manifest SHA-256 `9a6b3b9c84fc1552fa73f7eb9835d30b4ab781fa52604a55dea23bd1362665ab`. This is an explicit proposal, not deployment equality. Host changes remain uncommitted. |
| Observed deployment in refreshed handoff | Six VMs running; 45-entry inventory: 30 matching, one different, 14 NOT_READ (drives); live PLC identity unavailable | SCADA worker digest `4c71d0a8…` matches selected f587cd6 source, whereas dirty checkout expected `5c0bfd8b…`. This explains one difference only. No full equality to f587cd6 follows. |
| October22 prerequisite-review deployment | Selected 43 destinations: **31 MATCH, 0 MISMATCH, 12 NOT_READ**, all unread on drives | Dated inventory, superseded by October27 retained reads below. |
| October27 retained deployment/settings | Latest attempt and prerequisite refresh verified43/43 hashes, nine scanner/VFD environment files, aliases21–29 and worker/listener ownership then | Historical settings proof is available. Fresh route/interface/tool/context/export and actual capture-open support remain unresolved; drives-local stays disabled. |
| Proposed host changes | Capture-only helper, extension of existing `tests/pcap_modbus_summary.py`, capture configuration and unexecuted result template | No component is added to `deploy/deployment_manifest.json`; no guest component is represented as deployed. |

The selected source includes scanner stream/range fixes and host lifecycle
race fixes after the paper commit. `LOAD_CONTRACT.md` remains a proposal,
not implemented drag physics or a new private protocol to include in the map.
The current matrix contains 48 declared probes (33 allow, 15 deny). Probe
expectations/counts are not observed application edges or a fresh firewall PASS.

There are two explicit worktrees. The host coding worktree above contains the
extended analyzer; the clean measurement source is
`/home/kevin/vm/sorter-m2-measurement-source` (detached at the same f587cd6).
Use that clean tree for selected deployment identity. Run the reviewed host
startup/scenario tooling from the coding worktree, explicitly validating hashes
against the clean selected tree, after separate authorization. The manifest includes the firewall
copy of `tests/pcap_modbus_summary.py`, so source-only preflight in the coding
worktree correctly reports a mismatch after the host extension. The manifest
still describes the older guest component accurately. No guest analyzer update
is claimed or needed to use the extended analyzer **on the host**. Capture
identity records both actual host hashes and clean measurement source hashes,
including `undeployed_host_component_differences`. The clean measurement tree's
43-component source-only preflight passes; neither tree has a live PASS.

## Prerequisites and stop conditions

Before any *operating baseline*, resolve and record each prerequisite. Do not
repair or deploy as a side effect of collection.

- Select/approve a measurement revision and its exact manifest. From the clean
  measurement worktree, compare all 43
  selected source/destination entries afresh using canonical
  `tests/deployment_preflight.py --live --report ...`; reconcile the two extra
  dirty-manifest observer entries separately. No silent manifest/hash changes.
- October22 drives login/hash gaps were subsequently resolved by retained
  October27 operator reads. Reuse their historical scope, then independently
  establish current route/interface/tool/context/storage/log/export support.
  No current capture-open permission is proved by those reads.
- PLC web runtime was active, but no core/TCP 502 was present. Compiled
  `PROG_HASH=24115` is not live QW249 identity. Runtime recovery needs separate
  authorization; obtain live identity, master off, empty slots and clear faults
  from the canonical typed snapshot before a healthy-idle claim.
- XLe was retrying `PLC Modbus unavailable`; HMI was connected to OPC UA but
  `chute.poll_fresh:false`. Require fresh underlying PLC/OPC data and healthy
  plant/scanner/XLe status; zero display values do not prove a safe baseline.
- Saved FW/XLe policy files matched, but loaded nft rules were unreadable.
  Obtain separately authorized active-policy evidence. Existing broad Case A
  analyst exposure remains a documented configuration difference from hardening.
- Reconfirm tap-to-MAC attachments after any VM start. The handoff's names are
  observations for that inventory window, not stable libvirt identifiers.
- Capture-open privilege remains unverified. Host dumpcap invocation had a
  permission failure. No capabilities, groups, sudoers or tools are changed here.
  Guest plans require existing Python3.9+ with Linux pidfd/proc visibility,
  `/usr/bin/prlimit`, `/usr/bin/timeout`, `/usr/bin/tcpdump`, a pinned runtime
  archive and contract, an existing appropriate parent and separate authorization
  for the exact command. Missing prerequisites block collection.
- Measure clock offsets/uncertainty without setting clocks: bracket each
  canonical guest UTC read with host UTC+monotonic times; retain RTT/resolution,
  clock source and uncertainty interval. Sequential UTC samples and FW's local
  stratum-10 reference do not establish synchronization. If unavailable, use
  per-source order and mark cross-host latency UNAVAILABLE.
- Obtain a fresh safe typed baseline, required services/process inventory and
  applicable restoration gate. September 26's drive-stop PASS is historical.
  Review dedicated-XLe compatibility of the chosen runner before authorization.

**Scanner correction:** the prerequisite review freshly read PLC mbconfig/DB
camera destinations **10.10.1.27–29**, agreeing with selected source templates
and matrix. It explicitly supersedes the unsupported `.31–.33` planned-flow
claim in the earlier refreshed handoff; that historical packet remains
unchanged. **Later October27 reads verified drives bindings then; current route/interface
and capture-open checks remain outstanding.** The selected
43-entry manifest covers scanner.py and the unit template, not scanner
`/etc/scanner/tunnel{1,2,3}.conf` environment files, guest interface aliases or
PLC mbconfig.cfg. The October22 drives-access refresh already observed aliases21–29 locally
routed via lo; October27 separately verified their settings. Neither is a
current capture-open proof. The later environment/unit/alias/listener reads, rather than the
manifest alone, support the historical settings proof. Current route/interface
and support evidence still needs separately authorized reads. No filter or interface verification flag is changed.

A diagnostic capture of current retry/refusal traffic may be separately
approved without calling it healthy idle. A capture smoke test proves collection
mechanics only; it cannot clear the operating prerequisites above.

## Capture coverage and configuration

`tests/milestone2_capture.json` contains only capture points, filters and
provenance. `tests/milestone2_results.json` contains scenario records. Neither
file runs anything. The default host `plan`/`collect` accept only host points. Legacy guest
rendering is explicitly UNSUPPORTED. `tests/m2_guest_capture.py` prepares a
pinned guest entry reusing the same controller/supervisor; no separate lifecycle. It owns no scenario,
approval, serial login, identity provisioning, service or recovery lifecycle.
Existing serial and restoration mechanisms remain authoritative.

| Point | Verified interface / planned filter | Coverage and limitations |
| --- | --- | --- |
| `fw-l2` | Host `vnet1`, FW MAC `52:54:00:e5:cc:f1`, `tcp and (port 502 or port 4840 or port 8000)` | One FW L2 side: routed SCADA/XLe→L1 and viewer L3→L2. Choose this point for routed counts; do not add FW ingress and egress as separate edges. Guest equivalent is FW `enp8s0`, observed in handoff, requiring separate permission. |
| `plc-l1` | Host `vnet4`, PLC MAC `52:54:00:7c:c7:79`, `tcp and host 10.10.1.10 and port 502` | PLC↔VFD/scanner and plant→PLC, bypassing FW; also routed PLC traffic. Use only for the same-L1 subset when merging with fw-l2. Does not see plant→local VFD. Guest equivalent PLC `enp7s0`. |
| `xle-local` | XLe `lo`, `tcp and host 127.0.0.1 and port 8089` | XLe↔ASX HTTP. Invisible on host bridges/FW. Route verified; capture privilege unverified. |
| `scada-local` | SCADA `lo`, `tcp and host 10.10.2.10 and port 4840` | HMI↔OPC UA despite non-loopback destination IP. Route verified; connectedness is distinct from PLC freshness. |
| `drives-local` | PROPOSED `lo`, `tcp and port 502`; command generation BLOCKED | Historical aliases/settings were read. Fresh route/interface/tool/context evidence is still required; `verified_in_handoff:false` is unchanged. Cannot claim complete coverage without this point or a documented limitation. |

Expected roles: PLC initiates device Modbus; plant initiates PLC telemetry and
local VFD RPM reads; SCADA UA poller and dedicated XLe each initiate PLC Modbus;
XLe initiates ASX HTTP; HMI initiates local UA. These are source-derived roles,
not observed edges. Drives source IP must be observed. `.21–.26` are six VFD
aliases on one VM; services sharing an address require socket/log correlation.
No new viewer or administrative traffic is generated to populate the map.
PLC listeners 8080 and 8443 are observed admin surfaces, not process traffic;
the paper did not claim a web-admin port. Their omission from narrow process
filters must be explicit in the final analysis.

Bounds are explicit CLI options: default 10 s, 5,000 packets and 8 MiB **per
point**, SIGINT then one three-second escalation grace; maximum options remain
600 s, 200,000 packets, 64 MiB and five-second grace. tcpdump uses `-p -s 0`
(no promiscuous request, full 262144-byte snaplen), no ring overwrite,
LC_ALL=C and inherited hard RLIMIT_FSIZE. GNU timeout is a secondary bound.
An independent supervisor in this same collector owns the capture subprocesses,
uses Linux child-subreaper adoption, and tracks PID/PGID/session/start ticks,
boot ID and UID. Signals target validated pidfds, never a process name or a
blind process-group number. A dead timeout wrapper does not remove descendant
cleanup; children that create a new session are still owned descendants.
The supervisor reads only children of proven, pinned parent incarnations,
rechecks the kernel child edge after pinning each candidate, and rejects stale
edges or reused parents. Recorded ownership edges retain parent PID/start/UID/
boot identity; later descendant selection does not rebuild numeric-PPID ancestry.

The supervisor survives catchable controller termination and controller
SIGKILL. Pipe EOF requests cleanup; each point's absolute deadline also applies
independently of controller progress. Descendants receive SIGINT, then SIGKILL
by the configured grace, followed by at most one second of reap checks under
normal kernel scheduling. Catchable controller INT/TERM/HUP enters bounded
cleanup and records failure. SIGKILL cannot let the dead controller write a
terminal receipt: its surviving supervisor writes **its own** cleanup record;
the missing controller terminal/seal remains INCOMPLETE. The supervisor is
isolated from the controller's process group. Simultaneous destruction of the
independent supervisor and controller, host shutdown/kernel failure,
uninterruptible tasks or hostile privilege/namespace changes are outside this
userspace guarantee and require external containment. No such stronger
containment is configured here.
Required `/proc` task/children visibility is checked before capture launch.
Disappearing tasks are normal races; access denials are retained as discovery
errors. If visibility is later lost, previously proven targets are still
signalled through their retained pidfds, but unproven targets are not signalled.
Unknown descendant coverage or unreadable final child lists mean cleanup
INCOMPLETE, even if all known targets exited. The bound cannot guarantee cleanup
of undiscoverable descendants; this is a supported-visibility requirement.

Cleanup runs before analysis/hashing, even when artifact creation or supervisor
receipt writes fail. Original, discovery, cleanup, artifact, analysis and sealing errors
are independent. Terminal `failure.json` is attempted when collection.json
cannot be finalized. Hashing continues over readable artifacts; sealing errors
produce an incomplete sealing receipt. Exhausted/unwritable storage may prevent
both receipts and hashes; CLI streams and later read-only inspection are then
failure evidence, not PASS. No missing final evidence is reconstructed.

Reserve `points * max_bytes` plus logs/source metadata; RLIMIT_FSIZE is per
output file, not an aggregate disk quota. Packet/file caps ending the requested
window early are INCOMPLETE. `collect` refuses an existing evidence directory.
Capture runs in an operator-approved context; no elevation occurs in Python.

Run inert planning from the isolated worktree:

```sh
cd /home/kevin/vm/sorter-milestone2-baseline
/usr/bin/python3 tests/baseline_capture.py plan --point fw-l2 --point plc-l1 --output /home/kevin/vm/sorter-evidence/m2-capture-smoke-REVIEWED --scenario-id capture-smoke-REVIEWED --seconds 10 --packets 5000 --max-bytes 8388608
/usr/bin/python3 tests/baseline_capture.py plan --point xle-local --point scada-local --point drives-local --output /home/kevin/m2-local-REVIEWED --scenario-id capture-smoke-REVIEWED --seconds 10 --packets 5000 --max-bytes 8388608
```

The second legacy `baseline_capture.py plan` command deliberately reports
`UNSUPPORTED_LEGACY_GUEST_PATH`; it cannot clear local coverage. Use the
IMPLEMENT-001 sealed packet's inert `COMMANDS.sh` and NOT-REVIEWED contract
samples for the replacement below. Build and plan are local-only preparation.
Do not execute collection/export until independent review and separate approval.

`tests/m2_guest_capture.py build` creates a deterministic self-contained archive
of this collector, the existing analyzer and the small guest entry. Its fixed
bootstrap checks archive owner/mode/SHA **before executing archive code** and
holds its inode; the entry validates exact module/config/contract/source pins,
hostname, UID/EUID and paths. No host Git tree or third-party package is assumed
on the guest. The explicit guest mode retains the guest location; host `/proc`
is never used as guest orphan proof. Existing supervisor ownership/child-edge/
pidfd/deadline mechanisms are reused without new process discovery or signaling.

Existing contexts only: XLe/drives root; SCADA kevin (UID1000). No sudo, capability,
group or serial-profile change is proposed. The archive must already be owned
by root or the invoking UID and not group/other writable (root refuses a
kevin-owned archive). The destination's existing parent must be owned by the
invoking UID, without group/other write or symlink components; the final output
must not exist. Capture retains that same identity via `-Z` and `-p`, avoiding
root-created0700 directories handed to a dropped user. Example parent choices
are existing `/root` on XLe/drives and `/home/kevin` on SCADA; actual rights,
provisioned paths, archive ownership, installed tools and storage must be reviewed.
No directory, archive or contract is provisioned by this workflow.

`remote-collect` uses only canonical `deployment_preflight.guest_read`: one
640-second intended command within a fixed660-second shared transport/liveness
budget. Capture remains bounded by600 seconds,200000 packets,64MiB per point,
then the existing configured grace/reap allowances; no transport loss extends
that bound. The supervisor survives controller loss. Missing controller terminal,
canonical intended/separate-liveness receipt or cleanup evidence is INCOMPLETE,
even when later file retrieval succeeds. Collection has no scenario controls.

Read-only `export` uses the same authenticated transport, fixed inventory and
<=65536-byte base64 chunks, each intended20/shared30 seconds, fixed aggregate
export budget default600/max3600 seconds and80MiB default/256MiB maximum total.
These are retrieval limits, not capture extensions or guaranteed serial throughput.
At64MiB a slow serial channel may need a smaller separately planned capture;
expiry never resumes/retries automatically. Only the contract's flat capture
artifact allowlist may be read, with per-file caps. Componentwise no-symlink
opens, directory/file incarnation pins, exact offsets/lengths, per-chunk hashes,
whole-file hashes and repeated source inventory detect incomplete/changing bytes.
No guest archive write, chmod/chown or deployment-style `serial_copy.py` occurs.
Original bytes/modes remain untouched; readable host copies are independently
sealed. Transport traces preserve completion/prompt/nonces, UTC/monotonic windows and
separate liveness for every read. A collection-launch receipt cannot attest later
inventories/chunks. Missing export receipts can never be replaced by file hashes.

`verify-export` is host-only: verify both seals, exact contract/runtime/config/
collection bindings and canonical receipt pairs for the collection **and every
export read**. Export acceptance replays the exact inventory/chunk/final-inventory
order and coverage, string-only argv, guest/user/interpreter, distinct nonces and
liveness, requested result and fixed per-read/overall budgets. Both inventory
postflights must bind each complete expected identity to canonical supervision
records; liveness uses canonical PID/start/UID/boot, not supplied expected fields.
Malformed/duplicate/missing identities, receipts or results fail closed. This fixes
malformed-evidence acceptance, not an observed guest orphan. Use the recorded
explicit outer host interpreter for offline verification; a different interpreter
is not silently accepted. Host UTC regressions leave evidence INCOMPLETE. A standalone host `baseline_capture.py
verify` refuses guest packets without that proof. Failure-only retrieval is
explicit `--failure-evidence`; it preserves original failures separately from
transfer/storage/sealing errors and can **never** produce collection acceptance.
Current guest provisioning, rights, route/tools, clocks, log/export visibility
and actual packet-socket open remain unobserved; fixture PASS clears none of them.

## Evidence and offline analysis

Each host packet retains configuration.json, actual host source identity.json,
collection.json, supervision plan/terminal record, supervisor streams and
per-point PCAP/stdout/stderr, then SHA256SUMS and sealing.json. Schema 2 requires
both terminal records, matching token and independent orphan checks. sealing.json
is excluded from its own manifest and records that manifest digest; missing or
incomplete sealing blocks verification. Original terminal files stay immutable
after successful sealing; failures may leave explicitly partial evidence. Identity
includes Git HEAD/status/diff digest, actual component and host tool hashes,
selected manifest digest/count and paper/handoff references. It states guest
equality UNVERIFIED. Collection records scenario ID, point/interface/filter,
argv, UTC and monotonic bounds, configured limits, per-process exit status,
stop reason, raw statistics and PCAP digest. The selected manifest and commit
must match the explicit config. Host edits are distinguished by source hashes
and dirty status; no hash is a claim of guest deployment.

Use the single fail-fast smoke workflow below for collection and dependent
verification/analysis. Do not run a detached acceptance command list after
collection failure. `inspect` is the read-only path for failure evidence.

Derived analysis stays beside the sealed raw directory. Hash it separately
with the raw SHA256SUMS digest; never rewrite raw receipts or fill missing
checkpoints. A partial PCAP can be inspected with `--allow-incomplete`; it
still exits 2 and declares `file_complete:false`. Header/format/size-bound
errors fail analysis. Guest PCAPs use the same analyzer after verified retrieval.
No parser result alone overrides collection completeness.

The extended canonical analyzer reports IPv4 TCP endpoints, SYN initiators,
Modbus request/response FCs, unit IDs and zero-based address spaces/ranges,
request counts/rates over the observed packet span, per-profile interval
min/mean/max, exception codes and TCP RST/FIN counts. Coalesced complete ADUs
are supported. Actual retransmissions are **UNAVAILABLE**: identical tuple,
sequence and payload are counted as ambiguous candidates and excluded from
polling counts. Missing SYN means UNKNOWN initiator; missing reply does not
mean failure. A reverse RST after an observed SYN without SYN-ACK is recorded
as a TCP connection failure, independently of a complete Modbus exception
response (FC high bit plus exception byte). Retry noise with an unavailable
core is not a Modbus exception or healthy-idle baseline.

TCP/IP reassembly, IPv6/application decoding, OPC UA fields, HTTP fields,
request/response latency and timeouts are unavailable. Segments, unknown
functions, malformed requests and snaplen gaps are explicit. Classic Ethernet
PCAP, including VLAN and micro/nanosecond timestamps, is supported; pcapng and
Linux cooked `any` captures are rejected. Poll profiles combine connections
with identical source/destination/unit/FC/range; they are observed request
spacing, not a scheduler guarantee or automatically a single process's poll
period. Zero/one sample cannot establish a poll interval. Cross-point counts
are separate; deduplicate by coverage/tuple/TCP sequence/timestamp uncertainty
before building a combined map. Offload/duplicate visibility limits must be
recorded; do not derive an error rate from missing reconstructed ADUs.

For each observed edge record: capture point/hash, full source/destination
socket tuple, SYN evidence or UNKNOWN, first/last time with clock domain,
protocol/FC/range, count/rate/interval, parser gaps and attribution confidence.
Use application evidence to attribute it: bounded `journalctl --no-pager -n 500
-o short-iso-precise --since <start> --until <end> -u <unit>` for
sorter-xle, sorter-asx, sorter-plant, opcua-server, sorter-hmi and relevant
vfd/scanner instances; paired `ss -ntp`/PID and unit identity snapshots.
Read each separately through canonical guest_read and retain command status;
permission-denied logs remain missing evidence. Existing raw application log
files may need their deployed unit's bounded log path instead of journald.
Preserve hashes, event semantics and original monotonic order. Join only
fields the logs actually contain: epoch/nonce/lane/token/serial, scanner/event
sequence, XLe command/outcome and journal identity; ASX request IDs only when
actually logged. Where several processes share an IP and there is no matching
socket/PID plus unit-log evidence, application attribution remains AMBIGUOUS.
A PCAP address or port alone never proves a process. HMI `connected:true`
requires independent poll freshness and source quality evidence.

Collection failure policy: retain nonzero exits, permission errors, missing
stats, truncated files, packet/file/time-limit termination and transport
failures. Do not retry implicitly or label an empty readable PCAP a useful
baseline. Kernel drops must be reported and zero for host COMPLETE; an absent
interface-drop counter remains UNAVAILABLE, never zero. Statistics must agree
with offline packet counts. Interrupted/forced-kill/early exits or missing
receipts/artifacts are INCOMPLETE. Stop only this collector's processes; no
scenario abort, service restart or PLC reset is hidden in cleanup. Operators
use the chosen scenario's canonical abort/recovery procedure separately, then
record its outcome independently. No new scenario follows an unresolved
restoration gate. SHA256SUMS provides integrity, not signer authentication.

## Operating results and restoration

Copy `tests/milestone2_results.json` into the external scenario evidence packet.
All six records start **NOT RUN**, including every independent result axis.
Add the reserved canonical scenario ID, authorization reference/UTC, selected
source/manifest/deployed hashes, observed PLC identity, ASX plan digest,
seed/epoch/nonce, UTC window/clock bounds, expected assertions, actual outcome,
terminal evidence paths/hashes and explicit errors. Capture-only smoke is not
one of these six operating results. Functional failure stays FAIL even if
restoration passes; missing assertions are INCONCLUSIVE, never inferred PASS.
An attempted but unavailable check is INCONCLUSIVE/FAIL with reason. Unexecuted
scenarios remain NOT RUN. NOT REQUIRED needs an evidence-backed explanation
(e.g. no reset performed in a passive idle capture), not a substitute PASS.

| Scenario | Existing audited path and expected functional evidence | Remaining gate |
| --- | --- | --- |
| Healthy idle | `run_accumulation_attempt.py --dedicated-case healthy_idle`:10-second stopped observation, typed pre/post, current core and UA/HMI. | Complete accepted startup; separate capture authorization. |
| Normal sorting | Same host runner, case `normal_sorting`, dedicated worker mode `normal`: three identity-bound confirmations to trailers2/5/8 and matched journal. | Reviewed inline host worker, explicit package/control/reset scope. |
| Recirculation | Case `recirculation`: selected scanner seed1 yields GOOD barcode1001 absent from the unchanged ASX plan; require one recirculation, trailer0, reason1 and matching journal. | New focused review/authorization. No ASX plan changes or local XLe/ASX launch. |
| Backpressure | Case `backpressure`: three packages held by outbound drives885–887 off, validated DRIVE_STOPPED/DRIVE_OFF dwell/quality for3 seconds, then resume and three confirmations. | This is outbound-drive backpressure, **not** the legacy lane_hold/merge_hold fixture or a claim about its FIFO/spacing tests. No service transition. |
| Drive stop/resume | Case `drive_stop_resume`: lane1 only; coil882 off after slot activation, validated hold/RPM0 then positive RPM and one trailer2 confirmation. | Exact drive/package/reset scope, typed restoration. |
| Documented recovery | Case `documented_recovery`: the canonical remote-XLe reset/handshake, fresh epoch/nonce/ACK/heartbeat and journal proof. | Separately approved stopped no-package reset. No induced service failure/restart. |

Keep functional_outcome, capture_completeness, service_restoration,
plc_recovery, typed_postflight, orphan_checks and plan_restoration independent.
Use the existing `accumulation_state_snapshot.py` capture/compare contract,
`run_accumulation_attempt.py`, applicable migration/postflight reader and
canonical recovery, approval and identity mechanisms. No second lifecycle is
introduced. Fresh required services, no duplicate XLe/ASX, canonical plant,
original plan bytes, master off, empty slots, cleared faults/counters,
settled VFDs and restored operator controls must be independently established.
Store the typed report, service/process/VM records and restoration-gate receipt;
do not relabel a runner's finally as successful PLC recovery or orphan proof.
Typed preflight/self-comparison happens before the chosen scenario; typed
postflight and no-orphan checks follow even a failed scenario. An intentionally
changed baseline requires a documented new selection rather than editing
expected values to make the comparison pass.

Milestone 2 is complete only with an approved, freshly verified configuration,
usable captures covering the required network and local paths, an observed
communication map with attribution and
uncertainty, executed normal/recirculation/expected-recovery results plus the
idle/backpressure/drive controls or explicit limitations, independent
restoration results, and written baseline analysis tied to hashes. Preparation
or historical PASS counts do not satisfy these criteria.

## Retained historical diagnostic-smoke procedure (closed; do not repeat)

**REVIEW ONLY / NOT EXECUTED.** This supersedes the earlier independent command
list. Runtime startup, privileged FW/XLe policy reads, local guest captures and
this host smoke remain separate actions. The stopped-sorter smoke requires no
core start, package, login, new traffic or recovery. It is diagnostic only.

Before approval, reconfirm these host attachments against the table above:

```sh
virsh -c qemu:///system domiflist fw
virsh -c qemu:///system domiflist plc
ip -br link show dev vnet1
ip -br link show dev vnet4
```

Minimum capture privilege is Linux **CAP_NET_RAW for tcpdump's packet socket**.
The host command now uses `-p` (no promiscuous-mode request); it does not need
CAP_NET_ADMIN to reconfigure an interface. The Python controller/supervisor,
GNU timeout, hashing and offline analysis require ordinary same-user process
and file access, plus usable `/proc`, pidfds and child-subreaper support. Use an
operator-reviewed existing capture execution context with those rights and
signal access to its own children. Current permission denial is unresolved;
this task installs no capabilities, grants no sudo rule, changes no ownership,
groups or modes, and performs no capture-open test. Do not grant blanket
passwordless sudo to Python or arbitrary scripts. No privilege is required
merely to run `plan`, `inspect`, `verify` or offline summaries on readable files.

After separate approval, replace REVIEWED consistently with one unused ID and
run the following **as one Bash workflow** in the approved capture context:

```sh
(
set -euo pipefail
cd /home/kevin/vm/sorter-milestone2-baseline
EVIDENCE=/home/kevin/vm/sorter-evidence/m2-capture-smoke-REVIEWED
failure_evidence() {
    stage_rc=$?
    trap - EXIT
    if [ "$stage_rc" -ne 0 ]; then
        set +e
        /usr/bin/python3 tests/baseline_capture.py inspect "$EVIDENCE" --wait 18 > "${EVIDENCE}-failure.json" 2> "${EVIDENCE}-inspect.stderr"
        inspect_rc=$?
        printf '%s\n' "$stage_rc" > "${EVIDENCE}-stage-exit.txt"
        evidence_hash_rc=0
        hash_output_rc=0
        {
            for file in "$EVIDENCE/SHA256SUMS" "$EVIDENCE/sealing.json" "${EVIDENCE}-collector.stdout" "${EVIDENCE}-collector.stderr" "${EVIDENCE}-verify.json" "${EVIDENCE}-verify.stderr" "${EVIDENCE}-fw-l2-analysis.json" "${EVIDENCE}-fw-l2-analysis.stderr" "${EVIDENCE}-plc-l1-analysis.json" "${EVIDENCE}-plc-l1-analysis.stderr" "${EVIDENCE}-failure.json" "${EVIDENCE}-inspect.stderr" "${EVIDENCE}-stage-exit.txt"; do
                if [ -f "$file" ]; then sha256sum -- "$file" || evidence_hash_rc=1; fi
            done
        } > "${EVIDENCE}-failure-SHA256SUMS" || hash_output_rc=$?
        if [ "$hash_output_rc" -ne 0 ]; then evidence_hash_rc=$hash_output_rc; fi
        printf 'FAILED stage rc=%s; inspection rc=%s; failure-evidence hashing rc=%s; hash-output/sealing rc=%s; no acceptance\n' "$stage_rc" "$inspect_rc" "$evidence_hash_rc" "$hash_output_rc" >&2
    fi
    exit "$stage_rc"
}
trap failure_evidence EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
/usr/bin/python3 tests/baseline_capture.py collect --point fw-l2 --point plc-l1 --output "$EVIDENCE" --scenario-id capture-smoke-REVIEWED --seconds 10 --packets 5000 --max-bytes 8388608 --grace 3 --clock-note 'Host taps share one clock; guest offsets UNMEASURED; diagnostic only' > "${EVIDENCE}-collector.stdout" 2> "${EVIDENCE}-collector.stderr"
/usr/bin/python3 tests/baseline_capture.py verify "$EVIDENCE" > "${EVIDENCE}-verify.json" 2> "${EVIDENCE}-verify.stderr"
/usr/bin/python3 tests/pcap_modbus_summary.py "$EVIDENCE/fw-l2.pcap" --max-bytes 8388608 --max-packets 5000 > "${EVIDENCE}-fw-l2-analysis.json" 2> "${EVIDENCE}-fw-l2-analysis.stderr"
/usr/bin/python3 tests/pcap_modbus_summary.py "$EVIDENCE/plc-l1.pcap" --max-bytes 8388608 --max-packets 5000 > "${EVIDENCE}-plc-l1-analysis.json" 2> "${EVIDENCE}-plc-l1-analysis.stderr"
sha256sum "$EVIDENCE/SHA256SUMS" "$EVIDENCE/sealing.json" "${EVIDENCE}-collector.stdout" "${EVIDENCE}-collector.stderr" "${EVIDENCE}-verify.json" "${EVIDENCE}-verify.stderr" "${EVIDENCE}-fw-l2-analysis.json" "${EVIDENCE}-fw-l2-analysis.stderr" "${EVIDENCE}-plc-l1-analysis.json" "${EVIDENCE}-plc-l1-analysis.stderr" > "${EVIDENCE}-derived-SHA256SUMS"
)
```

`set -e` stops analysis after failed collect/verify and stops acceptance after
any later error. The EXIT trap preserves the failing stage exit code, waits
boundedly for independent cleanup evidence, and records failure evidence. An
inspection exit 2 is expected for incomplete evidence; it is not converted to
acceptance. Hash-output redirection is checked outside the redirected group;
failure to open that file sets both the hashing and the separate output/sealing
status to nonzero. Per-file hashing failures set the hashing status independently.
The original failed-stage exit status is retained even if failure evidence cannot
be sealed; the diagnostic on stderr must be retained by the operator. The
collector performs cleanup before returning a failure; after controller
SIGKILL its supervisor observes pipe EOF and performs its own cleanup. Killing
the shell with SIGKILL also prevents its trap: run the same read-only `inspect`
command later, with output outside the raw directory. Inspection never
finalizes or seals a dead controller's missing receipt. Do not retry collect
in an existing directory or proceed to acceptance after any failed stage.

Only `collect` starts capture-related processes and opens packet sockets;
its two PCAP limits are 10 seconds/5,000 packets/8 MiB each, 16 MiB combined
plus logs/metadata. Same-user signalling of owned processes is required for
cleanup. These commands do not require root Python. Hashing, verification,
analysis and the failure trap only read evidence and write host artifacts.
No guest command or policy/runtime operation is part of this workflow.

Acceptance for this diagnostic smoke requires all of the following:

- The reviewed tap/MAC mappings still match; source/manifest and actual host
  tool identities are retained, including the undeployed host analyzer delta.
- Collector exit 0; schema-2 terminal controller **and** supervisor records
  with matching collection token, all selected points accounted for, no errors,
  cleanup PASS and no remaining capture identities. Wrapper exit alone is not
  cleanup proof. `verify` independently checks recorded process identities.
- Process visibility preflight succeeds and no discovery error is recorded.
  Ownership requires a kernel child edge under a pinned, live parent incarnation,
  confirmed again after pinning the child; numeric PPID alone is insufficient.
  Permission failures or ambiguous ancestry leave cleanup INCOMPLETE even when
  every previously validated target has exited. Unrelated `/proc` rows are not
  scanned. A later visibility failure cannot bypass signalling those already
  proven targets, but cannot authorize signalling an unproven process.
- Each wrapper exits 0 with `stop_reason=duration_limit` and the full requested
  process window, no early packet/file cap, forced kill, interruption or loss.
- Each PCAP parses within limits, captured statistic equals offline count,
  received statistic exists, kernel drops are zero, and any reported interface
  drops are zero. Missing interface-drop counters stay UNAVAILABLE.
- All required artifacts and complete `sealing.json`/SHA256SUMS agree; offline
  verify and both summaries succeed, then derived evidence hashes succeed.
- Empty complete captures may satisfy mechanics only; useful communication
  claims need observed packets. `baseline_result` is NOT_EVALUATED, functional
  outcome remains NOT RUN, and no operating scenario is relabelled PASS.

This smoke misses XLe/SCADA loopback and drives-local flows, excludes SSH/NTP/
admin ports, and sees duplicated routed PLC traffic on both taps. Those remain
explicit coverage limits; no Milestone 2 operating baseline is cleared here.


## Historical host verification record (October22 preparation)

On this preparation branch, Python 3.11 with the repository's pinned
`requirements-dev.txt` in `/tmp/sorter-m2-host-test-venv` ran the full host
suite: **250 tests, PASS (historical preparation run)**. The 19 new collector/parser tests use synthetic
PCAP bytes, temporary files and inert Python children only; none opens a
capture socket or contacts a guest. Cases cover bounded time/file/packet
handling, independent GNU-timeout behavior (not a real controller-loss test),
partial multi-point launch, permission failures,
drops, truncated/missing/tampered evidence and TCP refusal versus Modbus
exceptions. Existing functional host tests remain fixture evidence.

Python compileall over devices/services/scada/tests/deploy and whitespace
checks passed. Generated guest capture text passed `bash -n` without execution;
planning created no collection directory. The selected manifest was unchanged
and clean measurement source-only preflight passed (43 components). Coding-tree
source-only preflight correctly failed for the undeployed host analyzer; the
guest hash was not changed to conceal that difference. The original dirty checkout's three
modified tracked files and untracked research work remain preserved.
These results do not attest the deployed services or clear any live blocker.

## Historical collector repair verification (October22)

The prerequisite review identified four defects: wrapper polling skipped living
children, default SIGTERM bypassed finalization, artifact failures could escape
sealing, and independent shell commands continued after a failed collection.
The repaired collector uses the independent subreaper/pidfd path above, handles
catchable controller termination, always attempts process cleanup before
metadata/analysis/hashing, preserves independent error categories, and requires
schema-2 terminal/supervision/sealing evidence. The single reviewed Bash block
has an explicit failure-evidence path and stops dependent stages.

Focused command (Python 3.11, pinned host test environment):

```sh
/tmp/sorter-m2-host-test-venv/bin/python -m unittest discover -s tests -p 'test_baseline_capture*.py' -v
/tmp/sorter-m2-host-test-venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
/tmp/sorter-m2-host-test-venv/bin/python -m compileall -q devices services scada tests deploy
```

The focused collector suite passes **24 tests** on both Python 3.11.16 and
`/usr/bin/python3` 3.14.4, including **12 subprocess
integration tests** in `tests/test_baseline_capture_processes.py`; the complete
host suite passes **262 tests**. Fixtures run real fake-capture child trees:
wrapper SIGKILL, controller SIGTERM, controller SIGKILL, combined losses,
setsid descendants, successful-wrapper exit with a surviving child, supervisor
artifact failure, controller artifact failure while children run, analysis and
hash failures, unavailable terminal/seal writes, independent original/cleanup
errors, and fail-fast shell sequencing. Assertions check actual process
identities are gone, adopted children are reaped, and an unrelated bystander is
left alive. Integration fixtures enforce a 2.5-second completion ceiling for
the .8-second collection/.15-second grace/one-second reap bounds plus scheduling
margin. No fixture
executes tcpdump, opens a capture socket, uses sudo or contacts a guest.
Syntax, generated workflow `bash -n`, tracked `git diff --check` and whitespace
checks for every new untracked file pass. Manifest/source selection, existing
host analyzer and scenario-result template are preserved. These are host-only
fixture results, not smoke approval or runtime recovery evidence.

## IMPLEMENT-001 host verification (October27)

The new packet `m2-implement-001-20261027T194508647598Z` contains exact before/
after source, hashes, focused diffs and sequential test receipts. **102 tests
PASS** on Python3.11.16:17 dedicated scenario,4 selected-sink log,11 packaged
guest lifecycle/export,12 collector,12 ownership,15 process and31 attempt
regressions. All new capture processes are inert synthetic writers, not tcpdump;
all service/log inputs are fixtures. Earlier RED/compatibility failures are
retained separately. This does not repeat or supersede the historical live host
capture mechanics PASS and does not establish guest readiness. Guest capture/
export templates remain NOT REVIEWED, NOT PROVISIONED and NOT EXECUTED.

## CORRECTION-002 focused evidence correction

New packet `m2-correction-002-20261027T210953493908Z` preserves IMPLEMENT-001
unchanged and addresses only A-01/B1/B2/B3. Idle remains accepted/closed. File
cursors include a <=256-byte boundary anchor hash, validated before a later
append read. Copytruncate/regrowth that changes that anchor is rejected; missing
anchors, rotation and short reads fail closed. Returned file status is
BOUNDED_EVENT_BYTES, not a promise of complete event history. An unchanged bounded
anchor cannot prove arbitrary earlier bytes were never changed/recreated;
whole-file history remains UNPROVEN even when attribution is SUPPORTED_WITH_LIMITS.
Journal events must match the current service InvocationID as well as unit/PID/
boot; missing or contradictory invocation records do not count. Raw evidence is
preserved. All guest support/access/rights and live scenarios remain NOT RUN.

### B1 acceptance clarification supplement

The frozen CORRECTION-002 delivery remains historical. Its B1 supplement enforces
exact cursor/suffix/witness ranges, lengths, types and digests, with raw bounded
previous/next witness bytes. All reads share the same opened regular file and
before/after stability fence; stdout/stderr inode and process-incarnation checks
remain. Missing legacy or malformed cursors cannot silently become an initial
snapshot. Initial observation is explicit; a supplied prior cursor is required
for every file sink in a later observation.

The original supplement's cursor policy used BOUNDED_OBSERVATION or INCOMPLETE.
ACCESS-LOG-005 below separates fresh initial positioning from event completeness.
Unstable or exhausted actual intervals remain non-supporting and their cursors
cannot clear that failed interval by a later append. There is no automatic
rebaseline. Pre/post assessments stay independent; a separately labeled new
baseline cannot retroactively repair a failed interval or historical evidence.

The accepted claim is bounded prior-content match and consistent returned bytes,
not lossless scenario/history continuity or exclusive line authorship. Rewrites
preserving/restoring the witness can escape endpoint detection. Whole-file history
remains UNPROVEN; any consumer requiring a stronger lossless guarantee must remain
INCOMPLETE. A functional/control PASS does not clear that separate evidence axis.

## ACCESS-LOG-005: explicit initial file position, separate interval evidence

Saved SCADA access evidence at
`m2-scada-service-log-access-20261027T225814461189Z` proves readable selected
files/current FD bindings, but both initial tails exceed8192 bytes. Their original
TRUNCATED/INCOMPLETE records remain unchanged. The saved offline replay proved
that their cursors could not start a later read; a quiet initial file had the
same positioning defect. This is a host preparation correction, not a live retry.

The canonical pre-scenario support call now explicitly requests
`initial_baseline=True` with no prior cursor/interval. A stable same-file read
with the existing stdout/stderr/incarnation/witness proofs may return a
BASELINE_VALID cursor even when the retained historical tail is TRUNCATED or
NO_EVENTS. That observation is positioning only: initial file
`application_attribution` remains INCOMPLETE, history remains UNPROVEN, and no
pre-baseline byte is promoted into scenario event evidence. Request and payload
must both name the initial phase; saved legacy observations cannot be relabeled.
`interval_start_readiness` records valid file positions plus the unchanged
journal requirements independently of historical file-event completeness.
Journal NO_EVENTS or mixed/unbound invocation evidence remains INCOMPLETE.

The next canonical read requires the complete prior assessment/cursors and its
guest UTC. It validates the same opened file, old <=256-byte witness, exact
suffix/next-witness ranges/digests/types and stable incarnation. A quiet complete
interval returns NO_EVENTS/INCOMPLETE attribution but keeps a BOUNDED_OBSERVATION
position; a later window cannot rewrite the earlier missing event evidence.
Bounded appended event bytes may support that actual interval only. An interval
exceeding8192 bytes, unstable file, rotation, missing/malformed cursor or changed
witness remains INCOMPLETE/refused; explicit initial mode with prior state is
rejected. No log clearing, rotation, truncation, restart or silent rebaseline.

Scenario attribution combines initial positioning/journal readiness with actual
post-interval event evidence, retaining every failed axis. Functional results,
capture completeness and whole-history claims remain independent. Defaults keep
the legacy initial observation policy; callers must request the new initial
phase explicitly. Accepted unrelated fixes, deadlines, source/manifest, capture
supervisor/export and all guest files remain unchanged. All6 scenarios NOT RUN;
new command bytes require Hermes review before any separately authorized use.

## CAPTURE-OVERLAP-006: detached complete controller, open proof and terminal join

Host-only preparation; NOT REVIEWED, NOT PROVISIONED and NOT EXECUTED on guests.
The separate ACCESS-LOG-005 packet remains immutable and its log runner/test
bytes stay frozen. This section is a precise subsequent documentation delta.
No selected source, manifest, service, privilege or configuration change occurs.

The direct `remote-collect` path remains synchronous and supported. For per-case
guest-local overlap, `m2_guest_capture.py remote-start` now launches exactly one
complete existing guest controller with stdin/stdout/stderr DEVNULL, close_fds
and a new session. The original controller retains its original private-pipe
supervisor and live child/pidfd cleanup authority. A one-use sibling `.launch`
reservation prevents replay of the same output path even after failure. This is
a correlation token/reservation, not operator approval. No second serial attach,
listener, login, service, scenario orchestrator or replacement supervisor exists.

The guest launch returns only after an at-most5-second actual-open checkpoint
for every selected point. Evidence combines the exact tcpdump listening message,
an already-proven owned process with matching capture argv/executable, its open
packet-socket FD and the Linux packet table's selected interface index/UID/type/
protocol/running row, rechecked incarnation and live owned pidfd. Permission,
discovery, format or ambiguity errors refuse readiness. Wrapper PID, Popen,
IN_PROGRESS, a PCAP header or a listening line alone is insufficient. The filter
is bound through exact executed argv and listening initialization; independent
kernel BPF-bytecode attestation is not claimed.

`capture-open.json` binds exact contract, runtime/modules, configuration, selected
source identity digest, scenario, collection token, UID/boot, controller and
supervisor incarnations, points/filters/argv, capture identities and original
per-point monotonic starts/deadlines. It is OPEN evidence, never COMPLETE.
`detached-start.json` records the matching controller/start binding. Both are
deliberately allowlisted, required and included in the original collection seal.
The external one-use reservation directory is not a PID/signaling authority.

The launch command plus its fresh separate canonical liveness must finish and
detach **before** scenario reads reuse that same console. Missing/failed liveness
leaves start INCOMPLETE and refuses scenario/join acceptance; the existing guest
controller/supervisor still enforce the original bound. No automatic relaunch.
If launch fails while holding the validated direct-child pidfd, only that live
owner may request TERM and, if its bounded wait fails, KILL of that same pinned
controller. The independent supervisor retains descendant ownership/cleanup;
the launcher does not signal discovered PIDs from files or claim descendant exit
from the controller's exit alone. Failed/missing cleanup evidence is INCOMPLETE.

`remote-join` is a separate bounded canonical read of original terminal evidence.
It never launches, adopts or signals persisted identities. Its guest deadline is
the latest original point deadline plus2*grace+REAP_SECONDS+5 seconds; late join
is refused and never renews capture time. It requires terminal collection/seal,
independent supervisor cleanup/postflight and controller-incarnation exit.
Controller SIGKILL can leave its own terminal receipt missing; the surviving
supervisor alone records its cleanup and acceptance remains INCOMPLETE.

Detached transport has distinct exact launch and join commands/receipt pairs;
neither is forged into the existing direct-collection receipt. Runtime export
and offline verification validate both pairs, original token/checkpoint/terminal
bindings and clocks, every existing B3 inventory/chunk/final-inventory pair,
both independent export namespace postflights and the join postflight. Full open
identities must equal canonical supervision, with the selected wrapper's actual
ownership chain. Caller-supplied expected PID/UID/boot cannot redefine ownership.
Retained records remain observations, never later signaling authority.

### Finite per-case schedule; no silent deadline extension

Startup stays separate: verification270/watchdog600/abort30, initialization280
and handshake95 are unchanged. A270-second startup plus540-second case cannot
fit one600-second capture. Begin per-case collection only after accepted startup
and every separately reviewed access/storage/provisioning prerequisite.

Capture remains <=600s/200000 packets/64MiB per point/grace3. Use one host clock
to record each launch request start and pair completion. `overlap_schedule`
performs pure admission arithmetic: every start must already be authenticated
OPEN, same host boot, liveness complete, and `now+540+5 <= earliest request start
+ capture seconds`. Request start precedes actual open, so this is conservative.
For600-second windows the case must start by earliest request+55 seconds with
the explicit5-second margin. Plan sequential launch pairs to finish within that
window (for example <=20s each, <=15s admission/case-launch remainder); their
individual canonical30-second limits do not promise both fit. Refuse a slow
schedule; do not reset a lease, shorten assertions or automatically retry.
Host tap collection remains separate and must cover the same approved case.

After the case, join original collections sequentially, within their original
join bounds. Capture caps/drops, process cleanup, functional outcome, log support,
coverage and export remain independent axes. A cap or early exit after OPEN
still makes collection INCOMPLETE. Join/export throughput is unmeasured; the
maximum file cap is not a promise that full-size verification/retrieval fits.

The new sealed006 packet contains its exact deterministic runtime archive/pins.
The older accepted bundle stays immutable; it does not implement this protocol.
All new contract/runtime provisioning is a separate reviewed write and NOT RUN.
Root XLe/drives and kevin SCADA contexts stay unchanged. Root SCADA is unsupported
by the canonical contract/transport even if an operator can supply a console.
Actual UID1000 packet rights remain unestablished. Drives f_bavail0 blocks guest
writes/provisioning/capture/scenarios until a non-destructive operator-reviewed
capacity resolution; root reservation does not waive that gate. Drives-local
remains DISABLED. Current console reservation is human coordination, rechecked
near action, not permanent exclusivity or a privilege grant.

Inert later command shapes (fresh exact reviewed pins/IDs/paths required):

```sh
# REVIEW ONLY. No authority, provisioning, capture or scenario action here.
cd /home/kevin/vm/sorter-milestone2-baseline
/usr/bin/python3 -B tests/m2_guest_capture.py remote-start --contract /home/kevin/vm/sorter-evidence/xle-capture-FRESH-REVIEWED.json --contract-sha256 REVIEWED_NEW_CONTRACT_SHA256 --evidence /home/kevin/vm/sorter-evidence/xle-start-FRESH-REVIEWED
# Complete its separate liveness and detach, then the same separately approved SCADA start.
# Pure overlap_schedule admission, then ONE separately approved existing case; no auto-run.
/usr/bin/python3 -B tests/m2_guest_capture.py remote-join --contract /home/kevin/vm/sorter-evidence/xle-capture-FRESH-REVIEWED.json --contract-sha256 REVIEWED_NEW_CONTRACT_SHA256 --start /home/kevin/vm/sorter-evidence/xle-start-FRESH-REVIEWED/capture-start.json --evidence /home/kevin/vm/sorter-evidence/xle-join-FRESH-REVIEWED
# On COMPLETE only: existing export and verify-export, same explicit outer interpreter.
# Failed start/join permits only separately scoped failure-evidence retrieval, never acceptance.
```

All host subprocess fixtures use inert fake packet openers/private files, no
AF_PACKET/real tcpdump/guest traffic. Production socket-probe parsing is tested
with deterministic proc fixtures; this is not installed-guest capture-open PASS.
All six operating scenarios remain NOT RUN. Prior failures/spent requests and
separate successful abort/recovery results are unchanged. Hermes review required.
