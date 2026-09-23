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
   10  run_nonce    scanner  echoes the most recent reset token

The no-read rate is configuration pushed from the controller rather than a
constant here, which is how a tunnel is actually set up and which keeps the
rate a writable tag: raising it is an attack on read reliability that never
touches a single packet the tunnel sends.

The sequence handshake is what makes the exchange a request and a response
rather than a shared variable. The PLC must wait for result_seq to catch up,
so a scan takes real time and can be late, lost, or answered out of order.
That is the failure domain a scan result actually lives in.

Reset first uses trig_seq=32766 (prepare). The scanner replaces the old
response with status=7, result_seq=32766, and the nonce at register 10. A
following trig_seq=32767 with the same nonce reseeds even when the seed is
unchanged, clears history and the previous response, and acknowledges with
status=6. Normal replies carry the nonce too. Prepare makes a cold PLC restart
safe when its private nonce starts again at 1 and the scanner still holds 1.

Only a manually launched test fixture with SORTER_REPEAT_BARCODE=1 forces the
second and later parcels to reuse the first recent barcode, returning duplicate
status. The deployed systemd service does not set this option.
"""
import sys
import os
import threading
import time

from pymodbus.server import StartTcpServer
from pymodbus.datastore import (ModbusSequentialDataBlock,
                                ModbusSlaveContext, ModbusServerContext)

# Read results. GOOD is the overwhelming majority; the rest are the ways a
# real tunnel fails, each of which the controller has to handle differently.
ST_GOOD, ST_NOREAD, ST_MULTIPLE, ST_OVERSIZE, ST_DUPLICATE, ST_INVALID = range(6)
ST_RESET = 6
ST_PREPARED = 7
RESET_SEQ = 32767
PREPARE_SEQ = 32766

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


class Scanner:
    def __init__(self, ctx, tag, lane):
        self.ctx, self.tag = ctx, tag
        self.s_dest = Stream(1103515245, 12345, 7919 * lane)
        self.s_fail = Stream(1103515245, 12345, 6271 * lane + 11)
        self.s_dims = Stream(1103515245, 12345, 5081 * lane + 23)
        self.last_seed = None
        self.last_seq = 0
        self.run_nonce = 0
        self.prepared_nonce = 0
        self.recent = []
        self.scan_count = 0
        # Test fixture only: a manually launched scanner can present the same
        # physical label on consecutive parcels. The packaged unit omits it.
        self.repeat_barcode = os.environ.get("SORTER_REPEAT_BARCODE") == "1"

    def step(self):
        ctx, tag = self.ctx, self.tag
        hr = ctx[0].getValues(3, 0, count=11)
        (trig_seq, trig_serial, seed, noread_rate,
         _, _, _, _, _, _, _) = hr
        rate_noread = noread_rate if noread_rate > 0 else DEFAULT_NOREAD

        if trig_seq == PREPARE_SEQ:
            if trig_serial and self.prepared_nonce != trig_serial:
                self.prepared_nonce = trig_serial
                ctx[0].setValues(3, 4, [PREPARE_SEQ, 0, ST_PREPARED,
                                        0, 0, 0, trig_serial])
            return

        if trig_seq == RESET_SEQ:
            if trig_serial and self.prepared_nonce == trig_serial:
                self.prepared_nonce = 0
                self.run_nonce = trig_serial
                self.last_seed = seed
                self.s_dest.seed(seed)
                self.s_fail.seed(seed)
                self.s_dims.seed(seed)
                self.recent = []
                self.last_seq = 0
                self.scan_count = 0
                ctx[0].setValues(3, 4, [RESET_SEQ, 0, ST_RESET,
                                        0, 0, 0, self.run_nonce])
                print(f"[{tag}] reset nonce={self.run_nonce} seed={seed}", flush=True)
            return

        if not self.run_nonce or trig_seq == self.last_seq or trig_seq == 0:
            return
        self.last_seq = trig_seq

        length = 20 + self.s_dims.next(100)
        width = 15 + self.s_dims.next(60)
        height = 10 + self.s_dims.next(50)

        roll = self.s_fail.next(1000)
        dest = self.s_dest.next(9) + 1
        barcode = dest * 1000 + (trig_serial % 1000)

        if roll < rate_noread:
            status, barcode = ST_NOREAD, 0
        elif roll < rate_noread + RATE_MULTIPLE:
            # Two labels in view. The tunnel cannot say which is the package's
            # own, so it reports the condition rather than guessing.
            status, barcode = ST_MULTIPLE, 0
        elif roll < rate_noread + RATE_MULTIPLE + RATE_OVERSIZE:
            status = ST_OVERSIZE
            length = OVERSIZE_CM + self.s_dims.next(40)
        elif roll < rate_noread + RATE_MULTIPLE + RATE_OVERSIZE + RATE_DUPLICATE:
            # The same label read twice: a real barcode belonging to a package
            # already counted. The read succeeded, which is what makes this
            # more dangerous than a no-read.
            status = ST_DUPLICATE
            if self.recent:
                barcode = self.recent[-1]
        elif roll < (rate_noread + RATE_MULTIPLE + RATE_OVERSIZE
                     + RATE_DUPLICATE + RATE_INVALID):
            # Decoded cleanly but into something that is not a valid label.
            status = ST_INVALID
            barcode = 99000 + self.s_dest.next(999)
        else:
            status = ST_GOOD

        if self.repeat_barcode and trig_serial > 1 and self.recent:
            barcode = self.recent[0]
            status = ST_DUPLICATE

        if barcode and status in (ST_GOOD, ST_OVERSIZE):
            self.recent.append(barcode)
            if len(self.recent) > 8:
                self.recent.pop(0)

        self.scan_count = (self.scan_count + 1) % 32000
        ctx[0].setValues(3, 4, [trig_seq, barcode, status,
                                length, width, height, self.run_nonce])
        print(f"[{tag}] seq={trig_seq:5d} serial={trig_serial:4d} "
              f"bc={barcode:5d} st={status} {length}x{width}x{height}cm",
              flush=True)


def simulate(ctx, tag, lane):
    scanner = Scanner(ctx, tag, lane)
    while True:
        time.sleep(DT)
        scanner.step()


if __name__ == "__main__":
    bind, tag = sys.argv[1], sys.argv[2]
    lane = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    block = ModbusSequentialDataBlock(0, [0] * 16)
    ctx = ModbusServerContext(slaves=ModbusSlaveContext(hr=block), single=True)
    threading.Thread(target=simulate, args=(ctx, tag, lane), daemon=True).start()
    StartTcpServer(context=ctx, address=(bind, 502))
