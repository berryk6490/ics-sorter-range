# Lane 1 independent plant slice

## Ownership and placement

The original `Sorter.st` cell arrays (`ib1`, `ob1`..`ob3`) advance from VFD
feedback inside OpenPLC. The tunnel trigger, divert, and trailer count then
come from those same arrays. That remains the default legacy mode, including
all three lanes. In physical mode (coil 918), `devices/plant.py` on the existing
**drives** VM owns lane 1 package position. It polls the real simulated VFD
feedback registers and sends serialized photoeye events to the PLC. The PLC
uses tunnel arrival to trigger scanner 1, publishes a token-bound actuator
command in its existing slot row, and waits for plant turnout and trailer
confirmation. It does not increment a trailer counter when it merely selects a
destination. XLe and ASX still see only the PLC/scan and sort-plan interfaces;
neither reads plant state.

No VM, interface, bridge address, route, or firewall rule was added. The plant
uses drives → PLC Modbus on existing L1, and reads the four existing VFDs at
`10.10.1.21`, `.24`, `.25`, `.26` register 5 (`speed_fb`). The scanner on `.27`
keeps its existing request/response map. XLe and ASX remain on SCADA, with ASX
bound to loopback. The four running VMs (plc, drives, fw, scada) are 768 MiB
each, 3 GiB total guest allocation on the 16 GB host.

Plant position is in cell equivalents. At 1750 rpm the model moves 10 cells
per second; it uses measured elapsed time and actual VFD rpm, so ramping or a
stopped VFD changes motion. Defaults are 60 cm package length and 100 cm clear
spacing with 50 cm per cell; `--length-cm` and `--spacing-cm` configure them.
The plant queues induction requests until that physical gap exists. The tunnel
photoeye is at induct cell 10; the three divert points are at 14, 16, 18.
Outbound packages enter at cell 2; trailer confirmation points are 12, 15,
18. The plant latches the PLC's slot destination at the first divert point.
A late ASX answer cannot change that package's path. If no timely command
exists, the package passes the induction tail and recirculates. The PLC's
existing two-slot bound and XLe journal identity remain in force.

Physical mode requires XLe multi mode and lanes 2 and 3 disabled. The PLC
stops the sorter with `plant_fault=2` if this configuration or run identity is
wrong. This avoids mixing plant lane 1 motion with legacy outbound cell motion.
The outbound VFDs may still run; lanes 2 and 3 are available in legacy mode.

## Modbus signal contract

All addresses are absolute OpenPLC holding registers. The plant writes event
payload 580..584, then sequence 585 last. It holds that event until the PLC
echoes sequence at 586. The PLC checks active epoch, scanner run nonce, slot
token, and serial before processing an event. It acknowledges rejected events
too, latches fault 3, and stops. Scanner, XLe, and existing slot maps are
unchanged.

| Address | Owner | Meaning |
| --- | --- | --- |
| Coil 918 | operator/PLC | `plant_mode`; false selects the original PLC cell model |
| 574..576 | PLC | induction request commit ID, slot token, serial |
| 530..553 | PLC | existing two slot rows; state 3, destination, command ID form the token-bound divert command read by plant |
| 580..585 | plant | photoeye event type, token, serial, actual outbound/trailer, position ×10, commit sequence |
| 586 | PLC | event sequence ACK |
| 587..589 | plant | active XLe epoch low/high and scanner run nonce, required before motion |
| 590 | plant | changing heartbeat; PLC bounds the gap at 30 scans |
| 591 | PLC | fault: 0 ready, 1 unavailable, 2 identity/configuration, 3 invalid event (latched until reset) |
| 592 | PLC | latched count of failed physical confirmations, cleared by reset |

Event types: 1 induction accepted, 2 tunnel photoeye, 3 divert photoeye with
actual outbound 0..3, 4 trailer confirmation with actual door 1..9, 5
recirculation, 6 failed confirmation. The PLC changes `inducted_ct` only on 1,
triggers scanner 1 only on 2, and sets slot `divert_tick` on 3. It changes a
trailer counter and terminal loaded outcome only on 4. Event 5 gives terminal
recirculation reason 1. Event 6 gives failed outcome reason 4, raises no-home,
and increments register 592 without loading a trailer. The HMI exposes the
plant fault and a persistent “LANE 1 FAILED CONFIRMATION” alarm from 592.

The plant cannot recover exact positions after a process crash during an
active run. On startup with coil 918 already set, it clears its run identity
and refuses to run until an operator stops and resets the PLC. A lost Modbus
connection is retried; loss of heartbeat stops the sorter. This active-run
restart path is fail-closed by design but has **not** had a live crash test.

## Deployment and verification

