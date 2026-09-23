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

At startup and on operator reset, the PLC sends all three scanners a reset
request and holds package movement until all three acknowledge. The exchange
is specified in `deploy/README.md`. When a package moves into cell 10, the
tunnel request sequence at `%QW118`
increments and `%QW119` carries its serial. The seed and no-read rate are
`%QW120..121`. The scanner is expected to return the matching sequence,
barcode, status, length, and run nonce at `%IW158..161` and `%IW164`. A valid barcode replaces the
serial in the belt cell and determines the divert target. The package then
moves onto an outbound belt (`%QW380..399` for outbound 1), whose speed
feedback enters `%IW132`. Trailer counters start at `%QW222`.

Lane 2 uses drive registers `%QW103..105`, feedback `%IW114`, tunnel request
`%QW122..125`, response `%IW169..172` plus `%IW175`, and belt `%QW280..299`.
Lane 3 uses `%QW106..108`, `%IW123`, `%QW126..129`, `%IW180..183` plus
`%IW186`, and `%QW300..319`.
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
result, then checks all three reset ACKs, induction, cell movement, same-scan
tunnel trigger, rejection of a stale result with the reused sequence, barcode
routing, and one correct trailer load. It checks that lanes 2 and 3
also issue an arrival trigger. It does not emulate network timing or prove
the guest services are wired to these registers.

## Guest verification

Use `virsh -c qemu:///system console plc` and
`virsh -c qemu:///system console drives` from the host terminal. Each console
has a login prompt; enter credentials there, then exit with Ctrl+]. The PLC
guest's old active `Sorter` file matched the repository version before commit
`f64a17f` byte for byte (SHA-256 `abb7579d...`). The previous program is
`f64a17f.st` with SHA-256 `624a693f...`. The reset-handshake source is
`scanner_reset.st` with SHA-256
`60797245b525eac761a59ca0f4091e012a48d5e27256ac66212dd2382aa11189`.
OpenPLC's `compile_program.sh` succeeded and `active_program` names
`scanner_reset.st`. The old program remains available. All three scanner
services on `drives` use repository `devices/scanner.py` (SHA-256
`da75c4414dd219ac477314a018572fd5ddc9ace45ad0a95f91ee7aef351d79b6`).

Copy `tests/live_first_package.py` to the PLC guest's home directory and run
`~/OpenPLC_v3/.venv/bin/python3 ~/live_first_package.py` there. The script
reads the PLC, induct drive 1, and camera 1 over Modbus, then checks the first
serial from induction through barcode and trailer load. It records the initial
operator enable bits, setpoints, and seed before reset, stops the sorter, and
restores those exact values even if the check fails. It runs two consecutive
cycles with seed 137 and requires the first barcode and trailer to match.

On 2026-09-23, with only `plc` and `drives` running, both cycles completed all
seven observed path checks. Run tokens were 2 and 3. Both first barcodes were
`6001` and both loaded at trailer 2-3. The script printed
`same-seed replay: first barcode and trailer match` and exited 0. Last sampled
package counters differed because the sorter kept running until cleanup after
the first trailer observation. A post-run read showed run `False`, all seven
operator enables `True`, setpoints `[200,200,200,233,233,233,2,14,14,14,30]`,
seed `137`, and program identity `24111`. All three scanner services were
active. Local checks: `bash tests/run_first_package.sh` passed; five Python
unit tests passed (three scanner reset cases and two cleanup failure cases).
The HMI path through `scada` remains untested.

The live command's output was:

```text
{"last": {"barcode": 5004, "drive_feedback": 200, "first_barcode": 6001, "inducted": 9, "response_nonce": 2, "result_seq": 2, "scan_code": 5004, "serial": 4, "status": 0, "trailer": 6, "trailers": [0, 0, 1, 0, 1, 1, 0, 0, 0], "trigger": 2}, "observed": {"barcode": true, "belt_cell": true, "camera_result": true, "drive_feedback": true, "inducted": true, "sensor_trigger": true, "trailer_load": true}}
{"last": {"barcode": 5002, "drive_feedback": 200, "first_barcode": 6001, "inducted": 3, "response_nonce": 3, "result_seq": 2, "scan_code": 5002, "serial": 2, "status": 0, "trailer": 6, "trailers": [0, 0, 0, 0, 0, 1, 0, 0, 0], "trigger": 2}, "observed": {"barcode": true, "belt_cell": true, "camera_result": true, "drive_feedback": true, "inducted": true, "sensor_trigger": true, "trailer_load": true}}
same-seed replay: first barcode and trailer match
```
