# First package and tunnel sensor slice

`Sorter.st` is the current three-lane PLC program. `gen_sorter.py` is an
identical copy of Structured Text despite its historical `.py` name;
`sorter.st` is an older version with scanner behavior still inside the PLC.
The nearby `../sorter_project/plc.xml` is an older one-lane Beremiz project.
The repository does not contain the drive, scanner, firewall, or HMI service
source. Their deployed versions and OpenPLC runtime configuration still need
inspection inside the guests.

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
`%IW132`, `%IW141`, and `%IW150`. The exact network mapping of these located
variables must be confirmed in the running OpenPLC configuration.

The four VM definitions each reserve 786432 KiB (768 MiB) and two vCPUs.
All four together reserve 3 GiB. `fw` connects to `ics-l1`, `ics-l2`, and
`ics-l3`; `plc` and `drives` connect to `ics-l1`; `scada` connects to `ics-l2`.
The smallest live PLC/drive/scanner test should need only `plc` and `drives`
(1.5 GiB) if their scanner and drive services are running. `fw` is needed for
access across zones, and `scada` is needed only to verify the HMI path.

## Host-side check

Run `bash tests/run_first_package.sh`. It compiles `Sorter.st` with the
installed matiec compiler, then executes the generated PLC code against its
actual located variables. The test supplies drive feedback and one scanner
result, then checks induction, cell movement, same-scan tunnel trigger,
barcode routing, and one correct trailer load. It checks that lanes 2 and 3
also issue an arrival trigger. It does not emulate network timing or prove
the guest services are wired to these registers.

## Guest verification once console login is available

Use `virsh -c qemu:///system console plc` and
`virsh -c qemu:///system console drives` from the host terminal. Each console
has a login prompt; enter credentials there, then exit the console with
Ctrl+]. On `plc`, inspect the OpenPLC runtime version, running program,
Modbus devices, and located-variable mapping. On `drives`, inspect the VFD
and scanner services and their register map. Confirm the deployed program
matches `Sorter.st` before any live test. Then reset the sorter, set the run
bit, and observe `%QW220`, `%QW260..279`, `%QW118..119`, `%IW158..161`, and
`%QW222` as the first package passes. This live step remains unverified until
guest login and runtime access are available.
