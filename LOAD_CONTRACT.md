# Plant-owned drag load, version 2 (proposal, not implemented)

This contract moves the mechanical load a drive carries from the PLC to the
drives plant while plant mode (coil 918) is on. Load follows plant-model
membership rather than controller belief; non-stateful door removal can still
undercount physical footprint overlap, as described below. Nothing
here is implemented. No PLC, drive, plant, SCADA or manifest file has changed
for it, and every statement about current behavior below cites the source it
was read from at commit b39d7e6.

The plant owns an effective drag sample for each belt, sent to its drive over
a separate loopback-only channel on the drives VM. The public Modbus port 502
retains the PLC's existing registers 0..8 but cannot write or select physical
drag in plant mode. A moving package contributes 0.9 A of torque current; a
package held against a *running* belt by ZONE_FULL, MERGE_CAPACITY, or
CHUTE_FULL contributes 3.6 A. No changes in this document are deployed.

**Read this first.** At most three packages own PLC slots at once, *plus*
terminal packages still clearing a door in the plant model: XLe can release a
terminal row before the package's rear clears the door (`services/xle.py:459-487`,
`devices/plant.py:523-528`). Three moving packages cannot overload a drive,
but three held packages at rated speed can trip its existing thermal model in
about 60 seconds. Extra clearing packages only make a trip easier, not harder;
neither the slot count nor this arithmetic demonstrates three simultaneous
holds on one running belt. Count actual per-belt model membership, not slots.

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

The drive reads registers 0..8 on a nominal 100 ms tick (`devices/vfd.py:63`,
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
reads register 5 from each every loop (`devices/plant.py:19-20`, `693-697`, `786`) and
writes nothing to them. The drive addresses `.21`..`.26` sit on the same NIC
as the plant (`NETWORK_BASELINE.md:49`), so a plant write to a drive is host
local and adds no network flow.

SCADA polls drive registers 0..8 (`scada/opcua_server.py:100-105`, `175`) and
publishes register 2 as `BeltLoad` beside `ThermalLoad`
(`scada/opcua_server.py:465-472`, `664-672`). The HMI renders both
(`scada/hmi_ua.py:123-126`, `317-318`, `916-917`). The typed accumulation
snapshot reads 0..8 (`tests/read_accumulation_state.py:26-40`) and records
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
(`devices/plant.py:319-323`, `466-471`). This is not a physical three-package
per-belt maximum after terminal release; a retained terminal package may share
a belt with a newly inducted package. No reproducible saturation result proves
otherwise. Three packages give 4.8 + 3(0.9) = 7.5 A at rated speed *if
moving*. For three held on a running belt, the steady rated-speed current is
1.8 A magnetising + 3.0 A windage +
3(3.6 A) drag = **15.6 A**. With 12.0 A rated and a trip accumulator of 40,
the heating rate is (15.6 / 12)^2 - 1 = 0.69 per second, giving
40 / 0.69 = **58.0 s** from a cold accumulator already at rated speed.
Solving for precisely 60 s gives (12 sqrt(1 + 40/60) - 4.8) / 3 =
3.564 A per held package; 3.6 A is the tenth-ampere rounded coefficient.
The drive's 100 ms tick ramps from rest to 1750 rpm in about 4.4 s at
400 rpm/s; this adds about 2 s of net thermal delay (60.0 s in a discrete
calculation). One held package gives 8.4 A and
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
is the only initiator of a private FC16 write of three contiguous 16-bit
registers `[mode, drag, seq]`: `mode` 1 selects plant drag, 0 requests legacy;
`drag` is `plant_drag_x10`; `seq` is `plant_load_seq`. The private datastore is
separate from public port 502. The drive reads all three fields in one
`getValues` call per sample, never as independent reads. The sequence is
1..30000, wrapping from 30000 to 1; zero is invalid. A value equal to the last *applied*
sequence is a repeat and does not reset the freshness watchdog; any other
valid value is fresh under the single-writer, drives-guest trust boundary.
The plant advances the sequence on each new sample even if drag is unchanged.
`plant_drag_x10` is torque-current contribution in tenths
of an ampere, not a package count; 9 means one riding package and 36 one held
package. Do not cap it at 108 from the three PLC slots: count all members on
each belt, including terminal packages still clearing the door. Define and
test the representable maximum and fail closed on overflow, never truncate or
clamp physical drag to a slot-derived maximum. The private mode indicator
must persist independently of sample freshness so a timeout cannot switch
the drive to legacy load. The trust boundary is the drives guest OS: physics
channel samples are trusted only insofar as that guest and its plant process
are trusted. Code execution on the drives guest, including another local
writer or compromise of the plant account, is out of scope for physics-channel
attacks. A loopback bind limits ordinary inter-guest reachability but does not
verify local writer identity. Deployment preflight must read back all six
listeners as bound *only* to 127.0.0.1 (ports 15021..15026), reject wildcard
or ICS-NIC binds, and check host routing/proxy policy before claiming isolation.

Expose drive-generated diagnostics to SCADA on public port 502 as read-only
registers 9 (`load_source`: 0 legacy, 1 plant fresh, 2 plant stale, 3 invalid
or no initial sample) and 10 (`load_used_x10`: actual torque contribution).
The public server must reject FC06/FC16 writes to these diagnostic registers;
they must never feed the drive's physics, even if another client writes port
502. The PLC's existing 0..8 mapping is unchanged; SCADA reads 0..10 instead
of 0..8. The private sample is not exported on the public endpoint. Host tests
must cover public write rejection and local fail-closed behavior; deployment
preflight must check actual loopback binding before any live validation.

## Load definition

Drag on a drive is the sum of contributions from packages the plant model
holds on that drive's belt. Drives 1..3 are the induct lanes and drives 4..6
the outbounds, matching the plant's own feedback indexing (`devices/plant.py:356`, `331`,
`482`). A package is on its lane belt from induction until it diverts, and on
outbound belt `p.outbound` after that (`devices/plant.py:397-398`,
`608-609`). A package counts if it is in `model.packages`. Queued requests not
yet inducted do not count (`devices/plant.py:319-326`, `466-474`), and neither
do terminal records in `model.recent`.

