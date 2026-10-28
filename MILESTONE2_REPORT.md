# Milestone 2 baseline report — evidence-backed draft

October27,2026. **INCOMPLETE. All six operating scenarios NOT RUN.**
This draft separates historical diagnostic evidence and partial startup proofs
from the healthy operating baseline still required. It is not a completion claim.

## Selected configuration and relation to Milestone 1

Milestone1 is the October14,2026 paper referencing1e271ed. Its SCADA-local
XLe/ASX placement, PLC identity24113,33-entry manifest and36 tested network
probes (30allow/6deny) remain the original dated claims. The paper is unchanged.

The selected Milestone2 measurement source is
`f587cd6d6e5d425834d1ae35b43fd1fa1a8e69e9`, clean separate measurement worktree,
manifest9a6b3b9c84fc1552fa73f7eb9835d30b4ab781fa52604a55dea23bd1362665ab,
43 declared components. It uses dedicated XLe/ASX on10.10.2.20, three PLC
slots and identity24115. SCADA10.10.2.10 hosts UA/HMI; selected scanners are
10.10.1.27–29, correcting the inventory handoff's31–33 reference. The current
48-entry flow matrix is an expectation (33allow/15deny), not fresh firewall
measurement. Broad CaseA exposure is a documented selected configuration,
not a hardening change performed for this report.

The latest startup's retained evidence verified43/43 destination hashes and
live24115, but that is an October27 observation, not permanent equality. The
manifest does not cover scanner environment files, actual aliases/interfaces
or loaded firewall policy. The retained October27 refresh separately read the
nine scanner/VFD environment files, aliases21–29 and worker ownership then;
October22 also recorded local routes to aliases21–29. Current route/interface/
tools/capture-open and export remain pending.
Host tooling edits are undeployed and separately
hashed; no guest update or manifest change is implied.

## Available evidence and limits

The October22 final diagnostic smoke independently passed host capture lifecycle,
integrity, parsing and cleanup. fw-l2 observed400 packets (200SYN/200RST+ACK),
plc-l1 observed440 (220SYN/220RST+ACK), no TCP payload; all are connection
attempt/refusal evidence. No Modbus request, successful response or application
exception was measured. Routed traffic is duplicated across taps, so those
counts cannot be added as unique communication. Guest-local traffic and clock
uncertainty were not measured. The accepted mechanics result remains PASS.

Attempt `m2-startup-20261027T162715081114Z` passed owned launch/listener,
bounded INITIALIZING policy and canonical no-package handshake (epoch48/nonce2),
typed comparison245 records/zero differences and43-entry live preflight. It
exhausted verification180 during the second XLe sample. Its status remains
INCOMPLETE/SPENT; conditional abort is independently PASS/CORE_STOPPED with
complete core-log export. One XLe sample is not stability; UA/HMI and final
acceptance were NOT RUN. It is not a healthy-idle operating result.

IMPLEMENT-001 is preparation only: healthy-idle observations must span10 seconds
between successful samples; guest capture/export reuses the existing collector
under pinned explicit contexts; selected SCADA/scanner/VFD file sinks replace
the incorrect all-journal assumption. Synthetic lifecycle/export/log fixtures
are not operating evidence. The guest runtime is unreviewed, undeployed and
unexecuted. Application attribution is a separate result; empty/truncated or
unavailable logs cannot establish it.

## Communication map

| Expected selected edge (source supported) | Capture/attribution evidence still required |
| --- | --- |
| PLC→VFD21–26 and scanner27–29, TCP502, same L1 | plc-l1 payloads, initiating tuples, unit/socket identity; drives bindings fresh |
| plant→PLC502, same L1 | plc-l1; distinguish plant worker using process/socket records and logs |
| SCADA UA poller and dedicated XLe→PLC502, routed L2/L1 | fw-l2; service/PID/log attribution, do not infer from IP alone |
| SCADA UA poller→VFD aliases21–26 TCP502, routed L2/L1 | fw-l2; distinguish from PLC device polling at the L1 tap |
| XLe→ASX127.0.0.1:8089 | XLe local capture plus ASX/XLe identities/journal |
| HMI→UA10.10.2.10:4840, local SCADA | SCADA local capture plus current PID/socket and UA freshness |
| plant→local VFD aliases502 | drives route/interface/ownership proof and local capture |

This is an **expected map**, not the required final observed application map.
Only SYN/refusal edges from the diagnostic packet are currently observed.
Observed endpoints/initiators, Modbus function/address profiles, request rates,
polling intervals, exceptions, retransmissions and resets under healthy operation:
**NOT RUN**. Unsupported or ambiguous parser measurements must be UNAVAILABLE.

## Operating results

| Scenario | Functional | Capture | Service restoration | PLC recovery | Typed postflight | Orphan checks |
| --- | --- | --- | --- | --- | --- | --- |
| Healthy idle | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Normal sorting | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Recirculation | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Outbound-drive backpressure | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Drive stop/resume | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Documented stopped recovery | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |

Each future row needs selected/observed identity, approval and scenario ID,
time/uncertainty, source/PCAP/service/journal hashes, expected and actual result,
independent restoration and incomplete-evidence notes. Plan restoration and application attribution also
remain NOT RUN. Fixture PASS is not an operating result.

## Written baseline analysis to complete

The diagnostic refused connections are consistent with the separately observed
stopped core; they do not characterize normal polling/function usage. The latest
partial handshake supports feasibility of the selected journal-backed protocol
under the stopped policy, not an accepted operational baseline. The transport
timeline explains the failed verification without inventing an XLe restart.

After measurement, add per-scenario endpoint/function/range/rate/interval tables,
protocol/TCP error rates with denominators and capture-loss limits, attribution
confidence, expected-vs-observed edges, operating/restoration outcomes, local-path
coverage, clock ambiguity and selected-vs-paper configuration differences. Until
then these analyses and final completion determination remain **NOT RUN**.

Execution/checklist: [MILESTONE2_COMPLETION.md](MILESTONE2_COMPLETION.md).
Historical source evidence: `m2-smoke-final-verification-20261022T022904Z`,
`m2-startup-20261027T162715081114Z` and its preserved authorization packet under
`/home/kevin/vm/sorter-evidence/`. Original packets and spent authorities are
not modified by this draft. The historical rejected birth-record field remains
UNRESOLVED; separate completed existing-core recovery remains PASS/CORE_STOPPED.
