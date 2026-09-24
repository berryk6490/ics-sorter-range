# Phase 2A: finite accumulation and downstream backpressure

This milestone is opt-in independent plant mode: coils 918 (plant), 919
(stateful photoeyes), and 920 (finite accumulation) must be on. The PLC
identity is **24113**. With coil 920 off, the earlier plant kinematics and
three-slot behavior remain available; with coil 918 off, the PLC cell model
remains the default. The serial plant event/ACK and raw photoeye rows are
unchanged. `devices/plant.py` alone owns coordinates, spacing, occupancy, and
motion. The PLC alone owns slots, accepted identity, route, photoeye quality,
outcome, and trailer counters. XLe and ASX receive no plant-internal state.

## Physical model and capacity

Coordinates are cell equivalents, with 50 cm/cell, default package length
60 cm (1.2 cells), and clear space 100 cm (2.0 cells). Minimum front-to-front
pitch is therefore **3.2 cells**. At 1750 RPM a belt advances about ten
cells/second. Each step integrates the *measured* feedback of the existing
three induct and three outbound VFDs; no extra motors exist. The plant uses
the configured package length and clearance to bound occupancy and clamps a
follower behind its leader. A lane's newest request waits outside the plant
until the induction approach has a full pitch free. The PLC has exactly three
global slots; a fourth ready package waits for slot release.

| Zone | Geometry in cells | Default nominal capacity | Motion source and hold point |
| --- | --- | ---: | --- |
| Per-lane induction approach (1) | 0–9 | 3 | Lane induct VFD; admission requires a pitch clear |
| Per-lane tunnel/decision (2) | 9–11.8 | 1 | Lane induct VFD; downstream hold at 11.6 |
| Per-lane premerge (3) | 11.8–14 | 1 | Lane induct VFD; merge hold at 13.6 |
| Shared merge admission (4) | One gate per lane into each outbound | One admission per safe gap | Entry coordinates 2, 5, 8; circular lane pointer |
| Outbound/trailer approach (5) | 2–20 on each outbound | 6 nominal, bounded by three global slots | Corresponding outbound VFD and trailer position |

The nominal zone capacity is `floor((zone length + clearance) / pitch)`,
with one package permitted to straddle a short zone. Geometric limits and
the three-slot PLC limit both apply. A package at a hold point has zero
simulated displacement even if its belt feedback remains positive; this is a
control abstraction of a mechanical stop or accumulation gate, **not** a
separate drive. Free motion follows the VFD ramp feedback. We do not model
braking distance or package-to-belt slip at a hold point. At the plant's
100 ms integration interval and the VFD's ramped speed feedback, a free
package can advance at most about one cell per scan at 1750 RPM; the clamp
places it at the safe hold point on that scan. No physical acceleration or
impact force is inferred from that clamp.

Within a lane, leaders move first and followers are clamped at least one
pitch behind. A full downstream zone backs up to premerge, decision, and
approach, then removes that lane from the PLC-accepted induction-ready mask.
A valid PLC route is necessary for admission to an outbound; an absent/late
route continues to the existing recirculation fallback. The merge considers
all ready lanes at each available gap, starting at a circular pointer that
advances after admission. It tests the destination outbound's actual
positions and feedback speed; it never admits two fronts closer than the
maximum package length plus clearance. Trailer counters still change only
on the PLC's accepted trailer confirmation event.

## Package states and quality

The per-slot zone is 1 approach, 2 decision, 3 premerge, 4 merge gate, 5
outbound, or 6 terminal. Motion is 1 MOVING, 2 HELD_DOWNSTREAM, 3
HELD_MERGE, 4 DRIVE_STOPPED, 5 AWAITING_ROUTE, 6 OUTBOUND, or 7 TERMINAL.
Motion value **8 JAMMED is reserved for Phase 2B**, rejected by this PLC and
never emitted by the normal plant. Hold reason is 0 none, 1 downstream zone
full, 2 merge capacity, 3 drive off, or 4 route pending. Dwell is bounded to
32000 deciseconds and resets when the motion/hold reason changes. Normal
backpressure is a muted, non-alarm HMI state. Drive off is a distinct state
even when a downstream hold would also be possible. AWAITING_ROUTE is
reserved for an explicit route wait; Phase 2A's safe route fallback remains
the existing PLC/XLe mechanism.

The PLC publishes quality 0 empty, 1 valid, 2 stale, or 3 rejected. When
fresh plant commits stop for over 15 PLC scans, it keeps the last coordinate,
marks quality stale, clears induction-ready, and the HMI freezes the drawing.
It does not extrapolate from the last speed. A terminal slot whose plant
two-second recent cache expires also retains its last position as stale
until XLe releases the slot. Ordinary expected holds pause the stateful
photoeye maximum-blocked and sensor-travel timers; leading/trailing edges
still come from the raw physical beam. A held blocked beam is not itself an
alarm. Existing route timeout, missed confirmation, scanner, XLe, and plant
fault recovery remain unchanged.

