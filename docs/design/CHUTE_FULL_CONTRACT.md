# Selected trailer chute-full protocol, version 1 (host implementation)

This is an opt-in **simulation** for global trailer 2, capacity three. Plant
coordinates, physical chute occupancy and the raw full switch belong to the
drives plant. The PLC owns accepted terminal outcomes, nine cumulative trailer
counters, the validated switch, inhibit and operator actions. XLe and ASX do
not read the plant. Motion 8 `JAMMED` remains reserved for Phase 2B; this
feature uses held motions 2/3 and hold reason 5 `CHUTE_FULL`.
The mode must be configured while stopped and before establishing a new
plant run identity; changing coil 921 during an active identity does not
recreate the plant model and is not a supported live action. The normal
legacy mode and all nine trailer cumulative counters retain their prior
meaning.

The active `Sorter.st` and byte-identical `gen_sorter.py` map words only through
QW788 and coils through 920. A source-wide mapped-address audit found
QW790..832 and coil 921 free at commit 7531bb1. All addresses below are
zero-based Modbus holding registers or coil numbers. A compiled/runtime map
check remains mandatory before deployment.

| Address | Writer | Name and meaning |
| --- | --- | --- |
| coil 921 | operator/PLC | `chute_mode`, opt-in, defaults off on reset |
| 790 | plant | `chute_raw_version`, exactly 1 |
| 791 | plant | `chute_raw_trailer`, exactly 2 |
| 792 | plant | `chute_raw_occupied`, 0..3 PLC-accepted physical confirmations |
| 793 | plant | `chute_raw_reserved`, 0..3 crossings awaiting PLC acceptance |
| 794 | plant | `chute_raw_full`, 0/1; occupied + reserved >= 3 |
| 795..796 | plant | active epoch low/high |
| 797 | plant | scanner reset nonce |
| 798 | plant | nonzero raw sample sequence, 1..30000 |
| 799..801 | plant | most recent crossing token, serial, event sequence (0 until transmitted) |
| 802 | plant | raw commit; 0 during update, then equals 798, written last |
| 803 | PLC | last accepted raw sample sequence |
| 804 | PLC | validated selected trailer, 2 when mode is enabled |
| 805 | PLC | accepted physical occupancy, 0..3 |
| 806 | PLC | chute state: 0 disabled/clear, 1 full-unacknowledged, 2 full-acknowledged, 3 empty-wait-resume, 4 unknown/fault |
| 807 | PLC | quality: 0 disabled, 1 fresh-valid, 2 stale, 3 rejected/identity mismatch |
| 808 | PLC | sample age in scans; stale after 15 scans |
| 809 | PLC | chute permissive, 0 inhibited, 1 open; fail closed if unknown; 1 in legacy mode |
| 810..813 | PLC | empty request trailer, epoch low/high, nonce |
| 814 | PLC | empty request sequence, committed last |
| 815 | plant | empty result: 0 pending, 1 completed, 2 rejected |
| 816 | plant | matching empty ACK sequence, written last |
| 817..820 | HMI/UA | action trailer, epoch low/high, nonce |
| 821 | HMI/UA | opcode: 1 acknowledge, 2 empty, 3 resume |
| 822 | HMI/UA | action sequence, committed last |
| 823 | PLC | matching processed action sequence |
| 824 | PLC | action result: 0 none, 1 accepted, 2 invalid state, 3 identity/stale |
| 825 | PLC/operator config | selected trailer, 2 in this version |
| 826 | PLC/operator config | physical capacity, 3 in this version |
| 827..829 | PLC | last **accepted** selected-trailer terminal event token, serial, sequence; retained after XLe slot release |
| 830..832 | plant | last reconciled PLC-accepted crossing token, serial, sequence; written before raw commit |

