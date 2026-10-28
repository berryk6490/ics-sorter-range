# XLe VM migration: preserved plant-fault-2 investigation

Investigation evidence: `/home/kevin/vm/sorter-evidence/xle-fault2-investigation-20260926T052750Z/`. It contains an unmodified copy of the first stop directory, current Git patch and untracked-source archive, direct PLC reads, plant/XLe/ASX/OpenPLC journals, service/PID inventory, OPC UA and HMI reads, the FW PCAP (SHA256 `defc69a52ba3fc49a941be0877adc0fee90872b0c003624495d11016a30dcb96`), journal integrity/epoch/outcome information, and `SHA256SUMS`. The guest runner's stdout was attached to the serial tool and was not written to a standalone guest log; the preceding turn captured its fault-stage and traceback text. No source or guest state was changed during evidence collection.

## Meaning and ownership

`Sorter.st` maps QW591 `plant_fault`: 0 ready, 1 unavailable (heartbeat age >30 scans or reset sentinel), 2 identity/configuration mismatch, 3 bad serialized plant event. At reset (line 781), the PLC sets fault 1 and captures the current event sequence in `plant_seen`. While plant mode QX114.6/coil 918 is true, lines 1615–1621 recalculate fault every scan: the PLC sets 2 if plant epoch/nonce QW587–589 differs from active epoch QW558–559 and scanner nonce QW509 **or if either XLe mode coil 914/915 is off**. It stops `sorter_run` on any nonzero plant fault. Fault 2 is not the latched protocol fault; fault 3 is held until reset. When plant mode is off, the health block does not recalculate QW591, so the last value remains displayed. Neither plant.py nor XLe writes QW591 directly.

The generated controller source was reproduced from the repository `Sorter.st` into the evidence directory without deploying it. `generated-controller/POUS.c` has the same assignments: reset to 1 at line 1583, live recomputation to 0/1/2 at lines 2877–2886, and invalid-event fault 3 at lines 3149/3230/3234. No other assignment was found in generated code.

The plant process reads the PLC epoch/nonce on its existing L1 Modbus link. On a new run key it resets its model and pending event, copies the current PLC event sequence, writes QW587–589, and emits `run identity epoch=... nonce=...`; then it writes QW590 heartbeat. A cold process start during active plant mode deliberately clears its identity and requires a PLC reset. No plant restart occurred in this failed run: `sorter-plant.service` was active with the canonical unflagged process PID 8682 in the preserved read.

**Root cause: reset/recovery-helper ordering defect.** The remote-XLe branch of `tests/recover_accumulation_state.py` waited for XLe epoch/heartbeat and `plant_fault=0`, but its `finally` wrote coils 914 and 915 false *before* coil 918 false. The intervening PLC scan met the explicit `NOT xle_mode OR NOT xle_multi` condition while plant mode remained true, setting QW591 to 2. Once coil 918 went false, the PLC retained that value. The same test's fake PLC reproduced the original failure before the helper fix; its archived output is `failing-focused-test.log`. The narrow fix disables plant mode first, checks a fresh exact plant identity tuple and heartbeat, and then disables XLe modes. The C harness independently reproduced the old unsafe teardown and the new safe order against generated code from `Sorter.st` without changing the PLC program.

This was a **real failed typed restoration**, not a fault to ignore in the comparator. The process was fail-closed: master off, slots empty, zero enforced/trailer counters, no induction during recovery. OPC UA and HMI showed the same fault 2 as direct PLC Modbus. A valid plant identity and heartbeat were present at the preserved read (epoch 24, nonce 39; QW587–589 `[24,0,39]`; heartbeat 18574, age 0), but the idle fault register retained the final incompatible-mode scan. The documented reset does not promise fault 0 immediately: it writes fault 1 and requires a new plant/XLe handshake. The helper could previously observe a transient valid handshake and still reintroduce fault 2 during its own teardown.

## Timeline and evidence limits

| UTC time | Actor and recorded action | Epoch / nonce | Plant identity, event seq/ack, fault | Slot / master |
| --- | --- | --- | --- | --- |
| 04:39:27.718 | XLe established active run | 23 / 38 | plant commit later logged at 04:39:28.505 | master initially off |
| 04:39:29.792 | plant logged lane-1 induction event | 23 / 38 | event log type 1, token 1 | slot 1, master on |
| 04:39:30.900 | XLe service stopped by bounded fault probe | 23 / 38 | no plant restart | slot 1 |
| 04:39:44.377 | plant logged tunnel event | 23 / 38 | event type 2 | package still moving |
| 04:39:50.183 | plant logged divert event, actual 0 | 23 / 38 | event type 3 | no route command |
| 04:40:14.018 | XLe service restored by probe's `finally` | 23 / 38 | runner terminal wait had expired; fault-stage read was QW569=1 and QW591=0 | one induction, zero trailers |
| 04:40:33.526 | XLe established new epoch after operator reset | 24 / 39 | reset request cleared slots/counters; exact reset scan time not logged | master off |
| 04:40:34.309 | plant logged fresh identity commit | 24 / 39 | QW587–589 became `[24,0,39]` | slots empty |
| 04:40:34.488 | helper sampled counters in failed recovery result | 24 / 39 | QW591=2; zone fault 0 | master off, slots empty, counters zero |
| 05:28:55 PLC read; 05:29:25 OPC UA/HMI reads | all three interfaces reported fault 2 | 24 / 39 | QW585=183, ACK=0, heartbeat age 0; no new plant identity mismatch | modes all off, slots empty |