## Register and OPC UA contract

All addresses below are zero-based Modbus registers. Coil 920 opts into
finite accumulation and resets off. The plant writes each raw slot row
`QW751..755`, `756..760`, and `761..765`: `[zone, motion, hold, dwell_ds,
sequence]`. It uses the corresponding existing position/identity telemetry
row (`QW600..609`, `610..619`, `660..669`) with the same sequence and run
epoch/nonce/token/serial. Plant writes `QW785=0` before the three raw rows,
then writes an incrementing nonzero commit value to QW785 after them.
`QW784` is a three-bit raw induction-capacity mask. This is a seqlock over
the three rows, not an authority to create a route or terminal outcome.

The PLC validates commit freshness, active epoch/nonce, slot token/serial,
lane-to-belt mapping, sequence equality/freshness, zone progression,
position bounds, and pairwise belt clearance of **32 tenths**. A newly
occupied slot binds its token before its first complete plant row is
accepted. It publishes only validated rows `QW766..770`, `771..775`, and
`776..780` in the same five-word layout. Quality is `QW781..783`.
`QW786` is PLC-accepted induction readiness; `QW787` is commit age in scans;
`QW788` is a latched zone fault: 0 clear, 1 identity/sequence, 2 physical
bounds/order/lane, 3 overlap. A fault stops the sorter and inhibits
induction. No zone row can set a trailer counter or terminal slot state.

The existing OPC UA service reads **only these PLC-validated words**.
`Process.Status` adds `AccumulationMode`, `ZoneFault`,
`ZoneInductReadyMask`, and `ZoneAgeScans`. Each `Plant.SlotN.ValidatedZone`
adds `[zone, motion, hold, dwell, sequence, quality]`. The HMI `/api` exposes
those fields and draws a muted hold label with package identity, zone,
reason, and dwell. Faults use alarm styling; stale quality is visibly
distinct and freezes the last position. XLe and ASX have no plant socket or
access to raw zone rows.

## Deterministic controls, verification, recovery

Normal `sorter-plant.service` has no block option. Only a temporary plant
test process may pass `--block-merge` or `--block-lane 1 --block-zone
premerge`, with `--block-after-token N --block-duration S` (`N>0`,
`0<S<=60`). A lane block can select `decision` or `premerge` and one of
lanes 1–3. The fixture resets on a new run identity, expires on its monotonic
timer, and is removed when the test process exits. It never writes PLC slot
identity or routes. `tests/live_accumulation.py` restores the original
operator enables, setpoints, modes, and seed in `finally`, stops XLe/ASX,
and resets occupied slots through coil 910 if a run failed. Its bounded
hold marker lets the browser capture a rendered state. The normal plant
service must be restarted after a temporary fixture exits.

Run host checks with `bash tests/run_first_package.sh`,
`python3 -m unittest discover -s tests -p 'test_*.py' -q`, and
`python3 tests/deployment_preflight.py --live`. Live scenario commands on
SCADA use `/home/kevin/opcua/bin/python
/home/kevin/sorter-services/live_accumulation.py {normal,lane_hold,merge_hold,drive_stop}`.
The lane fixture on drives is `/home/kevin/venv/bin/python
/home/kevin/plant.py --block-lane 1 --block-zone premerge
--block-after-token 1 --block-duration 20`; the merge fixture substitutes
`--block-merge`. For each fixture, stop only `sorter-plant.service` before
starting the process, and restart that canonical service afterward. The
rendered screenshots are collected with `tests/browser_accumulation.py`.
The existing 36-flow network baseline must remain unchanged.

Recovery from a stopped test or plant interruption: turn master coil 880
off, stop the temporary process, restore `sorter-plant.service`, use the
documented operator reset coil 910 if any slot or fault remains, wait for
the scanner/plant handshake, then restore the saved settings. Do not write
internal slot, fault, or outcome registers. Verify three slots empty,
faults clear, coil 920 off, services active, and no fixture process.

## Verified live results (2026-09-24 UTC)

Evidence is outside Git in
`/home/kevin/vm/sorter-evidence/phase2a-verified-20260924T0232Z/`;
`SHA256SUMS` covers each saved artifact. The live runner checked one scanner
result, one ASX request/decision, one XLe command/outcome, one journal row,
PLC terminal state, and the trailer count for each ID below. Each case
reported `cleanup: ok`, and every `pre_confirmation_counters` row was nine
zeroes. No photoeye, plant, or zone fault was raised during the normal or
held runs.

