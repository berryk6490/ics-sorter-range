# Three global plant slots (2026-09-23)

## Contract and ownership

The plant process on **drives** owns package coordinates and photoeye events. It
advances motion from the six VFD feedback values, with a 60 cm package and a
100 cm clear gap by default. The PLC owns three bounded slot records, scanner
triggers, route command validation, and confirmation-only trailer counts. XLe
reads only PLC rows, correlates ASX decisions with `(run epoch, scanner nonce,
slot token, serial, scanner sequence, lane)`, and journals terminal PLC
outcomes. ASX has no PLC or plant connection. OPC UA reads only PLC validated
telemetry; the HMI subscribes to OPC UA and never reads plant internals.

The new registers extend the existing map; the first two rows and the common
plant event payload retain their addresses.

| Data | Slot 0 | Slot 1 | Slot 2 |
| --- | --- | --- | --- |
| PLC row (12 words: token, serial, sequence, barcode, state, destination, actual, reason, scan, acceptance, divert, command) | QW530–541 | QW542–553 | QW647–658 |
| Lane identity | QW644 | QW645 | QW659 |
| Plant raw telemetry (epoch low/high, nonce, token, serial, belt, position ×10, last sensor, actual, sequence) | QW600–609 | QW610–619 | QW660–669 |
| PLC validated telemetry (same 10 words) | QW620–629 | QW630–639 | QW670–679 |
| View status (0 empty, 1 live, 2 stale, 3 identity mismatch) | QW640 | QW641 | QW680 |
| View age in scans | QW642 | QW643 | QW681 |

Commands remain QW520–529, with slot index 0–2; the PLC validates the full
identity, run epoch and nonce before accepting a route or terminal release.
Induction requests remain QW574–577 and the serialized plant event/ACK remains
QW578, QW580–586. An event with the wrong lane fails closed with plant fault 3.
Only a matching trailer-confirmation event increments a trailer counter. A
failed confirmation produces state 7/reason 4 and no trailer count.

The PLC scans ready inducts starting at a rotating lane pointer. Each accepted
request advances that pointer; a fourth ready package waits for a free slot.
At a shared outbound merge, earliest arrival wins and a same-time arrival
favors the lower lane. A waiting package holds at its divert gate. The 60 cm
package is 1.2 model cells and 100 cm spacing is 2 cells, for a required
3.2-cell (32-tenth) outbound separation. Plant mode has three slots globally;
the legacy PLC cell path still inducts into its first two multi-package slots
and the default one-package and three-lane cell models remain available.

## Two-slot consumer audit

| Consumer | Prior two-slot assumption | Three-slot behavior |
| --- | --- | --- |
| `Sorter.st` / `gen_sorter.py` | `st_*[0..1]`, slot command bound, event matching, scanner result binding, telemetry validation and publication | Arrays and matching loops cover 0–2. Legacy induction remains 0–1. New row and validated view at the addresses above. |
| `devices/plant.py` | QW530 count 24, two telemetry sequences/writers | Reads all three rows, writes three identity-bound telemetry rows; common event serialization and spacing logic unchanged. |
| `services/xle.py` | two rows, two lane words, two pending tasks | Reads split third row/lane, bounds pending at three, and performs the same fresh row/lane check before command and release. SQLite journal key and outcome format unchanged. |
| `scada/opcua_server.py` | 26-word two-row view, two Plant objects | Adds Slot3 and reads its 12-word validated view and lane through PLC Modbus. |
| `scada/hmi_ua.py` | two subscribed Plant objects and two drawn packages | Subscribes and renders three identity-bound packages; stale positions remain frozen. Legacy cells still use their prior drawing path. |
| Live runners | lane 3 `all` case asserted a two-slot maximum; earlier one/two-package runners intentionally observe their first two rows | Lane 3 `all` and the new three-slot, wrong-lane and service-stop runners read slot 3. Earlier one/two-package scenario limits remain intentional. |

## Verification

Host: 14 GiB reported by `free -h`, four existing 768 MiB guests (`plc`,
`drives`, `fw`, `scada`), 3 GiB assigned to guests. No bridge, firewall or VM
configuration changed. Host `bash tests/run_first_package.sh` passed, including
the new `plant_three_slots.c` case: three occupied rows, a fourth waiting,
repeated barcode identity, stale-token rejection, and confirmation-only
counters. `python3 -m unittest discover -s tests -p 'test_*.py'` passed 38
cases, including three-lane clearance and slot-3 journal recovery. The
existing lane 3 `all` live runner was updated for three rows and rerun (output
below). Source compilation in the PLC guest succeeded.

Live programs ran through the existing Modbus TCP interfaces. Every recorded
row below had the same ID in scanner/XLe scan, ASX request/decision, PLC
command/outcome, and SQLite journal. Times are PLC scans at 100 ms per scan.

| Run and package ID | Barcode | Commanded / actual | Scan → accepted → divert | PLC terminal outcome |
| --- | ---: | --- | --- | --- |
| Shared four: `l1-1-4-1-1` | 6001 | 1 / 1 | 168 → 171 → 224 | 5, loaded |
| `l2-1-4-2-2` | 5002 | 2 / 2 | 170 → 173 → 315 | 5, loaded |
| `l3-1-4-3-3` | 3003 | 3 / 3 | 171 → 176 → 226 | 5, loaded |
| `l1-1-4-4-4` | 5004 | 1 / 1 | 561 → 564 → 617 | 5, loaded after slot 0 release |

