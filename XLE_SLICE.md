# One-package sort decision slice

## Placement and boundaries

The existing isolated networks remain unchanged. `plc` and `drives` are on
L1 (`10.10.1.0/24`); `scada` is on L2 (`10.10.2.0/24`); `fw` routes between
them and also has L3 (`10.10.3.0/24`). The inspected `fw` nftables forward
chain has default drop and an explicit `10.10.2.10 -> 10.10.1.10:502` rule.
It allows SCADA to reach PLC Modbus, but has no SCADA-to-scanner rule. The
scanner result therefore crosses the existing scanner-to-PLC mapping before
XLe reads it from the PLC. The other explicit L2 rule permits SCADA to read
six drive Modbus endpoints. No firewall or bridge rule was changed.

The smallest placement is two Python processes on the existing `scada` VM:
`services/xle.py` connects through `fw` to the PLC on port 502; `services/asx.py`
listens only on SCADA loopback port 8089. ASX has no PLC client. The VM
definitions allocate 768 MiB each to `plc`, `drives`, `fw`, and `scada`, so
these four guests total 3 GiB of configured RAM, within the 16 GB host limit.
`fw` and `scada` are needed for this L2-to-L1 test; the browser fault check
also uses them. The active HMI and OPC UA services were left intact.

## Contract

The existing scanner tunnel 1 request uses PLC `%QW118..121`: sequence,
package serial, seed, and no-read rate. Its reply appears at PLC input
`%IW158..164`: sequence, barcode, status, dimensions, and run nonce. XLe
accepts a good/oversize/duplicate result only when reply sequence, nonce,
barcode, and barcode serial agree with the PLC's current trigger and scanned
package. This release enables lane 1 external mode with Modbus coil 914
(`%QX114.2`); it is off after reset. When on, lane 1 inducts **one package per
reset**. Lanes 2 and 3 retain their original behavior; the live runner
temporarily disables their enables to keep the test to one package.

XLe gives that package a unique `package_id` (`l1-NONCE-SERIAL-UUID`) and a
separate UUID `request_id`. It POSTs this JSON to ASX `/sort-plan`:

```json
{"package_id":"l1-4-1-b8f5f1cb","request_id":"6d3bc4f2-7054-4139-95b1-848c589f1dd4","barcode":6001,"scanner_sequence":1,"scanner_run_nonce":4,"lane":1}
```

ASX reads the editable `services/sort_plan.json` at each request and echoes
both IDs. A listed barcode returns `{"decision":"route","destination":2}`
with those IDs. An unlisted barcode returns `no_decision` and null destination.
The plan maps full barcode strings to trailer numbers 1..9; it does not infer
a route from the barcode's leading digit. XLe waits at most 0.8 seconds for
ASX and rejects a mismatched request/package ID, invalid destination, or late
response. In each case it logs `safe_fallback` with an explicit reason and
`action=recirculate`; it issues no sort command. The PLC retains a zero route
and reports recirculation at the end of the induct belt. Thus an unavailable
ASX does not silently select a trailer.

For a valid decision, XLe atomically writes holding registers 500..503:

| Address | Field | Owner |
| ---: | --- | --- |
| 500 | `command_id` (1..30000, changed for each instruction) | XLe |
| 501 | scanner barcode / package identity | XLe |
| 502 | destination trailer 1..9 | XLe |
| 503 | current scanner run nonce | XLe |
| 504 | echoed command ID | PLC |
| 505 | 0 idle; 1 accepted; 2 induct diverted; 3 loaded; 4 failed; 5 recirculated | PLC |
| 506 | reason: 0 none; 1 no decision; 2 invalid/late command; 3 collision; 4 no home; 5 wrong door | PLC |
| 507 | actual trailer 1..9, or 0 | PLC |
| 508 | current scanned barcode | PLC |
| 509 | current run nonce | PLC |

The PLC accepts a changed command ID only when the barcode and nonce match
the currently scanned lane 1 package, the destination is in range, and that
package is still upstream of the first divert at cell 14. It stores the
destination separately from barcode identity. The selected belt and door use
that stored destination; the loaded outcome identifies the actual trailer.
XLe logs the PLC state and failure reason under the same package and command
IDs. A late command is rejected with state 4/reason 2. The one-package limit
prevents a second scan from replacing the pending command's identity; a
production multi-package controller needs per-package command storage.

## Live browser fault check