| Case | Exact package ID → confirmed trailer | Peak occupied / held | Counter result | Minimum observed same-belt spacing |
| --- | --- | --- | --- | --- |
| Normal | `l1-1-3-1-1` → 2; `l2-1-3-2-2` → 5; `l3-1-3-3-3` → 8 | 3 / 0 | 2, 5, 8 each +1 | No simultaneous same outbound pair |
| Lane 1 downstream block | `l1-1-4-1-1` → 2; `l1-1-4-2-2` → 2; `l1-1-4-3-3` → 2 | 3 / 3 | trailer 2 +3 | 3.2 cells |
| Shared merge block | `l1-1-5-1-1` → 1; `l2-1-5-2-2` → 2; `l3-1-5-3-3` → 3; fourth `l1-1-5-4-4` → 1 | 3 / 3 | trailer 1 +2, 2 +1, 3 +1 | **3.2 cells** |
| Induct 1 VFD stop | `l1-1-6-1-1` → 2 | 1 / 0 | trailer 2 +1 | Not applicable |

The lane screenshot `lane_hold.png` shows three distinct packages held at
11.6, 8.4, and 5.2 cells with muted styling, no load, and no fault. The
merge screenshot `merge_hold.png` shows one package from each lane at its
13.6-cell gate, all `HELD_MERGE/MERGE_CAPACITY`, with three global slots
occupied. In the merge run the runner observed 246 polls with three occupied
slots and only the first three inducted; XLe's slot-release event preceded
the fourth package's scanner event. The fourth reused a slot with a new
token and did not overwrite any outcome.

The independent view probe captured slot 0 as zone 4, motion 3, hold 2,
quality 1 in Modbus `[4,3,2,2,197,1]`, OPC UA `[4,3,2,2,197,1]`, and HMI
API `[4,3,2,0,195,1]` (the HMI poll was two commit sequences earlier).
Fault 0, accumulation mode true, and ready mask 7 agreed in all three
views. The drive-stop trace recorded VFD feedback 0 RPM with PLC motion 4,
then 120 RPM with motion 1 after the ordinary drive-enable coil was
restored; its scan, ASX, command, journal, and confirmation appeared once.
`stale.png` is a **rendered HMI fixture**, not a live plant-service outage:
it proves the position transform stays fixed, transition becomes `none`,
and the package changes from held styling to unavailable/stale styling.

The earlier stateful plant path also passed live with accumulation coil 920
off: `l1-1-7-1-1`, `l2-1-7-2-2`, and `l3-1-7-3-3` loaded trailers 2, 5, and
8. The host PLC/C harness, 61 Python tests, Python compileall, HMI
JavaScript parse, exact 24-component deployment preflight, and 36/36 network
flow matrix passed. The final operator reset cleared the legacy run's
counters but, as in prior milestones, transiently set plant fault 1 while
plant mode was off. With master still off and slots empty, the documented
XLe journal-backed epoch and normal plant handshake cleared it without
induction. The final snapshot records master off, three empty slots,
faults and counters zero, saved enables/setpoints/seed, no block process,
normal services active, and all eight VM states equal to their initial
states.

On this PLC image, restarting `openplc.service` alone does not spawn the
core because the existing OpenPLC SQLite `Programs` table has no
`active_program` row. For this verification the compiled core was stopped
through its localhost `quit()` RPC, relaunched as the pre-existing detached
root process, and Modbus was started through `start_modbus(502)`.
`openplc.service` remained active. Repairing that unrelated webserver
installation is outside this milestone; a future cold restart should check
both the service and the core/Modbus listener before a live run.

Exact guest runner commands used were:

```sh
# PLC guest, after preserving the previous ST file:
cd /home/kevin/OpenPLC_v3/webserver && ./scripts/compile_program.sh xle_sorter.st
# SCADA guest, one at a time:
/home/kevin/opcua/bin/python /home/kevin/sorter-services/live_accumulation.py normal
/home/kevin/opcua/bin/python /home/kevin/sorter-services/live_accumulation.py lane_hold
/home/kevin/opcua/bin/python /home/kevin/sorter-services/live_accumulation.py merge_hold
/home/kevin/opcua/bin/python /home/kevin/sorter-services/live_accumulation.py drive_stop
/home/kevin/opcua/bin/python /home/kevin/live_plant_lane3.py all --stateful
# Host:
python3 tests/deployment_preflight.py --live
bash tests/run_network_reachability.sh
```

This is discrete, one-dimensional motion on six simulated belt drives.
There is no friction, belt slip, mechanical braking distance, collision
force, chute occupancy, package rotation, or jam physics. Phase 2B can use
the reserved JAMMED motion value with an independently defined physical
stall criterion; normal HELD_* and DRIVE_STOPPED values must never imply a
jam. Jam injection, alarms, and package removal are outside Phase 2A.
