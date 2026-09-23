#!/usr/bin/env python3
"""Independent lane 1 package motion, driven by the six existing VFDs.

The plant is a Modbus master on the existing drives VM and L1 network. It
reads PLC requests and slot commands, writes one sensor event at a time, and
waits for the PLC's acknowledgement. It never reads XLe or ASX state.
"""
import argparse
from collections import deque
from dataclasses import dataclass
import json
import logging
import time

LOG = logging.getLogger("sorter_plant")
PLC = "10.10.1.10"
VFD = ["10.10.1.21", "10.10.1.24", "10.10.1.25", "10.10.1.26"]
INDUCT, TUNNEL, DIVERT, TRAILER, RECIRC, FAILED_CONFIRM = range(1, 7)


@dataclass
class Package:
    token: int
    serial: int
    length: float
    position: float = 0.0
    outbound: int = 0
    outbound_position: float = 2.0
    tunnel_sent: bool = False
    divert_sent: bool = False
    terminal_sent: bool = False
    target: int = 0


class PlantModel:
    """Advance physical positions; caller supplies VFD feedback and PLC slots.

    Positions are cell equivalents: at 1750 rpm a belt moves ten cells/sec,
    matching the established simulation scale. Length and gap are in these
    cells, so spacing is independent of the PLC's old occupancy array.
    """

    def __init__(self, length_cm=60, spacing_cm=100, cell_cm=50,
                 miss_divert_token=0, fail_confirm_token=0):
        self.length = length_cm / cell_cm
        self.spacing = spacing_cm / cell_cm
        self.packages = []
        self.pending = deque()
        self.queued = deque()
        self.last_token = 0
        self.miss_divert_token = miss_divert_token
        self.fail_confirm_token = fail_confirm_token

    def request(self, token, serial):
        if token and token != self.last_token:
            self.last_token = token
            self.queued.append(Package(token, serial, self.length))

    def step(self, seconds, rpm, slots):
        """slots maps token to (serial, state, destination) from PLC rows."""
        if self.queued and rpm[0] > 0 and (
            not self.packages or
            all(p.outbound or p.position >= self.length + self.spacing
                for p in self.packages)
        ):
            p = self.queued.popleft()
            self.packages.append(p)
            self.pending.append((INDUCT, p.token, p.serial, 0, 0))
        for p in list(self.packages):
            if p.outbound:
                p.outbound_position += seconds * 10 * max(0, rpm[p.outbound]) / 1750
                if not p.terminal_sent and p.outbound_position >= 12 + 3 * ((p.target - 1) % 3):
                    p.terminal_sent = True
                    kind = FAILED_CONFIRM if p.token == self.fail_confirm_token else TRAILER
                    actual = 0 if kind == FAILED_CONFIRM else p.target
                    self.pending.append((kind, p.token, p.serial, actual,
                                         int(p.outbound_position * 10)))
                    self.packages.remove(p)
                continue
            p.position += seconds * 10 * max(0, rpm[0]) / 1750
            if p.position >= 10 and not p.tunnel_sent:
                p.tunnel_sent = True
                self.pending.append((TUNNEL, p.token, p.serial, 0, int(p.position * 10)))
            serial, state, dest = slots.get(p.token, (0, 0, 0))
            if serial != p.serial:
                state, dest = 0, 0
            if p.position >= 14 and not p.divert_sent:
                # Freeze the command at the first divert. A late ASX answer
                # cannot redirect a package already past that photoeye.
                if not p.target:
                    p.target = dest if state == 3 and 1 <= dest <= 9 else -1
                target_cell = 14 + 2 * ((p.target - 1) // 3) if p.target > 0 else 14
                if p.position >= target_cell:
                    p.divert_sent = True
                    belt = (p.target - 1) // 3 + 1 if p.target > 0 else 0
                    if p.token == self.miss_divert_token:
                        belt = 0
                    self.pending.append((DIVERT, p.token, p.serial, belt,
                                         int(p.position * 10)))
                    if belt:
                        p.outbound = belt
                        p.outbound_position = 2.0
            if p.divert_sent and not p.outbound and p.position >= 19:
                self.pending.append((RECIRC, p.token, p.serial, 0,
                                     int(p.position * 10)))
                self.packages.remove(p)


def read(client, address, count=1):
    result = client.read_holding_registers(address, count, slave=1)
    if result.isError():
        raise IOError(f"Modbus read {address}: {result}")
    return result.registers


def write(client, address, values):
    result = client.write_registers(address, values, slave=1)
    if result.isError():
        raise IOError(f"Modbus write {address}: {result}")


def run(args):
    from pymodbus.client import ModbusTcpClient
    from pymodbus.exceptions import ModbusException
    plc = ModbusTcpClient(args.plc, port=502, timeout=1)
    drives = [ModbusTcpClient(ip, port=502, timeout=1) for ip in args.vfd]
    model = PlantModel(args.length_cm, args.spacing_cm,
                       miss_divert_token=args.miss_divert_token,
                       fail_confirm_token=args.fail_confirm_token)
    active = None
    sequence = 0
    heartbeat = 0
    last_request = 0
    last = time.monotonic()
    outstanding = None
    armed = False
    while True:
        try:
            coils = plc.read_coils(918, 1, slave=1)
            if coils.isError():
                raise IOError("plant mode coil")
            mode = coils.bits[0]
            if not mode:
                armed = True
            if mode and not armed:
                # A cold process start cannot recover the positions of an
                # already active run. Clear the old identity immediately and
                # withhold heartbeat until a PLC reset drops plant mode.
                write(plc, 587, [0, 0, 0])
                LOG.error("active run at plant startup; PLC reset required")
                time.sleep(1)
                continue
            epoch = tuple(read(plc, 558, 2))
            nonce = read(plc, 509)[0]
            run_key = (epoch, nonce) if mode and epoch != (0, 0) else None
            if run_key != active:
                active = run_key
                model = PlantModel(args.length_cm, args.spacing_cm,
                                   miss_divert_token=args.miss_divert_token,
                                   fail_confirm_token=args.fail_confirm_token)
                outstanding = None
                last_request = read(plc, 574)[0]
                if active:
                    write(plc, 587, [*epoch, nonce])
                    LOG.info("run identity epoch=%s nonce=%s", epoch, nonce)
            heartbeat = heartbeat % 30000 + 1
            write(plc, 590, [heartbeat])
            now = time.monotonic()
            elapsed = min(now - last, 0.5)
            last = now
            if not active:
                time.sleep(0.1)
                continue
            request, token, serial = read(plc, 574, 3)
            if request and request != last_request and request == token:
                last_request = request
                model.request(token, serial)
                LOG.info("request token=%s serial=%s", token, serial)
            rpm = [read(client, 5)[0] for client in drives]
            rows = read(plc, 530, 24)
            slots = {rows[i]: (rows[i + 1], rows[i + 4], rows[i + 5])
                     for i in (0, 12) if rows[i]}
            model.step(elapsed, rpm, slots)
            if outstanding is not None:
                if read(plc, 586)[0] == outstanding:
                    outstanding = None
            if outstanding is None and model.pending:
                event = model.pending.popleft()
                sequence = sequence % 30000 + 1
                write(plc, 580, list(event))
                write(plc, 585, [sequence])
                outstanding = sequence
                LOG.info("sensor %s", json.dumps(dict(zip(
                    ("type", "token", "serial", "actual", "position"), event))))
            time.sleep(0.1)
        except (OSError, ValueError, ModbusException) as exc:
            LOG.error("plant poll failed: %s", exc)
            time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plc", default=PLC)
    parser.add_argument("--vfd", nargs=4, default=VFD)
    parser.add_argument("--length-cm", type=float, default=60)
    parser.add_argument("--spacing-cm", type=float, default=100)
    parser.add_argument("--miss-divert-token", type=int, default=0)
    parser.add_argument("--fail-confirm-token", type=int, default=0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(args)


if __name__ == "__main__":
    main()
