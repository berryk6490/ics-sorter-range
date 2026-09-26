# IS-4543 Milestone 1 — range build evidence

This page maps the original Milestone 1 proposal to the implemented sorter range.
The build and recorded checks below cover the technical range. The final network
diagram and clean-sort video are deferred submission artifacts; **the complete
Milestone 1 submission is not yet assembled**. This page does not claim a new
live test or a physical parcel system.

## Proposal-to-implementation checklist

| Proposed item | Implementation and primary evidence | Status |
| --- | --- | --- |
| Three isolated network segments with a firewall VM as the conduit | Level 1 (`plc`, `drives`), Level 2 (`scada`), Level 3 (`analyst`), and `fw` on all three bridges. [Network inventory, firewall policy, and 36-flow results](NETWORK_BASELINE.md); [machine-readable expectations](tests/network_flows.json); [recorded observations](tests/evidence/network_reachability_2026-09-23.json). | Built and measured in Case A |
| Install OpenPLC and write conveyor/divert Structured Text | Current source [`Sorter.st`](Sorter.st); [first-package PLC behavior and host/live checks](FIRST_SLICE.md); [three global slots, shared outbound, and trailer-confirmation evidence](THREE_SLOT_PLANT.md). | Built and tested |
| Build two Python VFD emulators | Six instances of [`devices/vfd.py`](devices/vfd.py), three induction and three outbound, exceed the proposed two. [Service list and holding-register contract](deploy/README.md). [Independent plant behavior](ACCUMULATION_BACKPRESSURE.md) uses VFD feedback for movement. | Built and tested |
| Register maps | [Drive and scanner maps](deploy/README.md), [PLC package-slot and plant map](THREE_SLOT_PLANT.md), [stateful photoeye map](STATEFUL_PHOTOEYES.md), and [accumulation map](ACCUMULATION_BACKPRESSURE.md). | Documented across linked files |
| Isolation proof | The recorded [flow sweep](tests/evidence/network_reachability_2026-09-23.json) matched 36/36 expectations (30 permitted, 6 denied). [Network baseline](NETWORK_BASELINE.md) includes a firewall allow-counter and drop-counter probe. | Evidence exists for the **declared Case A policy** |
| Network diagram | Create a concise diagram of the three bridges, VM interfaces, conduit, and principal protocols for the submission. | Deferred at operator request |
| Video of a clean sort | Record one live run showing package motion, scanner trigger, correct divert, and confirmation-only trailer increment. Earlier [three-lane live evidence](tests/evidence/network_live_2026-09-23.json) records trailers 2, 5, and 8. | Deferred at operator request |

## What the simulation demonstrates

[Independent plant mode](THREE_SLOT_PLANT.md) computes package movement from
simulated VFD speed feedback, emits sensor events, and waits for PLC event ACKs.
The PLC owns three package slots globally, scanner triggers, route acceptance,
terminal outcomes, and nine trailer counters. In
[stateful photoeye mode](STATEFUL_PHOTOEYES.md), package length and movement
produce leading and trailing beam transitions; the PLC validates them.
[Finite accumulation](ACCUMULATION_BACKPRESSURE.md) supplies lane and merge
backpressure, holds, and a fourth package waiting for a released slot.

The recorded three-lane run correlated scanner identities, routing decisions,
PLC outcomes, journal rows, and HMI telemetry. It left trailer counters at
zero before confirmations and ended with one confirmed package each at trailers
2, 5, and 8. See [the bounded run evidence](tests/evidence/network_live_2026-09-23.json)
and [the network baseline narrative](NETWORK_BASELINE.md).

The PLC also has a legacy internal belt-cell mode; opt-in independent plant
mode is the source of the physical-motion evidence here. Scanners, photoeyes,
VFDs, package motion, and trailers are simulated; Modbus and OPC UA exchanges
use the real interfaces between lab guests. The model has no optical image
processing, mechanical braking/slip, or physical safety PLC. Those are not
Milestone 1 proposal requirements.

## Interpret the isolation proof accurately

The three zone bridges are separate and inter-zone traffic uses the firewall
conduit. The live Case A firewall intentionally allows the analyst VM broad
access to Levels 1 and 2 so later attacks have a measurable baseline. Some
reverse and unlisted flows are denied. **Do not describe Case A as blocking
all Level 3 to Level 1 access.** The more restrictive Case B configuration,
attack replays, Zeek/Suricata detections, and protocol hardening belong to
later milestones.

The 36-flow sweep establishes only the enumerated TCP reachability and
denials, not all possible network traffic or Modbus/OPC UA authentication.

## Recheck before submission

From the workstation checkout, with the documented guest/service prerequisites:

```sh
bash tests/run_first_package.sh
python3 -m unittest discover -s tests -p 'test_*.py'
python3 tests/deployment_preflight.py --live
bash tests/run_network_reachability.sh
```

The first two commands are host-side regression checks. Deployment preflight
and reachability require the appropriate running and logged-in guests; follow
their documented preflight and restoration procedures. The network reachability
baseline used five guests (`plc`, `drives`, `fw`, `scada`, `analyst`) totaling
5 GiB of configured guest RAM. Do not substitute old check counts for results
of a new run. Record commit, date, exact command results, VM states, and any
failure separately. No live commands were executed to create this page.

**Ready for final Milestone 1 packaging when:** the diagram and video are
captured, their displayed topology and package results match the current
checkout, and a fresh validation record is attached. The sorter simulation
itself does not require additional plant features to meet this milestone.
