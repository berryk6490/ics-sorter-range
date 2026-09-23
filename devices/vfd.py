#!/usr/bin/env python3
"""Emulated variable frequency drive exposing a Modbus register profile.

Ramps actual speed toward the reference, draws current according to speed and
mechanical load, and protects itself against both overspeed and sustained
overload.

Register profile. Master-written registers are contiguous at the front
because the PLC writes its mapped output block as a unit: if a master-written
register sat past a drive-written one, every scan would overwrite the drive's
own feedback with whatever the controller happened to hold.

    0  cmd_word     master   bit0 run, bit1 forward, bit2 fault reset
    1  speed_ref    master   commanded speed, rpm
    2  belt_load    master   occupied cells on the belt this drive turns
    3  status_word  drive    bit0 ready, bit1 running, bit2 faulted
    4  out_freq     drive    Hz x 10
    5  speed_fb     drive    actual speed, rpm
    6  fault_code   drive    0 none, 1 overcurrent, 2 overspeed
    7  current      drive    A x 10
    8  thermal      drive    thermal accumulator, percent of trip

Current model. A real motor draws magnetising current whenever it is
energised, a component for friction and windage that scales with speed, and a
torque component set by mechanical load. Conveyor load torque is roughly
constant with speed, so the load term does not scale with rpm: a heavily
loaded belt draws heavily even when creeping.

Thermal model. Overcurrent protection is inverse-time, not a threshold. A
motor tolerates a large overload briefly and a small one for a long while, so
heating accumulates as I squared over rated squared and cools when the
current is below rated. This matters for the range: a gap-collapse attack
does not trip a belt instantly, it loads it, and the belt runs hot for
tens of seconds before dropping out. That delay is where an operator would
have a chance to notice, and where detection has something to detect.
"""
import sys
import threading
import time

from pymodbus.server import StartTcpServer
from pymodbus.datastore import (ModbusSequentialDataBlock,
                                ModbusSlaveContext, ModbusServerContext)

RATED_RPM = 1750
RAMP_RPM_PER_SEC = 400
RATED_CURRENT = 120         # A x 10, so 12.0 A at rated load

I_MAGNETISING = 18          # 1.8 A, drawn whenever the drive is energised
I_WINDAGE_AT_RATED = 30     # 3.0 A of friction and windage at rated speed
I_PER_PACKAGE = 9           # 0.9 A of torque current per occupied cell

# Inverse-time overload. The accumulator gains (I/rated)^2 - 1 per second
# above rated and sheds the same below it. The trip value is scaled so an
# overload a belt can actually reach produces a trip on a timescale worth
# watching: around 80 seconds at 14 packages on a belt, around 20 at 20.
# Real drives trip far slower, but a protection curve nobody lives long
# enough to observe teaches nothing.
THERMAL_TRIP = 40.0
THERMAL_COOL_SCALE = 0.25   # cooling is slower than heating, as in a real motor

FAULT_NONE, FAULT_OVERCURRENT, FAULT_OVERSPEED = 0, 1, 2
DT = 0.1


def simulate(ctx, tag):
    thermal = 0.0
    while True:
        time.sleep(DT)
        hr = ctx[0].getValues(3, 0, count=9)
        cmd, ref, belt_load, status, _, fb, fault, _, _ = hr

        if cmd & 0x4:
            # A fault reset clears the fault but not the heat. Resetting a
            # thermal trip and restarting into the same load is how a motor
            # gets destroyed, so the accumulator is only partly relieved and
            # a second trip comes faster than the first.
            fault = FAULT_NONE
            thermal = min(thermal, THERMAL_TRIP * 0.6)

        running = bool(cmd & 0x1) and not fault

        target = ref if running else 0
        step = int(RAMP_RPM_PER_SEC * DT)
        if fb < target:
            fb = min(fb + step, target)
        elif fb > target:
            fb = max(fb - step, target)

        if fb > RATED_RPM * 1.15:
            fault, fb, running = FAULT_OVERSPEED, 0, False

        if running or fb > 0:
            load = max(0, belt_load)
            current = (I_MAGNETISING
                       + int((fb / RATED_RPM) * I_WINDAGE_AT_RATED)
                       + load * I_PER_PACKAGE)
        else:
            current = 0

        # Heat accumulates only while the motor is energised. A stopped motor
        # cools whatever its load was.
        if current > 0:
            ratio = current / RATED_CURRENT
            delta = (ratio * ratio) - 1.0
            if delta > 0:
                thermal += delta * DT
            else:
                thermal += delta * DT * THERMAL_COOL_SCALE
        else:
            thermal -= (1.0 / THERMAL_COOL_SCALE) * DT
        thermal = max(0.0, thermal)

        if thermal >= THERMAL_TRIP and not fault:
            fault, fb, running, current = FAULT_OVERCURRENT, 0, False, 0

        status = 1 | (2 if running else 0) | (4 if fault else 0)
        out_freq = int((fb / RATED_RPM) * 600)
        thermal_pct = int((thermal / THERMAL_TRIP) * 100)

        ctx[0].setValues(3, 3, [status, out_freq, fb, fault,
                                current, thermal_pct])
        print(f"[{tag}] ref={ref:4d} fb={fb:4d} load={belt_load:2d} "
              f"I={current/10:5.1f}A therm={thermal_pct:3d}% "
              f"fault={fault} status={status:03b}", flush=True)


if __name__ == "__main__":
    bind, tag = sys.argv[1], sys.argv[2]
    block = ModbusSequentialDataBlock(0, [0] * 16)
    ctx = ModbusServerContext(slaves=ModbusSlaveContext(hr=block), single=True)
    threading.Thread(target=simulate, args=(ctx, tag), daemon=True).start()
    StartTcpServer(context=ctx, address=(bind, 502))