The plant writes QW802=0 before QW790..801 and QW830..832, then writes nonzero QW802 last.
The PLC samples only equal nonzero QW798/QW802 with active epoch/nonce and a
new sequence. Every sequence increments 1..30000 then wraps to 1; zero is
never committed. Sequence reuse is accepted only after an active epoch/nonce
change. Identity mismatch is quality 3, stale is quality 2, and neither opens
the permissive. A fresh clear sample after an acknowledged empty request is
required before resume. QW799..801 and QW827..829 reconcile accepted physical
crossings; the event ACK QW586 alone never proves a load. Wrong-destination
but physically accepted actual-trailer-2 events occupy chute space; failed or
rejected confirmations remain provisional/unknown and never increment a load
counter. A duplicate or late event cannot add occupancy again.
The PLC retains the last accepted terminal tuple after slot release and also
retains the last tuple already counted into chute inventory. A later sample
cannot use that same tuple for a second occupancy increase, even if its raw
sample sequence is new. Both tuples reset only with a new run identity.
The raw sample timeout is 15 PLC scans; an unaccepted physical reservation
becomes unknown after five seconds. The HMI PLC-poll freshness bound is 1.5
seconds. All three bounds are fail-closed and independent. For QW798, QW814,
and QW822, the next sequence is `old % 30000 + 1`; zero means no committed
request. A repeated sequence in one epoch/nonce is ignored or rejected;
changing epoch/nonce requires a fresh run and resets the sequence context.

After Resume, the plant can have committed one final hold sample before it
observes the new PLC permissive. A 15-scan handoff allows hold reason 5 only
for the **same validated slot and zone**, with the same held motion and route
to trailer 2. The PLC still checks the active epoch, nonce, token, serial,
event sequence, and zone geometry. If the plant does not publish movement or
another safe state before the bound expires, zone fault 1 stops the sorter.
The old program rejected the in-flight hold sample in the Resume scan; the
host `resume_handoff` case reproduces that failure, and `resume_expiry` proves
the corrected allowance is bounded.

Actions are run-bound and ordered. Full -> state 1 and inhibit. ACK -> state 2
without movement. EMPTY requires ACK and a safe pre-door geometry; the PLC
commits QW810..814. The plant clears inventory only for the matching fresh
request, writes status then ACK, and publishes a subsequent fresh clear sample.
That makes state 3, still inhibited. RESUME requires state 3, fresh quality 1,
no pending crossing, and healthy plant/zone/photoeye/XLe identity; it opens
QW809. A new full edge re-inhibits and begins a new acknowledgement cycle.
General run reset does not stand in for these in-flight actions. Cold plant
restart during an active run has no reconstructed positions or inventory:
quality becomes unavailable, permissive remains closed, and documented
stop/reset/handshake is required.

The selected trailer-2 door front is cell 15. The named boundary
`chute_hold_front(2, length_cells, max_step_cells)` returns cell 14.6: door
cell 15 minus a 0.4-cell front clearance. The function checks that the hold
is downstream of the largest merge entry (cell 8) and upstream of the door.
The plant clamps a front to that boundary **before** evaluating the trailer
beam or terminal event. Long VFD feedback integration steps also clamp at
each photoeye leading and trailing coordinate so that one raw sample sees
each transition; this applies to induction, tunnel, divert approach,
outbound entry, and trailer confirmation. The host proof exercises lengths
40, 60, 90, and
120 cm; RPM 0, 120, 1750, and 3000; and integration intervals 50, 100,
and 500 ms. Its maximum unconstrained step is 8.57 cells, yet the clamp
never crosses cell 15. No reverse jump occurs if a full edge arrives after a
front is already past 14.6. This model has an instantaneous mechanical hold;
it does not calculate braking force or slip. If a front is already past cell
15 before full becomes observable, the plant marks chute quality unknown,
suppresses a new terminal event, and requires operator reconciliation. A
package already DIVERTed remains on its outbound; followers retain at least
3.2 cells of front pitch. No chute-full hold creates a trailer event or
changes PLC slot state. Three later packages can fit at the three distinct
lane merge hold points, one per lane, within the three global slots. A
seventh ready package cannot receive a fourth PLC slot.

Only trailer 2 has a measured chute sensor. The HMI shows its independent
`occupied/capacity` and stale quality alongside the cumulative loaded count;
it makes no sensor claim for trailers 1 and 3–9. PLC poll freshness is
timestamped in the OPC UA server. An HMI API sample older than 1.5 seconds
is unavailable even if the UA session is still connected. The three actions
travel as one six-word OPC UA array and a Modbus FC16 write; they never read
plant internals. The PLC validates every action against current epoch and
scanner nonce. On cold plant restart, inventory cannot be reconstructed from
the cumulative PLC trailer counter, so the chute remains inhibited until a
documented stopped-run reset and physical reconciliation.

