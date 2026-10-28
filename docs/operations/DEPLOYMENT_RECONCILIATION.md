# Commit 3cfb613 deployment reconciliation

The canonical file and service inventory is `deploy/deployment_manifest.json`.
Run `python3 tests/deployment_preflight.py --live` from the host with the guest
serial consoles logged in. It checks repository hashes, guest hashes, and
required active services without writing to a guest. All 18 entries require
exact SHA-256 agreement. XLe and ASX are launched by bounded live runners,
not persistent systemd services.

## Initial state, 2026-09-23

`main` and `origin/main` were both `3cfb61334eb1ce9e14ae3d5c6e6ae03416ddabf3`;
the working tree was clean. `drives`, `plc`, `fw`, `scada`, and `analyst` were
running; `base`, `lfs-lab`, and `ubuntu24.04` were off. The PLC master coil
880 was off, all three slot state fields were zero, and lane fields 644,
645, 659 were zero. Coils 914..919 were all off. Plant fault 591 was 1;
photoeye fault 748..750 was `[0,0,0]`. Operator enables 881..887 were all
on, setpoints 200..210 were `[200,200,200,233,233,233,2,14,14,14,30]`,
and seed 247 was 137. `openplc`, `sorter-plant`, all three `scanner@tunnel`
instances, all six `vfd@` instances, `opcua-server`, and `sorter-hmi` were
active. There was one normal plant process and no XLe or ASX runner.

## Source and runtime identity

`Sorter.st`, the deployed
`/home/kevin/OpenPLC_v3/webserver/st_files/xle_sorter.st`, and the generated
repository `gen_sorter.py` source all had SHA-256
`780e4e6c7cba821d23e20a6e873bc87799e2d582e0dec4a2a48aec06a2e54d70`.
The running PLC reported QW249 = 24112; deployed `core/POUS.c` sets
`PROG_HASH` to 24112, and `./core/openplc` was running. Stateful register
defaults QW744..747 were `[2,2,120,400]`; QW748..750 were zero. The
runtime is thus tied to the checked-in program identity and source. No PLC
compile, replacement, or reboot was performed.

The successful stateful milestone invoked
`/home/kevin/opcua/bin/python /home/kevin/live_plant_lane3.py all --stateful`.
That root-level SCADA file and `tests/live_plant_lane3.py` both hash to
`e134852c11c6adfe9b1cc4f27c6a58ba1cfa658bbac447de96e9129aafd9598c`.
The older `/home/kevin/sorter-services/live_plant_lane3.py` hashes to
`9de07944e9c143597fc637ecab001cf82fe0c678e7668db58318fd6e94cb9697`.
The older copy lacks `--stateful`, photoeye-mode capture/restore, and
photoeye sampling/assertions. It is **preserved in place** as an obsolete
legacy runner, not deleted or overwritten. The root-level path is canonical
for future lane-3/stateful runs. The host serial HMI proxy now launches that
canonical path with `--stateful`.

The pre-reconciliation drives scanner hashed
`f5b32d2c2a7d83e318c57a66b3cd0b3ef3306be594f07f328c534600c2ac1419`.
The only differences from `devices/scanner.py` were an `os` import, a
comment/docstring, and the opt-in `SORTER_REPEAT_BARCODE=1` fixture that
forces later scans to reuse a recent barcode. All three normal service units
use `/home/kevin/scanner.py`; their environment files contain only `ADDR`
and `LANE`, and `systemctl show` reported no other environment. The fixture
is therefore disabled in normal service. The old source was preserved at
`/home/kevin/scanner.pre-3cfb613.py` with its original hash. The repository
source was installed to `/home/kevin/scanner.py` (SHA-256
`5f6f2d48b9424327e88b558a67924efe5bfc70fb061c8c13baa76cc18facd402`),
then all three scanner services were restarted and verified active. No other
deployed application file was replaced; all other manifest files already
matched exactly.

## Why plant fault 1 was present

