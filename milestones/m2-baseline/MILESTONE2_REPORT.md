# Milestone 2 revised-scope baseline report

October 28, 2026. The operator revised Milestone 2 closure to the healthy-idle
host-tap baseline and written analysis: 24 bounded 150-second segments on
`fw-l2` and `plc-l1`, with all 24 verified COMPLETE. The other five operating
scenarios and guest-local conduits move to Milestone 3. This is acceptance of
the capture and analysis deliverable, not a functional, postflight, or
restoration PASS for any scenario.

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

The historical October27 startup's retained evidence verified43/43 destination
hashes and live24115, but that is an October27 observation, not permanent
equality. The manifest does not cover scanner environment files, actual aliases/interfaces
or loaded firewall policy. The retained October27 refresh separately read the
nine scanner/VFD environment files, aliases21–29 and worker ownership then;
October22 also recorded local routes to aliases21–29. The October28 host-tap
pairings in the healthy-idle report are observations at that time, not
permanent route/interface or guest-local capture proof. Host tooling edits
are undeployed and separately hashed; no guest update or manifest change is
implied.

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

The reviewed host preparation requires healthy-idle observations to span
10 seconds between successful samples. Guest capture/export reuses the
existing collector under pinned contexts and selected SCADA/scanner/VFD
file sinks. Synthetic lifecycle/export/log fixtures are not operating
evidence. Guest-local capture remains unprovisioned and unexecuted.
Application attribution is a separate result; empty, truncated or
unavailable logs cannot establish it.

The revised-scope evidence is the 24-segment host capture under
`/home/kevin/vm/sorter-evidence/m2-healthy-idle-1h-20261028/`, its readable
copy under `/home/kevin/vm/m2-analysis-copy/`, and the analysis in
[MILESTONE2_BASELINE_HEALTHY_IDLE.md](MILESTONE2_BASELINE_HEALTHY_IDLE.md).
Each copied PCAP's SHA-256 was checked against its segment manifest and
readable collector result: 48/48 digest matches. The canonical verifier
returned COMPLETE for 24/24 copied segments. The original root-owned PCAP
payloads were not directly reread for this analysis; these checks establish
the readable copy's agreement with the retained digests and result records.
The report records the per-segment UTC windows, packet counts, filter limits,
Modbus function/address profiles and request cadences. Segment gaps, lack of
inter-VM clock-offset measurements, and process attribution limits remain.

The operator supplied
`/home/kevin/vm/sorter-evidence/m2-healthy-idle-1h-20261028-operator-startup.md`
as startup/preflight evidence. It was **NOT FOUND at that exact path** during
the coordinator's check, so it has not been read or independently verified.
The operator's assertion of startup, `master` off, no packages and a passing
handshake is distinct from the captured network observations. The packet
analysis alone does not prove those state claims.

## Communication map

| Selected edge | Revised-scope observation and attribution limit |
| --- | --- |
| PLC→VFD21–26 and scanner27–29, TCP502, same L1 | `plc-l1` has parsed FC16/FC3 request profiles and rates; current per-process/socket bindings are not proved by packets |
| Plant-profile source→PLC502, same L1 | `plc-l1` shows source `10.10.1.21` and FC16/FC1/FC3 profile; IP/address profile alone cannot identify `plant.py`; service logs/socket ownership remain needed |
| SCADA and dedicated XLe→PLC502, routed L2/L1 | Host taps show source endpoints and request profiles; application attribution requires current process/log evidence |
| SCADA→VFD aliases21–26 TCP502, routed L2/L1 | `fw-l2` shows direct FC3 reads; do not add duplicate `plc-l1` traffic as independent communication |
| XLe→ASX127.0.0.1:8089 | Unmeasured: both host-tap BPFs exclude 8089; XLe loopback is outside their vantage |
| HMI→UA10.10.2.10:4840, local SCADA | Unmeasured locally; zero 4840 at `fw-l2` alone says nothing about SCADA loopback |
| Plant→local VFD aliases502 | Drives-local capture blocked/not run; no local traffic inference |

This is a host-tap communication map. It does not establish process identities
where several services share an address or measure local loops. The analyzer
reports separate aggregate Modbus request/response counts; it does not match
transaction IDs or measure request-to-response latency. The segment-21 FC16
one-count aggregate difference is unexplained, possibly at a capture
boundary. It does not prove loss or rule it out. No TCP resets or Modbus
exceptions were reported within the filtered captures; excluded traffic and
short inter-segment gaps remain outside that claim. OPC UA/HMI payload
freshness, guest-local paths and application latency are UNAVAILABLE.

## Operating results

| Scenario | M2 scope | Functional | Capture | Service restoration | PLC recovery | Typed postflight | Orphan checks |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Healthy idle | HOST CAPTURE + ANALYSIS ACCEPTED | NOT RUN | NOT RUN (scenario axis) | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Normal sorting | MOVED TO M3 / NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Recirculation | MOVED TO M3 / NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Outbound-drive backpressure | MOVED TO M3 / NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Drive stop/resume | MOVED TO M3 / NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Documented stopped recovery | MOVED TO M3 / NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |

The previously documented six-case criteria are superseded for M2 closure
only; they were not retroactively executed. The standalone healthy-idle
capture/analysis acceptance does not fill the scenario's functional,
postflight, restoration, orphan, or plan axes. The results JSON retains
those independent axes as NOT RUN and a separate host-capture result.

## Written baseline analysis and remaining limits

The diagnostic refused connections are consistent with the separately observed
stopped core; they do not characterize normal polling/function usage. The
historical October27 partial handshake supports feasibility of the selected
journal-backed protocol
under the stopped policy, not an accepted operational baseline. The transport
timeline explains the failed verification without inventing an XLe restart.

The measured endpoint/function/range/rate/interval tables and segment-level
notes are in the healthy-idle analysis above. The five deferred cases have
no operating or restoration results. Neither the startup assertion nor the
host capture supplies missing typed-state, package, or service-restoration
evidence. Local OPC UA/HMI and XLe↔ASX paths remain for M3.

StageA017's approved one-use backup attempt failed INCOMPLETE; its authority
is spent and no backup was accepted. The operator closed StageA017 and
Read018. Neither historical action becomes PASS. The operator reports resolving the drives
disk-full condition by offline export/truncate and supplied
`/home/kevin/vm/sorter-evidence/m2-drives-vfd-logs-export-20261028T164317Z`
with six gzipped logs and matching SHA256SUMS. Matching export hashes alone
do not verify that truncation occurred or prove current drives capacity.

The original planning checklist remains historical to the six-case plan;
the operator's revised closure scope governs this report.
Historical source evidence: `m2-smoke-final-verification-20261022T022904Z`,
`m2-startup-20261027T162715081114Z` and its preserved authorization packet under
`/home/kevin/vm/sorter-evidence/`. Original packets and spent authorities are
not modified by this draft. The historical rejected birth-record field remains
UNRESOLVED; separate completed existing-core recovery remains PASS/CORE_STOPPED.