## Host-only and future live designs

The minimum future live case uses a run-specific trailer-2 sort plan (a
separately approved ASX change) and serially loads identities P1, P2, P3.
For each, scanner identity, XLe command, physical crossing reservation,
PLC-accepted terminal event, cumulative trailer-2 delta, and chute inventory
delta must match. With P4 already in motion, the third crossing must latch
full and hold P4 before the door. Capture P4 token/serial, zone, hold reason,
position, and absence of trailer increment. Acknowledge must preserve the
hold; Empty must produce a matching request/ACK and a fresh clear raw sample
but preserve the hold; Resume must release P4, which then confirms once.
Capture direct PLC Modbus, OPC UA, HMI API, and rendered browser states at
each phase. Stop immediately on any identity mismatch or unexpected crossing.

The capacity/FIFO stress case first loads P1–P3 to fill the chute, then
holds P4–P6 in three distinct lane merge positions with the global slots
occupied. Present P7 as a ready request and prove that it remains unallocated
until a slot is released. After Acknowledge, Empty, and Resume, record merge
admission order and every position sample; require >=3.2-cell outbound front
spacing. P4–P6 confirm in order and refill the chute; P7 may then take a
new slot token but must respect the renewed full condition. This case must
use three lane identities and does not assume three held packages fit on one
outbound. If live geometry cannot sustain the three lane holds, stop the
stress run and revise its requested package count without reducing clearance.

The seven-package stress case is **not** part of the first live stage.
Required minimum-case evidence is a timestamped read-only preflight,
source and guest SHA-256 hashes, committed PLC rows,
plant event/ACK and provisional inventory, scanner/XLe/ASX/journal identity,
OPC UA/HMI API, genuine browser screenshot, trailer counters before/after
confirmation, typed before/after restoration and no-orphan process proof.
On failure, preserve terminal evidence first, stop induction, restore any
run-specific ASX plan and all operator settings, use the canonical reset and
journal-backed identity handshake, and compare typed postflight. A failed
functional result remains failed even if cleanup passes.

Host verification commands are `python3 -m unittest tests.test_chute_full
tests.test_chute_supervisory_contract tests.test_accumulation_state_snapshot
-q`, `bash tests/run_first_package.sh`,
`python3 -m unittest discover -s tests -p 'test_*.py' -q`,
`python3 -m compileall -q devices scada tests`, and `git diff --check`.
The canonical typed reader uses schema 3; it checks exact mode/config,
zero postflight inventory, safe clear/permissive state, and records raw/action
sequences only as information. `chute_migration_postflight.py` also compares
the prior schema-2/PLC-24114 snapshot with schema-3/PLC-24115 after the
intended identity change while enforcing every prior typed field.

## First live stage and preserved failure

At 2026-09-26 09:02 UTC, the first deployment passed a 41-component exact
guest/source hash preflight and PLC identity 24115. Approved service actions
were one drives `sudo -n /usr/bin/systemctl stop sorter-plant.service` and
restorative `start`, one local PLC `quit()` → compile → root core relaunch →
`start_modbus(502)`, and independent PID-verified OPC UA and HMI reloads.
The byte-identical prior guest files and generated PLC core are backed up in
`/home/kevin/sorter-backups/chute-preflight-PpZ2fWYc/`; host metadata,
hashes, exact rollback instructions and the old schema-2/24114 baseline are
under `/home/kevin/vm/sorter-evidence/chute-preflight-PpZ2fWYc/`.

The first no-package reset reached a new plant identity and zero faults but
failed its ready-mask postcheck: the recovery helper had momentarily enabled
accumulation and left `QW786=7` after modes were disabled. The corrected
helper leaves accumulation/chute off during stopped-run handshake. Its host
regression reproduces the retained mask. A second no-package reset passed:
epoch 32, scanner nonce 3, fresh plant identity `[32,0,3]`, heartbeat age 0,
master off, empty slots, zero enforced counters/faults and ready mask 0.

