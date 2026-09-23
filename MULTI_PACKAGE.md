# Lane 1 multi-package external sorting

## Placement, identity, and capacity

The scanner, PLC, XLe, ASX, PLC command, and physical belt outcome remain in
that order. Tunnel 1 and the six drives are on `drives` (L1). The PLC is on L1;
XLe and simulated ASX run on `scada` (L2). The existing `fw` rule permits
SCADA-to-PLC Modbus. ASX listens only on SCADA loopback and has no PLC client.
No bridge address, firewall rule, or isolated network was changed. The four
required VMs each reserve 768 MiB, totaling 3 GiB on the 16 GB host.

Set coil 914 (`xle_mode`) and new coil 915 (`xle_multi`) after a completed
scanner reset, while stopped. With 915 off, the existing one-package mode and
500..512 command/status registers retain their behavior. With both on, lane 1
has **two PLC slots**. The PLC reserves a free slot at induction and stops
inducting when both are occupied, including slots holding terminal outcomes.
XLe releases a slot only after reading and logging its terminal result. A
released slot can be reused without resetting the run; the PLC gives the next
package a new token. `xle.py --packages 2` runs a finite verification batch;
`--packages 0` keeps serving packages through the same two bounded slots.
The continuous XLe option was not exercised live; PLC slot reuse was tested
locally through a third package with no reset.

The stable package identity is `(scanner_run_nonce, slot_token,
package_serial, scanner_sequence)`. XLe's `package_id` is
`l1-NONCE-TOKEN-SERIAL` and its ASX request has a separate UUID `request_id`.
The barcode is data, never the sole identity. PLC lane 1 cells and all three
outbound belts carry the token alongside barcode and route. At each scanner
read, divert, collision, door, no-home, or recirculation event, the token
selects the same slot. Terminal states stay visible until XLe releases them.

## Register and message contract

PLC Modbus coil 915 enables multi mode. XLe writes the payload to holding
registers 520..527, then commits it by changing register 528. The PLC echoes
the processed command ID at 554 and returns status at 529. XLe waits up to
1.5 seconds for that specific ACK before using the shared mailbox again.

| Address | Direction | Meaning |
| ---: | --- | --- |
| 520 | XLe → PLC | operation: 1 route, 2 release terminal slot |
| 521 | XLe → PLC | slot index 0 or 1 |
| 522..525 | XLe → PLC | token, serial, scanner sequence, barcode |
| 526..527 | XLe → PLC | trailer 1..9 (route only), run nonce |
| 528 | XLe → PLC | changed command ID, written last |
| 529 | PLC → XLe | 1 accepted, 2 rejected, 3 terminal slot released |
| 554 | PLC → XLe | command ID whose status is at 529 |
| 530..541 | PLC → XLe | slot 0 row |
| 542..553 | PLC → XLe | slot 1 row |

Each 12-word row is `[token, serial, scanner_sequence, barcode, state,
destination, actual_trailer, reason, scan_tick, accept_tick, first_divert_tick,
accepted_command_id]`. States are 0 free, 1 inducted, 2 scanned and awaiting a
decision, 3 route accepted, 4 moved to an outbound belt, 5 loaded, 6
recirculated, 7 failed. Reasons are 0 none, 1 no decision, 3 collision, 4 no
home, 5 wrong door. A command is rejected if any identity field mismatches,
its destination is invalid, the slot is not awaiting a decision, or the token
has passed cell 13. A duplicate or late command cannot alter an accepted
route or another slot. XLe matches ASX replies by both package and request ID
and logs a recirculation fallback on unknown, invalid, delayed, mismatched,
or PLC-rejected decisions. The PLC will not infer a route from barcode digits
in multi mode.

ASX reads `services/sort_plan.json` on each request. It first checks the
editable `barcode_serial` key, such as `6001:2`, then the `barcodes` default.
This allows two packages bearing `6001` to receive different plans. The
`delays_ms` table in the test plan delays one package without blocking the
other HTTP request. ASX uses a threaded loopback server; it never writes PLC
registers. The `SORTER_REPEAT_BARCODE=1` scanner option is a test-only fixture
that emits the same barcode on later parcels with duplicate status. The
deployed scanner systemd unit has no such option.

## Host verification

`bash tests/run_first_package.sh` passed the default three-lane package,
six scanner reset fault cases, the original valid/unknown/stale/failed
one-package external cases, and four multi cases. The multi output was:

