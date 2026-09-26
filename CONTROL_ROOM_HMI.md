# Optional control room HMI

The original HMI stays at `/`. The new browser screen is served at
`/hmi-next` by the same Flask process on SCADA port 8000. It is an
alternative presentation of the existing `/api`; the data path remains
plant → PLC validation → OPC UA → HMI API → browser. The browser does not
connect to Modbus, PLC, or plant.py.

## What the screen shows

- Three induction lanes and three outbound belts, with selectable package
  identity and the last **PLC validated** plant position. No browser-side
  motion estimate is applied to plant mode. Occupied slots with unavailable
  rows keep the last position and are marked stale.
- Zone, motion, hold reason and dwell time, conditioned photoeyes, six VFD
  feedback values, nine **confirmed-load** counters, and process faults.
- The trailer 2 chute's measured occupancy and distinct Acknowledge, Empty,
  Resume actions. Other trailers have no modeled full sensor.
- A current-browser observation list for package appearance and holds. This
  list is not a durable event log or a substitute for XLe's journal.

The Master button calls the existing `/cmd/run/0|1` endpoint. Chute actions
call the existing `/chute/action/<action>` endpoint. The Python service still
checks OPC UA availability and, for chute actions, sample freshness and PLC
action acknowledgement. The new page is not an authentication or safety layer.

## Review and verification

Run `node --test scada/hmi_next/test_app.mjs` for the package identity, stale
quality, and last-position checks. `node --check scada/hmi_next/app.js` checks
browser-script syntax. The new page uses native JavaScript and SVG; it does
not need Node, npm, Java, or a frontend server in the SCADA VM.

After the ongoing chute tests finish, deploy `scada/hmi_ua.py` and the whole
`scada/hmi_next/` directory together using the repository's normal backup,
hash and service procedure. Update the deployment manifest for the changed
HMI source and added static files. Verify both `/` and `/hmi-next`, that
both use the same `/api` snapshot, and that live three-lane motion, hold,
fault, and stale states render correctly. Recheck that all operator actions
and restoration behave as before. This branch has **not** been deployed or
tested against live guests.

The map focuses on plant mode. The legacy PLC cell mode remains available on
the original page; the new page identifies that mode but does not render
its per-cell package representation.