The shared run reached three simultaneously occupied slots. The fourth
induction sensor event (sequence 39) followed the release of token 1; the
first three inductions were sequences 28–30. There were 334 polls with three
occupied slots while induction was due. All three first packages used outbound
1; lane 2 waited at the merge. Minimum observed shared-outbound spacing was
**32 tenths of a cell**. Counters were `[0,0,0,0,0,0,0,0,0]` after divert and
before any trailer confirmation, then `[2,1,1,0,0,0,0,0,0]`. The journal held
four distinct matching outcomes. Rendered Firefox evidence:
[three concurrent](tests/artifacts/plant-three-concurrent.png),
[merge wait](tests/artifacts/plant-three-shared-wait.png),
[shared outbound](tests/artifacts/plant-three-shared-outbound.png), and
[fourth token](tests/artifacts/plant-three-fourth-token.png).

| Additional live case | Exact result |
| --- | --- |
| Late ASX, nonce 5 | `l3-1-5-3-3`, barcode 3003, 1.2 s delayed response past XLe's 0.8 s timeout: no PLC command, acceptance tick 0, divert tick 228, state 6/reason 1 recirculated. `l1-1-5-1-1` and `l2-1-5-2-2` loaded at trailers 1/2; counters `[1,1,0,0,0,0,0,0,0]`. Three slots occupied and pre-confirmation counters all zero. |
| Failed confirmation, nonce 9 | `l3-1-9-3-3`: destination 3, actual 0, state 7/reason 4, failed lane QW646 = 3; trailer 3 stayed zero. `l1-1-9-1-1` and `l2-1-9-2-2` loaded at trailers 1/2; counters `[1,1,0,0,0,0,0,0,0]`. Plant service's sensor log recorded token 3 events 1, 2, 3, 6. Three slots occupied and pre-confirmation counters all zero. |
| Wrong-lane event, nonce 6 | With tokens 1/2/3 in rows 0/1/2, injected a tunnel event for token 3 with lane 2. PLC ACKed sequence 59, set plant fault 3, stopped run, left all rows at state 1 and all trailer counters zero. Reset cleared the deliberate fault. |
| Plant service interruption, nonce 14 | IDs `l1-1-14-1-1`, `l2-1-14-2-2`, `l3-1-14-3-3` occupied three rows. Before stop: row states 3/2/2, run true, fault 0, inducted 3, request token 3, all trailers zero. After heartbeat timeout: states 4/3/4 (already queued divert events), fault 1, run false, inducted/request unchanged, all trailers zero, no XLe outcome. All three validated telemetry rows became stale with frozen positions. Restart during the active run yielded identity fault 2, run false and zero trailers; stop/reset/restart restored fault 0. [Rendered stale HMI](tests/artifacts/plant-three-service-stopped.png). |
| Repeated barcode and XLe restart, nonce 18 | Tokens 1 and 4 had barcode 6001 but IDs `l1-1-18-1-1` and `l1-1-18-4-4`; both loaded trailer 1. `l2-1-18-2-2` and `l3-1-18-3-3` loaded 2/3. XLe restarted with three occupied rows and an already journaled terminal outcome. Events: 4 `plc_command`, 4 `plc_outcome`, 3 `plc_recovered`, 1 `plc_outcome_already_recorded`, 4 releases. SQLite journal had exactly four unique rows; counters `[2,1,1,0,0,0,0,0,0]`. |
| Prior lane 3 `all` live regression | Rows `(lane, token, destination, actual)` were `(1,1,2,2)`, `(2,2,5,5)`, `(3,3,8,8)`; maximum occupied 3; counters `[0,1,0,0,1,0,0,1,0]`. |

The deployed tunnel 1 `/home/kevin/scanner.py` has SHA-256
`f5b32d2c2a7d83e318c57a66b3cd0b3ef3306be594f07f328c534600c2ac1419`.
Compared with `devices/scanner.py`, the repository version only adds the
`os` import, the `SORTER_REPEAT_BARCODE=1` opt-in fixture, and its explanatory
comment. The normal unit has no such environment flag. For the repeated-label
run, the repository source was copied to a separate temporary guest path and
started with that flag; the original deployed source was not overwritten.
The temporary fixture was removed and all three normal scanner units were
restarted.

Two unsuccessful setup runs were excluded from these pass results. The first
four-package runner allowed an unintended fifth induction after token 4;
raising lane 2/3 induction intervals at the third induction corrected it.
Turning off lane run coils in an attempted correction also stopped in-flight
VFDs, so the runner now changes induction intervals while leaving the drives
running. The first repeat fixture lacked privilege to bind TCP port 502; the
scanner's latched reset fault was cleared using operator acknowledge and retry
before the successful run. A fast single-event Modbus poll also missed an
INDUCT event in an initial failed-confirmation observation; the plant log
confirmed it, and the rerun verified ordered tunnel/divert/failed-confirmation
signals. These results are not counted as passing runs.

The plant still models belt motion and photoeyes in software; there is no
physical parcel or sensor. Events use one serialized Modbus payload, so an
external slow poller may miss an event although the PLC ACK sequence and plant
log retain evidence. An active plant process restart cannot reconstruct package
positions; it deliberately requires a stopped PLC reset. Capacity remains
three occupied packages globally, with no durable plant motion state.
The three-package failed-confirmation case was checked through Modbus and
plant/XLe logs; its alarm was not separately captured in a rendered browser.
The browser did render the shared-motion and active-stop fault cases above.
