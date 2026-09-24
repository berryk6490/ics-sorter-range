# Stateful photoeyes, opt-in plant slice

## Ownership and protocol

`devices/plant.py` remains the sole owner of package coordinates. It advances
them from the six VFD speed feedback registers and the configured package
length (60 cm by default, in 50 cm cell equivalents) and induction spacing
(100 cm by default). Each beam is true from the package's leading edge until
its trailing edge passes: clear → leading edge → blocked → trailing edge →
clear. The model samples five beams in path order: induction entry at induct
cell 0, tunnel at cell 10, divert approach at cell 12, outbound entry at
outbound cells 2/5/8 for lanes 1/2/3, and trailer confirmation at outbound
cell 12/15/18 for destination doors 1/2/3. Thus speed and length set block
duration; stopping a VFD freezes both position and beam state.

Coil 919 (`%QX114.7`) enables the stateful mode for a run. Reset clears it;
coil 918 still selects the independent plant mode. With 919 off, the prior
three-lane cell and serialized plant-event behavior is unchanged. With it on,
the plant still sends events 1..6 in QW578 and QW580..585 and waits for PLC
acknowledgement QW586. The PLC withholds the ACK for induction, tunnel,
divert, and trailer events until the matching beam has had a validated falling
edge. The PLC still owns slot admission, scanner trigger, route command,
outcome and trailer count. A trailer counter cannot rise from a route command
or a raw beam alone: the identity-bound trailer event must follow a validated
confirmation beam. XLe and ASX continue to use only the PLC slot/event path.

| Address | Owner | Meaning |
| --- | --- | --- |
| coil 919 | operator/test | Opt-in stateful mode; reset returns it to off. |
| QW690..697, 698..705, 706..713 | plant → PLC | One row per slot: epoch low/high, scanner run nonce, slot token, package serial, lane, raw beam mask, commit sequence. Payload precedes sequence. Bits 0..4 are induction, tunnel, divert approach, outbound entry, trailer. |
| QW720..727, 728..735, 736..743 | PLC → OPC UA | One PLC-validated row per slot: epoch low/high, nonce, token, serial, validated raw mask, debounced mask, quality. The HMI and XLe have no plant-process read path. |
| QW744..747 | operator/test | Debounce scans (1..5, default 2), minimum blocked scans (1..30, default 2), maximum blocked scans (2..300, default 120), maximum travel scans (5..1000, default 400). Invalid values use defaults. PLC task period is 100 ms. |
| QW748..750 | PLC → OPC UA | Latched fault slot bitmask, expected/failed sensor number 1..5, lane. Fault clears on PLC reset. |

The PLC accepts a raw row only when its epoch, nonce, token, serial, lane,
sequence, and bounded mask match the active slot. Per-beam candidates must
remain stable for the debounce setting before the PLC detects a rising or
falling edge. It checks minimum and maximum blocked duration, next expected
beam, and maximum travel time to the next beam. Quality is 0 good, 1 identity
mismatch, 2 travel timeout or stuck clear, 3 blocked too long or stuck
blocked, 4 blocked too briefly, 5 unexpected order, 6 stale raw row. For an
unexpected downstream beam the fault names the *missing expected* sensor.
Any nonzero quality stops the sorter and latches the slot fault. A missing
plant update also stops movement via the existing plant heartbeat. The plant
can still hold coordinates for packages already present, but no absent
confirmation becomes a trailer count.

OPC UA exposes `Process/Plant/Slot{1,2,3}/Photoeyes` and
`Process/Status/{PhotoeyeMode,PhotoeyeFaultMask,PhotoeyeFaultSensor,PhotoeyeFaultLane}`.
The HMI displays the validated raw mask, conditioned mask and quality beside
each package, with a named fault banner. The page has no connection to
`plant.py` or its raw QW690..713 rows. Legacy cell drawing remains available.

## Deterministic fixtures and recovery

The normal plant service starts without a fixture. A temporary plant process
may specify all three of `--photoeye-fault-sensor`,
`--photoeye-fault-token`, and `--photoeye-fault-kind`; partial arguments are
rejected. Available kinds are `stuck_clear`, `stuck_blocked`, `bounce`,
`missed`, and `early`. The modifier applies only to that token and sensor in
that process. The fixture disappears when that process exits. Tests pause the
normal plant process while the temporary one runs, stop the sorter and drop
plant/photoeye mode, terminate the fixture, then replace the paused process
with the original systemd service. `tests/run_photoeye_fault_host.py` binds
the two live cases to token 1 and handles the temporary process in `finally`;
it refuses to begin unless the normal plant process is alone and the sorter
and both plant modes are off. No bridge, firewall, or VM definition is
changed.