`devices/plant.py` and `deploy/systemd/sorter-plant.service` were installed on
drives as `/home/kevin/plant.py` and
`/etc/systemd/system/sorter-plant.service`; the unit is active and enabled.
PLC `Sorter.st` was copied to
`/home/kevin/OpenPLC_v3/webserver/st_files/xle_sorter.st`, compiled with
`./scripts/compile_program.sh xle_sorter.st` (finished successfully), and
OpenPLC restarted with Modbus on 502. SCADA HMI and OPC UA sources were copied
to their existing guest paths and their units restarted. Transfers used the
serial consoles; the isolated bridges stayed unchanged.

Host checks:

```text
bash tests/run_first_package.sh                    PASS (legacy three-lane, scanner reset, XLe, heartbeat, new plant PLC test)
python3 -m unittest discover -s tests -p 'test_*.py'  29 tests, OK
python3 -m py_compile devices/plant.py scada/opcua_server.py scada/hmi_ua.py tests/live_plant.py  PASS
```

Live tests used `tests/live_plant.py` on SCADA, actual PLC and VFD Modbus
interfaces, scanner 1, XLe, and ASX. The test saved full JSON output in
`/tmp/plant-{one,two,failure}-final.out` on SCADA. Rows below are direct PLC
Modbus slot values (`scan/accept/divert` are PLC scans, about 100 ms each):

| Case | Package ID suffix | Barcode | ASX destination | PLC scan/accept/divert | PLC outcome | Trailer counters | Max occupied slots |
| --- | --- | ---: | ---: | --- | --- | --- | ---: |
| one | nonce 2 / token 1 / serial 1 | 6001 | 2 | 110/113/143 | loaded, actual 2, reason 0 | trailer 2 = 1 | 1 |
| two, first | nonce 3 / token 1 / serial 1 | 6001 | 2 | 110/113/143 | loaded, actual 2, reason 0 | trailer 2 = 1 | 2 |
| two, second | nonce 3 / token 2 / serial 2 | 5002 | 5 | 140/142/188 | loaded, actual 5, reason 0 | trailer 5 = 1 | 2 |
| failed confirmation | nonce 4 / token 1 / serial 1 | 6001 | 2 | 110/114/143 | failed, actual 0, reason 4 | all nine = 0; no-home = 1 | 1 |

The two-package plant log shows token 2 induced at 17:14:05, while token 1's
trailer confirmation was at 17:14:24. Sensor order for each token was
`induct → tunnel → divert → trailer`: token 1 used actual outbound 1/trailer
2; token 2 used outbound 2/trailer 5. The injected failure log shows token 1
`induct → tunnel → divert outbound 1 → failed confirmation`, with no trailer
photoeye event. XLe and ASX JSON events in the live result carry the same full
package ID and request ID. XLe recorded scanner-to-divert times of 3.3 s for
token 1 and 4.8 s for token 2. A final smoke run on the final enabled plant
service also loaded barcode 6001 to trailer 2 (nonce 5).

The final live runner also polled the plant event registers directly and
asserted `(type, token, serial, actual)` for every event. In a second
two-package run (nonce 6) the sequence was
`(1,1,1,0), (1,2,2,0), (2,1,1,0), (2,2,2,0), (3,1,1,1),
(3,2,2,2), (4,1,1,2), (4,2,2,5)`; PLC rows again loaded barcode 6001 to 2
and 5002 to 5, with both slots simultaneously occupied. In the repeated
failure run (nonce 7) the event sequence was `(1,1,1,0), (2,1,1,0),
(3,1,1,1), (6,1,1,0)` and the PLC row was barcode 6001, destination 2,
actual 0, reason 4. These runs passed `tests/live_plant.py`'s scanner,
ASX request, XLe command, PLC outcome, and HMI assertions. The failure
injection was again removed and the normal enabled plant service restored.

The rendered Firefox HMI showed “NO-HOME” and “LANE 1 FAILED CONFIRMATION”,
connected to OPC UA, with zero loaded trailers; see
[`tests/artifacts/plant-failed-confirmation.png`](tests/artifacts/plant-failed-confirmation.png).
Each live runner restores seed, setpoints, lane enables, XLe mode, and plant
mode in `finally`, and leaves master run false. The temporary failure-injection
process was stopped and the normal plant unit restored. Existing scanner, VFD,
HMI, and OPC UA services were active at final guest verification; SCADA and fw
were then shut down to restore the VM set present at the start. PLC and drives
remain running, with the normal plant unit active. The HMI belt-cell drawing is still fed by
the legacy PLC arrays, so it appears empty in physical mode; sensor and
outcome alarms/counters are live. The new physical movement is lane 1 only;
legacy lanes and their cell movement are retained.
