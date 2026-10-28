# Lane movement boundary contract

This contract records the physical coordinates used by independent plant mode.
Coordinates are package-front positions in cells. An interval `[a,b)` includes
`a` and excludes `b`. The PLC uses bounded tolerance around transition points
for committed telemetry; that tolerance does not redefine the plant's physical
classification. The plant owns coordinates and raw events; the PLC validates
identity, sequence, zone, event order, and terminal outcome.

| Path and state | Plant zone | Front coordinate | Admission and event |
| --- | --- | --- | --- |
| Lane induction approach | 1 APPROACH | `[0,9)` | Induct sensor at entry; lane induct VFD |
| Tunnel and scanner decision | 2 DECISION | `[9,11.8)` | Tunnel sensor at 10; lane induct VFD |
| Post-scan lane and premerge approach | 3 PREMERGE | `[11.8,14)` | Divert approach sensor at 12; lane induct VFD |
| Route-accepted merge hold | 4 MERGE | front `13.6` | Only a valid PLC destination can enter; waits for safe outbound admission |
| Undecided or fallback recirculation tail | 7 RECIRC_TAIL | `[14,19]` | At 14, physical DIVERT event reports actual 0 and irrevocably selects fallback; RECIRC event at 19 |
| Shared outbound | 5 OUTBOUND | front `[2,20]` on selected outbound | Only a valid PLC route and safe merge gap admit it; outbound VFD controls motion |
| Divert approach after admission | 5 OUTBOUND | starts at lane entry 2, 5, or 8 | PLC still requires the serialized DIVERT event and exact destination identity |
| Trailer confirmation | 5 OUTBOUND, then 6 TERMINAL | trailer front 12, 15, or 18 | Physical confirmation event determines actual trailer; PLC alone increments counter |
| Recirculation terminal history | 7 RECIRC_TAIL | front `19` | Short-lived telemetry remains on the lane until PLC receives RECIRC and releases the slot |

The premerge interval is exclusive at 14. No undecided package may enter
route-dependent zone 4 or 5. If a PLC route is present before the 13.6 hold,
the package waits for merge admission and then moves on an outbound. If it
reaches 14 without an accepted route, the plant fixes target to fallback,
emits DIVERT actual 0, and continues on its lane to RECIRC at 19. A later
ASX answer cannot redirect it. Lane blocks and unavailable merge capacity
hold packages at 8.8, 11.6, or 13.6; they do not move these boundaries.
The PLC accepts only the bounded zone-3-to-zone-7 fallback transition for
the same run, lane, token, serial, and fresh event sequence with no destination.
Wrong identity, stale sequence, impossible zone jump, wrong lane, overlap,
or an attempted routed recirculation tail remain rejected.

Before correction, `PlantModel.step_zones` selected PREMERGE for every lane
coordinate at or beyond 11.8, including 14 through 19. The PLC's zone-3
admissibility ended at 14. The preserved old-behavior failures are
`xle-fault2-investigation-20260926T052750Z/failing-recirc-zone-test.log` and
`failing-recirc-plc-test.log` under `sorter-evidence` outside Git. The former
observed zone 3 at positions 14–18; the latter showed the PLC had no valid
recirculation-tail zone. The plant classification and missing bounded PLC
zone are the two sides of this protocol defect. Broadening zone 3 would
mislabel physical movement and weaken the PLC check.

## Host proof and deployment gate

The old plant test failed with zone 3 at fronts 14, 15, 16, 17, and 18;
the old generated PLC test failed when a no-route package reached the
post-14 coordinate. These failures remain preserved in the investigation
directory. With the narrow zone-7 contract, the focused Python model tests
pass for front 13.99, 14, 14.01, 19, valid route, fallback, lane block, and
merge block. The generated PLC/C harness passes the recirculation-tail,
old-zone rejection, routed-tail rejection, wrong-identity rejection, stale
sequence rejection, and previous normal/overlap/clearance cases. The full
PLC/C harness passed; the 190-test Python suite passed on rerun. One first
Python-suite attempt hit an unrelated monitor PID-exit race; its focused
rerun and the full rerun passed. Python compileall and `git diff --check`
passed.

The deployed PLC program identity is 24114. The predeployment 24113 typed
snapshot recorded master off, slots empty, faults and enforced counters zero.
Because the updated manifest named the target identity, the first capture
self-check correctly detected the old 24113 identity. A labeled evidence copy
changed only snapshot identity metadata to the directly read 24113 and
self-compared 219/219. After deployment, a new 24114 baseline self-compared.

Ten affected deployed files were backed up with ownership, mode, mtime and
SHA-256 before the approved plant stop/start, PLC core compile/relaunch, and
HMI reload. Deployment preflight verified all 40 source/guest hashes. One
affected XLe-loss run at epoch 27/nonce 3 moved `l1-27-3-1-1` through validated
zones 1→2→3→7 and physically recirculated at front 19. PLC and XLe agreed on
terminal state 6/reason 1, actual trailer 0 and exactly one journal outcome;
there was no route command, wrong-trailer confirmation, plant fault, or zone
fault. OPC UA and HMI API saw the same validated zone/fault state. The
corrected recovery handshake established epoch 28/nonce 4 with current plant
identity, fresh heartbeat, master off, empty slots, faults/counters zero and
typed postflight **219/219 PASS**. A fresh three-lane run at epoch 29/nonce 5
loaded trailers 2, 5 and 8. Its post-reset typed comparison also passed
**219/219**. The earlier epoch-23 failed run remains FAIL; this is a separate
fixed-code result.

Evidence is outside Git at
`/home/kevin/vm/sorter-evidence/xle-recirc-deployment-prep-20260926T062200Z/`
and `/home/kevin/vm/sorter-evidence/xle-recirc-affected-live-20260926T070000Z/`.