The PLC reset block initializes `plant_fault := 1` (unavailable). When
plant mode is disabled, the plant-health block does not recalculate it, so
it can remain 1 with master stopped and all slots empty. It is not evidence
of a moving package or a latched photoeye fault in this idle configuration.
The documented runner resets the PLC/scanners, establishes the XLe run
identity and healthy plant heartbeat while plant mode is on, and the PLC
then sets fault 591 to 0. After the fault fixtures, readback was again
`plant_fault=1`, modes off, master off, photoeye faults zero. A final normal
stateful run used that reset/handshake path and left fault 591 = 0 with
master and both plant/photoeye modes off. No internal state register was
forced. Another PLC reset while plant mode is off will set the unavailable
sentinel to 1 again; this is current program behavior.

## Commands and observed checks

```
bash tests/run_first_package.sh
PYTHONTRACEMALLOC=1 PYTHONWARNINGS=error::ResourceWarning python3 -m unittest discover -s tests -p 'test_*.py' -q
python3 -m compileall -q devices services scada tests
python3 tests/deployment_preflight.py --live
bash tests/run_network_reachability.sh
python3 tests/serial_command.py scada '/home/kevin/opcua/bin/python /home/kevin/live_plant_lane3.py all --stateful' --timeout 240
python3 tests/run_photoeye_fault_host.py stuck_blocked
python3 tests/run_photoeye_fault_host.py missed
python3 tests/serial_command.py scada '/home/kevin/opcua/bin/python /home/kevin/live_plant_lane3.py all --stateful' --timeout 240
```

The PLC/C harness passed 32 cases. Python discovery passed 47 under
ResourceWarning escalation. Syntax compilation and all 18 manifest entries
passed. The network matrix matched 36/36 flows: PLC 12, drives 2, SCADA 13,
analyst 9. The first normal stateful run had package IDs
`l1-1-10-1-1`, `l2-1-10-2-2`, `l3-1-10-3-3`, barcodes 6001/5002/3003,
ASX decisions and confirmed trailers 2/5/8, three occupied slots observed,
and trailer counters `[0,1,0,0,1,0,0,1,0]`. Photoeye faults were zero and
HMI API reported all three lanes. The recovery run had IDs
`l1-1-15-1-1`, `l2-1-15-2-2`, `l3-1-15-3-3` and the same barcodes,
destinations, counters, and zero photoeye faults.

The token-1 `stuck_blocked` induction fixture reported fault `[1,1,1]`,
quality 3, occupied slot 1, master stopped, HMI fault `[1,1,1]`, and all nine
trailer counters zero. The token-1 `missed` tunnel fixture reported fault
`[1,2,1]`, quality 5 at the divert approach raw/conditioned mask 4,
occupied slot 1, master stopped, HMI fault `[1,2,1]`, and all nine trailer
counters zero. Each host runner removed its fixture and restored the normal
plant service. These checks used actual guest Modbus and HMI API paths.

The revised host serial HMI proxy also launched the canonical root-level
runner through `POST /test/lane3_run/all` and its start marker. Its SCADA
output reported `stateful=true`, package IDs `l1-1-16-1-1`,
`l2-1-16-2-2`, `l3-1-16-3-3`, trailer counters
`[0,1,0,0,1,0,0,1,0]`, and photoeye faults `[0,0,0]`. Proxy `/api`
reported photoeye mode on with fault 0 during the run, then mode off with
fault 0. The proxy was stopped after this check.

A direct SCADA OPC UA browse of `Process/Status` and all three
`Process/Plant/Slot*/Photoeyes` nodes agreed with the PLC Modbus registers
and HMI API at final cleanup: plant fault 0, photoeye mode off, fault triple
`[0,0,0]`, and all three slot photoeye rows ending with raw/conditioned
mask 0 and quality 0. No rendered browser check was needed for this
deployment-only task; the HMI source and service hashes were unchanged.

The ResourceWarning allocation site was
`tests/test_xle_recovery.py:222`. A SQLite connection used as a context
manager was committed/rolled back but never closed. `contextlib.closing`
now owns it. The focused case and the full warning-escalated suite completed
without the warning.

Final readback: master off; every slot state and lane field zero; modes
914..919 off; plant fault 0; photoeye faults `[0,0,0]`; enables, setpoints,
and seed exactly as initially recorded; program identity 24112. All initial
VMs and services retained their states, no fixture remained, and only one
unflagged plant process was running. XLe/ASX runners had exited. Network
policy, VM definitions, PLC source/runtime, and package-routing behavior
were unchanged.
