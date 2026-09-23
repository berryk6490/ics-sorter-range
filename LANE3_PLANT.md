# Three-lane independent plant milestone

## Topology and ownership

All three simulated induct belts now move in the drives VM plant process. It
reads actual simulated RPM feedback from VFDs `10.10.1.21`–`.23` and the three
outbound VFDs `.24`–`.26`. The PLC consumes serialized plant photoeyes and owns
scanner triggers, two package slots, divert commands, outcomes, and counters.
Tunnel 3 retains its existing request registers `QW126..129` and response
registers `IW180..186`. Its result is bound to the lane 3 slot token and serial.
XLe reads that PLC slot, asks the loopback ASX plan, sends the slot-bound PLC
command, and journals the PLC outcome. The HMI sees only PLC-validated
telemetry through the existing OPC UA server. Neither XLe nor the HMI reads
plant process state directly. No VM, isolated bridge, or firewall rule was
added or changed.

The protocol adds no registers. `QW577` carries the induction request lane
`1..3`; `QW578` carries the committed sensor event lane. `QW644..645` hold
the lane for the two PLC slot rows, and `QW646` identifies the last failed
confirmation lane. Telemetry belt codes are `1` for induct 1, `5` for induct
2, `6` for induct 3, and `2..4` for outbounds 1..3. The HMI maps those codes
to `ib0..ib2` and `ob0..ob2`. XLe package IDs start `l1-`, `l2-`, or `l3-`
and include epoch, scanner run nonce, slot token, and serial. ASX uses the
editable `tests/lane3_plan.json` for the bounded verification runs; it does
not derive a trailer from barcode digits.

## Capacity, priority, and safety

The two PLC slots remain the **global** capacity across three inducts. Each
scan increments the three induction timers. When a slot is free, the PLC
checks ready and enabled lanes in circular order starting at
`plant_next_lane`; after a successful request it advances the pointer to the
next lane. After reset the order starts 1, 2, 3. Disabled or unready lanes
are skipped. The PLC waits for the plant's induction acknowledgement before
assigning the other slot. A full slot set leaves ready timers saturated,
without creating an untracked package. The default PLC cell route remains
available when plant mode is off.

At outbound entry positions 2, 5, and 8, the plant admits a package only
when every package on that outbound is at least its configured package length
plus clear spacing away. The defaults are 60 cm and 100 cm, or `1.2 + 2.0 =
3.2` cell equivalents. The first arrival is considered first; a same-update
tie is lane 1, then 2, then 3. A blocked package waits at its divert gate
and emits no divert photoeye until admitted. The plant advances outbound
positions before testing new admissions. A PLC sort decision alone never
increments a trailer: only a matching plant confirmation event does.

The plant now synchronizes its event sequence from the PLC's committed
`QW585` at each new run. A wrong-lane event injected over Modbus during
verification advanced that register beyond the plant's own sequence. Before
this fix, the next run's first induction reused the PLC's already-seen
sequence and remained unacknowledged. Reading `QW585` at run establishment
made the next event unique; the subsequent live shared and three-lane runs
passed after reset.

## Live evidence, 2026-09-23

The four existing guests (`fw`, `plc`, `drives`, `scada`) were used for live
verification. Each has 768 MiB configured, totaling 3 GiB on the 16 GB host.
PLC and drives were initially running; fw and scada were initially off.
The live runner read PLC Modbus slots, lane registers, event ACKs, trailer
counters and OPC UA-backed HMI API, and matched scanner/XLe/ASX events and
the XLe journal by package ID. All runs had at most two occupied slots.

