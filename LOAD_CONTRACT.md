# Plant-owned drag load, version 2 (proposal, not implemented)

This contract moves the mechanical load a drive carries from the PLC to the
drives plant while plant mode (coil 918) is on. Load is physical truth: the
package is on the belt whether or not the controller believes it is. Nothing
here is implemented. No PLC, drive, plant, SCADA or manifest file has changed
for it, and every statement about current behavior below cites the source it
was read from at commit b39d7e6.

The plant owns an effective drag sample for each belt, sent to its drive over
a separate loopback-only channel on the drives VM. The public Modbus port 502
retains the PLC's existing registers 0..8 but cannot write or select physical
drag in plant mode. A moving package contributes 0.9 A of torque current; a
package held against a *running* belt by ZONE_FULL, MERGE_CAPACITY, or
CHUTE_FULL contributes 3.6 A. No changes in this document are deployed.

**Read this first.** The three global PLC slots cap a belt at three packages.
Three moving packages cannot overload a drive, but three held packages at
rated speed can trip its existing thermal model in about 60 seconds. This is
a proposed hold-abuse path, not a claim that a particular run has achieved
three simultaneous holds on one running belt.

## Current behavior

The PLC publishes load every scan as the count of nonzero cells in each
20-cell legacy array: `ib1`..`ib3` for the inducts and `ob1`..`ob3` for the
outbounds (`Sorter.st:1017-1047`, array type at `Sorter.st:5`). Those arrays
shift only inside four `IF NOT plant_mode` blocks: induct 1 at
`Sorter.st:2210-2443`, induct 2 at `2446-2573`, induct 3 at `2576-2703`, and
all outbounds at `2705-3111`. Outside those blocks the only writes are the
reset clears at `Sorter.st:906-911`, which also zero the six load words at
`Sorter.st:913-918`. After the canonical reset, every drive therefore sees
load 0 for the whole plant-mode run. No guard was found for enabling plant
mode without a reset; in that case each array freezes at its last legacy
contents and the drives see that frozen count instead of zero.

The drive reads registers 0..8 once per 100 ms tick (`devices/vfd.py:63`,
`70-71`) and computes current as magnetising plus windage scaled by speed
plus 0.9 A per unit of `belt_load` (`devices/vfd.py:49-51`, `94-97`). The
inverse-time accumulator gains `(I/rated)^2 - 1` per second above 12.0 A and
cools at a quarter of that rate below it (`devices/vfd.py:47`, `59-60`,
`101-112`). It trips at 40.0 with fault code 1 (`devices/vfd.py:114-115`).

The PLC's located drive words are command, reference and load at `%QW100`
onward, three per drive, and speed feedback at `%IW105`, `%IW114` and so on,
a stride of nine (`Sorter.st:661-684`). Together with `FIRST_SLICE.md:37-40`
this is consistent with OpenPLC writing drive registers 0..2 as one block and
reading 0..8. `mbconfig.cfg` itself lives on the `plc` guest and is not in
this checkout, so the block widths are inferred from the located variables.
The drive datastore has 16 registers (`devices/vfd.py:130`); it writes 3..8
(`devices/vfd.py:121-122`), so 9..15 are unused today. The PLC reads no drive
status or fault word, only speed feedback, so a thermal trip reaches the PLC
as zero feedback (`Sorter.st:664-684`).

The plant is already a Modbus master to all six drives on the `drives` VM. It
reads register 5 from each every loop (`devices/plant.py:19-20`, `786`) and
writes nothing to them. The drive addresses `.21`..`.26` sit on the same NIC
as the plant (`NETWORK_BASELINE.md:49`), so a plant write to a drive is host
local and adds no network flow.

