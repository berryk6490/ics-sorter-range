# Plant-owned belt load, version 1 (proposal, not implemented)

This contract moves the mechanical load a drive carries from the PLC to the
drives plant while plant mode (coil 918) is on. Load is physical truth: the
package is on the belt whether or not the controller believes it is. Nothing
here is implemented. No PLC, drive, plant, SCADA or manifest file has changed
for it, and every statement about current behavior below cites the source it
was read from at commit b39d7e6.

The recommendation is a new plant-written register 9 on each drive. `vfd.py`
reads load from register 9 while the plant sample is fresh and falls back to
register 2 otherwise. The PLC program and its Modbus mapping stay unchanged.

**Read this first.** Under the load definition below, the three global PLC
slots cap every belt at three packages. At three packages no drive reaches
rated current at any reference speed, so the thermal model still cannot trip
in plant mode. This contract makes load truthful and observable; it does not
by itself make an overload reachable. The options are listed under open
decisions.

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
4.8 + 2.7 = 7.5 A at rated speed. The legacy gap-collapse chain in the drive
docstring (`devices/vfd.py:32-35`) needs 12 or more on a belt at default
speed and remains a legacy-mode behavior only.

## Registers

| Drive register | Writer | Name and meaning |
| ---: | --- | --- |
| 0..2 | PLC | unchanged: command, speed reference, legacy `belt_load` |
| 3..8 | drive | unchanged: status, frequency, speed, fault, current, thermal |
| 9 | plant | `plant_load`, packages on this belt, 0..20 |
| 10 | plant | `plant_load_seq`, 1..30000 then wraps to 1; 0 means released or never written |
| 11 | drive | `load_source`: 0 legacy register 2, 1 plant fresh, 2 plant stale, 3 plant invalid |
| 12 | drive | `load_used`, the value the current model actually used this tick |

The plant writes registers 9..10 as one FC16 request per drive. In pymodbus
3.6.9 `ModbusSequentialDataBlock.setValues` applies a write as one list slice
assignment, so the drive thread reads load and sequence together. Register 11
and 12 are drive-written and sit past the plant block, following the same
ordering rule the drive already documents for the PLC block
(`devices/vfd.py:8-11`). The PLC's 0..2 write cannot reach them.

The drive widens its read from 9 to 13 registers and keeps its write of 3..8,
adding 11..12 as a separate write. The plant never reads 11..12 and the PLC
never reads past 8.

## Load definition

Load on a drive is the number of packages the plant model holds on that
drive's belt. Drives 1..3 are the induct lanes and drives 4..6 the outbounds,
matching the plant's own feedback indexing (`devices/plant.py:356`, `331`,
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
so it is never counted on two belts. A held package (zone full, merge
capacity, chute full, drive stopped) still counts; held is not unloaded.

The unit is one package, the same as one occupied legacy cell, so the drive's
0.9 A per unit keeps its meaning (`devices/vfd.py:51`).

## Update rate, freshness and absence

The plant computes and writes load after each `model.step`
(`devices/plant.py:805`) inside the existing `if mode:` publication block
(`devices/plant.py:806`), so load describes the positions it just advanced.
It writes all six drives every loop whether or not a value changed, with a
fresh sequence each time. The loop sleeps 100 ms plus its Modbus time
(`devices/plant.py:859`) and 500 ms after an error (`devices/plant.py:860-862`).
The drive samples every 100 ms.

The drive treats the plant sample as fresh while `plant_load_seq` is nonzero
and has changed within the last 30 drive ticks (3.0 s). That matches the PLC's
plant heartbeat bound of 30 scans at a 100 ms task
(`Sorter.st:1826`, `3399`). A fresh sample outside 0..20 is invalid. When the
sample is stale, invalid, released, or never written, the drive uses register
2 exactly as today and reports the reason in `load_source`.