| Run | Exact package identity and result | Counters / other evidence |
| --- | --- | --- |
| Three lanes, slot reuse | `l1-1-15-1-1`: barcode 6001, ASX 2, PLC actual 2/reason 0, slot 0. `l2-1-15-2-2`: 5002, ASX 5, actual 5/0, slot 1. `l3-1-15-3-3`: 3003, ASX 8, actual 8/0, reused slot 1 after its recorded release. | Trailer 2, 5, and 8 each 1; three distinct journal outcomes; all three lanes observed in HMI API. PLC scan/accept/divert ticks were `168/170/224`, `169/173/254`, `591/593/705`. |
| Shared outbound, lanes 2 and 3 | `l2-1-14-1-1`: 5001, ASX 3, actual 3/0. `l3-1-14-2-2`: 3002, ASX 2, actual 2/0. | Both used outbound 1; lane 3 waited at position 14; minimum measured simultaneous gap was 32 tenths of a cell. Trailer 2=1 and trailer 3=1. Sensor event order for both was induction, tunnel, divert, confirmation. All nine counters were zero at the first divert, before confirmation. |
| Lane 3 failed confirmation | `l3-1-8-1-1`: barcode 3001, ASX 8, PLC actual 0/reason 4. | All nine trailer counters stayed zero; `QW646=3`; HMI showed `LANE 3 FAILED CONFIRMATION` and the failed package on outbound 3. |
| Wrong-lane event | `l3-1-11-1-1` occupied slot 0; injected tunnel event claimed lane 2. | PLC acknowledged event sequence 8, latched plant fault 3, stopped master, retained slot state 1, and left all nine trailer counters zero. Scanner/PLC reset cleared the test fault. |

The lane 3 shared run's PLC ticks were scan/accept/divert `170/174/316`:
14.2 seconds from command acceptance to its admitted divert. The three-lane
run's lane 3 ticks were `591/593/705`, an 11.2-second margin. These are
simulated PLC scan times (100 ms per tick), not wall-clock conveyor timings.

Firefox rendered the full three-lane flow and the lane 2/lane 3 shared belt.
Screenshots are in `tests/artifacts/plant-lane3-{induct,merge-wait,shared-outbound,trailers,failed-confirmation}.png`.
The shared screenshots show separate identities on the same outbound and
lane 3 held at its gate; the failure screenshot shows the lane 3 alarm.

Verification commands and final results:

```sh
# Host: legacy and plant PLC harnesses, including lane 3 success/failure
bash tests/run_first_package.sh                         # PASS
python3 -m unittest discover -s tests -p 'test_*.py'   # 36 passed
python3 -m py_compile devices/plant.py services/xle.py scada/hmi_ua.py \
  tests/live_plant_lane3.py tests/live_plant_wrong_lane3.py \
  tests/browser_plant.py tests/serial_hmi_proxy.py       # PASS
# On SCADA, through the existing serial console and guest Modbus route:
/home/kevin/opcua/bin/python live_plant_lane3.py all     # PASS
/home/kevin/opcua/bin/python live_plant_lane3.py shared  # PASS
/home/kevin/opcua/bin/python live_plant_wrong_lane3.py  # PASS
# With drives temporarily running plant.py --fail-confirm-token 1:
/home/kevin/opcua/bin/python live_plant_lane3.py failure # PASS
# Host, using the serial HMI proxy and Firefox WebDriver:
python3 tests/browser_plant.py lane3_all                 # PASS
python3 tests/browser_plant.py lane3_shared              # PASS
python3 tests/browser_plant.py lane3_failure             # PASS
```

The host PLC harness and Python suite include legacy three-lane mode, lane 3
scanner response, circular priority, slot reuse, wrong-lane rejection,
shared clearance, and failed confirmation. A first live attempt used an old
OpenPLC core despite a successful compile; its stale core incorrectly kept
plant fault 2. A later attempt used the old drives plant process and rejected
lane 3. Both binaries/processes were replaced before the passing runs. The
wrong-lane test then exposed the event-sequence reset issue described above;
the final shared and three-lane results use the fixed plant source.

## Recovery and limits

After a wrong-lane fault or a plant service interruption, stop master, drop
plant mode, reset the PLC and all three scanners, then restore operator
settings and start a new run after plant and XLe establish a fresh identity.
A cold plant restart cannot reconstruct exact in-flight positions. The
independent model still omits parcel deformation, slip, skew, bouncing
photoeyes, mechanical jams, and real merge actuators. It has only two global
in-flight slots; this milestone does not test a larger live queue. The
legacy cell model remains the default-mode path for all three lanes.
