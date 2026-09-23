# Lane 2 independent plant extension

## Topology and ownership

Legacy PLC cells retain all three induct lanes. Their merge points on each
outbound are cells 2 (lane 1), 5 (lane 2), and 8 (lane 3). Plant mode now
simulates lanes 1 and 2 on the existing drives VM; lane 3 remains disabled in
plant mode and retains its legacy path in default mode. The plant reads speed
feedback from induct VFDs `10.10.1.21` and `.22` and outbound VFDs `.24`–`.26`.
`deploy/systemd/sorter-plant.service` now orders the plant after both induct
VFD units and identifies both modeled lanes; the existing drives guest unit
was updated and systemd reloaded without stopping the normal service.
The PLC owns scanner requests, slot-bound commands, outcomes, and counters.
XLe reads PLC slots and asks loopback ASX for a decision. Neither XLe nor the
HMI reads the plant process directly. The HMI receives PLC-validated telemetry
via the existing OPC UA server.

The two PLC slots are the total in-flight capacity across both plant lanes.
The PLC alternates induction priority when both lanes' intervals have expired;
lane 1 wins the first tie after reset. Requests are serialized until the plant
acknowledges induction. Each lane has a one-package waiting position at its
divert gate. At a shared outbound, the plant admits the first ready parcel
only if every package already on that outbound is at least package length plus
configured clear spacing from the entry point. The default threshold is
`1.2 + 2.0 = 3.2` cell equivalents. Simultaneous arrivals are ordered lane 1
then lane 2. A waiting parcel remains at its gate and does not emit a divert
photoeye event until admitted. Once admitted, both use the same outbound VFD
feedback; they cannot overlap. The model holds rather than dropping a blocked
parcel, and the two-slot cap bounds the queue.

## Protocol additions

The existing event, slot, and two-row telemetry registers remain at their
previous addresses. Added holding registers:

| Register | Owner | Meaning |
| --- | --- | --- |
| 577 | PLC | induction request lane (1 or 2), written before commit at 574 |
| 578 | plant | event lane, written before event commit at 585 |
| 644..645 | PLC | lane identity for slot rows 530..541 and 542..553 |
| 646 | PLC | lane of most recent failed confirmation (0 if none) |

The PLC rejects an event whose lane differs from its occupied token/serial
slot and latches plant fault 3. XLe refuses to route an occupied scanned slot
without a valid lane, includes the lane in its package ID and ASX request,
and checks the lane again before command or release. The outcome journal keeps
the existing epoch/nonce/token/serial/scan-sequence key because the global
token already distinguishes both lanes. The PLC trigger/response mapping for
tunnel 2 remains `QW122..125`/`IW169..175`; plant tunnel arrival initiates it.

Telemetry row `belt=1` means lane 1 induct, `belt=5` means lane 2 induct,
and `belt=2..4` means outbound 1..3. OPC UA adds `Process/Plant/SlotN/Lane`
beside its existing row, status, and age. The HMI builds identity
`l<lane>-<epoch>-<nonce>-<token>-<serial>` and uses only the validated PLC
coordinate. Stale or unavailable telemetry freezes the last displayed
position.

## Verification and physical limits

The PLC harness includes a two-lane request, tunnel 2 response, two ASX-bound
commands to one outbound, two physical confirmations, and an event with a
wrong lane identity. The Python model tests merge waiting, clearance, final
outcomes, and a lane 2 failed confirmation. Legacy tests remain in the same
`tests/run_first_package.sh` and Python regression suites.

This is still a deterministic kinematic model. It does not simulate a real
merge actuator, parcel deformation, belt slip, skew, bouncing photoeyes,
mechanical jams, or recovery of exact positions after a cold plant-process
restart. Lane 3 remains PLC-internal in legacy mode. A plant restart with an
active run remains fail-closed: stop master, drop plant mode, reset the PLC and
scanner handshake, restore operator settings, and begin a fresh run.

### Live results (2026-09-23)

All four existing guests (`fw`, `plc`, `drives`, `scada`) were used. Their
configured memory is 768 MiB each, 3 GiB total. No bridge, firewall, or VM
definition changed. The drives plant and scanner services, PLC Modbus server,
SCADA OPC UA/HMI, and temporary XLe/ASX processes communicated through the
existing guest interfaces. The test runner read PLC Modbus rows, photoeye
events, lane registers, trailer counters, and OPC UA-backed HMI API; it also
matched XLe and ASX events by package ID. The plan for the shared-outbound
cases is editable in `tests/lane2_shared_plan.json`.