SCADA polls drive registers 0..8 (`scada/opcua_server.py:100-105`, `175`) and
publishes register 2 as `BeltLoad` beside `ThermalLoad`
(`scada/opcua_server.py:465-472`, `664-672`). The HMI renders both
(`scada/hmi_ua.py:123-126`, `317-318`, `916-917`). The typed accumulation
snapshot reads 0..8 (`tests/read_accumulation_state.py:34-40`) and records
`belt_load` and `thermal` as informational
(`tests/accumulation_state_snapshot.py:224-225`).

## Reachability

A drive trips only if current exceeds 12.0 A. With the drive's own tick loop,
from a cold accumulator and including the 4.4 s ramp at 400 rpm/s
(`devices/vfd.py:46`, `83-88`), the trip times are:

| Reference | Current at N packages | Least N that trips | N=12 | N=14 | N=16 | N=20 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 200 rpm, default induct (`Sorter.st:787`) | 2.1 A + 0.9 N | 12 | 257.2 s | 80.0 s | 45.0 s | 22.2 s |
| 233 rpm, default outbound (`Sorter.st:788`) | 2.1 A + 0.9 N | 12 | 257.2 s | 80.0 s | 45.0 s | 22.2 s |
| 1750 rpm, rated | 4.8 A + 0.9 N | 9 | 60.0 s | 37.7 s | 26.8 s | 16.1 s |

At rated speed the steady-state rates give 36.3 s at 14 and 15.3 s at 20; the
ramp adds about 1.5 s. The docstring's "around 80 seconds at 14 packages on a
belt, around 20 at 20" (`devices/vfd.py:53-56`) is correct at the default
references, not at rated speed. Both statements are true; the docstring omits
which speed it assumes.

In plant mode the PLC allocates a package only into one of three global slots
(`Sorter.st:2163-2168`, `THREE_SLOT_PLANT.md:42`), and the plant further holds
each induction until the previous package on that lane has cleared one pitch
(`devices/plant.py:319-323`, `466-471`). A host saturation run of
`PlantModel` with default 60 cm packages and 100 cm spacing, all three lanes
requesting continuously, outbounds running and stopped, free and accumulation
modes, never placed more than three packages on any belt. Three packages give
4.8 + 3(0.9) = 7.5 A at rated speed *if moving*. For three held on a running
belt, the steady rated-speed current is 1.8 A magnetising + 3.0 A windage +
3(3.6 A) drag = **15.6 A**. With 12.0 A rated and a trip accumulator of 40,
the heating rate is (15.6 / 12)^2 - 1 = 0.69 per second, giving
40 / 0.69 = **58.0 s** from a cold accumulator already at rated speed.
Solving for precisely 60 s gives (12 sqrt(1 + 40/60) - 4.8) / 3 =
3.564 A per held package; 3.6 A is the tenth-ampere rounded coefficient.
The existing 100 ms ramp from rest to 1750 rpm adds about 2 s of net thermal
delay (60.0 s in a discrete calculation). One held package gives 8.4 A and
two give 12.0 A at rated speed, neither above the overload threshold.

For comparison, legacy *riding* loads at rated speed and a cold accumulator
give 4.8 + 14(0.9) = 17.4 A and 40 / ((17.4/12)^2 - 1) = 36.3 s,
or 4.8 + 20(0.9) = 22.8 A and 40 / ((22.8/12)^2 - 1) = 15.3 s.
The table above includes startup ramp and rounds those to 37.7 and 16.1 s.
Its 80.0 and 22.2 s figures for 14 and 20 riding packages are at *default*
200/233 rpm, not at rated speed. The old gap-collapse example is legacy-mode
only; plant-mode overload requires hold drag on a running belt.

## Separate physics and diagnostic channels

The existing six drive endpoints on their ICS-L1 addresses, port 502, retain
the PLC's 0..2 command/reference/legacy-load block and the drive's 3..8
status block. Register 2 remains meaningful in legacy mode but is *never*
selected as load in plant mode. No plant-owned physics input is addressable on
port 502; its datastore must not be shared with the private physics listener.
Direct writes to public register 2 cannot alter plant-mode current or heat.