After a field-input fault, stop master, drop plant and photoeye mode, reset
the PLC/scanners, and restore the captured operator setpoints and enables.
Reset clears the latched quality/fault state and leaves the sorter stopped.
The normal plant service must be active before the next run. The fault runner
does these PLC steps in `finally`, including on failure; the host test
orchestration removes its specific temporary process and checks the normal
service is active.

## Verification on 2026-09-23

The host started with `plc`, `drives`, `fw`, `scada`, and `analyst` running and
left those five VMs running. The normal plant service, three scanners,
OpenPLC Modbus, OPC UA, and HMI were active at the end. The deployed sources
were copied over the existing serial consoles. OpenPLC compiled successfully.
After a PLC reboot the web service did not automatically start its core;
the core was started on the PLC guest, then its configured Modbus port 502 was
enabled through its local runtime socket. The program registered QW744..750
as `[2,2,120,400,0,0,0]`. No host bridge address or firewall rule changed.

`/home/kevin/opcua/bin/python /home/kevin/live_plant_lane3.py all --stateful`
on SCADA passed over the real VM Modbus path. The three PLC slot identities
were `l1-1-2-1-1`, `l2-1-2-2-2`, `l3-1-2-3-3`. Scanner barcodes
6001/5002/3003 received ASX destinations 2/5/8; XLe recorded matching PLC
commands and loaded outcomes. Trailer counters were
`[0,1,0,0,1,0,0,1,0]`. Each lane's sampled raw masks included
`0,1,2,4,8,16`; final photoeye fault registers were `[0,0,0]`, and all
three lanes appeared through the HMI API. The event stream retained
induction → tunnel → divert → confirmation order and the journal held one
outcome per identity.

The induction `stuck_blocked` fixture for token 1 produced fault `[1,1,1]`,
quality 3, raw/conditioned mask 1, an occupied slot, a stopped master, and
all nine trailer counters zero. The tunnel `missed` fixture for token 1
produced fault `[1,2,1]`: the divert approach beam arrived while the tunnel
beam was still expected, so quality 5 named sensor 2. Raw/conditioned masks
were 4 at detection; master stopped, slot 1 remained occupied, and all nine
trailer counters stayed zero. Both faults were visible through the actual HMI
API. The first missed-tunnel run revealed an attribution bug (sensor 3 was
named); the PLC was corrected and the case rerun to the result above.

Firefox WebDriver rendered the missed-tunnel banner `PHOTOEYE LANE 1 SENSOR 2
FAULT` and the package label `PE RAW 4 FILTERED 4 QUALITY 5` via the serial
HMI proxy. Evidence: `tests/artifacts/photoeye-missed-hmi.png`. The earlier
browser frame included two other lane-1 packages because that exploratory
runner had not yet raised the induction interval after token 1. The final
fault runner does so; its asserted slot and counter result is the verified
single-token case. The legacy stale/unavailable browser regression also
passed after its synthetic fixture was expanded to all three slots.

The dedicated host runner was also executed for `missed`: it reported
`fixture missed removed; normal plant service restored` after its PLC
cleanup checks. Host verification: `bash tests/run_first_package.sh` passed all 32 PLC
harness executions, including ten new photoeye cases; Python discovery
passed 47 tests; `python3 -m py_compile` passed; network reachability was
36/36 expected flows (PLC 12/12, drives 2/2, SCADA 13/13, analyst 9/9).
The browser used the existing Firefox WebDriver because `agent-browser` was
not installed on the host.

Final cleanup readback from PLC Modbus: master false; lane/outbound enables
`[1,1,1,1,1,1,1]`; coils 914..919 all false; QW200..210
`[200,200,200,233,233,233,2,14,14,14,30]`; seed 137; QW744..750
`[2,2,120,400,0,0,0]`. `sorter-plant`, OPC UA, HMI, the checked VFD and
scanner services were active. Only the normal unflagged plant process
remained. The original five VM states were unchanged. The Case A work stayed
on remote `wip/case-a-integrity-validation`; this branch remained `main`.

The later phased latch-evidence workflow is documented in
`MISSED_TUNNEL_VALIDATION.md`. It leaves the PLC fault latched while the
normal plant service returns, captures Modbus, OPC UA, HMI and rendered-browser
evidence, then performs the documented reset. It does not alter the legacy
one-shot `live_photoeye_fault.py` runner or the photoeye protocol.

Remaining physical limits: these are five ideal geometric beam locations on
simulated belts, not real optics, actuators or mechanics. The Modbus update
interval bounds detectable pulse width; a beam shorter than the PLC's sample
and debounce window may be missed and must fail by travel timeout or order
fault. The current fixture models deterministic input faults, not arbitrary
electrical waveforms. The event/ACK path is deliberately retained for
compatibility, so this milestone has not removed its serialization limit.