When the plant is absent or stalls, the PLC raises plant fault 1 after 30
scans and clears `sorter_run` (`Sorter.st:1826`, `1831`). Once `sorter_run` is
off, the PLC commands every drive stopped (`Sorter.st:1052-1092`), and a
stopped drive cools whatever its load (`devices/vfd.py:101-111`). The fallback
to register 2 therefore meets a belt that is already being stopped. In plant
mode after a reset, register 2 is 0.

On a cold plant start during an active run the plant refuses to act and
withholds its heartbeat (`devices/plant.py:741-748`); it writes no load, so
the drives fall back. On a new run identity the plant builds an empty model
(`devices/plant.py:752-761`) and its first writes are zero.

## Legacy compatibility

With plant mode off the plant has no active run and skips all publication
(`devices/plant.py:751`, `778-780`), so it writes no load and the drives use
register 2 unchanged. The legacy arrays, their load publication and the legacy
gap-collapse behavior are untouched. When the plant leaves an active run it
writes `plant_load_seq = 0` once to each drive as an explicit release, so the
drive returns to register 2 at the next tick instead of after the 3.0 s bound.

The PLC program, `mbconfig.cfg`, the located variables and `%QW102`..`%QW117`
keep their current meaning. In plant mode those load words still carry the
frozen legacy count and are ignored by the drive while the plant is fresh.

## `vfd.py` docstring correction

Register line 15 becomes "legacy load, used when no fresh plant load is
present" and the table gains registers 9..12. The comment at
`devices/vfd.py:53-58` states its speed. Proposed text:

    Inverse-time overload. The accumulator gains (I/rated)^2 - 1 per second
    above rated and sheds a quarter of that below it. From a cold accumulator
    at the default 200 rpm induct and 233 rpm outbound references, a belt
    trips in about 80 s at 14 packages and 22 s at 20. At rated speed the
    same loads trip in about 36 s and 15 s. Below 12 packages at the default
    references, or 9 at rated speed, current never exceeds rated and the
    accumulator only cools. In plant mode three global slots cap a belt at
    three packages, so only legacy mode can reach a trip.

The paragraph at `devices/vfd.py:29-35` keeps its gap-collapse example with
"in legacy mode" added.

## Attacks

Legacy gap collapse through the induction interval setpoints `rate_sp_1..3`
(`%QW207..209`, `Sorter.st:57-59`, `2270-2272`) is unchanged: drives in
legacy mode still read register 2.

Writing `%QW102..117` at the PLC has no lasting effect in either mode today,
because the program rewrites them every scan (`Sorter.st:1022-1047`). Writing
drive register 2 directly races the OpenPLC master's next block write. In
plant mode under this contract, both paths lose their effect entirely while
the plant sample is fresh.

The new surface is a direct FC16 to drive registers 9..10. Drives answer any
Modbus client (`devices/vfd.py:133`); SCADA's poller already has an allowed
path to them (`NETWORK_BASELINE.md:80`). A spoofed `plant_load` persists only
until the plant's next write, so a sustained spoof must out-write the plant.
The drive uses whichever value is in the register at its 100 ms sample. A
spoof of 14 held against the plant at the default reference trips the belt in
about 80 s; 20 trips it in about 22 s. The trip then reaches the PLC only as
zero feedback, and the plant shows every package on that belt as drive
stopped (`devices/plant.py:501`, `575-576`). This is the same race the legacy
register 2 already allows, moved to a register the PLC does not own.

Denying the plant, or writing `plant_load_seq = 0`, forces the drive onto
register 2. In plant mode that value is zero after reset, which is today's
behavior, and the PLC stops the sorter after 3.0 s of plant silence anyway. An
attacker who silences the plant and keeps advancing `plant_load_seq` with a
false load hides the loss of the plant from the drive, but not from the PLC
heartbeat. Values above 20 are rejected rather than clamped, so a single
extreme write cannot become a maximum load.