Each drive also listens on a distinct **127.0.0.1-only** port on the drives VM
(proposed 15021..15026, mapped to induct 1..3 and outbound 1..3). The plant
is the sole authorized writer of a private, atomic sample:
`plant_mode_active`, `plant_drag_x10`, and a monotonically wrapping sequence
number. `plant_drag_x10` is torque-current contribution in tenths of an
ampere, not a package count; 9 means one riding package and 36 one held
package. With the current three-slot limit it is 0..108 (allow up to 720 for
20 packages only if the model is later expanded). The private mode indicator
must persist independently of sample freshness so a timeout cannot switch
the drive to legacy load. The listener is not reachable from any other guest;
loopback binding alone does not authenticate local writers, so an OS-enforced
plant-service-only connection policy is required. A compromised drives host
or plant account is explicitly outside this attack model.

Expose drive-generated diagnostics to SCADA on public port 502 as read-only
registers 9 (`load_source`: 0 legacy, 1 plant fresh, 2 plant stale, 3 invalid
or no initial sample) and 10 (`load_used_x10`: actual torque contribution).
The public server must reject FC06/FC16 writes to these diagnostic registers;
they must never feed the drive's physics, even if another client writes port
502. The PLC's existing 0..8 mapping is unchanged; SCADA reads 0..10 instead
of 0..8. The private sample is not exported on the public endpoint. Exact
local writer isolation and public write rejection need host tests before any
implementation or deployment.

## Load definition

Drag on a drive is the sum of contributions from packages the plant model
holds on that drive's belt. Drives 1..3 are the induct lanes and drives 4..6
the outbounds, matching the plant's own feedback indexing (`devices/plant.py:356`, `331`,
`482`). A package is on its lane belt from induction until it diverts, and on
outbound belt `p.outbound` after that (`devices/plant.py:397-398`,
`608-609`). A package counts if it is in `model.packages`. Queued requests not
yet inducted do not count (`devices/plant.py:319-326`, `466-474`), and neither
do terminal records in `model.recent`.

The model removes a package when its footprint leaves the belt: at the lane
recirculation exit, cell 19 (`devices/plant.py:101`, `375-381`, `565-571`),
or once its rear has passed the trailer door on an outbound
(`devices/plant.py:345-351`, `523-528`). Membership in `model.packages` on a
belt is therefore the same as footprint overlap with that belt, using package
front `p.position` or `p.outbound_position` and length `p.length`
(`devices/plant.py:185-195`). The model does not represent a package
straddling a divert; at the divert step it moves wholly from lane to outbound,
so it is never counted on two belts. A moving/riding package contributes
9 tenths of an ampere, matching
the current legacy coefficient (`devices/vfd.py:51`). A stationary package
with `Hold.ZONE_FULL`, `Hold.MERGE_CAPACITY`, or `Hold.CHUTE_FULL` against a
*running* belt contributes 36 tenths. Count each package exactly once on its
current belt; never count queued requests or terminal records. `DRIVE_OFF`
is not drag on a running belt: a stopped drive draws no motor current, so its
packages neither heat it nor become a hold-abuse trip merely by sitting
still. When it restarts, recompute their drag from the current hold/motion
state, not from a stale `DRIVE_OFF` flag. Mixed loads sum linearly, e.g. two
held plus one riding contribute 2(3.6) + 0.9 = 8.1 A of torque current;
at rated speed total current is 4.8 + 8.1 = 12.9 A. Drive thermal behavior
otherwise retains the existing inverse-time curve and reset semantics.

## Update rate, freshness and absence

The plant computes and writes each belt's drag after `model.step`
(`devices/plant.py:805`) inside the existing `if mode:` publication block
(`devices/plant.py:806`), so the private sample describes the positions and
hold states it just advanced. It writes all six drives every loop whether or
not drag changed, with a new sequence each time. The loop sleeps 100 ms plus
its Modbus time (`devices/plant.py:859`) and 500 ms after an error
(`devices/plant.py:860-862`).
The drive samples every 100 ms.

