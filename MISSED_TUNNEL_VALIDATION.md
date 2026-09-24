# Phased missed-tunnel validation

This is an evidence-only check of the existing stateful photoeye behavior at
PLC identity 24112. The plant fixture remains the existing token-1,
lane-1 `--photoeye-fault-sensor tunnel --photoeye-fault-kind missed` process.
No application service or register protocol changes are needed. The host
runner is `tests/run_missed_phased_host.py`; its SCADA half is
`tests/live_missed_phased.py` at canonical path
`/home/kevin/live_missed_phased.py`. The exact hash and path are in
`deploy/deployment_manifest.json`.

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
