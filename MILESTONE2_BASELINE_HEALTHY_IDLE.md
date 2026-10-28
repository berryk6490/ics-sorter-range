# Milestone 2 healthy-idle network baseline

2026-10-28. Read-only analysis of an in-progress capture. **4 of 24 planned
150-second segments were complete and sealed at analysis time**
(`seg01`–`seg04`, 2026-10-28T17:25:55–17:35:59 UTC, 600.5 s total); the
background collector was still writing `seg05` onward. Nothing in the range,
the VMs, git, or the capture process was touched to produce this — see the
**Known gap** below for what that constraint cost the analysis.

## 1. Method

Two host-side `tcpdump` taps, run by a root supervisor
(`tests/baseline_capture.py`), one 150-second segment at a time, 24
segments total (= 1 hour), scenario ID `healthy-idle`, **PLC run epoch 49**:

| Point | Interface | Attachment | BPF filter |
| --- | --- | --- | --- |
| `fw-l2` | `vnet3` | `fw` MAC `52:54:00:e5:cc:f1` on `ics-l2`/`virbr-l2` | `tcp and (port 502 or port 4840 or port 8000)` |
| `plc-l1` | `vnet5` | `plc` MAC `52:54:00:7c:c7:79` on `ics-l1`/`virbr-l1` | `tcp and host 10.10.1.10 and port 502` |

I independently re-checked both tap/MAC pairings against live `virsh dumpxml`
for `fw` and `plc` just now — `vnet3` is still exactly `fw`'s `ics-l2`
interface and `vnet5` is still exactly `plc`'s `ics-l1` interface. That
confirms the "corrected from the stale config" note in the task: the
tap-to-interface mapping is currently accurate, not stale.

Each segment is capped at 200,000 packets / 64 MiB per point and sealed with
a per-segment `SHA256SUMS` + `sealing.json` immediately after capture
(`tests/baseline_capture.py seal`/`verify`). `clock_note` on every segment
reads **"host clock"** — a single host wall clock, not a cross-VM
synchronized source; there is no measured offset between `fw`'s, `plc`'s, or
the host's clocks in this evidence, consistent with the clock-uncertainty
caveat already on record in `MILESTONE2_BASELINE.md`. Segments ran
back-to-back with small (~1.3 s) gaps between them (collector restart
overhead, within its configured 3 s grace) — there is a short blind window
between segments, not a single continuous 600.5 s packet stream.

## Known gap: Modbus-level analysis was not possible from this account