**Load is membership in `model.packages` on that belt**, not a general
footprint-overlap assertion. Lane recirculation removes a package at cell 19
(`devices/plant.py:101`, `375-381`, `565-571`). In the accumulation path, an
outbound package remains until its rear passes the trailer door
(`devices/plant.py:523-528`); in the non-accumulation stateful path, the same
rear-clearance condition applies (`devices/plant.py:345-351`). In the
non-stateful non-accumulation path, the model removes an outbound package on
the first step when its front reaches or passes the trailer door
(`devices/plant.py:329-344`), rather than deliberately waiting for rear
clearance. This can undercount footprint overlap if its rear is still on the
belt at that step; no universal duration or amount of undercount is asserted.
The model does not represent a package straddling a divert; at the
divert step it moves wholly from lane to outbound,
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

**Drive-local fail-closed rule:** measure elapsed time monotonically since the
last *applied new* `plant_load_seq` on each VFD, not since TCP acceptance or a
repeated value. An atomic private `[mode, drag, seq]` sample with valid mode,
representable drag and a valid sequence different from the last applied one
is fresh. A duplicate sequence, even with different mode or drag, is ignored
and does not reset the watchdog. If no new sequence arrives within 500 ms in
plant mode, set latched `fault_code = 3` (simulation integrity lost), stop the VFD locally,
and freeze thermal integration. The watchdog must remain effective even when
the Modbus listener accepts traffic that the simulation tick does not apply;
host tests must simulate accepted-but-unapplied, repeated, and partial updates.
Hold the **last valid drag** only inside that 500 ms window; report
`load_source = 2` on missed expected updates during the window, not as a
license to run indefinitely. At timeout retain the last drag for diagnosis,
but no current/heat integration continues under fault 3. An out-of-range or
malformed private sample does not replace the last valid value and sets
`load_source = 3`; it cannot reset the freshness clock. The plant alone
initiates the private mode handshake. **Entry:** the drive selects plant drag
only on a fresh `mode = 1` sample; an attempted entry or run without such a
sample fails closed with fault 3 (no fresh sample by 500 ms also faults 3),
never with public register 2 as substitute. **Exit:** a fresh `mode = 0`
sample selects legacy register 2 only when speed feedback is exactly 0;
while moving, ignore that mode-off request and continue the plant-mode
watchdog from the last *applied* sample (the ignored sample does not refresh
it). The plant sends a new sequence on a later stopped exit attempt. Mode
and last valid drag remain latched through stale timeouts: neither an expired
sequence nor a PLC command silently selects legacy. **Fault-3 restoration:**
require *both* PLC fault-reset bit (cmd bit 2) and a fresh valid private sample
received **after** fault 3 latched; the drive remains stopped and frozen until
both are present. A mode-1 sample restores plant selection; mode 0 may select
legacy only with speed feedback 0. A reset bit alone, a pre-fault sample or
an unchanged sequence cannot restore the drive. Here “trusted” means received
on the private loopback channel under the drives-guest OS trust boundary; it
does not claim local writer identity checks. A run with fault 3 is **invalid
evidence**, not an overload/attack outcome; preserve the fault and reject
functional claims.

