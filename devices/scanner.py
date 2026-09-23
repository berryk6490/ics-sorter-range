#!/usr/bin/env python3
"""Emulated camera tunnel exposing a Modbus register profile.

    python3 scanner.py 10.10.1.27 tunnel1 <stream>

One instance per induct lane. The PLC writes a trigger when a package reaches
the tunnel cell; this returns a barcode, a read result and dimensions.

Why this exists. Until now the PLC invented a barcode at induction, which put
the controller in charge of physical truth: it could not be wrong about what a
package was, because it decided. Physically a package exists before anyone
knows what it is, and the identity arrives from a device that can fail. Moving
barcode generation here means the controller's belief and the package's
identity are separate things that can disagree, which is the precondition for
every identification and routing attack worth running.

Register profile. Master-written registers sit at the front, for the same
reason as the drive profile: OpenPLC writes a mapped output block as a unit.

    0  trig_seq     master   incremented by the PLC to request a scan
    1  trig_serial  master   which package is under the tunnel
    2  seed         master   PRNG root, mirrored from the PLC's master_seed
    3  noread_rate  master   per mille, pushed down from the controller
    4  result_seq   scanner  echoes trig_seq when the result is ready
    5  barcode      scanner  destination x 1000 + serial, 0 on a failed read
    6  status       scanner  see the status constants below
    7  length_cm    scanner
    8  width_cm     scanner
    9  height_cm    scanner
   10  scan_count   scanner  cumulative scans performed

The no-read rate is configuration pushed from the controller rather than a
constant here, which is how a tunnel is actually set up and which keeps the
rate a writable tag: raising it is an attack on read reliability that never
touches a single packet the tunnel sends.

The sequence handshake is what makes the exchange a request and a response
rather than a shared variable. The PLC must wait for result_seq to catch up,
so a scan takes real time and can be late, lost, or answered out of order.
That is the failure domain a scan result actually lives in.

Determinism. Every random draw comes from a stream seeded by the PLC's
master_seed, so a scenario replays identically. Reseeding happens whenever the
seed register changes, which is what a run reset looks like from here.
"""
import sys
import threading
import time

from pymodbus.server import StartTcpServer
from pymodbus.datastore import (ModbusSequentialDataBlock,
                                ModbusSlaveContext, ModbusServerContext)

# Read results. GOOD is the overwhelming majority; the rest are the ways a
# real tunnel fails, each of which the controller has to handle differently.
ST_GOOD, ST_NOREAD, ST_MULTIPLE, ST_OVERSIZE, ST_DUPLICATE, ST_INVALID = range(6)

# Per mille rates for the non-good outcomes. The no-read rate arrives from the
# controller; the rest are properties of the tunnel. Together they sum to
# roughly the 30 per mille the range was baselined at, so a clean run stays
# comparable with earlier data.
DEFAULT_NOREAD = 22
RATE_MULTIPLE = 4
RATE_OVERSIZE = 3
RATE_DUPLICATE = 2
RATE_INVALID = 1

OVERSIZE_CM = 120           # any dimension past this is an oversize result
DT = 0.05                   # service loop period, faster than the PLC scan


class Stream:
    """A seeded generator, one per purpose so that drawing a dimension cannot
    shift the barcode sequence.

    Deliberately not the same tiny generator the PLC uses. The PLC is stuck
    with small constants because it does INT arithmetic and anything larger
    overflows; at those moduli the low bits are badly distributed and rates
    below about one percent either never fire or fire at several times their
    nominal rate. Nothing here is constrained that way, so it uses a
    full-width LCG and takes the high bits, which is what makes a three per
    mille event actually occur three times in a thousand."""

    M = 2 ** 31

    def __init__(self, a, c, salt):
        self.a, self.c, self.salt = a, c, salt
        self.v = 1

    def seed(self, v):
        self.v = (abs(v) * 2654435761 + self.salt) % self.M

    def next(self, n):
        self.v = (self.v * self.a + self.c) % self.M
        return (self.v >> 16) % n


def simulate(ctx, tag, lane):
    # Stream constants differ per lane so three tunnels fed the same seed do
    # not produce the same barcodes.
    # Distinct salts so three tunnels handed the same run seed do not read
    # the same barcodes.
    s_dest = Stream(1103515245, 12345, 7919 * lane)
    s_fail = Stream(1103515245, 12345, 6271 * lane + 11)
    s_dims = Stream(1103515245, 12345, 5081 * lane + 23)

    last_seed = None
    last_seq = 0
    recent = []                 # serials seen lately, for duplicate detection

    while True:
        time.sleep(DT)
        hr = ctx[0].getValues(3, 0, count=11)
        (trig_seq, trig_serial, seed, noread_rate,
         result_seq, _, _, _, _, _, scan_count) = hr
        rate_noread = noread_rate if noread_rate > 0 else DEFAULT_NOREAD

        if seed and seed != last_seed:
            last_seed = seed
            s_dest.seed(seed)
            s_fail.seed(seed)
            s_dims.seed(seed)
            recent = []
            scan_count = 0
            print(f"[{tag}] reseeded to {seed}", flush=True)

        if trig_seq == last_seq or trig_seq == 0:
            continue
        last_seq = trig_seq

        length = 20 + s_dims.next(100)
        width = 15 + s_dims.next(60)
        height = 10 + s_dims.next(50)

        roll = s_fail.next(1000)
        dest = s_dest.next(9) + 1
        barcode = dest * 1000 + (trig_serial % 1000)

        if roll < rate_noread:
            status, barcode = ST_NOREAD, 0
        elif roll < rate_noread + RATE_MULTIPLE:
            # Two labels in view. The tunnel cannot say which is the package's
            # own, so it reports the condition rather than guessing.
            status, barcode = ST_MULTIPLE, 0
        elif roll < rate_noread + RATE_MULTIPLE + RATE_OVERSIZE:
            status = ST_OVERSIZE
            length = OVERSIZE_CM + s_dims.next(40)
        elif roll < rate_noread + RATE_MULTIPLE + RATE_OVERSIZE + RATE_DUPLICATE:
            # The same label read twice: a real barcode belonging to a package
            # already counted. The read succeeded, which is what makes this
            # more dangerous than a no-read.
            status = ST_DUPLICATE
            if recent:
                barcode = recent[-1]
        elif roll < (rate_noread + RATE_MULTIPLE + RATE_OVERSIZE
                     + RATE_DUPLICATE + RATE_INVALID):
            # Decoded cleanly but into something that is not a valid label.
            status = ST_INVALID
            barcode = 99000 + s_dest.next(999)
        else:
            status = ST_GOOD

        if barcode and status in (ST_GOOD, ST_OVERSIZE):
            recent.append(barcode)
            if len(recent) > 8:
                recent.pop(0)

        scan_count = (scan_count + 1) % 32000
        ctx[0].setValues(3, 4, [trig_seq, barcode, status,
                                length, width, height, scan_count])
        print(f"[{tag}] seq={trig_seq:5d} serial={trig_serial:4d} "
              f"bc={barcode:5d} st={status} {length}x{width}x{height}cm",
              flush=True)


if __name__ == "__main__":
    bind, tag = sys.argv[1], sys.argv[2]
    lane = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    block = ModbusSequentialDataBlock(0, [0] * 16)
    ctx = ModbusServerContext(slaves=ModbusSlaveContext(hr=block), single=True)
    threading.Thread(target=simulate, args=(ctx, tag, lane), daemon=True).start()
    StartTcpServer(context=ctx, address=(bind, 502))