The drive treats an authenticated plant sample as fresh while its sequence
has changed within the last 30 drive ticks (nominally 3.0 s). That matches
the PLC's plant heartbeat bound of 30 scans at a 100 ms task (`Sorter.st:1826`,
`3399`); actual wall-clock timing still depends on scan cadence. A stale
sample keeps the **last valid drag**, sets `load_source = 2`, and cannot
select public register 2 while plant mode remains active. An out-of-range or
malformed private sample does not replace the last valid value and sets
`load_source = 3`; without *any* valid initial sample the drive must inhibit
run in plant mode, not borrow legacy load. Mode entry requires a valid active
sample before energizing; its trusted active marker remains latched on
timeout. A mode-off transition is accepted only through the trusted local
plant/control handshake with drives stopped, not through sequence expiry.

When the plant is absent or stalls, the PLC raises plant fault 1 after 30
scans and clears `sorter_run` (`Sorter.st:1826`, `1831`). Once `sorter_run` is
off, the PLC commands every drive stopped (`Sorter.st:1052-1092`), and a
stopped drive cools whatever its load (`devices/vfd.py:101-111`). The drive
holds the last valid plant drag during the heartbeat-to-stop window; it does
not hide a stalled plant by silently switching to the PLC's zero legacy load.
**The plant must withhold its PLC heartbeat if any private drive update fails.**
Today it publishes heartbeat before `model.step` and before any proposed
private drive write (`devices/plant.py:773-774`, `805`); that order must change
in implementation. Otherwise the plant could keep the PLC heartbeat fresh
while a drive's physics sample goes stale, and the assumed 3.0 s stop would
never occur. Host tests must cover that failure path and the stopped-drive
restoration separately.

On a cold plant start during an active run the plant refuses to act and
withholds its heartbeat (`devices/plant.py:741-748`); it sends no new private
sample, so the drive retains the last valid drag (or inhibits run if it has
none). On a new run identity the plant builds an empty model
(`devices/plant.py:752-761`) and must establish an authenticated active zero
sample before the drives may run. These are implementation requirements, not
behaviors of the currently deployed drive.

## Legacy compatibility

With plant mode off the plant has no active run and skips all publication
(`devices/plant.py:751`, `778-780`), so the drives use register 2 unchanged
after a trusted mode-off handshake. The legacy arrays, their load publication
and the legacy gap-collapse behavior are untouched. No sequence-zero release
or timeout fallback is used: the last valid drag stays selected throughout
plant mode, including plant failure, until a safe mode transition completes.

The PLC program, `mbconfig.cfg`, the located variables and `%QW102`..`%QW117`
keep their current meaning. In plant mode those load words still carry the
frozen legacy count but are ignored by the drive even when the plant is stale.
Consequently neither an explicit release-to-register-2 mechanism nor a guard
against frozen legacy counts is part of this load contract (former decisions
5 and 6). The separate mode-entry handshake must still be tested fail-closed.

## `vfd.py` docstring correction

Register line 15 becomes "legacy load, used only when plant mode is off";
the public diagnostic table gains read-only registers 9..10 and describes
the separate private channel. The comment at
`devices/vfd.py:53-58` states its speed. Proposed text:

    Inverse-time overload. The accumulator gains (I/rated)^2 - 1 per second
    above rated and sheds a quarter of that below it. From a cold accumulator
    at the default 200 rpm induct and 233 rpm outbound references, a belt
    trips in about 80 s at 14 riding packages and 22 s at 20. At rated
    speed the same riding loads trip in about 36 s and 15 s. In plant mode,
    a moving package adds 0.9 A of torque current and a package held against
    a running belt adds 3.6 A. Three held packages at rated speed draw
    4.8 + 3(3.6) = 15.6 A and trip in about 58 s at speed, or about 60 s
    starting cold from rest with the existing ramp. Three moving packages
    draw only 7.5 A and cannot trip the thermal model.