There is no scan-level fault trace for the recovery interval. The **first** assignment of fault 2 cannot be timestamped: a brief identity mismatch before the plant's new commit may also have produced it. The deterministic *final* assignment follows from the helper's coil order and PLC source, and was reproduced in the focused fake and generated PLC/C harness. The old plant event sequence was captured as `plant_seen` during reset; the C harness confirms that replaying that unchanged sequence after reset cannot increment a counter or complete a package.

## Hypothesis review

| Hypothesis | Finding |
| --- | --- |
| A: reset leaves old identity | A transient mismatch is possible, but the preserved tuple matched epoch 24/nonce 39. It does not explain persistent fault 2 after a valid commit. |
| B: recommit rejected/missed | Plant log records the fresh commit; current PLC tuple matches it. No rejected-ACK evidence. |
| C: helper does not wait for plant commit | True as a contract gap: it checked fault 0 but not the exact fresh tuple/heartbeat. Fixed alongside the proven teardown ordering. |
| D: slots cleared before plant reseed | Reset cleared slots while a package was in motion; plant model reset on run-key change. No false terminal/counter followed. This is intentional stop/reset behavior, not the final fault-2 cause. |
| E: plant service restarted | Rejected by service journal and unchanged canonical PID. |
| F: old event after reset | No logged new old event. Current event sequence/ACK and PLC reset guard do not support it as fault-2 cause. |
| G: outstanding terminal/event | The package had not reached terminal when probe timed out; no trailer count or journal outcome. Not the fault-2 condition. |
| H: typed snapshot too early | Rejected as sole explanation: fault 2 was still present in a later read-only snapshot while modes were off. The helper's teardown made it persist. |

## Bounded recovery contract and status

The source fix is host-tested only. The required live recovery is: verify master off and canonical services present; issue documented reset through coil 910; wait for all scanner ACKs and the dedicated XLe journal-backed epoch; turn on external and plant modes; require *fresh* QW587–589 equal to current QW558–559/QW509, a changed heartbeat with age ≤30, zero plant/XLe faults, empty slots, zero enforced counters, and no stale event outcome; turn **plant mode off first**, then external modes; read QW591 again. A timeout reports expected/observed identity, heartbeat, fault and event sequence/ACK. No internal fault register write or service restart is part of this action.

The originally preserved run remains a **functional FAIL** and its typed
postflight remains a **FAIL**. Under a separate, fresh authorization, the
corrected helper was backed up and deployed with exact manifest hash, then
one operator reset/handshake ran at 05:55:26–05:55:28 UTC on September 26.
The **later restoration PASS** advanced scanner nonce 39→40, XLe epoch
24→25, and committed plant identity `[24,0,39]`→`[25,0,40]` with a new
heartbeat and age 0. Plant fault cleared 2→0 by PLC protocol. Master stayed
off, slots and enforced counters were zero, services retained their PIDs,
and direct Modbus, OPC UA, and HMI API agreed. The canonical typed
postflight passed 219/219 with no differences and no validation orphan.
Evidence and verified `SHA256SUMS` are in
`/home/kevin/vm/sorter-evidence/xle-fault2-authorized-recovery-20260926T055347Z/`.
This restoration does not change the original functional result.

## Separate undecided-package movement defect found during host review

The recovery-helper defect explains the retained **plant** fault 2; it does
not explain why the undecided package failed to reach a terminal outcome.
The earlier read after the probe showed `z_fault=2`, slot state 4 and a raw
position beyond 14 cells. In accumulation mode, `devices/plant.py` currently
labels an unrouted package as zone 3 (`PREMERGE`) all the way to the 19-cell
recirculation exit. `Sorter.st` rejects zone-3 positions above 14 cells,
sets `z_fault=2`, and stops the sorter. A new deterministic Python model test
failed with positions 14–18 still marked zone 3; a generated PLC/C test failed
when it attempted to validate the needed recirculation-tail zone. Their failing
logs are in the investigation evidence. This is a separate physical/PLC
contract defect, strongly supported by source and the preserved zone read.

A proposed bounded zone-7 `RECIRC TAIL` contract, plant/HMI mapping, and PLC
validation patch is preserved as `proposed-recirc-tail-fix.patch` in the
investigation directory. It has **not** been applied to the current checkout
or deployed. It would require PLC identity 24114, coordinated plant/HMI
deployment, host regression, and a separately authorized live validation.
That proposed patch was not used as proof. The movement boundary was
subsequently specified in `XLE_VM_MOVEMENT_BOUNDARY.md`; production source
is now changed locally for host verification, but the live PLC still runs
identity 24113 until a separately authorized deployment.
