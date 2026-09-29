"""Reference tracker for the opt-in sighting layout: the PLC's role in host tests.

It sees only what the PLC would: raw beam edges from the plant, the belt speed
feedback, and scanner results through the private tunnel channel. It never
reads plant packages or the plant journal. Every attribution comes from its
own tracked footprints (SIGHTING_CONTRACT.md sections 2, 3 and 6). It is a test
fixture and the behavioral reference for a later Structured Text version, not
deployed code.

Known simplification: the recycle return is assumed to accept every parcel
leaving outbound A, so tests stay within recycle capacity.
"""
from dataclasses import dataclass
import itertools
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "devices"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
from plant import belt_speed  # noqa: E402
from xle import PassLedger  # noqa: E402


@dataclass
class Tracked:
    token: int
    serial: int
    section: str
    front: float
    length: float
    sighting: str = ""
    barcode: int = 0
    door: str = ""
    fired_at: float = -1.0
    fired_door: str = ""


class ReferenceTracker:
    def __init__(self, layout, ledger, run_identity, sort_plan, scanner,
                 slot_limit=3, tolerance=20.0, chute_speed_min=90.0, step=0.1,
                 max_feedback_age=0.25):
        self.layout = layout
        self.ledger = ledger
        self.run = ledger._run(run_identity)
        self.plan = sort_plan
        self.scanner = scanner
        self.slot_limit = slot_limit
        self.tolerance = tolerance
        # Leading-edge window from the fire: one plant step to divert, the
        # chute path at its slowest supported speed, and one step of sampling.
        self.window = step + layout.chute_eye / chute_speed_min + step
        self.tokens = itertools.count(1)
        self.serials = itertools.count(1)
        self.allocated = []
        self.tracked = []
        self.pending = []
        self.events = []
        self.alarms = []
        self.stopped = False
        self.clock = 0.0
        self.permits = {"induction": True, "handoff": True, "merge": True}
        self.coils = {door.name: False for door in layout.doors}
        self.max_reserved = 0
        self.max_feedback_age = max_feedback_age
        self._merge_expected = {}
        self._merge_seen = set()
        self._last_edge_seq = 0

    # Outputs the PLC would write before the next plant step.
    def outputs(self):
        return dict(self.coils), dict(self.permits)

    def update(self, seconds, primary_rpm, outbound_rpm, edges,
               outbound_feedback_age=0.0):
        if self.stopped:
            return
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("tracker step must be positive and finite")
        fresh = (outbound_rpm is not None and math.isfinite(outbound_rpm) and
                 math.isfinite(outbound_feedback_age) and
                 0 <= outbound_feedback_age <= self.max_feedback_age)
        if not fresh:
            if any(edge.beam == "merge_entry" and edge.rising for edge in edges) or \
                    self._merge_expected:
                return self._stop("merge_visibility_fault", reason="stale_speed")
            return self._stop("speed_feedback_stale")
        before = {id(t): t.front for t in self.tracked}
        start = self.clock
        self.clock += seconds
        outbound_speed = belt_speed(outbound_rpm)
        self._merge_speed = outbound_speed
        self._refresh_merge(start, outbound_speed, seconds)
        outbound = outbound_speed * seconds
        self._advance("outbound", outbound, None, self.layout.outbound_length, None)
        self._advance("recycle", outbound, self.layout.recycle_hold,
                      self.layout.recycle_length, None)
        self._advance("primary", belt_speed(primary_rpm) * seconds,
                      self.layout.primary_hold, self.layout.primary_length, None)
        self._predict_merge(before, start, outbound_speed, seconds)
        for edge in edges:
            if edge.seq <= self._last_edge_seq:
                return self._stop("merge_visibility_fault", reason="stale_edge_sequence",
                                  seq=edge.seq)
            self._last_edge_seq = edge.seq
            if edge.rising:
                self._rising(edge)
            if self.stopped:
                return
        for key, predicted in self._merge_expected.items():
            if self.clock > predicted["deadline"]:
                return self._stop("merge_visibility_fault", reason="missing_edge",
                                  section=key[0], token=key[1], serial=key[2],
                                  expected=predicted["time"])
        self._expire()
        self._set_outputs()

    @staticmethod
    def _merge_key(t):
        return (t.section, t.token, t.serial)

    def _refresh_merge(self, start, speed, seconds):
        for key, item in self._merge_expected.items():
            t, boundary = item["tracked"], item["boundary"]
            if t.front >= boundary:
                continue  # A missed crossing still expires if motion later stops.
            if speed == 0 or (key[0] == "recycle" and not self.permits["merge"]):
                for field in ("time", "early", "deadline"):
                    item[field] += seconds
                continue
            expected = start + (boundary - t.front) / speed
            window = seconds + self.tolerance / speed
            item.update(time=expected, early=expected - window,
                        deadline=expected + window)

    def _predict_merge(self, before, start, speed, seconds):
        if speed <= 0:
            return
        for t in self.tracked:
            key = self._merge_key(t)
            if key in self._merge_seen or key in self._merge_expected:
                continue
            prior = before.get(id(t), t.front)
            if t.section == "outbound":
                boundary = self.layout.recycle_merge
                crossing = prior < boundary and t.front >= boundary - self.tolerance
            elif t.section == "recycle":
                boundary = self.layout.recycle_length
                crossing = (self.permits["merge"] and
                            prior <= boundary and
                            t.front >= boundary - self.tolerance)
            else:
                continue
            if crossing:
                expected = start + max(0.0, boundary - prior) / speed
                window = seconds + self.tolerance / speed
                self._merge_expected[key] = {
                    "tracked": t, "boundary": boundary, "time": expected,
                    "early": expected - window, "deadline": expected + window,
                }

    def _alarm(self, kind, **fields):
        self.alarms.append({"kind": kind, "time": self.clock, **fields})

    def _stop(self, kind, **fields):
        self._alarm(kind, **fields)
        self.stopped = True

    def _open(self, section, front):
        occupancy = Tracked(next(self.tokens), next(self.serials), section, front,
                            self.layout.max_length)
        self.allocated.append((occupancy.token, occupancy.serial))
        self.tracked.append(occupancy)
        return occupancy

    def _members(self, section):
        return sorted((t for t in self.tracked if t.section == section),
                      key=lambda t: -t.front)

    def _room(self, section, front, length):
        for other in self._members(section):
            if other.front >= front:
                if other.front - other.length - front < self.layout.gap:
                    return False
            elif front - length - other.front < self.layout.gap:
                return False
        return True

    def _reserved(self):
        return sum(t.section in ("primary", "outbound") for t in self.tracked)

    def _advance(self, section, distance, hold, end, transfer):
        gate = {"primary": "handoff", "recycle": "merge"}.get(section)
        # Parcels on one belt move together. A follower is limited only behind
        # a leader that was held this step; an unread leader's assumed maximum
        # length must not slow a follower that the belt is still carrying.
        leader, leader_held = None, False
        for t in self._members(section):
            start = t.front
            target = t.front + distance
            if leader is not None and leader_held:
                target = min(target, leader.front - leader.length - self.layout.gap)
            if hold is not None and t.front <= hold < target and not self.permits[gate]:
                target = hold
            if target >= end:
                if transfer is not None and transfer(t):
                    continue
                target = end
            t.front = max(t.front, target)
            leader, leader_held = t, t.front - start < distance - 1e-9

    def _head(self, section):
        members = self._members(section)
        return members[0] if members else None

    def _nearest(self, section, x):
        near = [t for t in self.tracked if t.section == section and
                abs(t.front - x) <= self.tolerance]
        return min(near, key=lambda t: abs(t.front - x)) if near else None

    def _rising(self, edge):
        layout = self.layout
        if edge.beam == "primary_entry":
            self._open("primary", 0.0)
        elif edge.beam == "outbound_entry":
            # A belt is FIFO: the handoff edge belongs to the primary head.
            head = self._head("primary")
            if head is None:
                return self._stop("unattributed_transfer", seq=edge.seq)
            self.tracked.remove(head)
            self.events.append({"kind": "transferred", "token": head.token,
                                "sighting": head.sighting})
            self._open("outbound", 0.0)
        elif edge.beam in ("primary_tunnel", "outbound_tunnel"):
            section = edge.beam.split("_")[0]
            x = layout.primary_tunnel if section == "primary" else layout.outbound_tunnel
            t = self._nearest(section, x)
            if t is None or t.sighting:
                return self._alarm("unattributed_read", beam=edge.beam, seq=edge.seq)
            self._sight(t, section, edge)
        elif edge.beam == "end_eye":
            t = self._nearest("outbound", layout.end_eye)
            if t is None:
                return self._alarm("unexpected_object", beam=edge.beam, seq=edge.seq)
            self._recycled(t)
        elif edge.beam.startswith("chute_"):
            self._chute(edge.beam[len("chute_"):], edge)
        elif edge.beam == "merge_entry":
            self._merge_edge(edge)
        elif edge.beam == "recycle_gate":
            # Re-anchor the parcel that just reached the hold point.
            near = [t for t in self.tracked if t.section == "recycle" and
                    t.front <= layout.recycle_hold + self.tolerance]
            if not near:
                return self._alarm("unexpected_object", beam=edge.beam, seq=edge.seq)
            t = max(near, key=lambda t: t.front)
            t.front = max(t.front, layout.recycle_hold)

    def _merge_edge(self, edge):
        if self._merge_speed <= 0:
            return self._stop("unexpected_merge_entry", reason="edge_while_stopped",
                              seq=edge.seq)
        if abs(edge.time - self.clock) > self.max_feedback_age:
            return self._stop("merge_visibility_fault", reason="stale_edge_time",
                              seq=edge.seq)
        candidates = [(key, item) for key, item in self._merge_expected.items()
                      if item["early"] <= edge.time <= item["deadline"]]
        if len(candidates) != 1:
            late = any(edge.time > item["deadline"]
                       for item in self._merge_expected.values())
            return self._stop("merge_visibility_fault" if late else
                              "unexpected_merge_entry",
                              reason="late_edge" if late else
                              "ambiguous_or_unexpected_edge", seq=edge.seq,
                              candidates=[key for key, _ in candidates])
        key, item = candidates[0]
        t = item["tracked"]
        self._merge_expected.pop(key)
        self._merge_seen.add(key)
        if key[0] == "outbound":
            self.events.append({"kind": "merge_pass_by", "token": t.token,
                                "serial": t.serial, "seq": edge.seq})
            return
        self.tracked.remove(t)
        new = self._open("outbound", self.layout.recycle_merge)
        self.events.append({"kind": "merge_admitted", "token": new.token,
                            "serial": new.serial, "seq": edge.seq})

    def _sight(self, t, section, edge):
        readable, barcode, length = self.scanner(section)
        epoch, nonce = self.run
        t.sighting = f"{epoch}:{nonce}:{section}:{t.token}:{t.serial}:{edge.seq}"
        t.barcode = barcode if readable else 0
        if readable and length:
            t.length = length
        event = {"kind": "sighting", "section": section, "sighting": t.sighting,
                 "token": t.token, "serial": t.serial, "barcode": t.barcode}
        if section == "outbound":
            verdict = self.ledger.open(self.run, t.sighting, t.barcode, readable)
            event.update(verdict)
            if verdict["decision"] == PassLedger.EXCEPTION:
                t.door = "E"
            elif verdict["decision"] == PassLedger.ROUTE:
                t.door = self.plan.get(t.barcode, "")
        self.events.append(event)

    def _recycled(self, t):
        self.tracked.remove(t)
        if t.sighting:
            closed = self.ledger.close(self.run, t.sighting, "recycled")
            self.events.append({"kind": "recycled", "sighting": t.sighting,
                                "barcode": t.barcode})
            if closed["exception_failed"]:
                return self._stop("exception_divert_failed", sighting=t.sighting)
        # A fresh, unidentified association rides the recycle return. Its
        # coordinate starts behind the recycle start by the remaining outbound.
        self._open("recycle", t.front - self.layout.outbound_length)

    def _chute(self, door, edge):
        match = [p for p in self.pending if p.fired_door == door and
                 0 <= self.clock - p.fired_at <= self.window]
        if len(match) != 1:
            return self._stop("unexpected_chute_entry", door=door, seq=edge.seq)
        t = match[0]
        self.pending.remove(t)
        self.tracked.remove(t)
        outcome = "exception_entry" if door == "E" else "confirmed"
        self.ledger.close(self.run, t.sighting, outcome)
        self.events.append({"kind": outcome, "sighting": t.sighting, "door": door,
                            "barcode": t.barcode, "edge": edge.seq})

    def _expire(self):
        for t in list(self.pending):
            if self.clock - t.fired_at > self.window:
                self.pending.remove(t)
                if t.fired_door == "E":
                    self.ledger.close(self.run, t.sighting, "stopped")
                    return self._stop("exception_divert_failed", sighting=t.sighting)
                self.events.append({"kind": "unconfirmed", "sighting": t.sighting})
        for t in list(self.tracked):
            if t.section == "outbound" and t.front > self.layout.end_eye + self.tolerance:
                self.tracked.remove(t)
                if t.sighting:
                    self.ledger.close(self.run, t.sighting, "lost")
                self._alarm("package_lost", sighting=t.sighting, token=t.token)

    def _set_outputs(self):
        if self.stopped:
            self.coils = dict.fromkeys(self.coils, False)
            self.permits = dict.fromkeys(self.permits, False)
            return
        for door in self.layout.doors:
            on = False
            for t in self._members("outbound"):
                if (t.door == door.name and not t.fired_door and
                        t.front - t.length <= door.diverter <= t.front):
                    if any(p.fired_door == door.name for p in self.pending):
                        return self._stop("attribution_fault", door=door.name)
                    t.fired_at, t.fired_door = self.clock, door.name
                    self.pending.append(t)
                    self.events.append({"kind": "fired", "door": door.name,
                                        "sighting": t.sighting})
                if t.fired_door == door.name and t in self.pending:
                    on = True
            self.coils[door.name] = on
        reserved = self._reserved()
        self.max_reserved = max(self.max_reserved, reserved)
        self.permits = {
            "induction": reserved < self.slot_limit,
            "handoff": self._room("outbound", 0.0, self.layout.max_length),
            "merge": reserved < self.slot_limit and self._room(
                "outbound", self.layout.recycle_merge, self.layout.max_length),
        }