The paragraph at `devices/vfd.py:29-35` identifies gap-collapse overload as
legacy-mode only and hold abuse as the plant-mode overload path.

## Attacks

Legacy gap collapse through the induction interval setpoints `rate_sp_1..3`
(`%QW207..209`, `Sorter.st:57-59`, `2270-2272`) is unchanged: drives in
legacy mode still read register 2.

Writing `%QW102..117` at the PLC has no lasting effect in either mode today,
because the program rewrites them every scan (`Sorter.st:1022-1047`). Writing
drive register 2 directly races the OpenPLC master's next block write. In
plant mode under this contract, neither path affects the drive's current,
whether the plant sample is fresh or stale. A client on public port 502 also
cannot write the read-only diagnostic view to influence the motor.

**Hold abuse is the plant-mode attack path.** Manipulated process conditions
or controller decisions can leave packages at ZONE_FULL, MERGE_CAPACITY, or
CHUTE_FULL while their belt continues to run. Sustaining three such holds on
one belt at rated speed contributes 3(3.6) = 10.8 A drag, for 15.6 A total;
from a cold accumulator that trips in 40 / ((15.6/12)^2 - 1) = 58.0 s at
speed (about 60 s including the ramp). This requires three *simultaneous*
held packages on the *same running belt* and is not demonstrated by the
three-slot upper bound alone. The trip reaches the PLC as zero speed feedback,
and the plant reports
drive-stopped motion (`devices/plant.py:501`, `575-576`). At the default low
200/233 rpm references three held packages give about 12.9 A total and a
steady-speed cold trip around 257 s; do not use the 60 s rated-speed figure
for those settings. One or two held packages *alone* cannot overload at rated
speed, but a mixed hold-plus-riding load can (two held and one riding give
12.9 A and about 257 s at rated speed).

For comparison, *legacy* riding loads of 14 and 20 at rated speed give
17.4 A and 22.8 A and trip in about **36 s** and **15 s** respectively
from a cold accumulator already at rated speed (about 37.7 s and 16.1 s
including the ramp). The 80 s / 22 s figures are for the default 200/233 rpm
references, not rated speed, and are not a plant-mode three-slot scenario.

The private loopback plant-to-drive physics channel is **excluded from the
attack model**. No direct `plant_drag` or sequence spoofing, suppression or
second writer is an attack path in this contract. Other guests cannot reach
the listener; local compromise of the drives VM or plant service is out of
scope. Do not count public-port register writes as equivalent to changing
physical drag. Process-side manipulation that genuinely creates holds remains
in scope. A plant failure is a reliability case: `load_source = 2` marks
stale data, holds last drag, and the PLC heartbeat stops the sorter after its
nominal 3.0 s bound; it never selects register 2 in plant mode.

Detection can compare drive `load_used_x10` and `load_source` with the
plant/PLC hold and motion telemetry. SCADA's `BeltLoad` reads controller-owned
register 2 today and would otherwise still show zero while a held belt heats.
Publish the drive's read-only actual drag and source, clearly distinguished
from the legacy `BeltLoad`; the physical load is not a writable PLC parameter.

## Affected files