Detection gains three signals. `load_source` and `load_used` show what the
drive actually used. Sequence steps other than `old % 30000 + 1` at the drive
mark a second writer. The plant's own log of what it wrote can be compared
with the drive's `load_used`. Plant-to-drive writes are host local, so the
`fw` capture (`tests/pcap_modbus_summary.py:53-57`) sees a spoof only when it
comes from another guest.

SCADA's `BeltLoad` reads register 2 today, so in plant mode it would keep
showing zero while the drive heats on register 9. The UA comment at
`scada/opcua_server.py:465-468` calls load and thermal "the causal chain from
a parameter write to a stopped belt". Keeping that true requires publishing
`load_used` and `load_source` rather than register 2 alone.

## Affected files

| File | Current SHA-256 | Change |
| --- | --- | --- |
| `devices/vfd.py` | `c81fed84ba284eb78cb760331bbf63b79ff1b309fd43945b6f68db9d94d3c62d` | read 0..12, source selection, freshness, registers 11..12, docstring; a pure step function so the host can test it |
| `devices/plant.py` | `e2331171c82af5d304b0b23b5108c30249f738ce14804baf9dc6cd226dae46f3` | per-belt load from the model, FC16 to 9..10 each loop, release on leaving a run |
| `scada/opcua_server.py` | `2113361697ee9171b21313e96f4a484ad49f29f94a983e2da7075f28917c19c8` | read 13 drive registers, publish `LoadUsed` and `LoadSource` |
| `scada/hmi_ua.py` | `b7f9d266c3126e541e5abe35c86f66978fb4c4f84834de0acead7a8bc603f2bb` | show the load actually used and its source |
| `tests/read_accumulation_state.py` | `7adcabb70b0ff648dec21c23bacd16df6b21466caac9ac2eb3a7f8d967362aa8` | read 13 drive registers; new reader schema |
| `tests/accumulation_state_snapshot.py` | `1781df6863475ef471c41a9121459066eec09ab466e6afe125a5417f5cdca0d4` | new drive fields, informational |
| `deploy/README.md` | not in manifest | drive register table |
| `deploy/deployment_manifest.json` | owner update | new hashes for `vfd.py`, `plant.py`, both SCADA files and both copies of `read_accumulation_state.py` |

New host tests would cover the trip table above, source selection, the 3.0 s
fallback, sequence wrap and release, rejection above 20, per-belt counting
through divert and removal, the three-package bound under saturation, and no
plant load writes without an active run. `Sorter.st`
(`952ff58580a2d1a0444b6b0e91f745ff74a923484607645173909c5ab4be3dc6`), the
systemd units, the firewall and `tests/network_flows.json` do not change.

## Open decisions

1. Trip reachability. With three slots, plant load never exceeds three and no
   drive can trip in plant mode. Choose one: accept load as observable only;
   weight load by package mass or footprint and rescale current; give plant
   mode its own per-package current; or raise the slot count, which is a PLC
   change.
2. Registers 10..12. The sequence is what separates a fresh sample from a
   stuck one; `load_source` and `load_used` are what make the drive's choice
   visible. Accept all three, or register 9 only as first proposed.
3. Freshness bound and stale behavior. Recommended 3.0 s to match the PLC
   heartbeat, then fall back to register 2. The alternative is to hold the
   last plant value.
4. Out-of-range handling. Recommended: reject values above 20. The
   alternative is to clamp to 20.
5. Explicit release. Recommended: the plant writes sequence 0 on leaving a
   run. It lets any writer force legacy load, which in plant mode is zero.
6. Frozen legacy arrays. If plant mode is enabled without a reset, register 2
   carries a frozen nonzero count that the fallback would use. Guarding that
   transition is a PLC change.
7. SCADA exposure and the typed snapshot. Publishing `load_used` changes two
   SCADA files and the reader schema; the new fields would be informational,
   as VFD load already is (`PHASE2A_RESTORATION.md:40`).
8. Testability. Host tests of the drive need its tick extracted from the
   infinite `simulate` loop (`devices/vfd.py:66-125`), with no change to its
   arithmetic.
