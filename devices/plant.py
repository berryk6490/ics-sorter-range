#!/usr/bin/env python3
"""Independent lane 1/2/3 package motion, driven by existing VFDs.

The plant is a Modbus master on the existing drives VM and L1 network. It
reads PLC requests and slot commands, writes one sensor event at a time, and
waits for the PLC's acknowledgement. It never reads XLe or ASX state.
"""
import argparse
from collections import deque
from dataclasses import dataclass
from enum import IntEnum
import json
import logging
import math
import time

LOG = logging.getLogger("sorter_plant")
PLC = "10.10.1.10"
VFD = ["10.10.1.21", "10.10.1.22", "10.10.1.23",
       "10.10.1.24", "10.10.1.25", "10.10.1.26"]
ENTRY = {1: 2.0, 2: 5.0, 3: 8.0}
INDUCT, TUNNEL, DIVERT, TRAILER, RECIRC, FAILED_CONFIRM = range(1, 7)
PHOTOEYES = ("induction", "tunnel", "divert", "outbound", "trailer")


class Zone(IntEnum):
    APPROACH = 1
    DECISION = 2
    PREMERGE = 3
    MERGE = 4
    OUTBOUND = 5
    TERMINAL = 6
    RECIRC_TAIL = 7


class Motion(IntEnum):
    MOVING = 1
    HELD_DOWNSTREAM = 2
    HELD_MERGE = 3
    DRIVE_STOPPED = 4
    AWAITING_ROUTE = 5
    OUTBOUND = 6
    TERMINAL = 7
    JAMMED = 8  # Phase 2B reserved; never produced here.


class Hold(IntEnum):
    NONE = 0
    ZONE_FULL = 1
    MERGE_CAPACITY = 2
    DRIVE_OFF = 3
    ROUTE_PENDING = 4


TUNNEL_ZONE_START = 9.0
PREMERGE_START = 11.8
RECIRC_START = 14.0
RECIRC_EXIT = 19.0
ZONE_LIMITS = {Zone.APPROACH: (0.0, TUNNEL_ZONE_START),
               Zone.DECISION: (TUNNEL_ZONE_START, PREMERGE_START),
               Zone.PREMERGE: (PREMERGE_START, RECIRC_START),
               Zone.RECIRC_TAIL: (RECIRC_START, RECIRC_EXIT),
               Zone.OUTBOUND: (2.0, 20.0)}
PREMERGE_HOLD = 13.6
DECISION_HOLD = 11.6


def lane_zone(position, target, divert_sent):
    """Classify a lane front; crossing 14 requires an irrevocable fallback."""
    if position < TUNNEL_ZONE_START:
        return Zone.APPROACH
    if position < PREMERGE_START:
        return Zone.DECISION
    if position < RECIRC_START:
        return Zone.PREMERGE
    if target == -1 and divert_sent and position <= RECIRC_EXIT:
        return Zone.RECIRC_TAIL
    raise ValueError(f"lane position {position} crossed route boundary without fallback")


@dataclass
class ZoneBlock:
    """One bounded run-scoped test hold; no fixture is present by default."""
    kind: str
    after_token: int
    duration: float
    lane: int = 0
    zone: str = ""
    start: float = -1.0

    def __post_init__(self):
        if (self.kind not in ("merge", "lane") or
            self.after_token <= 0 or not 0 < self.duration <= 60 or
            (self.kind == "lane" and (self.lane not in ENTRY or
                                      self.zone not in ("decision", "premerge"))) or
            (self.kind == "merge" and (self.lane or self.zone))):
            raise ValueError("bounded merge or lane-zone block required")

    def active(self, clock, package, threshold):
        if self.start < 0 and package.token == self.after_token and package.position >= threshold:
            self.start = clock
        return self.start >= 0 and clock - self.start < self.duration


@dataclass(frozen=True)
class PhotoeyeFixture:
    """One run-scoped, token-bound fault; normal service passes None."""
    sensor: str
    token: int
    kind: str

    def __post_init__(self):
        if self.sensor not in PHOTOEYES or self.token <= 0 or self.kind not in (
                "stuck_clear", "stuck_blocked", "bounce", "missed", "early"):
            raise ValueError("fixture requires one valid sensor, token and fault kind")


