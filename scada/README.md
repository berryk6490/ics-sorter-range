# SCADA HMI source

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