The first **minimum live attempt remains FAIL**. Its original result and
hashed evidence are preserved under
`/home/kevin/vm/sorter-evidence/chute-minimum-20260926T0913Z/`. In epoch 33,
nonce 4, lane-1 identities `l1-33-4-1-1`, `-2-2`, and `-3-3` loaded trailer 2
from barcodes 6001, 5002 and 2003. The fourth identity `l1-33-4-4-4`, barcode
7004, reached zone 4 and held at cell 13.6 with hold reason 5. Genuine
browser screenshots and independent OPC UA reads show: Full `3/3`,
Acknowledge `3/3`, Empty `0/3` with cumulative counter still 3, and Resume
permissive 1. The PLC then latched zone fault 1 before the fourth confirmation;
the counter remained 3 and XLe journal held only three terminal outcomes.
The temporary ASX plan was restored to its original SHA-256, canonical PLC
recovery advanced to epoch 34 with fresh plant identity `[34,0,5]`, and both
typed postflights and no-orphan checks passed. Cleanup did not change the
functional FAIL result.

The failed raw/validated rows show the same slot token/serial and held zone 4
sample still committed at the Resume scan while chute permissive had already
opened. The prior PLC validator rejected hold reason 5 whenever permissive
was 1. The `resume_handoff` PLC/C case fails against that source and passes
with the bounded 15-scan allowance described above. `resume_expiry` proves a
same-identity hold that never releases still faults. The separately approved
PLC-core transition installed corrected source SHA-256
`952ff58580a2d1a0444b6b0e91f745ff74a923484607645173909c5ab4be3dc6`.
All 15 previous PLC files matched their byte-for-byte rollback backups first.
The PLC/C cases `resume_handoff`, `resume_expiry`, `resume_wrong_identity`,
and `resume_wrong_zone` passed: repeated held commits cannot renew the grace
period or apply it to another identity or zone. The core compiled and returned
`OK` to `start_modbus(502)`. The 43-entry live deployment preflight verified
identity 24115 and every canonical guest hash.

## Approved minimum-case retry, 2026-09-26

The single retry is recorded under
`/home/kevin/vm/sorter-evidence/chute-minimum-retry-20260926T173333Z/`.
The no-package handshake established epoch 35, nonce 2, plant identity
`[35,0,2]`, fresh heartbeat, master off, empty slots and clear faults.
The new typed baseline passed. The ASX plan was backed up byte-for-byte,
temporarily set to the four-package trailer-2 plan (SHA-256
`97bf1116950a096a8f308cbcb0cc7effb9be9a76c86d7bdefdbdb41df42c164a`),
then restored to its original SHA-256
`b8ce42bb2a16a47d01f8efb50d981c82e897496477d657ea0aeedf680295bc2b`.
No ASX service restart was used.

The live run used epoch 36 and scanner nonce 3. PLC, plant, scanner, XLe and
the SQLite journal correlated these identities, each with destination and
actual trailer 2 and one loaded outcome:

| Package ID | Barcode | Token | PLC command | Actual trailer |
| --- | ---: | ---: | ---: | ---: |
| `l1-36-3-1-1` | 6001 | 1 | 1 | 2 |
| `l1-36-3-2-2` | 5002 | 2 | 2 | 2 |
| `l1-36-3-3-3` | 2003 | 3 | 3 | 2 |
| `l1-36-3-4-4` | 7004 | 4 | 7 | 2 |

Rendered browser, HMI API and independent OPC UA reads captured Full at
physical occupancy 3/3, quality 1, permissive 0 and cumulative count 3;
Acknowledge at 3/3 without release; Empty at 0/3, state 3 and permissive 0
while the cumulative count stayed 3; Resume with permissive 1 and count 3;
and P4's matching confirmation with cumulative count 4 and new occupancy
1/3. Five genuine browser screenshots are in `attempt/browser/`. Direct PLC
and raw plant samples are in `attempt/direct-plc-raw-plant.jsonl` (1,155
records); the journal has four outcomes and passes SQLite integrity check.
The first failed run remains labeled FAIL.

The controller preserved terminal evidence before cleanup. Canonical recovery
then established epoch 37, nonce 4, current plant identity `[37,0,4]`,
healthy heartbeat, plant and zone faults zero, master off, empty slots and
zero enforced counters. The original ASX bytes were restored. Same-program
and migration typed comparisons each passed all 245 enforced fields with no
differences; no validation orphan remained. Functional, evidence, service
restoration, PLC recovery and typed restoration separately report `PASS` in
`attempt/result.json`. The seven-package capacity/FIFO stress case above
remains untested live.
