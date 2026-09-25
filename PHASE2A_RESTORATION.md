# Canonical Phase 2A typed restoration contract

`tests/accumulation_state_snapshot.py` is the sole baseline and postflight
contract for detached accumulation scenarios. It uses the manifest-approved
drives `read_accumulation_state.py --with-vfds` reader for PLC and six VFD
registers. Every guest command uses `deployment_preflight.guest_read`: the
serial wrapper must see its nonce-bound completion marker and prompt, then a
separate attachment must return a different nonce-bound liveness marker.
Service reads use the noninteractive absolute `systemctl --no-pager` command.
The snapshot never writes PLC, VFD, or service state. Schema version is 1;
the guest reader schema is 2 and PLC program identity is 24113.

## Field contract

Addresses are zero-based. `EXACT` fields match the baseline; `RESET_ZERO`
fields must finish at zero or false; `SAFE_INVARIANT` fields must satisfy the
stated predicate; `DYNAMIC_HEALTH` fields must be healthy and settled;
`INFORMATIONAL` fields are recorded without equality. The comparator emits a
record for every enforced element, including its class, expected condition,
observed value, and typed difference. Missing required fields fail closed.

| Field or range | Class | Required postflight result |
| --- | --- | --- |
| PLC coils 880–887, 914–915, 918–920 | EXACT | Original master, lane/drive enables, route/external/plant/photoeye/accumulation controls |
| PLC coils 910, 912–913, 916–917 | RESET_ZERO | No reset, acknowledgement, or retry command left asserted |
| PLC coil 880 | SAFE_INVARIANT | Master off, including when baseline was off |
| PLC QW200–210, 247, 744–747 | EXACT | RPM, spacing, induction interval, no-read setpoints, seed, photoeye debounce/block/travel configuration |
| VFD 0 and 1, each of six drives | EXACT | Original command word and configured RPM reference |
| QW214–220, 222–242, 250–254 | RESET_ZERO | Per-run scan/process, induction, recirculation, divert, trailer, wrong-door and scanner counters; QW221 serial-next is informational |
| QW222–230 independent trailer view | RESET_ZERO | Nine trailers empty; duplicate readback guards the process row |
| Slot rows QW530/542/647 state offset 4; lane QW644/645/659 | RESET_ZERO | Three empty PLC slots, no lane assignment |
| QW591–592, QW748–750, QW255–256, QW561, QW569, QW788, QW786 | RESET_ZERO | Plant/confirmation, photoeye, scanner, run-identity, XLe liveness and zone faults clear; no induction-ready mask |
| Validated zone QW766–783 state, hold, dwell, quality; QW784 raw ready | RESET_ZERO | No occupied/held zone and no ready mask after cleanup; sequence words are informational |
| VFD fault register 6, six drives | RESET_ZERO | No drive fault |
| VFD feedback register 5 and status register 3, six drives | DYNAMIC_HEALTH | Feedback 0–40 RPM after settling; status ready, neither running nor faulted |
| Manifest required service states | SAFE_INVARIANT | Same complete service set, all active |
| VM power states for plc, drives, scada, fw, analyst | EXACT | Original VM states |
| Canonical plant process | SAFE_INVARIANT | Exactly one unflagged `/home/kevin/plant.py` process; PID may change |
| Temporary guest processes and host HMI proxy | SAFE_INVARIANT | No monitor, worker, fixture, proxy, or temporary XLe/ASX process |
| QW248, 558–559, 509, 587–589; QW766–788 sequence/age; VFD load/frequency/current/thermal; timestamp/PID | INFORMATIONAL | Recorded; may change across reset or process restart |
| Inactive slot payload, raw plant/photoeye rows | INFORMATIONAL | Captured for evidence; historical payload values are not restoration targets |

The snapshot includes the complete three 12-word slot rows, lane occupancy,
zone raw and validated rows, all six VFD register profiles, scanner and XLe
health, and all manifest service states. No plant process memory is used.
The documented fixture block is a temporary `plant.py --block-...` process;
its absence is checked with the other temporary processes. There is no block
register in the PLC operator map.

## Required detached lifecycle

First deploy the changed guest reader to its two manifest destinations and
run `python3 tests/deployment_preflight.py --live --report "$EVIDENCE_DIR/deployment-preflight.json"`.
Before launching the
readiness monitor or requesting operator approval, capture and validate a
safe baseline:

```sh
python3 tests/accumulation_state_snapshot.py capture --output "$EVIDENCE_DIR/typed-before.json"
python3 tests/accumulation_state_snapshot.py compare --before "$EVIDENCE_DIR/typed-before.json" --after "$EVIDENCE_DIR/typed-before.json" --output "$EVIDENCE_DIR/typed-baseline-check.json"
```

Then start/probe the drives monitor. Launch the detached scenario with
`--typed-baseline "$EVIDENCE_DIR/typed-before.json"` and
`--preflight-report "$EVIDENCE_DIR/deployment-preflight.json"` in addition to the
documented arguments. The baseline must be safe, less than ten minutes old,
and both full preflight and baseline must precede monitor launch. After the
worker is ready, run `post-ready-gate` before approval; it is the sole
post-ready state check and does not repeat deployment hashes. Launch writes a `PENDING` restoration
gate at `/home/kevin/vm/sorter-evidence/phase2a-restoration-gate.json`.
Only a passing `verify-clean` changes it to `PASS`. An unresolved gate blocks
the next scenario even when it uses a different evidence directory.
If the ten-minute baseline window expires, stop the monitor, capture a new
baseline while no validation process remains, and start a new monitor/run.

After release or abort, wait/collect, restore the fixture if used, stop and
collect the monitor, and call `verify-clean`. It saves `typed-after.json` and
`typed-restoration-report.json`; the latter carries every field result and
fails the command if required restoration is absent. For independent review:

```sh
python3 tests/accumulation_state_snapshot.py compare --before "$EVIDENCE_DIR/typed-before.json" --after "$SCENARIO_DIR/typed-after.json" --output "$EVIDENCE_DIR/typed-comparison.json"
```

Do not add separate baseline requirements in Hermes commands. An unresolved
failure requires operator investigation and documented recovery before a new
scenario. Timestamps, PIDs, scanner nonces, VFD feedback, and inactive rows
are never required to match numerically.