| Run | Package ID; scan; ASX destination; PLC actual/reason | Trailer counters | Other evidence |
| --- | --- | --- | --- |
| Distinct outbounds | `l1-1-2-1-1`; 6001; 2; 2/0. `l2-1-2-2-2`; 5002; 5; 5/0 | trailer 2=1, trailer 5=1 | both PLC slots occupied; tunnel 2 scanner response and four ordered plant events per package |
| Shared outbound | `l1-1-5-1-1`; 6001; 2; 2/0. `l2-1-5-2-2`; 5002; 3; 3/0 | trailer 2=1, trailer 3=1 | lane 2 held at its merge gate; minimum simultaneous outbound spacing 3.2 cell equivalents; both row telemetry identities observed |
| Failed confirmation | `l1-1-2-1-1`; 6001; 2; 2/0. `l2-1-2-2-2`; 5002; 3; 0/4 | trailer 2=1; trailer 3=0 | lane 2 emitted failure event 6; PLC register 646=2; HMI showed “LANE 2 FAILED CONFIRMATION” and NO-HOME |
| Recovery batch after plant restart/reset | `l1-1-5-1-1`; 6001; 2; 2/0. `l2-1-5-2-2`; 5002; 3; 3/0 | trailer 2=1, trailer 3=1 | same shared path and 3.2 cell clearance verified again |
| Lane 1 single-package regression | `l1-1-10-1-1`; 6001; 2; 2/0 | trailer 2=1 | existing `live_plant.py one` completed after the lane 2 fault/recovery work |

The shared run's PLC ticks (100 ms each) were lane 1 scan/accept/divert
`169/172/225`, lane 2 `170/173/316`. Thus the decision was accepted 5.3 s
before lane 1's divert and 14.3 s before lane 2's merge-admitted divert.
The failed-confirmation run's lane 2 ticks were `169/172/316`; a PLC
destination of 3 did not increment trailer 3 without a confirmation event.

For the final active-run interruption, the observer used slower 60-unit VFD
setpoints to leave time for the host service stop. Both slots were moving:
`l1-1-8-1-1` and `l2-1-8-2-2`. After `sorter-plant.service` stopped, PLC
fault 591 became 1, master stopped, induced count and request sequence both
remained 2, both slots remained state 3, all nine trailer counters remained
zero, and XLe logged no PLC outcome. Both telemetry rows became stale. A
rendered Firefox check found both package IDs at unchanged positions on two
polls and showed the global `PLANT FAULT 1` banner. Restarting the service
with that run still active changed fault 591 to 2, cleared plant identity
587..589 to zero, and still left every trailer counter at zero. The scanner/
PLC reset in `tests/restore_plant_stop.py` cleared the alarm and restored the
saved operator settings with master stopped.

Rendered browser evidence is in
`tests/artifacts/plant-lanes-{induct,merge-wait,shared-outbound,shared-trailers,failed-confirmation,service-stopped}.png`.
The browser trace for the shared path showed the lane 2 package at its gate
while lane 1 entered outbound 1, then two different positions on outbound 1.
The failure screenshot shows the lane 2 alarm and only trailer 2 loaded.

Commands used for these live checks (on SCADA unless otherwise noted):

```sh
/home/kevin/opcua/bin/python /home/kevin/sorter-services/live_plant_lanes.py distinct
/home/kevin/opcua/bin/python /home/kevin/sorter-services/live_plant_lanes.py shared
# Drives: temporarily run plant.py --fail-confirm-token 2 instead of the service.
/home/kevin/opcua/bin/python /home/kevin/sorter-services/live_plant_lanes.py failure
/home/kevin/opcua/bin/python /home/kevin/sorter-services/live_plant_stop.py --lane2
# Drives, after /tmp/plant-stop-ready exists: sudo systemctl stop sorter-plant.service
# Drives, after checks: sudo systemctl start sorter-plant.service
/home/kevin/opcua/bin/python /home/kevin/sorter-services/restore_plant_stop.py
```

`tests/browser_plant.py shared`, `lane2_failure`, and `fault` exercised the
rendered HMI through the existing serial proxy. In the first repeated stop
attempt, the host stopped the plant after the two observed packages had
already completed; the observer timed out and that run was discarded. The
observer now uses slower VFD setpoints for lane 2 interruption tests. Its
subsequent run passed with both slots in motion. This timing sensitivity is a
test-control limit, not a loss of an occupied plant package.

Final local checks: `bash tests/run_first_package.sh` passed the legacy and
plant PLC harnesses, including tunnel 2, shared outbound, and lane 2 failure;
`python3 -m unittest discover -s tests -p 'test_*.py'` passed 33 tests.
