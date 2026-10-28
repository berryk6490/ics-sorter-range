# Phased missed-tunnel validation

This is an evidence-only check of the existing stateful photoeye behavior at
PLC identity 24112. The plant fixture remains the existing token-1,
lane-1 `--photoeye-fault-sensor tunnel --photoeye-fault-kind missed` process.
No application service or register protocol changes are needed. The host
runner is `tests/run_missed_phased_host.py`; its SCADA half is
`tests/live_missed_phased.py` at canonical path
`/home/kevin/live_missed_phased.py`. The exact hash and path are in
`deploy/deployment_manifest.json` (SHA-256
`a63db2d292a376a2e9b168f7a6bed896712333e2b861fe178d38407640866216`).

Run from a **clean** `main` checkout after the SCADA runner has been copied to
its manifest destination. All guest serial consoles must already be logged in.
The host preflight checks every manifest hash and active service, PLC identity,
stopped master, empty slots, clear plant/scanner/photoeye faults, and modes.
It records all VMs, services, operator settings, scanner device nonces, plant
PID, counters, seed and photoeye timing settings before changing anything.

```
python3 tests/deployment_preflight.py --live
python3 tests/run_missed_phased_host.py
```

The workflow has explicit handoffs:

1. The host pauses the canonical normal plant process and starts only the
   token-1 missed-tunnel fixture. The SCADA runner resets scanners, starts
   ASX and XLe, enables lane 1, and waits for fault QW748..750 `[1,2,1]`.
   It writes `fault.json` and **does not reset** the PLC.
2. While the fault is latched, the host optionally renders the HMI through
   its existing serial proxy. Browser failure is recorded, but cannot skip
   restoration. The host terminates the fixture and restarts the normal
   `sorter-plant.service` process. It then writes `plant_restored`.
3. The SCADA runner confirms the fault, slot identity and zero trailer
   counters remain after plant-only restoration. It writes
   `plant_only.json`. Only after the host validates that artifact does it
   write `recover`, permitting PLC/scanner reset.
4. The SCADA runner stops master, disables plant/photoeye mode, issues the
   documented reset coil, waits for scanner completion and cleared photoeye
   fault, restores captured operator settings and stops XLe/ASX. The host
   then runs the canonical stateful three-lane test.

Both halves have bounded waits. A missing fault, row, latency reference,
Modbus/OPC UA/HMI agreement, or plant-only latch proof fails the run. Host
`finally` restores the normal plant before signaling the guest to abort;
guest `finally` performs documented PLC reset and operator restoration.
The host retains the primary failure separately from cleanup errors. If the
guest cannot finish cleanup, the host performs the same documented reset
after plant restoration. No internal PLC state register is forced.

Evidence is written outside Git under
`/home/kevin/vm/sorter-evidence/missed-photoeye-<UTC timestamp>/` by default,
with `SHA256SUMS`. The full raw QW690..697 and validated QW720..727 rows,
slot QW530..541, event QW580..586, PLC tick, wall/monotonic timestamps,
VFD command/status/feedback and plant telemetry are captured before and at
the fault, and after the normal plant returns. The reported detection
latency is `(fault PLC tick - observed validated induction falling-edge PLC
tick) × 100 ms`; it is a sampled upper bound because the 100 ms PLC scan
and Modbus polling can place the actual transition between reads. A missing
upstream falling-edge observation is a test failure. Normal-run evidence
includes scanner, ASX, XLe, PLC, journal, HMI and confirmation-only counters.

The normal scanner nonce, PLC tick and plant PID are expected to change
through reset and service restart. Final cleanup compares operator values,
service/VM states, empty slot states, stopped master, and removed fixtures,
without requiring stale runtime metadata to match byte-for-byte.

## Verified live run, 2026-09-24 UTC

The successful evidence directory is
`/home/kevin/vm/sorter-evidence/missed-photoeye-20260924T010425Z/`.
The clean preflight found PLC identity 24112, scanner and device nonces 26,
plant PID 3391, stopped master, empty slots, plant/photoeye faults zero,
all modes off, and 19/19 exact manifest hashes. The five initially running
VMs remained running; the other three remained off. All required services
were active. The successful host runner returned `PASS` with no primary or
cleanup error. Its `SHA256SUMS` passed `sha256sum -c`.

