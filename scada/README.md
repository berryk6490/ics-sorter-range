# SCADA HMI source

The later XLe heartbeat alarm, Acknowledge, and Retry display is documented
in [XLE_LIVENESS.md](../XLE_LIVENESS.md).

The deployed sources and units were copied from the `scada` VM before any
changes to that guest. The exact original sources are in `baseline/`; the
root copies contain the fault display and OPC UA mapping updates.

| Deployed path | Original SHA-256 |
| --- | --- |
| `/home/kevin/hmi_ua.py` | `f608078bdf136634d9cfb6305308b57c2424649858870ef103d8df954ec71d3e` |
| `/home/kevin/opcua_server.py` | `27c633d9e3f3dbc0ba9b86747b9d836be6254542ed60a098ec534421f09d878e` |
| `/etc/systemd/system/sorter-hmi.service` | `d3292f03a46765c0779ad696954f55da306181ff628f47f399ccc5abc5dd4253` |
| `/etc/systemd/system/opcua-server.service` | `a9d64b2674d99ec292cde15793c2bec762830e9dea15b1f9432df74af6a641f9` |

The units run the two Python files using `/home/kevin/opcua/bin/python`.
Secrets, virtual environments, logs, and generated files are excluded.

The updated OPC UA server extends the PLC holding-register read from 50 to
59 words (starting at 200), and the coil read from 32 to 34 bits (starting at
880). It exposes `Process/Status/ScannerResetState`, `ScannerFaultMask`,
`ScannerAckMask`, and `ScannerWaitScans` as read-only Int16 values, plus
`ScannerFaultAck` and `ScannerRetry` as writable Boolean commands. The HMI
subscribes to those nodes and adds a tunnel-specific alarm. State 3 shows an
**Acknowledge** button; state 4 keeps the alarm visible and shows **Retry
Reset**. The HMI command endpoint writes those controls through OPC UA.

Before the change, the running HMI had only jam, collision, no-read, and
no-home process alarms; it could not identify a scanner reset fault. With
tunnel 2 stopped, its updated `/api` returned
`connected=True, scanner_state=3, scanner_fault_mask=2,
scanner_ack_mask=5, inducted=0, run=False`. After the tunnel service returned,
the fault values remained. The HMI acknowledgement command returned
`{"ok":true}` and state became 4 with mask 2. The retry command returned
`{"ok":true}`; after all scanners acknowledged, state became 0 and mask 0.
The HMI page response includes the `SCANNER TUNNEL` alarm rendering code;
browser-level rendering was not tested.
The final deployed HMI and OPC UA source checksums match `scada/hmi_ua.py`
(`080f4fbdd67fec2dada2fef83b84babe54a1004ea408a62108f9fd82bb482a76`)
and `scada/opcua_server.py`
(`4911944891d6a9dde70c01434c305efd4bd15b693c8a2888ded0e7b16cd7f8a5`).
Both services were active before the SCADA VM was returned to its initial off
state.