The PLC independently raises plant fault 1 when heartbeat age is **greater
than** 30 scans and clears `sorter_run` (`Sorter.st:1819-1831`; nominal 100 ms
task at `Sorter.st:3399`). It then commands drives off
(`Sorter.st:1052-1092`); the existing VFD can still draw current while
decelerating (`devices/vfd.py:83-111`). Neither that scan count nor PLC command
delivery bounds physical stale-drag exposure at 3.0 s. The **drive-local
500 ms fault** above is the primary integrity stop, independent of PLC
heartbeat; no hard wall-clock stop can be inferred from PLC scans alone.
**The plant must also withhold its PLC heartbeat if any private drive update
fails**, as defense in depth. Today it publishes heartbeat before `model.step`
and before any proposed private drive write (`devices/plant.py:773-774`,
`805`); move that write after all six private updates, with failed/partial
updates withholding the heartbeat. A successful transport write alone is not
proof of applied freshness; host tests must cover the local watchdog, this
heartbeat failure path, and stopped-drive restoration separately.

On a cold plant start during an active run the plant refuses to act and
withholds its heartbeat (`devices/plant.py:741-748`); it sends no new private
sample, so the drive may hold last drag only until the local 500 ms fault (or
fault immediately on attempted mode entry without a valid sample). On a new
run identity the plant builds an empty model
(`devices/plant.py:752-761`) and must establish a fresh private `mode = 1`,
zero-drag sample before the drives may run. These are implementation
requirements, not behaviors of the currently deployed drive.

## Legacy compatibility

Today, with plant mode off the plant has no active run and skips publication
(`devices/plant.py:751`, `778-780`). Under this proposal, the plant must
initiate a fresh private `mode = 0` sample once speed feedback is 0; only then
does the drive resume register 2. The legacy arrays, their load publication
and the legacy gap-collapse behavior are untouched. No sequence-zero release
or timeout fallback is used: last valid drag is selected only inside the
500 ms grace window in plant mode; timeout latches fault 3 and stops, not
legacy selection. A mode-0 sample while moving cannot release to legacy.

The PLC program, `mbconfig.cfg`, the located variables and `%QW102`..`%QW117`
keep their current meaning. In plant mode those load words carry zero after
reset or a frozen prior count after an unguarded mode change; the drive ignores
them during the fresh/grace period and after fault 3.
Consequently no sequence-zero release or guard against frozen legacy counts is
part of this load contract (former decisions 5 and 6). The explicit fresh
mode-0 exit and fresh mode-1 entry must be tested fail-closed.

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