The fault package was `l1-1-27-1-1`, slot token 1, serial 1, scanner
sequence 0, barcode 0, destination 0, actual trailer 0, state 1. The
complete transient raw QW690..697 row was
`[1,0,27,1,1,1,4,176]`; the PLC-validated QW720..727 row was
`[1,0,27,1,1,4,4,5]`. Fault QW748..750 was `[1,2,1]` at PLC tick
198. The validated induction trailing edge was observed at tick 39;
sampled detection latency was **15,900 ms**. Raw/conditioned mask 4 means
the divert approach beam appeared while tunnel beam 2 was still expected;
quality 5 is the PLC order fault. Event QW580..586 was
`[2,1,1,0,100,105,104]`: the tunnel event had not been acknowledged.
The plant telemetry row located the package on induct 1 around position
12.1 cells at the fault. The induct and outbound 1 VFDs showed command 3,
120 rpm reference and feedback, and running status 3 in the fault sample;
their feedback had reached zero by the plant-only readback. Master was off,
the slot's scanner sequence/barcode and route/actual/command fields were
zero, XLe logged only run identity establishment, ASX produced no request,
and all nine trailer counters were zero.

Direct PLC Modbus, direct OPC UA and HMI `/api` all reported fault
`[1,2,1]` and the same eight-word validated row. The rendered HMI screenshot
`missed-hmi.png` shows `PHOTOEYE LANE 1 SENSOR 2 FAULT (SLOTS 1)` and the
affected package `l1-1-27-1-1` with `PE RAW 4 FILTERED 4 QUALITY 5`.

The host terminated only the fixture and restarted the canonical normal
plant service (new PID 3424) **before** signaling PLC recovery. At tick
368, fault `[1,2,1]` and validated row `[1,0,27,1,1,4,4,5]` remained
latched across Modbus, OPC UA and HMI. Master was off; slot token and state
remained `[1,1]`; inducted count stayed 1; all trailer counters stayed zero.
The restarted plant reported plant fault 2 because the fresh process did
not inherit the active run identity, a fail-closed condition while the
photoeye fault remained latched. No scanner result, route or false trailer
completion appeared. Only after this proof did the runner issue the
documented master-off, modes-off, scanner reset. `recovery.json` shows
QW748..750 `[0,0,0]`, empty slot states and master off.

The canonical `/home/kevin/live_plant_lane3.py all --stateful` run then
produced:

| Package ID | Barcode | ASX decision | PLC/XLe command | Confirmed trailer | Journal |
| --- | ---: | ---: | ---: | ---: | --- |
| `l1-1-29-1-1` | 6001 | 2 | 2 | 2 | loaded once |
| `l2-1-29-2-2` | 5002 | 5 | 5 | 5 | loaded once |
| `l3-1-29-3-3` | 3003 | 8 | 8 | 8 | loaded once |

The runner's ASX, XLe, PLC and journal records agree on each ID.
Pre-confirmation trailer counters were all zero; final counters were
`[0,1,0,0,1,0,0,1,0]`; HMI telemetry covered lanes 1, 2 and 3.
Final readback: master off, all slot states zero, plant/photoeye faults zero,
all modes off, enables `[1,1,1,1,1,1,1]`, setpoints
`[200,200,200,233,233,233,2,14,14,14,30]`, seed 137, photoeye settings
`[2,2,120,400]`. VM and service states matched preflight. The plant PID
and scanner nonces changed as expected; no fixture, XLe or ASX process
remained. The host PLC/C harness passed 32 cases, full Python discovery
passed 53 tests under ResourceWarning escalation, and syntax compilation
passed.

Key SHA-256 values from the successful `SHA256SUMS`:

| Evidence | SHA-256 |
| --- | --- |
| `fault.json` | `af860d18dd2f1e225bdedc1d2baef661137d2fb0b5b26de8c1798cba4c39b815` |
| `plant_only.json` | `cee7df37b96d2a2fadf8853c06887a911ca27c3587964c383aacb1732963f0ca` |
| `missed-hmi.png` | `85a1bfaa26fca0d7365d74067e33398881930dfd68de6dfd6ce359a86f0817ea` |
| `normal-run.log` | `a1e23c5e83d1380302681f3ffee9cb90d6b6a3068a6f220d7d668029b5a8e33e` |
| `final.json` | `fe7de95867b03f594c5368a4e3bda41d1ef6c47fddca9ddbb129911c1e9f1034` |

An earlier attempt at `missed-photoeye-20260924T010012Z` completed the
fault, screenshot, plant-only latch and reset phases, but its host collector
incorrectly required a nonempty ASX log; it stopped before launching the
normal three-lane test. Its cleanup succeeded, and a separate canonical
normal run loaded trailers 2/5/8 and cleared the reset's plant-unavailable
sentinel. The collector now writes and accepts empty process logs; a focused
regression covers that case. The successful run above verified the complete
path after that correction.

Remaining measurement limit: 15,900 ms is measured between two observed
PLC ticks, not a hardware optical edge. The PLC scans every 100 ms and
Modbus polling can bracket a transition between reads. The normal plant
restart necessarily loses its process-local package coordinates and run
identity; recovery requires the documented PLC/scanner reset before another
sort. No production behavior was changed by this milestone.
