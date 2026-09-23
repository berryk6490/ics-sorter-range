# First package and tunnel sensor slice

`Sorter.st` is the current three-lane PLC program. `gen_sorter.py` is an
identical copy of Structured Text despite its historical `.py` name;
`sorter.st` is an older version with scanner behavior still inside the PLC.
The nearby `../sorter_project/plc.xml` is an older one-lane Beremiz project.
The repository includes copies of the deployed drive and scanner sources in
`devices/`, plus their nine instance configurations and register maps in
`deploy/README.md`. Firewall and HMI service sources have not been copied.

## Data path visible in the PLC source

The operator run bit `%QX110.0` enables the drive command and rpm setpoint at
`%QW100` and `%QW101`. The drives' actual-speed feedback enters `%IW105`.
Each PLC scan adds that feedback divided by 1750 to an accumulator; a whole
unit shifts lane 1's 20 cells. A package serial is inducted into cell 0 after
the configured number of shifts (`%QW207`, default 14) if the gap is clear.
The count is `%QW220`, and lane 1 cells are visible at `%QW260..279`.

When a package moves into cell 10, the tunnel request sequence at `%QW118`
increments and `%QW119` carries its serial. The seed and no-read rate are
`%QW120..121`. The scanner is expected to return the matching sequence,
barcode, status, and length at `%IW158..161`. A valid barcode replaces the
serial in the belt cell and determines the divert target. The package then
moves onto an outbound belt (`%QW380..399` for outbound 1), whose speed
feedback enters `%IW132`. Trailer counters start at `%QW222`.

Lane 2 uses drive registers `%QW103..105`, feedback `%IW114`, tunnel request
`%QW122..125`, response `%IW169..172`, and belt `%QW280..299`. Lane 3 uses
`%QW106..108`, `%IW123`, `%QW126..129`, `%IW180..183`, and `%QW300..319`.
The other three drive command groups are `%QW109..117` and feedback registers
`%IW132`, `%IW141`, and `%IW150`. OpenPLC's `mbconfig.cfg` on `plc` maps the six
drives to `10.10.1.21..26:502` and the three cameras to `10.10.1.27..29:502`.
Each drive accepts command, speed reference, and belt load in holding
registers 0..2 and publishes actual speed in register 5. Each camera accepts
trigger sequence, serial, seed, and no-read rate in registers 0..3 and
publishes result sequence, barcode, status, and dimensions in registers 4..10.
The PLC is `10.10.1.10` and serves its located variables on Modbus TCP 502.

The four VM definitions each reserve 786432 KiB (768 MiB) and two vCPUs.
All four together reserve 3 GiB. `fw` connects to `ics-l1`, `ics-l2`, and
`ics-l3`; `plc` and `drives` connect to `ics-l1`; `scada` connects to `ics-l2`.
The smallest live PLC/drive/scanner test needs only `plc` and `drives`
(1.5 GiB). `fw` is needed for
access across zones, and `scada` is needed only to verify the HMI path.

## Host-side check

Run `bash tests/run_first_package.sh`. It compiles `Sorter.st` with the
installed matiec compiler, then executes the generated PLC code against its
actual located variables. The test supplies drive feedback and one scanner
result, then checks induction, cell movement, same-scan tunnel trigger,
barcode routing, and one correct trailer load. It checks that lanes 2 and 3
also issue an arrival trigger. It does not emulate network timing or prove
the guest services are wired to these registers.

## Guest verification

Use `virsh -c qemu:///system console plc` and
`virsh -c qemu:///system console drives` from the host terminal. Each console
has a login prompt; enter credentials there, then exit with Ctrl+]. The PLC
guest's old active `Sorter` file matched the repository version before commit
`f64a17f` byte for byte (SHA-256 `abb7579d...`). The new source was installed
as a separate OpenPLC program, `f64a17f.st`, with SHA-256 `624a693f...`.
OpenPLC's `compile_program.sh` succeeded and `active_program` now names
`f64a17f.st`. The old program entry remains available.

Copy `tests/live_first_package.py` to the PLC guest's home directory and run
`~/OpenPLC_v3/.venv/bin/python3 ~/live_first_package.py` there. The script
reads the PLC, induct drive 1, and camera 1 over Modbus, then checks the first
serial from induction through barcode and trailer load. It records the initial
lane 2 and lane 3 enable bits before reset, stops the sorter, and restores
those exact bits even if the check fails. It also changes the scanner seed
before reset and restores the previous seed afterward, because a same-seed
PLC reset alone does not reseed the deployed scanner. On 2026-09-23, a live run
exited 0: first barcode `2001` reached trailer 1-2, with drive feedback,
belt-cell occupancy, trigger sequence 1, and a matching camera result all
observed. Only `plc` and `drives` were running. The HMI path through `scada`
remains untested. `deploy/README.md` explains the scanner replay gap and the
duplicate result observed on a later same-seed reset.

The updated live test was run again on the same two guests. After its explicit
seed transition, serial 1 received barcode `6001` and loaded at trailer 2-3;
the script exited 0. A post-run read showed sorter run off, all three lane
enables on as they were before the test, and `master_seed` restored to 137.
Two local cleanup checks also force a measurement failure and a partial setup
failure, and verify that the initial lane values are restored in both cases.