| File | Current SHA-256 | Change |
| --- | --- | --- |
| `devices/vfd.py` | `c81fed84ba284eb78cb760331bbf63b79ff1b309fd43945b6f68db9d94d3c62d` | separate loopback physics listener and state, plant-mode drag selection with hold-last on stale, read-only public diagnostics 9..10, docstring; a pure tick function for host tests |
| `devices/plant.py` | `e2331171c82af5d304b0b23b5108c30249f738ce14804baf9dc6cd226dae46f3` | per-belt riding/held drag after each step, private samples and safe mode handshake; withhold PLC heartbeat on any private write failure; no physics writes to port 502 |
| `scada/opcua_server.py` | `2113361697ee9171b21313e96f4a484ad49f29f94a983e2da7075f28917c19c8` | read 11 public drive registers, publish actual drag and source as read-only diagnostic nodes |
| `scada/hmi_ua.py` | `b7f9d266c3126e541e5abe35c86f66978fb4c4f84834de0acead7a8bc603f2bb` | show the load actually used and its source |
| `tests/read_accumulation_state.py` | `7adcabb70b0ff648dec21c23bacd16df6b21466caac9ac2eb3a7f8d967362aa8` | read 11 public drive registers; new reader schema |
| `tests/accumulation_state_snapshot.py` | `1781df6863475ef471c41a9121459066eec09ab466e6afe125a5417f5cdca0d4` | new drive fields, informational |
| `deploy/README.md` | not in manifest | public diagnostics, private listener ports, service-account isolation and mode handshake |
| drive service/network policy | not in manifest | bind private listener only to 127.0.0.1; enforce plant-account-only local writes; deny public writes to diagnostics |
| `deploy/deployment_manifest.json` | owner update | new hashes for `vfd.py`, `plant.py`, both SCADA files and both copies of `read_accumulation_state.py` |

New host tests would cover 9/36-tenths coefficients, the rated-speed 58 s
three-hold curve and ramp, mixed riding/held loads, legacy selection only
outside plant mode, stale hold-last/source=2, PLC heartbeat withheld if any
private drive update fails, invalid/no-initial fail-closed,
sequence freshness, safe mode transition, no public-port physics writes,
read-only diagnostics and loopback/plant-writer isolation, per-belt ownership
through divert/removal, and the three-slot bound. `Sorter.st`
(`952ff58580a2d1a0444b6b0e91f745ff74a923484607645173909c5ab4be3dc6`), the
and `tests/network_flows.json` need no PLC or inter-guest flow change; the
drive service and local writer policy do change in any future implementation.

## Decisions and remaining design work

1. **Adopted for this proposal:** riding = 0.9 A, held against a running belt
   = 3.6 A; three held can trip in about 60 s at rated speed. No slot-count
   increase or fabricated package load. Realizing three concurrent holds on
   one belt still requires a bounded model test.
2. **Sequence counter is the only remaining channel-field choice:** retain a
   private per-drive monotonic sequence (proposed 1..30000, wrapping to 1)
   to detect repeated samples even if drag is unchanged; finalize its width,
   wrap handling and atomicity during design. `load_source` and
   `load_used_x10` are required read-only diagnostics, not alternatives.
3. **Settled:** after a nominal 3.0 s stale interval, keep the last valid
   plant drag and set source 2; never select register 2 while plant mode is
   active. PLC heartbeat is the stop mechanism. Inhibit running without an
   initial valid active sample; reject malformed/out-of-range samples rather
   than clamping them (source 3 and last valid drag retained).
4. **Settled attack boundary:** a separate loopback-only physics listener
   accepts writes only from the plant service; public port 502 keeps its
   existing command/reference inputs and adds only read-only *drag*
   diagnostics, never a physical drag input. Local host/service compromise
   and physics-channel writes are excluded from the attack model. Host-side
   enforcement, not bind address alone, must be tested before deployment.
5. **Not applicable:** no sequence-zero release to PLC register 2 in plant
   mode. Legacy selection requires a stopped, trusted mode-off handshake.
6. **Not applicable:** frozen legacy arrays cannot affect plant-mode load,
   fresh or stale. A mode-entry interlock is required independently.
7. SCADA and typed-snapshot schema changes remain informational as VFD load
   is today (`PHASE2A_RESTORATION.md:40`); specify the final names in Phase 2
   implementation review, not by assuming existing `BeltLoad` is physical.
8. Drive host tests require extracting the tick from the infinite `simulate`
   loop (`devices/vfd.py:66-125`); keep its legacy arithmetic intact.
