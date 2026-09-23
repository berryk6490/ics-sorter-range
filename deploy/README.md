# Drives guest simulation

`devices/vfd.py` and `devices/scanner.py` are byte-for-byte copies of the
running `/home/kevin/vfd.py` and `/home/kevin/scanner.py` on the `drives` VM.
The unit templates and nine instance environment files here are also copies
of the deployed configuration. No log files, credentials, virtual environment,
or generated files are included. The deployed Python environment reports
`pymodbus 3.6.9`; `devices/requirements.txt` pins that version.

| Service instance | Modbus TCP address | Script | Purpose |
| --- | --- | --- | --- |
| `vfd@induct1` | `10.10.1.21:502` | `vfd.py` | Induct lane 1 motor |
| `vfd@induct2` | `10.10.1.22:502` | `vfd.py` | Induct lane 2 motor |
| `vfd@induct3` | `10.10.1.23:502` | `vfd.py` | Induct lane 3 motor |
| `vfd@outbnd1` | `10.10.1.24:502` | `vfd.py` | Outbound belt 1 motor |
| `vfd@outbnd2` | `10.10.1.25:502` | `vfd.py` | Outbound belt 2 motor |
| `vfd@outbnd3` | `10.10.1.26:502` | `vfd.py` | Outbound belt 3 motor |
| `scanner@tunnel1` | `10.10.1.27:502` | `scanner.py` | Lane 1 barcode tunnel, stream 1 |
| `scanner@tunnel2` | `10.10.1.28:502` | `scanner.py` | Lane 2 barcode tunnel, stream 2 |
| `scanner@tunnel3` | `10.10.1.29:502` | `scanner.py` | Lane 3 barcode tunnel, stream 3 |

All addresses must be assigned to the guest's isolated NIC before starting
the units. These address aliases are machine network configuration and are
not copied here. The supplied unit templates expect scripts in `/home/kevin`,
a Python environment at `/home/kevin/venv`, and instance files under
`/etc/vfd` and `/etc/scanner`. Place the repository copies at those paths and
run `systemctl daemon-reload` before enabling the nine named instances. The
units run as root, restart on failure, and append output to per-instance files
under `/var/log`. Those paths and policies are copied from the working guest.

## Drive holding registers

All six VFD instances use the same map. The PLC writes registers 0–2; the
drive updates 3–8 every 100 ms.

| Address | Name | Meaning |
| ---: | --- | --- |
| 0 | `cmd_word` | bit 0 run, bit 1 forward, bit 2 fault reset |
| 1 | `speed_ref` | requested rpm |
| 2 | `belt_load` | occupied cells |
| 3 | `status_word` | bit 0 ready, bit 1 running, bit 2 faulted |
| 4 | `out_freq` | frequency in tenths of hertz |
| 5 | `speed_fb` | actual rpm |
| 6 | `fault_code` | 0 none, 1 overcurrent, 2 overspeed |
| 7 | `current` | current in tenths of an ampere |
| 8 | `thermal` | percent of trip threshold |

## Scanner holding registers

All three tunnels use the same map. The PLC writes registers 0–3; the scanner
checks for a new trigger every 50 ms and writes 4–10. `result_seq` must match
the triggering `trig_seq` before the PLC accepts a result.

| Address | Name | Meaning |
| ---: | --- | --- |
| 0 | `trig_seq` | new request sequence |
| 1 | `trig_serial` | package serial under the tunnel |
| 2 | `seed` | random-stream seed from the PLC |
| 3 | `noread_rate` | failed-read rate per mille; 0 selects the scanner's default 22 |
| 4 | `result_seq` | completed request sequence |
| 5 | `barcode` | destination × 1000 + serial, or 0 for an unreadable package |
| 6 | `status` | 0 good, 1 no-read, 2 multiple, 3 oversize, 4 duplicate, 5 invalid |
| 7 | `length_cm` | package length |
| 8 | `width_cm` | package width |
| 9 | `height_cm` | package height |
| 10 | `scan_count` | cumulative scans, wrapping at 32000 |

## Reset behavior observed on the running guests

The `scanner.py` docstring describes reseeding when a run resets, but its code
actually reseeds only when register 2 changes to a different nonzero value.
The PLC's `reset_cmd` clears package state while retaining a valid
`master_seed`, so a reset with the same seed does **not** replay the scanner's
barcode sequence or clear its recent-barcode history. Consecutive live tests
with seed 137 produced different first-package destinations. In a later run,
the first serial received status 4 with barcode `7002`, a duplicate from an
earlier package. The live test now changes the seed to 138 and back to 137
before reset so the scanner clears its recent-barcode history; it restores
the original seed after the test. This repository preserves the deployed code
exactly. A general same-seed replay would need a separate reset signal or an
explicit seed change protocol.
