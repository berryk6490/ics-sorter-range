# Milestone 2 healthy-idle network baseline

2026-10-28. Full result: **all 24 of 24 planned segments** analyzed, from a
readable copy of the sealed evidence (`/home/kevin/vm/m2-analysis-copy`, not
the sealed originals). Nothing in the range, the VMs, git, or the sealed
evidence was touched to produce this.

If you're new to this project: this is a small lab network that simulates a
parcel-sorting PLC (programmable logic controller) and the devices around
it, talking **Modbus TCP** (a simple industrial request/response protocol,
port 502) and **OPC UA** (an industrial protocol used by the plant's
SCADA/HMI layer, port 4840). "Healthy idle" means the sorter software is up
and the PLC is running; the operator reports `master` off and no packages
moving, but those state claims are not independently verified here. This
capture is meant to answer "what does normal background chatter look like
with nothing happening," so that a later attack-detection system can tell
that apart from something wrong.

## 1. Method

Two host-side packet taps, 150 seconds at a time, 24 segments back-to-back
(= 1 hour), **PLC run epoch 49**:

| Tap name | Host interface | Which VM/NIC it's attached to | What it's filtered to |
| --- | --- | --- | --- |
| `fw-l2` | `vnet3` | the firewall VM's `ics-l2` interface, MAC `52:54:00:e5:cc:f1` | TCP ports 502 (Modbus), 4840 (OPC UA), 8000 (HMI) |
| `plc-l1` | `vnet5` | the PLC VM's `ics-l1` interface, MAC `52:54:00:7c:c7:79` | TCP port 502 traffic to/from the PLC's address, `10.10.1.10` |

I re-checked both tap/MAC pairings myself against the live `virsh dumpxml`
for the firewall and PLC VMs — `vnet3` is still exactly the firewall's
`ics-l2` interface and `vnet5` is still exactly the PLC's `ics-l1` interface.
That confirms the task's "corrected from the stale config" note: this
mapping is currently correct.

**Why 24 segments instead of one continuous hour:** the capture tool
(`tests/baseline_capture.py`) caps a single run at 200,000 packets per tap.
The `plc-l1` tap alone runs at roughly 1,040 packets/second at idle (see
below), so a single capture would hit that cap in well under 200 seconds.
150-second segments stay safely under the cap with room to spare.

**Range state during capture:** the operator reports healthy idle (PLC run
epoch 49, `master` off, no packages), a full live preflight, manual core
startup with `start_modbus(502)`, and a PASS no-package handshake. The
operator supplied `/home/kevin/vm/sorter-evidence/m2-healthy-idle-1h-20261028-operator-startup.md`
as startup/preflight evidence, but it was NOT FOUND at that exact path as of
the coordinator's check. I have not read or verified that record. The capture
and packet analysis cannot independently prove `master` off, no packages, or
the startup/handshake outcome.

**Clock:** every segment's `clock_note` reads "host clock" — a single host
wall clock, not something synchronized across the VMs. There's no measured
clock offset between the firewall, PLC, or host in this evidence.