The paragraph at `devices/vfd.py:29-35` must be corrected to identify
gap-collapse overload as legacy-mode only and hold abuse as the proposed
plant-mode overload path; the current text does not make that distinction.

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
three-slot allocation alone. The trip reaches the PLC as zero speed feedback,
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
second writer is an in-scope attack path: this assumes the drives guest OS and
plant process are trusted; code execution on that guest is out of scope.
Loopback-only reachability must be checked at deployment, not inferred from
the proposed bind. Do not count public-port register writes as equivalent to
changing physical drag. Process-side manipulation that genuinely creates
holds remains in scope. A failed physics update is a reliability/integrity
failure: hold last drag only inside the 500 ms grace window, then fault 3,
stop locally, freeze thermal and invalidate the run as attack evidence.
The PLC heartbeat is additional fault signaling, not the stale-drag timer;
register 2 is never selected in plant mode.

Detection can compare drive `load_used_x10` and `load_source` with the
plant/PLC hold and motion telemetry. SCADA's `BeltLoad` reads controller-owned
register 2 today and would otherwise still show zero while a held belt heats.
Publish the drive's read-only actual drag and source, clearly distinguished
from the legacy `BeltLoad`; the physical load is not a writable PLC parameter.

## Affected files

| File | Current SHA-256 | Change |
| --- | --- | --- |
| `devices/vfd.py` | `c81fed84ba284eb78cb760331bbf63b79ff1b309fd43945b6f68db9d94d3c62d` | separate loopback physics listener, atomic private sample read, mode entry/exit/reset rules, 500 ms integrity watchdog/fault 3 with thermal freeze, hold-last only in grace window, read-only public diagnostics 9..10, docstring; a pure tick function for host tests |
| `devices/plant.py` | `e2331171c82af5d304b0b23b5108c30249f738ce14804baf9dc6cd226dae46f3` | per-belt riding/held drag after each step, private FC16 `[mode, drag, seq]` samples with fresh stopped-mode exit; withhold PLC heartbeat on any private write failure; no physics writes to port 502 |
| `scada/opcua_server.py` | `2113361697ee9171b21313e96f4a484ad49f29f94a983e2da7075f28917c19c8` | read 11 public drive registers, publish actual drag and source as read-only diagnostic nodes |
| `scada/hmi_ua.py` | `b7f9d266c3126e541e5abe35c86f66978fb4c4f84834de0acead7a8bc603f2bb` | show the load actually used and its source |
| `tests/read_accumulation_state.py` | `7adcabb70b0ff648dec21c23bacd16df6b21466caac9ac2eb3a7f8d967362aa8` | read 11 public drive registers; new reader schema |
| `tests/accumulation_state_snapshot.py` | `1781df6863475ef471c41a9121459066eec09ab466e6afe125a5417f5cdca0d4` | new drive fields, informational |
| `deploy/README.md` | not in manifest | public diagnostics, private listener ports, trusted-host attack boundary, loopback preflight and stopped mode handshake |
| drive service/network policy | not in manifest | bind private listener only to 127.0.0.1; preflight all six binds and routing/proxy policy; deny public writes to diagnostics |
| `deploy/deployment_manifest.json` | owner update | new hashes for `vfd.py`, `plant.py`, both SCADA files and both copies of `read_accumulation_state.py` |

