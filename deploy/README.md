# Drives guest simulation

`devices/vfd.py` and `devices/scanner.py` are the reproducible sources for
`/home/kevin/vfd.py` and `/home/kevin/scanner.py` on the `drives` VM.
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
checks every 50 ms and writes 4–10. A normal result is accepted only when
`result_seq` matches the request and `run_nonce` matches the current run.

| Address | Name | Meaning |
| ---: | --- | --- |
| 0 | `trig_seq` | normal request 1–30000; 32767 requests reset; 0 is idle |
| 1 | `trig_serial` | package serial; reset nonce when `trig_seq` is 32767 |
| 2 | `seed` | random-stream seed from the PLC |
| 3 | `noread_rate` | failed-read rate per mille; 0 selects the scanner's default 22 |
| 4 | `result_seq` | completed request sequence |
| 5 | `barcode` | destination × 1000 + serial, or 0 for an unreadable package |
| 6 | `status` | 0 good, 1 no-read, 2 multiple, 3 oversize, 4 duplicate, 5 invalid, 6 reset acknowledged |
| 7 | `length_cm` | package length |
| 8 | `width_cm` | package width |
| 9 | `height_cm` | package height |
| 10 | `run_nonce` | reset token echoed in the ACK and every normal result |

## Reset exchange

On init or operator reset, the PLC increments a private nonce (1–30000), sends
`trig_seq=32767`, `trig_serial=nonce`, and the current seed to all three
tunnels, then holds package movement. Each scanner reseeds its destination,
failure, and dimension streams even if the seed is unchanged; clears its
recent-barcode history, request sequence, and scan count; and replaces the
entire previous response with `[32767, 0, 6, 0, 0, 0, nonce]` at registers
4–10. Repeated polls of the same reset command do not reseed again. The PLC
waits for all three ACKs with the matching nonce, then writes `trig_seq=0`.
Normal replies echo the nonce in register 10. This rejects a delayed response
from the preceding run even if its normal sequence number is reused.

Register 10 previously held `scan_count`; it now holds `run_nonce`. Its address
and the 11-register read block are unchanged. A PLC and scanner from different
protocol versions must not be mixed. The nonce wraps after 30000 resets;
the handshake assumes no response from 30000 runs earlier remains in flight.
After a cold PLC restart, a scanner service restart is needed if its current
nonce happens to equal the PLC's initial nonce; the two-run live replay below
exercises ordinary operator resets, not that restart case.