**Integrity check (this task's first ask):** I computed SHA-256 directly
over all 48 copied `.pcap` files and compared each one against (a) its
segment's sealed `SHA256SUMS` and (b) the hash the collector itself recorded
in `segNN.result.json` at capture time.

> **All 48 match on both counts. No mismatches.** I also ran the project's
> own `tests/baseline_capture.py verify()` against all 24 segments (possible
> this time because this copy is owned by me, unlike the sealed originals —
> see the previous version of this report for why that mattered) — all 24
> return `status: COMPLETE` with no errors.

## 2. Baseline table per conduit

Numbers below are averaged across all 24 segments (3,603.1 seconds of
capture time per tap); spread across segments was negligible for every
figure here (see §3). Request and response counts/rates below are separate
aggregates parsed from Modbus payloads, not matched exchanges. The analyzer
does not match
transaction IDs to pair requests with responses or measure application
latency (`tests/pcap_modbus_summary.py:223`).

**`fw-l2` tap — 784,549 parsed Modbus requests over the hour, 217.7 requests/sec:**

| Talker pair (crosses the firewall) | Function codes | Req. rate | Mean polling interval |
| --- | --- | --- | --- |
| SCADA-host source (`10.10.2.10`) → PLC (`10.10.1.10`) | FC1 (read 42 coils, `880–921`, one call) + 16 separate FC3 reads covering most of the PLC's status/photoeye/fault/journal register blocks | 8.62 req/s | 115.99 ms (115.85–116.29 ms across the hour) |
| SCADA-host source (`10.10.2.10`) → each of 6 VFDs (`10.10.1.21–26`) directly | FC3 read holding registers `0–8` (all 9) | 8.62 req/s each | 115.99 ms, same cadence as its PLC polling |

No FC16 (write) traffic crosses this tap at all — consistent with PLC→VFD
and PLC→scanner commands being local-only (never routed through the
firewall).

**`plc-l1` tap — 1,463,461 parsed requests / 1,463,460 parsed responses
over the hour, 406.2 requests/sec** (see the aggregate difference in §3):

| Talker pair | Function codes | Req. rate | Mean polling interval |
| --- | --- | --- | --- |
| PLC → each of 6 VFDs (`10.10.1.21–26`) | FC16 write holding registers `0–2` (command word, speed reference, belt load) + FC3 read holding registers `0–8` (all 9: status, frequency, feedback, fault, current, thermal) | 9.50 req/s each | 105.23 ms (105.15–105.39 ms) |
| PLC → each of 3 scanners (`10.10.1.27–29`) | FC16 write holding registers `0–3` (trigger sequence, serial, seed, no-read rate) + FC3 read holding registers `0–10` (all 11) | 9.50 req/s each | 105.23 ms, same cadence as the VFD polling |
| Plant-profile flow from `10.10.1.21` → PLC (process unverified) | FC16 write register `590` + FC1 read coils `918–921` + FC3 read registers `509`, `558–559` | 9.92 req/s | 100.82 ms (100.75–100.90 ms) |
| SCADA-host source (`10.10.2.10`) → PLC | same profile as the `fw-l2` row above (also visible PLC-side) | 8.62 req/s | 115.99 ms |
| XLe-host source (`10.10.2.20`) → PLC (process unverified) | FC1 read coils `914–915` only (the two XLe-mode flags) | 19.82 req/s | 50.45 ms (50.43–50.47 ms) — the tightest poll in this whole capture |

A note on the plant-profile row: the observed source IP `10.10.1.21` is also a
VFD address. The function and address profile is consistent with the
documented `plant.py` poller, but an IP and register profile do not establish
a process identity. Current service logs and socket ownership would be
needed to attribute this traffic to `plant.py` rather than another process
on the `drives` VM.

**Sizes** (measured directly from the ADU bytes, not inferred): FC1 and FC3
*requests* are a fixed 12 bytes no matter what they're asking for — the
request only carries an address and a quantity, never the data itself. FC16
*requests* run 15–21 bytes depending on how many registers are being
written (15 B for the plant's single-register write, 19 B for a VFD's
3-register write, 21 B for a scanner's 4-register write). FC16 *responses*
are a fixed 12-byte echo. FC1 *responses* run 10–15 bytes (10 B for the
2-coil XLe read, up to 15 B for SCADA's 42-coil read). FC3 *responses* run
11–129 bytes (11 B for a single register, up to 129 B for SCADA's two
60-register block reads).

Within these filtered captures, the parser reported no Modbus exceptions,
TCP resets or FINs, duplicate/retransmitted payloads, or non-IPv4/non-TCP
frames in the 24 segments. These zero counts do not describe traffic excluded
by the BPFs, unobserved local conduits, or the short gaps between segments.

## 3. Stability across the hour

**No drift.** Segment 1 vs. segment 24: `fw-l2`'s packet rate differs by
**-0.01%**, `plc-l1`'s by **-0.08%** — essentially identical. Per-talker
polling intervals, averaged within each segment, vary by at most a few
hundredths of a millisecond across all 24 segments (e.g., the VFD/scanner
group's mean interval ranges 105.15–105.39 ms over the whole hour — a 0.23%
band). This traffic does not speed up, slow down, or drift over an hour of
idle.

**One thing that is NOT perfectly flat:** segments 13, 16, and 17 show
`plc-l1` running at ~1034.2–1034.4 pkt/s, versus ~1040–1041 pkt/s for every
other segment (seg14–15 are back to normal in between seg13 and seg16–17).
That's a real, small (~0.6%), non-monotonic dip affecting 3 of 24 segments,
not a gradual trend — I don't have an explanation for it from this data
(no reported kernel drops, errors, or exceptions in those segments; interface
drop counts are unavailable), so I'm
flagging it rather than smoothing it over. It's small enough not to change
any of the numbers above meaningfully, but it's the one thing in this
dataset I'd want someone to look at if they're building alerting thresholds
on top of it.

**One unexplained aggregate difference:** across the hour, `plc-l1` recorded
343,310 FC16 requests and 343,309 FC16 responses, with the one-count
difference in segment 21. The analyzer does not pair request and response
transaction IDs; it cannot identify an unmatched write. A capture boundary
is possible, but unproven. Zero reported kernel drops in that segment do not
explain the difference or prove no packet was missed; `interface_dropped` is
null, so the interface drop count is unavailable.

## 4. What normal idle looks like, and what could be mistaken for an attack

**What's present:** dense Modbus polling in all 24 segments at both taps —
four distinct observed request cadences (PLC↔drives at ~105 ms, a
plant-profile flow at ~101 ms, SCADA at ~116 ms, XLe at ~50 ms), each using
a small, repeating set of addresses. Segment gaps limit claims of literal
continuity, and process attribution for the plant-profile flow needs logs. "Idle" is not quiet — it's a steady hum of status checks.

**What's absent from these filtered captures:** no observed Modbus request
profile resembling bulk transfer or a change in scanner-trigger structure.
The parsed Modbus requests and responses use the recurring sizes and
addresses described above. I want to be precise about
the limits of that claim, though: this analysis reads Modbus *structure*
(function code, address range, size, timing), not register *values*.
I can't literally see whether a scanner's trigger-sequence register held
"0" (idle) the entire hour — I'm inferring "nothing happening" from the
complete absence of anything behaving differently, not from reading the
numbers inside the packets.

At `fw-l2`, the BPF includes ports 4840 and 8000; neither appeared in
that filtered vantage during the segments. The `plc-l1` BPF excludes both
ports. **Both BPFs exclude port 8089**, and XLe↔ASX loopback traffic cannot
reach either host tap. These observations say nothing about whether the
SCADA-local OPC UA/HMI or XLe-local conduits carried traffic.

**What could be mistaken for an attack by a naive detector:**
- The sheer regularity. A detector that treats "too-perfectly-periodic" as
  suspicious (a common heuristic for beaconing/C2 traffic) would flag *all
  four* of these polling loops — they're clockwork by design, every one of
  them within fractions of a millisecond of its own mean, for a full hour.
- The SCADA-host source polling the six VFDs **directly**, as well as the PLC —
  someone who only expects "SCADA talks to the PLC, the PLC talks to the
  VFDs" would read this as SCADA doing something it shouldn't. It's normal
  here, but it's also exactly the kind of flow a scope-of-access reviewer
  should be told about explicitly rather than discover later.
- The one-count FC16 request/response aggregate difference in segment 21
  (§3). It warrants a completeness note; this parser cannot identify a
  failed write or confirm a capture-boundary explanation.
- The small multi-segment dip in `plc-l1`'s rate (§3) could look like
  throttling, a brief block, or a partial outage to a rate-based alarm, with
  no other corroborating signal (zero reported kernel drops, resets or
  exceptions; interface drop count unavailable) to explain it either way.

## 5. Known gaps — deferred to Milestone 3

Three planned local conduits were **not captured** in this baseline. The
`fw-l2` filter includes 4840 and 8000, with zero packets at that vantage;
that does not measure SCADA loopback. The `plc-l1` filter excludes those
ports, and both BPFs exclude 8089. XLe loopback is outside both taps.

- **`scada-local`** (the OPC UA server ↔ HMI subscription, on SCADA's own
  loopback) — not run.
- **`xle-local`** (the XLe↔ASX link on `127.0.0.1:8089`) — not run.
- **`drives-local`** (local Modbus on the `drives` VM) — blocked and
  not attempted in this capture; a later operator-reported offline export/
  truncate does not itself verify current capture access or prior state.

All three move to Milestone 3.

## 6. Evidence index

All paths under `/home/kevin/vm/m2-analysis-copy/` (the readable working
copy; the sealed originals are under `/home/kevin/vm/sorter-evidence/m2-healthy-idle-1h-20261028/`
and were not touched). SHA-256 values below are truncated to 12 hex
characters for readability — the full 64-character digest for every file is
in that segment's own `SHA256SUMS`, and I independently confirmed every one
of the 48 matches both the sealed manifest and the collector's own recorded
hash (§1).

| Segment | Window (UTC) | `fw-l2` packets / SHA-256 (12 hex) | `plc-l1` packets / SHA-256 (12 hex) |
| --- | --- | --- | --- |
| seg01 | 17:25:55.409–17:28:25.536 | 78,744 / `a75c8e7cdd5d` | 156,213 / `b615199d252d` |
| seg02 | 17:28:26.875–17:30:57.011 | 78,201 / `1169bf36efac` | 156,247 / `072721c1cbd5` |
| seg03 | 17:30:58.384–17:33:28.508 | 78,686 / `bd20d9c19560` | 156,189 / `558b98c3c1ee` |
| seg04 | 17:33:29.841–17:35:59.965 | 78,630 / `43e4ac332cde` | 156,165 / `0dd6a2b172ee` |
| seg05 | 17:36:01.275–17:38:31.412 | 78,030 / see SHA256SUMS | 156,026 / see SHA256SUMS |
| seg06 | 17:38:32.777–17:41:02.906 | 78,687 / see SHA256SUMS | 156,192 / see SHA256SUMS |
| seg07 | 17:41:04.248–17:43:34.375 | 78,636 / see SHA256SUMS | 156,172 / see SHA256SUMS |
| seg08 | 17:43:35.730–17:46:05.850 | 78,634 / see SHA256SUMS | 156,158 / see SHA256SUMS |
| seg09 | 17:46:07.170–17:48:37.293 | 78,691 / see SHA256SUMS | 156,208 / see SHA256SUMS |
| seg10 | 17:48:38.634–17:51:08.766 | 78,744 / see SHA256SUMS | 156,342 / see SHA256SUMS |
| seg11 | 17:51:10.139–17:53:40.263 | 78,852 / see SHA256SUMS | 156,355 / see SHA256SUMS |
| seg12 | 17:53:41.571–17:56:11.693 | 78,797 / see SHA256SUMS | 156,317 / see SHA256SUMS |
| seg13 | 17:56:13.010–17:58:43.148 | 78,795 / see SHA256SUMS | 155,270 / see SHA256SUMS (rate dip, §3) |
| seg14 | 17:58:44.477–18:01:14.598 | 78,795 / see SHA256SUMS | 156,315 / see SHA256SUMS |
| seg15 | 18:01:15.889–18:03:46.012 | 78,797 / see SHA256SUMS | 156,321 / see SHA256SUMS |
| seg16 | 18:03:47.320–18:06:17.456 | 78,305 / see SHA256SUMS | 155,298 / see SHA256SUMS (rate dip, §3) |
| seg17 | 18:06:18.791–18:08:48.928 | 78,852 / see SHA256SUMS | 155,300 / see SHA256SUMS (rate dip, §3) |
| seg18 | 18:08:50.257–18:11:20.386 | 78,796 / see SHA256SUMS | 156,301 / see SHA256SUMS |
| seg19 | 18:11:21.675–18:13:51.795 | 78,845 / see SHA256SUMS | 156,346 / see SHA256SUMS |
| seg20 | 18:13:53.146–18:16:23.287 | 78,841 / see SHA256SUMS | 156,282 / see SHA256SUMS |
| seg21 | 18:16:24.603–18:18:54.724 | 78,792 / see SHA256SUMS | 156,247 / see SHA256SUMS (FC16 aggregate difference, §3) |
| seg22 | 18:18:56.030–18:21:26.149 | 78,795 / see SHA256SUMS | 156,245 / see SHA256SUMS |
| seg23 | 18:21:27.504–18:23:57.645 | 78,796 / see SHA256SUMS | 156,221 / see SHA256SUMS |
| seg24 | 18:23:58.975–18:26:29.115 | 78,741 / see SHA256SUMS | 156,100 / see SHA256SUMS |
| **Total** | **17:25:55.409–18:26:29.115 (1h00m33.7s)** | **1,888,482 packets** | **3,746,830 packets** |

## Summary

I analyzed all 24 of 24 planned healthy-idle segments (one full hour,
17:25:55–18:26:29 UTC, PLC run epoch 49). All 48 pcaps' SHA-256 hashes match
their sealed manifests exactly — no integrity problems. The hour is
remarkably clean within the captured filters: zero reported kernel drops,
zero Modbus exceptions and zero TCP resets, with no substantial drift in
observed rate or timing. Interface drop counts are unavailable (`null`).
Four distinct request cadences dominate the observed Modbus traffic
(PLC↔VFDs/scanners every ~105 ms, plant-profile flow every ~101 ms, SCADA
every ~116 ms, XLe every ~50 ms), plus SCADA polling
the VFDs directly across the firewall — a real, documented edge, not a
surprise, but one worth flagging to anyone reviewing access scope. The two
things I'd flag loudest: a small, unexplained ~0.6% rate dip in three
non-adjacent segments (13, 16, 17) with no reported kernel drops or errors,
and the limits of the filters: 4840/8000 had zero packets only at the
`fw-l2` vantage, while 8089 was excluded by both BPFs. Local OPC UA/HMI
and XLe↔ASX traffic remain unmeasured and move to Milestone 3. Report:
`/home/kevin/vm/sorter-milestone2-baseline/MILESTONE2_BASELINE_HEALTHY_IDLE.md`.