New host tests would cover 9/36-tenths coefficients, the rated-speed 58 s
three-hold curve and ramp, mixed riding/held loads, legacy selection only
outside plant mode, and hold-last/source=2 solely inside the 500 ms window.
Test one FC16 write and one three-field `getValues` read, sequence wrap,
duplicate versus different valid sequence, missing/repeated/accepted-but-
unapplied samples, timeout fault 3, stopped output and frozen thermal. Mode-transition
tests must prove fresh mode 1 entry, no-fresh entry fault 3 (never legacy),
fresh mode 0 exit only with speed feedback 0, moving mode 0 ignored without
watchdog refresh, and fault-3 restoration only with cmd bit 2 *and* a fresh
post-fault sample. Test invalid samples, heartbeat withholding on any of six
failed or partial updates, public diagnostic write rejection and per-belt membership
through divert and mode-specific removal. A lifecycle test must assert each
belt's actual `model.packages` count and drag through terminal release,
rear clearance, a slow/stopped outbound, and reinduction; it must not derive a
three-package cap solely from PLC slots. Deployment preflight must establish
all six physics ports are loopback-only before a run. `Sorter.st`
(`952ff58580a2d1a0444b6b0e91f745ff74a923484607645173909c5ab4be3dc6`)
and `tests/network_flows.json` need no PLC or inter-guest flow change; drive
service binding and diagnostics policy do change in a future implementation.

## Decisions and remaining design work

1. **Closed coefficients, pending test acceptance:** riding = 0.9 A and held
   against a running belt = 3.6 A. Three sustained holds at rated speed yield
   the calculated ~60 s trip; this is not a demonstrated reachable scenario.
   Slot count does not bound terminal packages still clearing a door.
2. **Closed private sequence/protocol:** the plant alone initiates one FC16
   write of `[mode, drag, seq]`; the drive reads all three in one `getValues`
   call. Sequence is one 16-bit register, 1..30000 wrapping to 1. Equal to
   last applied is not fresh and does not reset the watchdog; any other valid
   value is fresh under the single-writer trust assumption. Diagnostics
   `load_source` and `load_used_x10` are read-only, not physics inputs.
3. **Closed integrity failure policy:** hold last drag inside 500 ms of the
   last applied fresh sample; on expiry latch fault 3, stop locally, freeze
   thermal and invalidate run evidence. Missing entry or invalid samples
   cannot select register 2 in plant mode. PLC heartbeat withholding on any
   failed private update is defense in depth, not a physical stop deadline.
4. **Closed attack boundary:** trust the drives guest OS; local code execution
   and private-channel attacks are out of scope. Public port 502 has no
   physics input and diagnostic registers must reject writes. Loopback bind
   and host routing/proxy policy require deployment preflight verification;
   loopback does not verify local process identity.
5. **Closed mode handshake:** only a fresh private mode-1 sample enters plant
   drag. Only a fresh private mode-0 sample with speed feedback 0 returns to
   legacy; moving mode-0 requests are ignored without watchdog refresh. Fault
   3 requires cmd bit 2 plus a fresh sample received after the fault for
   restoration. Sequence zero and timeout never release to register 2.
6. **Closed frozen-array choice:** no separate PLC array guard is proposed;
   frozen counts cannot feed plant-mode physics under the tested mode and
   no-fallback rules. On a failed entry the drive faults 3, not legacy load.

**Remaining implementation tasks and test acceptance criteria (not design
decisions):** prove per-belt model counts and drag through terminal release,
slow/stopped door clearance and reinduction; separately demonstrate a bounded
three-hold same-belt case before claiming reachability. Host-test atomic
private writes/reads, sequence wrap and duplicates, accepted-but-unapplied
updates, the 500 ms watchdog, stopped/frozen fault 3, entry, moving exit,
stopped exit and two-condition restoration; reject public diagnostic writes.
Deployment preflight must confirm all six loopback-only listeners and routing/
proxy policy. Set final SCADA and typed-snapshot field names in implementation
and keep them informational as VFD load is today
(`PHASE2A_RESTORATION.md:40`), never relabeling legacy `BeltLoad` as physical.
Extract the VFD tick from the infinite `simulate` loop
(`devices/vfd.py:66-125`) for host tests without changing legacy arithmetic.
No implementation or Phase 3 work is authorized by this proposal; the
contract proceeds to approval after this docs-only revision.