@dataclass
class Package:
    token: int
    serial: int
    length: float
    lane: int = 1
    position: float = 0.0
    outbound: int = 0
    outbound_position: float = 2.0
    tunnel_sent: bool = False
    divert_sent: bool = False
    terminal_sent: bool = False
    target: int = 0
    trailer_position: float = 0.0
    zone: int = 0
    motion: int = 0
    hold: int = 0
    dwell: int = 0
    state_clock: float = -1.0


class PlantModel:
    """Advance physical positions; caller supplies VFD feedback and PLC slots.

    Positions are cell equivalents: at 1750 rpm a belt moves ten cells/sec,
    matching the established simulation scale. Length and gap are in these
    cells, so spacing is independent of the PLC's old occupancy array.
    """

    def __init__(self, length_cm=60, spacing_cm=100, cell_cm=50,
                 miss_divert_token=0, fail_confirm_token=0,
                 stateful=False, photoeye_fixture=None, accumulation=False,
                 zone_block=None):
        self.length = length_cm / cell_cm
        self.spacing = spacing_cm / cell_cm
        if accumulation and (self.length <= 0 or self.spacing < 0 or
                             self.length + self.spacing < 3.2 - 1e-9):
            raise ValueError("accumulation requires a positive package and at least 3.2 cells pitch")
        self.packages = []
        self.pending = deque()
        self.queued = deque()
        self.last_token = 0
        self.lane_by_token = {}
        self.clock = 0.0
        self.recent = {}
        self.last_event = {}
        self.miss_divert_token = miss_divert_token
        self.fail_confirm_token = fail_confirm_token
        self.stateful = stateful
        self.photoeye_fixture = photoeye_fixture
        self.accumulation = accumulation
        self.zone_block = zone_block
        self.merge_next_lane = 1
        self.beam_first_high = {}
        self.beam_latched = set()

    def request(self, token, serial, lane=1):
        if lane not in ENTRY:
            raise ValueError(f"unsupported plant lane {lane}")
        if token and token != self.last_token:
            self.last_token = token
            self.lane_by_token[token] = lane
            self.queued.append(Package(token, serial, self.length, lane))

    def step(self, seconds, rpm, slots):
        """slots maps token to (serial, state, destination) from PLC rows."""
        if self.accumulation:
            return self.step_zones(seconds, rpm, slots)
        self.clock += seconds
        self.recent = {token: record for token, record in self.recent.items()
                       if record[0] > self.clock}
        active_tokens = {p.token for p in self.packages} | set(self.recent)
        self.last_event = {token: value for token, value in self.last_event.items()
                           if token in active_tokens}
        self.lane_by_token = {token: lane for token, lane in self.lane_by_token.items()
                              if token in active_tokens or token in slots or
                              any(p.token == token for p in self.queued)}
        for p in list(self.queued):
            if rpm[p.lane - 1] > 0 and all(
                other.lane != p.lane or other.outbound or
                other.position >= self.length + self.spacing
                for other in self.packages):
                self.queued.remove(p)
                self.packages.append(p)
                self.pending.append((INDUCT, p.token, p.serial, 0, 0))
        # Advance outbound parcels first. Admission below then compares every
        # candidate with the new positions, independent of list insertion order.
        for p in list(self.packages):
            if p.outbound:
                p.outbound_position += seconds * 10 * max(0, rpm[p.outbound + 2]) / 1750
                if not p.terminal_sent and p.outbound_position >= 12 + 3 * ((p.target - 1) % 3):
                    p.terminal_sent = True
                    p.trailer_position = 12 + 3 * ((p.target - 1) % 3)
                    kind = FAILED_CONFIRM if p.token == self.fail_confirm_token else TRAILER
                    actual = 0 if kind == FAILED_CONFIRM else p.target
                    self.pending.append((kind, p.token, p.serial, actual,
                                         int(p.outbound_position * 10)))
                    if not self.stateful:
                        self.recent[p.token] = (self.clock + 2.0, p.serial,
                                                p.outbound + 1,
                                                int(p.outbound_position * 10),
                                                kind, actual)
                        self.packages.remove(p)
                if self.stateful and p.terminal_sent and p.outbound_position > p.trailer_position + p.length:
                    kind = FAILED_CONFIRM if p.token == self.fail_confirm_token else TRAILER
                    self.recent[p.token] = (self.clock + 2.0, p.serial,
                                            p.outbound + 1,
                                            int(p.outbound_position * 10),
                                            kind, 0 if kind == FAILED_CONFIRM else p.target)
                    self.packages.remove(p)
        candidates = []
        for p in list(self.packages):
            if p.outbound:
                continue
            speed = seconds * 10 * max(0, rpm[p.lane - 1]) / 1750
            p.position += speed
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
                    belt = (p.target - 1) // 3 + 1 if p.target > 0 else 0
                    if p.token == self.miss_divert_token:
                        belt = 0
                    candidates.append((p, belt, target_cell))
            if p.divert_sent and not p.outbound and p.position >= 19:
                self.pending.append((RECIRC, p.token, p.serial, 0,
                                     int(p.position * 10)))
                self.recent[p.token] = (self.clock + 2.0, p.serial,
                                        1 if p.lane == 1 else 5 if p.lane == 2 else 6,
                                        int(p.position * 10), RECIRC, 0)
                self.packages.remove(p)

        # First arrival wins; a simultaneous arrival is lower lane first.
        # One waiting package per lane can accumulate at its divert gate.
        # Capacity is the three PLC slots, and no outbound pair may be closer
        # than one package length plus the configured clear spacing.
        candidates.sort(key=lambda item: (item[2] - item[0].position, item[0].lane))
        for p, belt, target_cell in candidates:
            if belt:
                entry = ENTRY[p.lane]
                clear = all(other is p or other.outbound != belt or
                            abs(other.outbound_position - entry) >= self.length + self.spacing
                            for other in self.packages)
                if not clear:
                    p.position = target_cell
                    continue
                p.outbound = belt
                p.outbound_position = entry
            p.divert_sent = True
            self.pending.append((DIVERT, p.token, p.serial, belt,
                                 int(p.position * 10)))

    def zone_capacity(self, zone, package_length=None):
        """Maximum fronts in a finite zone at the configured clear spacing."""
        low, high = ZONE_LIMITS[Zone(zone)]
        length = self.length if package_length is None else package_length
        return max(1, math.floor((high - low + self.spacing) /
                                 (length + self.spacing)))

    def can_induct(self, lane):
        if any(p.lane == lane for p in self.queued):
            return False
        pitch = self.length + self.spacing
        return all(p.outbound or p.lane != lane or p.position >= pitch
                   for p in self.packages)

    def induct_ready_mask(self):
        return sum((1 << (lane - 1)) for lane in ENTRY if self.can_induct(lane))

    def zone_row(self, token, serial):
        for p in self.packages:
            if p.token == token and p.serial == serial:
                return [p.zone, p.motion, p.hold, p.dwell]
        if token in self.recent and self.recent[token][1] == serial:
            if self.recent[token][4] == RECIRC:
                return [Zone.RECIRC_TAIL, Motion.TERMINAL, Hold.NONE, 0]
            return [Zone.TERMINAL, Motion.TERMINAL, Hold.NONE, 0]
        return [0, 0, 0, 0]

    def _state(self, p, zone, motion, hold, seconds):
        if p.motion == motion and p.hold == hold and hold and p.state_clock != self.clock:
            p.dwell = min(32000, p.dwell + max(1, int(seconds * 10)))
        elif p.motion != motion or p.hold != hold or not hold:
            p.dwell = 0
        p.zone, p.motion, p.hold = int(zone), int(motion), int(hold)
        p.state_clock = self.clock

    def _lane_blocked(self, p, candidate):
        block = self.zone_block
        if not block or block.kind != "lane" or block.lane != p.lane:
            return False
        threshold = DECISION_HOLD if block.zone == "premerge" else 8.8
        if block.start < 0 and p.token == block.after_token and candidate >= threshold:
            block.start = self.clock
        return block.start >= 0 and self.clock - block.start < block.duration

    def _merge_blocked(self, p):
        block = self.zone_block
        return bool(block and block.kind == "merge" and
                    ((block.start < 0 and block.active(self.clock, p, PREMERGE_HOLD)) or
                     (block.start >= 0 and self.clock - block.start < block.duration)))

    def step_zones(self, seconds, rpm, slots):
        """Finite lane zones, FIFO motion and round-robin outbound admission."""
        self.clock += seconds
        self.recent = {k: v for k, v in self.recent.items() if v[0] > self.clock}
        active_tokens = {p.token for p in self.packages} | set(self.recent)
        self.last_event = {k: v for k, v in self.last_event.items() if k in active_tokens}
        self.lane_by_token = {k: v for k, v in self.lane_by_token.items()
                              if k in active_tokens or k in slots or
                              any(p.token == k for p in self.queued)}
        for p in list(self.queued):
            if rpm[p.lane - 1] > 0:
                pitch = self.length + self.spacing
                if any(q.lane == p.lane and not q.outbound and q.position < pitch
                       for q in self.packages):
                    continue
                self.queued.remove(p)
                self.packages.append(p)
                self.pending.append((INDUCT, p.token, p.serial, 0, 0))
                self._state(p, Zone.APPROACH, Motion.MOVING, Hold.NONE, seconds)
        # Outbound speed feedback is the only outbound motion source.
        for belt in (1, 2, 3):
            members = sorted((p for p in self.packages if p.outbound == belt),
                             key=lambda p: -p.outbound_position)
            leader = None
            for p in members:
                speed = seconds * 10 * max(0, rpm[belt + 2]) / 1750
                candidate = p.outbound_position + speed
                bound = (leader.outbound_position - max(p.length, leader.length) - self.spacing
                         if leader else 100.0)
                p.outbound_position = min(candidate, bound)
                motion = (Motion.DRIVE_STOPPED if rpm[belt + 2] <= 0 else
                          Motion.HELD_DOWNSTREAM if p.outbound_position < candidate - 1e-6 else
                          Motion.OUTBOUND)
                hold = (Hold.DRIVE_OFF if motion == Motion.DRIVE_STOPPED else
                        Hold.ZONE_FULL if motion == Motion.HELD_DOWNSTREAM else Hold.NONE)
                self._state(p, Zone.OUTBOUND, motion, hold, seconds)
                leader = p
                if not p.terminal_sent and p.outbound_position >= 12 + 3 * ((p.target - 1) % 3):
                    p.terminal_sent = True
                    p.trailer_position = 12 + 3 * ((p.target - 1) % 3)
                    kind = FAILED_CONFIRM if p.token == self.fail_confirm_token else TRAILER
                    self.pending.append((kind, p.token, p.serial,
                                         0 if kind == FAILED_CONFIRM else p.target,
                                         int(p.outbound_position * 10)))
                if p.terminal_sent and p.outbound_position > p.trailer_position + p.length:
                    kind = FAILED_CONFIRM if p.token == self.fail_confirm_token else TRAILER
                    self.recent[p.token] = (self.clock + 2, p.serial, belt + 1,
                                            int(p.outbound_position * 10), kind,
                                            0 if kind == FAILED_CONFIRM else p.target)
                    self.packages.remove(p)
        for lane in ENTRY:
            members = sorted((p for p in self.packages if p.lane == lane and not p.outbound),
                             key=lambda p: -p.position)
            leader = None
            for p in members:
                speed = seconds * 10 * max(0, rpm[lane - 1]) / 1750
                candidate = p.position + speed
                bound = 100.0
                cause = Hold.NONE
                if leader:
                    bound = min(bound, leader.position - max(p.length, leader.length) - self.spacing)
                    cause = Hold.ZONE_FULL
                if self._lane_blocked(p, candidate):
                    hold_point = DECISION_HOLD if self.zone_block.zone == "premerge" else 8.8
                    if hold_point < bound:
                        bound, cause = hold_point, Hold.ZONE_FULL
                if p.position >= PREMERGE_HOLD and p.target > 0:
                    bound, cause = min(bound, PREMERGE_HOLD), Hold.MERGE_CAPACITY
                if p.position < PREMERGE_HOLD and candidate >= PREMERGE_HOLD:
                    serial, state, dest = slots.get(p.token, (0, 0, 0))
                    if serial == p.serial and state == 3 and 1 <= dest <= 9:
                        p.target = dest
                        bound, cause = min(bound, PREMERGE_HOLD), Hold.MERGE_CAPACITY
                p.position = max(p.position, min(candidate, bound))
                if p.position >= 10 and not p.tunnel_sent:
                    p.tunnel_sent = True
                    self.pending.append((TUNNEL, p.token, p.serial, 0, int(p.position * 10)))
                if p.position >= RECIRC_START and not p.divert_sent:
                    serial, state, dest = slots.get(p.token, (0, 0, 0))
                    if serial != p.serial or state != 3 or not 1 <= dest <= 9:
                        p.target = -1
                    if p.target == -1:
                        p.divert_sent = True
                        self.pending.append((DIVERT, p.token, p.serial, 0, int(p.position * 10)))
                if p.divert_sent and p.position >= RECIRC_EXIT:
                    p.position = RECIRC_EXIT
                    self.pending.append((RECIRC, p.token, p.serial, 0, int(p.position * 10)))
                    self.recent[p.token] = (self.clock + 2, p.serial,
                                            1 if lane == 1 else 5 if lane == 2 else 6,
                                            int(p.position * 10), RECIRC, 0)
                    self.packages.remove(p)
                if p not in self.packages:
                    continue
                zone = lane_zone(p.position, p.target, p.divert_sent)
                if rpm[lane - 1] <= 0:
                    motion, hold = Motion.DRIVE_STOPPED, Hold.DRIVE_OFF
                elif p.position < candidate - 1e-6:
                    motion = Motion.HELD_MERGE if cause == Hold.MERGE_CAPACITY else Motion.HELD_DOWNSTREAM
                    hold = cause
                else:
                    motion, hold = Motion.MOVING, Hold.NONE
                self._state(p, zone, motion, hold, seconds)
                leader = p
        # All ready lanes are considered at each gap. The circular pointer
        # prevents a lower-numbered lane from repeatedly winning ties.
        ready = [p for p in self.packages if not p.outbound and p.target > 0 and
                 p.position >= PREMERGE_HOLD and not p.divert_sent]
        ready.sort(key=lambda p: (p.lane - self.merge_next_lane) % 3)
        for p in ready:
            if self._merge_blocked(p):
                self._state(p, Zone.MERGE, Motion.HELD_MERGE, Hold.MERGE_CAPACITY, seconds)
                continue
            belt = (p.target - 1) // 3 + 1
            entry = ENTRY[p.lane]
            if any(other is not p and other.outbound == belt and
                   abs(other.outbound_position - entry) <
                   max(p.length, other.length) + self.spacing for other in self.packages):
                self._state(p, Zone.MERGE, Motion.HELD_MERGE, Hold.MERGE_CAPACITY, seconds)
                continue
            if rpm[belt + 2] <= 0:
                self._state(p, Zone.MERGE, Motion.DRIVE_STOPPED, Hold.DRIVE_OFF, seconds)
                continue
            p.outbound = belt
            p.outbound_position = entry
            p.divert_sent = True
            self._state(p, Zone.OUTBOUND, Motion.OUTBOUND, Hold.NONE, seconds)
            self.pending.append((DIVERT, p.token, p.serial, belt, int(p.position * 10)))
            self.merge_next_lane = p.lane % 3 + 1

    def telemetry(self, token, serial):
        """Return belt, position ×10, latest sent sensor, actual; all bounded."""
        for package in self.packages:
            if package.token == token and package.serial == serial:
                kind, actual = self.last_event.get(token, (0, 0))
                belt = package.outbound + 1 if package.outbound else (
                    1 if package.lane == 1 else 5 if package.lane == 2 else 6)
                position = (package.outbound_position if package.outbound
                            else package.position)
                return belt, min(300, int(position * 10)), kind, actual
        record = self.recent.get(token)
        if record and record[1] == serial:
            _, _, belt, position, kind, actual = record
            return belt, position, kind, actual
        return 0, 0, 0, 0

    def photoeyes(self, token, serial):
        """Five raw beam bits in path order, derived only from position/length.

        The physical channels are selected by lane and destination; the slot
        row carries their occupancy beside the PLC's package identity.
        """
        if not self.stateful:
            return 0
        package = next((p for p in self.packages
                        if p.token == token and p.serial == serial), None)
        if package is None:
            fixture = self.photoeye_fixture
            if fixture and fixture.token == token and fixture.kind == "stuck_blocked":
                index = PHOTOEYES.index(fixture.sensor)
                if (token, index) in self.beam_latched:
                    return 1 << index
            return 0
        p = package
        if p.outbound:
            entry = ENTRY[p.lane]
            natural = [False, False, False,
                       entry <= p.outbound_position <= entry + p.length,
                       p.terminal_sent and p.trailer_position <= p.outbound_position <= p.trailer_position + p.length]
        else:
            natural = [0 <= p.position <= p.length,
                       10 <= p.position <= 10 + p.length,
                       12 <= p.position <= 12 + p.length,
                       False, False]
        fixture = self.photoeye_fixture
        if fixture and fixture.token == token:
            index = PHOTOEYES.index(fixture.sensor)
            key = (token, index)
            if natural[index] and key not in self.beam_first_high:
                self.beam_first_high[key] = self.clock
            if fixture.kind in ("stuck_clear", "missed"):
                natural[index] = False
            elif fixture.kind == "stuck_blocked":
                if natural[index]:
                    self.beam_latched.add(key)
                natural[index] = key in self.beam_latched
            elif fixture.kind == "bounce" and key in self.beam_first_high:
                elapsed = self.clock - self.beam_first_high[key]
                if elapsed < .35:
                    natural[index] = int(elapsed / .05) % 2 == 0
            elif fixture.kind == "early" and not p.outbound:
                natural[index] = p.position <= 1.0
        return sum(1 << index for index, blocked in enumerate(natural) if blocked)


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
    fixture = (PhotoeyeFixture(args.photoeye_fault_sensor, args.photoeye_fault_token,
                               args.photoeye_fault_kind)
               if args.photoeye_fault_sensor else None)
    block = (ZoneBlock("merge", args.block_after_token, args.block_duration)
             if args.block_merge else
             ZoneBlock("lane", args.block_after_token, args.block_duration,
                       args.block_lane, args.block_zone) if args.block_lane else None)
    model = PlantModel(args.length_cm, args.spacing_cm,
                       miss_divert_token=args.miss_divert_token,
                       fail_confirm_token=args.fail_confirm_token,
                       photoeye_fixture=fixture, zone_block=block)
    active = None
    sequence = 0
    telemetry_sequence = [0, 0, 0]
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
            stateful_result = plc.read_coils(919, 1, slave=1)
            if stateful_result.isError():
                raise IOError("photoeye mode coil")
            stateful = bool(stateful_result.bits[0])
            accumulation_result = plc.read_coils(920, 1, slave=1)
            if accumulation_result.isError():
                raise IOError("accumulation mode coil")
            accumulation = bool(accumulation_result.bits[0])
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
                if block is not None:
                    block.start = -1.0
                model = PlantModel(args.length_cm, args.spacing_cm,
                                   miss_divert_token=args.miss_divert_token,
                                   fail_confirm_token=args.fail_confirm_token,
                                   stateful=stateful, photoeye_fixture=fixture,
                                   accumulation=accumulation, zone_block=block)
                outstanding = None
                # Reset may have accepted a separately injected event. Continue
                # after the PLC's committed sequence so its seen-sequence
                # guard cannot discard this run's first INDUCT photoeye.
                sequence = read(plc, 585)[0]
                telemetry_sequence = [0, 0, 0]
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
            request, token, serial, lane = read(plc, 574, 4)
            if request and request != last_request and request == token:
                last_request = request
                model.request(token, serial, lane)
                LOG.info("request lane=%s token=%s serial=%s", lane, token, serial)
            rpm = [read(client, 5)[0] for client in drives]
            rows = [read(plc, 530, 12), read(plc, 542, 12),
                    read(plc, 647, 12)]
            slots = {row[0]: (row[1], row[4], row[5])
                     for row in rows if row[0]}
            model.step(elapsed, rpm, slots)
            if mode:
                if accumulation:
                    # Seqlock: PLC ignores partially rewritten slot rows.
                    write(plc, 785, [0])
                for index, row in enumerate(rows):
                    token, serial = row[:2]
                    belt, position, event_type, actual = model.telemetry(token, serial)
                    telemetry_sequence[index] = telemetry_sequence[index] % 30000 + 1
                    base = 600 + index * 10 if index < 2 else 660
                    write(plc, base, [*epoch, nonce, token, serial, belt,
                                      position, event_type, actual])
                    write(plc, base + 9, [telemetry_sequence[index]])
                    if accumulation:
                        write(plc, 751 + index * 5,
                              [*model.zone_row(token, serial), telemetry_sequence[index]])
                    if stateful and token in model.lane_by_token:
                        raw_base = 690 + index * 8
                        raw_sequence = telemetry_sequence[index]
                        write(plc, raw_base, [*epoch, nonce, token, serial,
                                              model.lane_by_token[token],
                                              model.photoeyes(token, serial)])
                        write(plc, raw_base + 7, [raw_sequence])
                if accumulation:
                    write(plc, 784, [model.induct_ready_mask(), heartbeat])
            if outstanding is not None:
                if read(plc, 586)[0] == outstanding:
                    outstanding = None
            if outstanding is None and model.pending:
                event = model.pending.popleft()
                sequence = sequence % 30000 + 1
                event_lane = model.lane_by_token[event[1]]
                write(plc, 578, [event_lane])
                write(plc, 580, list(event))
                write(plc, 585, [sequence])
                outstanding = sequence
                model.last_event[event[1]] = (event[0], event[3])
                if event[0] in (TRAILER, RECIRC, FAILED_CONFIRM) and not stateful:
                    model.lane_by_token.pop(event[1], None)
                LOG.info("sensor %s", json.dumps(dict(zip(
                    ("type", "token", "serial", "actual", "position"), event))))
            time.sleep(0.1)
        except (OSError, ValueError, ModbusException) as exc:
            LOG.error("plant poll failed: %s", exc)
            time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plc", default=PLC)
    parser.add_argument("--vfd", nargs=6, default=VFD)
    parser.add_argument("--length-cm", type=float, default=60)
    parser.add_argument("--spacing-cm", type=float, default=100)
    parser.add_argument("--miss-divert-token", type=int, default=0)
    parser.add_argument("--fail-confirm-token", type=int, default=0)
    parser.add_argument("--photoeye-fault-sensor", choices=PHOTOEYES)
    parser.add_argument("--photoeye-fault-token", type=int, default=0)
    parser.add_argument("--photoeye-fault-kind", choices=(
        "stuck_clear", "stuck_blocked", "bounce", "missed", "early"))
    parser.add_argument("--block-merge", action="store_true")
    parser.add_argument("--block-lane", type=int, choices=(1, 2, 3), default=0)
    parser.add_argument("--block-zone", choices=("decision", "premerge"), default="premerge")
    parser.add_argument("--block-after-token", type=int, default=0)
    parser.add_argument("--block-duration", type=float, default=0)
    args = parser.parse_args()
    if bool(args.photoeye_fault_sensor) != bool(args.photoeye_fault_token) or (
            args.photoeye_fault_sensor and not args.photoeye_fault_kind) or (
            args.photoeye_fault_kind and not args.photoeye_fault_sensor):
        parser.error("photoeye fixture requires sensor, positive token and kind together")
    if (args.block_merge and args.block_lane) or (
            bool(args.block_merge or args.block_lane) != bool(args.block_after_token)) or (
            bool(args.block_merge or args.block_lane) != bool(args.block_duration)):
        parser.error("one bounded zone block requires kind, token and duration")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(args)


if __name__ == "__main__":
    main()