`tests/baseline_capture.py verify()` and `tests/pcap_modbus_summary.py` both
need to open the actual `.pcap` bytes inside each `segNN/` directory. Every
`segNN/` directory is **root-owned, mode 0700** (so is the earlier partial
run's directory, see §4); I'm running as `kevin`, and this host has no
passwordless `sudo` (`sudo -n true` → "interactive authentication is
required"). I confirmed this empirically — calling `baseline_capture.verify()`
on `seg01` as `kevin` fails immediately:
```
PermissionError: [Errno 13] Permission denied: '.../seg01/SHA256SUMS'
```
So none of the following could be produced, and are **not** in this report:
Modbus function-code breakdown, request/response (transaction) rate,
polling interval between SCADA/XLe→PLC and PLC→each VFD/scanner, mean packet
size, or an independent re-verification of the sealed hashes. This is a real
finding, not a formality: the capture tool's own `verify` path — the thing
meant to let someone *other than* the collector confirm the evidence — is
unusable by the account doing this review. Whoever ran this collection ran
it as root; an unprivileged reviewer can't close the loop on it without
either sudo or a deliberate world-readable export step, neither of which
exists yet for this batch.

What I *can* and did report below comes entirely from the world-readable
`segNN.result.json` sibling file the sealer drops next to each 0700
directory — packet counts, elapsed time, drop counters, and the `pcap_sha256`
the collector itself computed at capture time (not independently
re-verified by me).

## 2. Per-conduit baseline table

Only raw per-tap packet rates are available (see gap above) — **not**
Modbus-transaction rate, function codes, polling intervals, or packet
sizes.

| Segment | `fw-l2` packets | `fw-l2` rate (pkt/s) | `plc-l1` packets | `plc-l1` rate (pkt/s) |
| --- | ---: | ---: | ---: | ---: |
| seg01 | 78,744 | 524.51 | 156,213 | 1040.56 |
| seg02 | 78,201 | 520.87 | 156,247 | 1040.73 |
| seg03 | 78,686 | 524.14 | 156,189 | 1040.43 |
| seg04 | 78,630 | 523.77 | 156,165 | 1040.26 |
| **Total/avg** | **314,261** | **523.32** | **624,814** | **1040.49** |

Zero kernel drops and zero/null interface drops on every segment, both taps
— all four captures are complete, not sampled.

Two things worth noting that don't require packet-level parsing:
- The rate is extremely stable: `fw-l2` varies by <1% and `plc-l1` by <0.05%
  across all four segments — a clean steady-state idle signature, not a
  bursty one.
- `plc-l1` runs almost exactly **2.0×** the `fw-l2` rate (1040.49 / 523.32 =
  1.988). That's consistent with the tap topology on record: `plc-l1` sees
  both the routed SCADA/XLe→PLC traffic that also crosses `fw-l2`, *plus*
  local PLC↔VFD/scanner traffic that never reaches the firewall. I can't
  confirm the split with function-code data, but the near-exact 2× ratio is
  itself a reasonable topology sanity check.

## 3. Flows present/absent at idle, and what could be mistaken for an attack

With no sorting running and `master` off, there is still substantial,
continuous TCP/502 (and presumably 4840/8000, unseparated — see gap)
traffic at both taps throughout all four segments — healthy idle is not
quiet. I can't say which Modbus functions or addresses make up that traffic,
so I can't contrast "present during idle" against "present only during
sorting" at the protocol level; `normal_sorting` is itself still NOT RUN per
`MILESTONE2_REPORT.md`, so there's no comparison baseline to use even if I
could parse these pcaps.

What I can flag from counts alone, for whoever builds a detector against
this baseline later:
- A naive rate- or volume-based detector will need this ~523/~1040 pkt/s
  steady idle floor as its "normal," or every healthy-idle window will look
  like a scan.
- This project's own Oct 22 capture-mechanics smoke test (cited in
  `MILESTONE2_REPORT.md`) recorded the opposite-looking signature — hundreds
  of SYN+RST/ACK pairs with zero payload — when the PLC core was stopped. A
  detector tuned only on *this* healthy-idle baseline could easily flag that
  refusal pattern as anomalous (correctly) but for the wrong reason if it
  isn't also given that refusal-traffic fixture; conversely, a detector that
  only knows the refusal pattern would have no way to recognize *this*
  traffic as normal. Both fixtures are needed, not just this one.
- I have no visibility into retransmissions, TCP resets, or Modbus
  exceptions in this traffic (needs the pcap bytes) — if any exist in
  healthy idle, they're currently invisible to this report and could be
  mistaken for attack indicators by a rule that doesn't know they're
  baseline-normal.

## 4. Evidence index

All paths under `/home/kevin/vm/sorter-evidence/`. Hashes are the
collector's own self-reported `pcap_sha256` from each sealed
`segNN.result.json` — **not independently re-verified** (see gap above).

| Segment | Window (UTC) | `fw-l2` packets / SHA-256 | `plc-l1` packets / SHA-256 |
| --- | --- | --- | --- |
| `m2-healthy-idle-1h-20261028/seg01` | 17:25:55.409–17:28:25.536 | 78,744 / `a75c8e7c…4cc8032` | 156,213 / `b6151992…5e89889` |
| `m2-healthy-idle-1h-20261028/seg02` | 17:28:26.875–17:30:57.011 | 78,201 / `1169bf36efac7493…` | 156,247 / `072721c1cbd59660…` |
| `m2-healthy-idle-1h-20261028/seg03` | 17:30:58.384–17:33:28.508 | 78,686 / `bd20d9c195606f47…` | 156,189 / `558b98c3c1eebb8d…` |
| `m2-healthy-idle-1h-20261028/seg04` | 17:33:29.841–17:35:59.965 | 78,630 / `43e4ac332cde95c1dc23d027ae753b5c878f8858dd4838ba9b1497be8e8d12df` | 156,165 / `0dd6a2b172ee8e6ef35e06d65a2b6adcef782ec3602125bf60e82548c87875f8` |
| `m2-healthy-idle-1h-20261028/seg05`+ | — | **not yet sealed** (0-byte `.result.json` as of this analysis; background job still running) | — |
| `m2-healthy-idle-seg1-20261028` (earlier 193 s partial run) | — | 101,175 packets (as given; could not independently confirm — directory is 0700, no sibling result file exists for it at all, unlike the segments above) | 200,000 packets (as given; same caveat — this count equals the segment's packet cap, so it may have hit the 200,000-packet ceiling rather than run the full 193 s) |

Per the task's guidance, rates above use the `seg01`–`seg04` set, not the
partial run.

## Summary

I analyzed the 4 of 24 planned healthy-idle segments (`seg01`–`seg04`,
600.5 s, PLC epoch 49) that were complete and sealed when I looked; the
background collector was still writing later segments and nothing was
touched to speed that up. The idle traffic is steady and complete (zero
drops): `fw-l2` averages 523.3 pkt/s and `plc-l1` averages 1040.5 pkt/s,
almost exactly double, which matches the documented tap topology. I could
**not** produce the Modbus function-code, transaction-rate, polling-interval,
or packet-size breakdown the task asked for, because the capture tool's own
`verify`/`summarize` path requires reading the sealed `.pcap` bytes, and
every segment directory is root-owned mode 0700 with no passwordless sudo
on this host for the `kevin` account running this analysis — I confirmed
that failure mode directly rather than guessing around it. That's the one
thing I'd flag loudest: as it stands, nobody without root can independently
verify or analyze this evidence, which undercuts the point of sealing it
for review. Report: `/home/kevin/vm/sorter-milestone2-baseline/MILESTONE2_BASELINE_HEALTHY_IDLE.md`.