Before changing the PLC, tunnel 2's scanner service was stopped and a PLC
reset was requested. The rendered Firefox HMI showed `SCANNER TUNNEL 2 RESET
FAULT` and `ACKNOWLEDGE`; the PLC fault mask was 2, ACK mask 5, wait 200,
inducted 0, and run false. After the scanner service restarted, the alarm
remained. Clicking the rendered Acknowledge button changed the display to
`SCANNER TUNNEL 2 RESET FAULT — ACKNOWLEDGED` and `RETRY RESET`.
Clicking Retry cleared the alarm only after all three scanner ACKs arrived.
The three screenshots are
[`fault`](tests/artifacts/scanner-fault.png),
[`acknowledged`](tests/artifacts/scanner-acknowledged.png), and
[`cleared`](tests/artifacts/scanner-cleared.png). The localhost-only
`tests/serial_hmi_proxy.py` forwarded the actual guest HMI through the
existing serial console to headless Firefox; it added no bridge address.
Tunnel 2 service was restored and active; PLC reset state returned to 0.

## Tests and actual output

`bash tests/run_first_package.sh` passed the original three-lane simulation
and six scanner-fault cases. Its new compiled PLC cases printed:

```text
valid: barcode 6001 command 1 destination 2 trailer 1-2 loaded
unknown: no command, explicit recirculation reason 1
stale: wrong-run command rejected
failed: command 1 no-home outcome reason 4
```

`python3 -m unittest discover -s tests -p 'test_*.py'` passed 12 tests,
including valid plan, unknown package, delayed ASX, stale IDs, scanner reset,
and live-test cleanup checks. The stale/failed/unknown cases above were
compiled PLC simulation and Python tests, not separate live VM fault runs.

With SCADA and firewall shut down again, the existing
`tests/live_first_package.py` ran twice on the deployed PLC and drives VMs
with external mode off. Both cycles observed drive feedback, cell movement,
scanner trigger/result, and trailer load. Both first barcodes were `6001`,
both trailers were 2-3 (`trailer=6`), and it printed `same-seed replay: first
barcode and trailer match`. This confirms the default route remains active.

The final live run used all four guests. The test runner reported:

```json
{"actual_trailer": 2, "command_id": 3, "inducted": 1, "package": 6001, "reason": 0, "recirc": 0, "run_nonce": 4, "scan_seen": true, "state": 3, "trailer_counts": [0, 1, 0, 0, 0, 0, 0, 0, 0]}
```

XLe's event log linked the same package throughout:

```jsonl
{"barcode": 6001, "event": "scan", "lane": 1, "package_id": "l1-4-1-b8f5f1cb", "request_id": "6d3bc4f2-7054-4139-95b1-848c589f1dd4", "scanner_run_nonce": 4, "scanner_sequence": 1}
{"destination": 2, "event": "asx_decision", "package_id": "l1-4-1-b8f5f1cb", "request_id": "6d3bc4f2-7054-4139-95b1-848c589f1dd4"}
{"barcode": 6001, "command_id": 3, "destination": 2, "event": "plc_command", "package_id": "l1-4-1-b8f5f1cb", "run_nonce": 4}
{"actual_trailer": 2, "command_id": 3, "event": "plc_outcome", "package_id": "l1-4-1-b8f5f1cb", "reason": 0, "state": "loaded"}
```

The initial live attempt exposed a reset timing race in the test runner: it
set external mode before the PLC acted on reset, so reset disabled that mode.
After the runner waited for the reset transition, the next run reached trailer
1-2, but a second package was inducted. The one-package guard and a cleanup
wait fixed that test condition; the final run above recorded exactly one
induction. These attempts were investigated, not counted as passes.

After the final run, PLC run was false, coil 914 was false, operator enable
coils 881..887 were all true as initially observed, setpoints 200..210 were
`[200,200,200,233,233,233,2,14,14,14,30]`, and seed 247 was 137.
The simulated ASX plan and scanner/VFD devices ran as guest processes; the
scanner-to-PLC device exchange, SCADA-to-PLC Modbus command, PLC belt/divert
logic, and PLC outcome read used the actual VM interfaces. No physical
scanner, actuator, trailer, or external ASX system was involved.

## Reproduce

With `plc`, `drives`, `fw`, and `scada` running, copy `Sorter.st` to the
PLC `webserver/st_files/xle_sorter.st`, compile with
`./scripts/compile_program.sh xle_sorter.st`, and start OpenPLC Modbus using
the guest's existing runtime configuration. Copy `services/` to SCADA
`~/sorter-services/`. In SCADA's shell run `python3 ~/sorter-services/asx.py`
and `~/opcua/bin/python ~/sorter-services/xle.py` in separate processes.
On PLC run `~/OpenPLC_v3/.venv/bin/python3 ~/live_xle_package.py` after
copying `tests/live_xle_package.py` there. The runner restores the operator
settings and leaves the sorter stopped even after failure.