```text
different: tokens 1/2 barcodes 6001/5002 outcomes 5/5 trailers 2/5 scan-to-divert 3/3 scans
repeat: tokens 1/2 barcodes 6001/6001 outcomes 5/5 trailers 2/5 scan-to-divert 3/3 scans
timeout: tokens 1/2 barcodes 6001/5002 outcomes 5/6 trailers 2/0 scan-to-divert 3/3 scans
reuse: released slot accepted token 3; stale token 1 rejected; trailer 8 loaded without reset
```

The compiled PLC tests also reject a wrong token and a second command for an
already routed slot. `python3 -m unittest discover -s tests -p 'test_*.py'`
passed 15 tests, including ASX correlation, command commit order, scanner
reset, and the opt-in duplicate fixture.

## Live VM verification

The final PLC and XLe sources were copied over their existing serial
consoles; the PLC guest compiled successfully and its Modbus runtime was
restarted. `tests/live_xle_multi.py` ran on SCADA with `plc`, `drives`, `fw`,
and `scada` only. It reset with seed 137, enabled lane 1 multi mode, allowed
two inductions, then raised the interval to prevent a third. It read both
terminal rows and all nine trailer counters over the actual SCADA-to-PLC
Modbus path. XLe and ASX JSON events used the same package and request IDs.
The final outputs from the ACK-enabled version were:

| Case | Package ID | Scanner barcode | ASX / XLe action | PLC result | Scan → first divert | Command → first divert |
| --- | --- | ---: | --- | --- | ---: | ---: |
| Different, first | `l1-2-1-1` | 6001 | route 2, command 1 | state 5, trailer 2, reason 0 | 3200 ms | 3000 ms |
| Different, second | `l1-2-2-2` | 5002 | route 5, command 2 | state 5, trailer 5, reason 0 | 3200 ms | 3000 ms |
| Timeout, first | `l1-3-1-1` | 6001 | route 2, command 5 | state 5, trailer 2, reason 0 | 3300 ms | 3000 ms |
| Timeout, second | `l1-3-2-2` | 5002 | ASX answered at 1.2 s; XLe timed out at 0.8 s | state 6, recirculated, reason 1, no command | 3300 ms | — |
| Repeat, first | `l1-4-1-1` | 6001 | route 2, command 8 | state 5, trailer 2, reason 0 | 3300 ms | 3100 ms |
| Repeat, second | `l1-4-2-2` | 6001 | `6001:2` plan → trailer 5, command 9 | state 5, trailer 5, reason 0 | 3300 ms | 3100 ms |

For both successful two-trailer runs, Modbus trailer counters were
`[0,1,0,0,1,0,0,0,0]` and recirculation was zero. In the timeout run they
were `[0,1,0,0,0,0,0,0,0]` with recirculation one. The delayed ASX response
was logged as late delivery after the XLe fallback; no PLC command was sent
for that package. The two package IDs and scanner sequences remained distinct
even when both barcodes were `6001`. The measured 0.8-second decision timeout
fits the observed 3.2–3.3-second scanner-to-divert window at these speeds.
This is a PLC scan-time measurement at the tested setpoints, not a guarantee
at every belt speed.

After the multi runs, the deployed one-package external case loaded trailer
2 with scanner barcode 6001, state 3/reason 0, and 100 ms scan-to-accept.
The default-route live runner replayed twice with seed 137: first barcode
6001 and trailer 6 matched. Those baseline checks ran before the final
command-ACK-only update; the unchanged legacy behavior also passed the final
host suite.

An early multi live attempt saw a transient PLC Modbus read failure under
heavy polling. It was not counted as a pass: both packages recirculated.
XLe now retries bounded reads and both observers poll at 100 ms. All final
live cases above passed. The runner restored original enable coils and
setpoints after each case. A final Modbus read showed sorter run false,
coils 881..887 true, external coils 914/915 false, setpoints
`[200,200,200,233,233,233,2,14,14,14,30]`, seed 137, and both slots free.
All three deployed scanner services were active; the temporary repeat
fixture was stopped. The remaining simulated parts are camera/barcode data,
ASX decisions, belts, diverters, and trailers inside the VM model. The
Modbus exchanges and the cross-zone XLe/PLC path used the real VM interfaces.
