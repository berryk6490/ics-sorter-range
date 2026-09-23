#!/usr/bin/env python3
"""Level 2 OPC UA supervisory server for the induct sorter.

Modbus master southbound (PLC 10.10.1.10 + six drive emulators), OPC UA
server northbound on 4840. Replaces the poller that currently lives inside
hmi_app.py; the HMI becomes a UA client against this process.

Per opcua_plan.md sections 2-4:
  - southbound polling ported from hmi_app.py, same registers, same 0.5s
  - namespace built to mirror the process, not the register layout
  - writable nodes carry a UA write access level, and a client write is
    relayed down to the corresponding holding register on the PLC
  - SecurityPolicy NoSecurity, anonymous, no certificate (Milestone 1/3
    build state; see SECURITY below for the Milestone 5 flip)

Runs on scada (10.10.2.10). Needs its own venv, separate from ~/hmi:
    python3 -m venv ~/opcua
    ~/opcua/bin/pip install asyncua "pymodbus==3.6.9"
    ~/opcua/bin/python opcua_server.py
"""
import asyncio
import logging
import sys

from asyncua import Server, ua
from pymodbus.client import ModbusTcpClient

# ------------------------------------------------------------------ config

PLC = "10.10.1.10"
DRIVES = [
    ("Induct1", "10.10.1.21"),
    ("Induct2", "10.10.1.22"),
    ("Induct3", "10.10.1.23"),
    ("Outbound1", "10.10.1.24"),
    ("Outbound2", "10.10.1.25"),
    ("Outbound3", "10.10.1.26"),
]
# The belts advance several cells per second, so a 500 ms poll delivers the
# process in jumps the operator screen cannot interpolate: everything moves
# five cells at once, twice a second. 100 ms puts roughly one cell between
# updates, which is the resolution the motion actually needs. Cost is 10 Modbus
# transactions per second per device rather than 2, which is unremarkable for a
# supervisory poll and still well under what a real SCADA node runs.
POLL_SEC = 0.1

ENDPOINT = "opc.tcp://10.10.2.10:4840/sorter/"
URI = "urn:sorter:level2"

# Milestone 1/3: "none". Milestone 5 hardening: "signencrypt". The switch
# is deliberately one constant so the same server binary is measured in
# both states and nothing else about it changes between the runs.
SECURITY = "none"
CERT = "certs/scada-cert.der"
KEY = "certs/scada-key.pem"
TRUSTLIST = "certs/trusted"

# --------------------------------------------------------- register map
# Bases match gen_sorter.py. %QW200 is holding register 200. %QX110.0 is
# coil 880 (110 * 8 + 0). Do not "simplify" these; the wrong base reads
# zero everywhere and looks like a dead process.

SP_BASE, IB_BASE, OB_BASE, COIL_BASE = 200, 260, 380, 880
SP_COUNT, CELL_COUNT, COIL_COUNT = 59, 60, 38

# Offsets into the SP block (absolute address = SP_BASE + offset).
SP = {
    "speed_sp": 0,      # 0,1,2   induct 1-3 speed setpoint, rpm
    "ob_speed": 3,      # 3,4,5   outbound 1-3 speed setpoint, rpm
    "min_gap": 6,
    "rate_sp": 7,       # 7,8,9   induct 1-3 interval, scans per package
    "noread_sp": 10,
    "scan": 11,         # 11,12,13  last barcode read per tunnel
    "noread_ct": 14,
    "coll_ct": 15,
    "jam_ct": 16,
    "missort_ct": 17,
    "recirc_ct": 18,
    "nohome_ct": 19,
    "inducted_ct": 20,
    "serial_next": 21,
    "trailer": 22,      # 22..30  loaded count, trailer 1-1 .. 3-3
    "bad": 31,          # 31..39  wrong-destination count, same order
    "div_act": 40,      # 40,41,42
    # Logical clocks and run identity. Appended after the original block, so
    # every offset above is unchanged.
    "scan_ct": 43,
    "shift_ct": 44,     # 44,45,46  induct 1-3
    "master_seed": 47,
    "run_id": 48,
    "prog_hash": 49,
    "scanner_state": 55,
    "scanner_fault_mask": 56,
    "scanner_ack_mask": 57,
    "scanner_wait": 58,
}

# Drive profile, read as one block per drive. Master-written registers sit at
# the front because the PLC writes its mapped output block as a unit; see the
# header of vfd.py for why that ordering is forced.
DRV = {"cmd": 0, "ref": 1, "belt_load": 2,
       "status": 3, "out_freq": 4, "speed_fb": 5,
       "fault": 6, "current": 7, "thermal": 8}
DRV_COUNT = 9

# Coil offsets from COIL_BASE.
# Absolute coil numbers derived below. COIL_BASE is 880, so "run" is coil 880 (%QX110.0).
COIL = {"run": 0, "auto": 1, "lane_run": 2, "ob_run": 5,
        "lane_div": 8, "door": 17,
        "jam_alarm": 26, "coll_alarm": 27,
        "noread_alarm": 28, "nohome_alarm": 29,
        "reset_cmd": 30, "fault_reset": 31,
        "scanner_fault_ack": 32, "scanner_retry": 33,
        "xle_fault_ack": 36, "xle_retry": 37}
COIL_ABS = {k: COIL_BASE + v for k, v in COIL.items()}

log = logging.getLogger("opcua_server")


def s16(v):
    """Modbus returns unsigned; PLC INT is signed."""
    return v - 65536 if v > 32767 else v


def u16(v):
    return v + 65536 if v < 0 else v


# ------------------------------------------------------------ modbus link

class ModbusLink:
    """Southbound half. Blocking pymodbus calls, driven from the event loop
    via asyncio.to_thread and serialized by a single lock so the sync
    clients are never touched concurrently."""

    def __init__(self):
        self.plc = ModbusTcpClient(PLC, port=502, timeout=2)
        self.drv = [ModbusTcpClient(ip, port=502, timeout=2) for _, ip in DRIVES]
        self.lock = asyncio.Lock()

    def _read_all(self):
        if not self.plc.connected:
            self.plc.connect()
        sp = self.plc.read_holding_registers(SP_BASE, SP_COUNT, slave=1)
        ib = self.plc.read_holding_registers(IB_BASE, CELL_COUNT, slave=1)
        ob = self.plc.read_holding_registers(OB_BASE, CELL_COUNT, slave=1)
        co = self.plc.read_coils(COIL_BASE, COIL_COUNT, slave=1)
        live = self.plc.read_holding_registers(568, 2, slave=1)
        if sp.isError() or ib.isError() or ob.isError() or co.isError() or live.isError():
            raise IOError("plc read")

        drives = []
        for c in self.drv:
            try:
                if not c.connected:
                    c.connect()
                r = c.read_holding_registers(0, DRV_COUNT, slave=1)
                drives.append([s16(x) for x in r.registers]
                              if not r.isError() else [0] * DRV_COUNT)
            except Exception:
                drives.append([0] * DRV_COUNT)

        return {
            "sp": [s16(v) for v in sp.registers],
            "ib": [s16(v) for v in ib.registers],
            "ob": [s16(v) for v in ob.registers],
            "coils": list(co.bits[:COIL_COUNT]),
            "liveness": [s16(v) for v in live.registers],
            "drives": drives,
        }

    def _write(self, addr, value):
        if not self.plc.connected:
            self.plc.connect()
        r = self.plc.write_register(addr, u16(int(value)), slave=1)
        return not r.isError()

    def _write_coil(self, addr, value):
        if not self.plc.connected:
            self.plc.connect()
        r = self.plc.write_coil(addr, bool(value), slave=1)
        return not r.isError()

    async def read_all(self):
        async with self.lock:
            return await asyncio.to_thread(self._read_all)

    async def write(self, addr, value):
        async with self.lock:
            return await asyncio.to_thread(self._write, addr, value)

    async def write_coil(self, addr, value):
        async with self.lock:
            return await asyncio.to_thread(self._write_coil, addr, value)


# --------------------------------------------------------------- namespace

class Namespace:
    """Northbound half. Builds the tree from opcua_plan.md section 3 and
    holds the node handles the poller updates."""

    def __init__(self, idx):
        self.idx = idx
        self.ro = {}        # key -> node
        self.rw = {}        # key -> node
        self.rw_addr = {}   # node.nodeid -> absolute PLC holding register
        self.rw_coil = {}   # node.nodeid -> absolute PLC coil
        self.rw_key = {}    # node.nodeid -> key, for logging

    async def _var(self, parent, name, vtype=ua.VariantType.Int16, init=0):
        return await parent.add_variable(self.idx, name, ua.Variant(init, vtype))

    async def _add_ro(self, parent, key, name, vtype=ua.VariantType.Int16):
        n = await self._var(parent, name, vtype,
                            False if vtype == ua.VariantType.Boolean else 0)
        self.ro[key] = n
        return n

    async def _add_rw(self, parent, key, name, addr):
        n = await self._var(parent, name)
        await n.set_writable()          # AccessLevel: CurrentRead | CurrentWrite
        self.rw[key] = n
        self.rw_addr[n.nodeid] = addr
        self.rw_key[n.nodeid] = key
        return n

    async def _add_rw_bool(self, parent, key, name, coil):
        n = await self._var(parent, name, ua.VariantType.Boolean, False)
        await n.set_writable()
        self.rw[key] = n
        self.rw_coil[n.nodeid] = coil
        self.rw_key[n.nodeid] = key
        return n

    async def build(self, server):
        objects = server.nodes.objects
        sorter = await objects.add_object(self.idx, "Sorter")

        # Induct1..3
        for i in range(3):
            o = await sorter.add_object(self.idx, f"Induct{i+1}")
            await self._add_rw(o, f"induct{i}.speed_sp", "SpeedSetpoint",
                               SP_BASE + SP["speed_sp"] + i)
            await self._add_ro(o, f"induct{i}.speed_fb", "SpeedFeedback")
            await self._add_rw(o, f"induct{i}.rate_sp", "RateSetpoint",
                               SP_BASE + SP["rate_sp"] + i)
            await self._add_ro(o, f"induct{i}.scan", "ScanCode")

        # Outbound1..3
        for i in range(3):
            o = await sorter.add_object(self.idx, f"Outbound{i+1}")
            await self._add_rw(o, f"outbound{i}.speed_sp", "SpeedSetpoint",
                               SP_BASE + SP["ob_speed"] + i)
            await self._add_ro(o, f"outbound{i}.speed_fb", "SpeedFeedback")

        # Process
        proc = await sorter.add_object(self.idx, "Process")
        await self._add_rw(proc, "min_gap", "MinGapSetpoint",
                           SP_BASE + SP["min_gap"])
        await self._add_rw(proc, "noread_sp", "NoReadRateSetpoint",
                           SP_BASE + SP["noread_sp"])
        for key, name in (("inducted_ct", "InductedCount"),
                          ("missort_ct", "MissortCount"),
                          ("coll_ct", "CollisionCount"),
                          ("jam_ct", "JamCount"),
                          ("nohome_ct", "NoHomeCount")):
            await self._add_ro(proc, key, name)

        # --- extension beyond plan section 3, needed for HMI parity -------
        # The section 3 tree omits everything the operator screen actually
        # renders besides counters: run/auto state, the four alarms, the
        # no-read and recirc counters, and belt occupancy. Step 3 of plan
        # section 7 requires the UA-backed HMI to show the same numbers the
        # Modbus-backed one showed, which is not possible without these.
        # Grouped separately so the plan's tree stays legible.
        for key, name in (("noread_ct", "NoReadCount"),
                          ("recirc_ct", "RecircCount")):
            await self._add_ro(proc, key, name)

        # The run flags are commands, not status: they are coils sitting on the
        # PLC's Modbus server waiting to be written, exactly like the setpoints.
        # Exposing them read-only would have misrepresented what they are.
        status = await proc.add_object(self.idx, "Status")
        await self._add_rw_bool(status, "run", "Run", COIL_ABS["run"])
        await self._add_rw_bool(status, "auto", "Auto", COIL_ABS["auto"])
        for key, name in (("jam_alarm", "JamAlarm"), ("coll_alarm", "CollisionAlarm"),
                          ("noread_alarm", "NoReadAlarm"), ("nohome_alarm", "NoHomeAlarm")):
            await self._add_ro(status, key, name, ua.VariantType.Boolean)
        for key, name in (("scanner_state", "ScannerResetState"),
                          ("scanner_fault_mask", "ScannerFaultMask"),
                          ("scanner_ack_mask", "ScannerAckMask"),
                          ("scanner_wait", "ScannerWaitScans")):
            await self._add_ro(status, key, name)
        await self._add_ro(status, "xle_heartbeat_age", "XLeHeartbeatAge")
        await self._add_ro(status, "xle_liveness", "XLeLivenessState")
        await self._add_rw_bool(status, "scanner_fault_ack", "ScannerFaultAck",
                                COIL_ABS["scanner_fault_ack"])
        await self._add_rw_bool(status, "scanner_retry", "ScannerRetry",
                                COIL_ABS["scanner_retry"])
        await self._add_rw_bool(status, "xle_fault_ack", "XLeFaultAck",
                                COIL_ABS["xle_fault_ack"])
        await self._add_rw_bool(status, "xle_retry", "XLeRetry",
                                COIL_ABS["xle_retry"])
        for i in range(3):
            await self._add_rw_bool(status, f"lane_run{i}", f"Induct{i+1}Running",
                                    COIL_ABS["lane_run"] + i)
            await self._add_rw_bool(status, f"ob_run{i}", f"Outbound{i+1}Running",
                                    COIL_ABS["ob_run"] + i)

        belts = await sorter.add_object(self.idx, "Belts")
        for i in range(3):
            o = await belts.add_object(self.idx, f"Induct{i+1}")
            n = await o.add_variable(self.idx, "Cells",
                                     ua.Variant([0] * 20, ua.VariantType.Int16))
            self.ro[f"ib{i}.cells"] = n
        for i in range(3):
            o = await belts.add_object(self.idx, f"Outbound{i+1}")
            n = await o.add_variable(self.idx, "Cells",
                                     ua.Variant([0] * 20, ua.VariantType.Int16))
            self.ro[f"ob{i}.cells"] = n
        # ------------------------------------------------- end extension

        # Run: the reproducibility controls. Seed and run id are writable so a
        # scenario can be selected from Level 2; Reset is the coil that re-runs
        # init. Grouping them apart from the process makes them legible in a
        # browse as what they are, which matters because two of the three are
        # also attack surface: predicting the barcode stream is reconnaissance,
        # and Reset erases the evidence of anything that happened.
        run = await sorter.add_object(self.idx, "Run")
        await self._add_ro(run, "scan_ct", "ScanCount")
        for i in range(3):
            await self._add_ro(run, f"shift_ct{i}", f"Induct{i+1}ShiftCount")
        await self._add_rw(run, "master_seed", "MasterSeed",
                           SP_BASE + SP["master_seed"])
        await self._add_rw(run, "run_id", "RunId", SP_BASE + SP["run_id"])
        # Read only on purpose. The PLC sets it at init from a value baked in
        # at generation time, so a client cannot forge it from here; changing
        # it means changing the program.
        await self._add_ro(run, "prog_hash", "ProgramHash")
        await self._add_rw_bool(run, "reset_cmd", "Reset", COIL_ABS["reset_cmd"])

        # Operator fault reset, on Process rather than Run: it is a normal
        # part of running the line, not part of scenario control.
        await self._add_rw_bool(proc, "fault_reset", "FaultReset",
                                COIL_ABS["fault_reset"])

        # Trailers
        trailers = await sorter.add_object(self.idx, "Trailers")
        for belt in range(3):
            for door in range(3):
                i = belt * 3 + door
                o = await trailers.add_object(self.idx, f"Trailer_{belt+1}_{door+1}")
                await self._add_ro(o, f"trailer{i}.loaded", "LoadedCount")
                await self._add_ro(o, f"trailer{i}.bad", "BadCount")

        # Drives
        drives = await sorter.add_object(self.idx, "Drives")
        for i, (name, _) in enumerate(DRIVES):
            o = await drives.add_object(self.idx, name)
            # The full seven-register drive profile, not a subset. CommandWord
            # and SpeedReference are what the PLC wrote down to the drive;
            # Sorter/Induct{n}/SpeedSetpoint is what the operator set on the
            # PLC. Those normally agree, and the cases where they diverge are
            # exactly the interesting ones: a direct write to the drive, or a
            # write to the PLC's %QW100 block, moves one without the other.
            await self._add_ro(o, f"drive{i}.cmd", "CommandWord")
            await self._add_ro(o, f"drive{i}.ref", "SpeedReference")
            # Mechanical load the controller told the drive it is carrying,
            # and the thermal accumulator the drive derives from it. Together
            # they are the causal chain from a parameter write to a stopped
            # belt, visible as tag data rather than inferred from a trip.
            await self._add_ro(o, f"drive{i}.belt_load", "BeltLoad")
            await self._add_ro(o, f"drive{i}.status", "StatusWord")
            await self._add_ro(o, f"drive{i}.speed_fb", "SpeedFeedback")
            await self._add_ro(o, f"drive{i}.thermal", "ThermalLoad")
            # OutputFreq is Hz x 10 and Current is A x 10, raw as the drive
            # profile holds them, so the UA tree mirrors the register map
            # one for one. Scaling belongs in the client.
            await self._add_ro(o, f"drive{i}.out_freq", "OutputFreq")
            await self._add_ro(o, f"drive{i}.current", "Current")
            await self._add_ro(o, f"drive{i}.fault", "FaultCode")


# ---------------------------------------------------------- write handler

class WriteHandler:
    """Fires on any data change to a writable node, including this server's
    own refresh from the poll. Distinguishing the two is done by value:
    the poller records what it is about to write, and a change matching
    that record is an echo, not a client write.

    Known limit: a client writing a value identical to the current polled
    value is indistinguishable from an echo and is dropped. That write is
    a no-op at the register level anyway, but it means the write log is a
    log of effective writes, not of every UA write on the wire. For
    Milestone 2/4 the packet capture is the authoritative record; this log
    is corroboration.
    """

    def __init__(self, ns, link):
        self.ns = ns
        self.link = link
        self.expected = {}      # nodeid -> value last pushed by the poller
        self.armed = False      # see arming note below

    def datachange_notification(self, node, val, data):
        # Subscribing to our own nodes makes asyncua deliver an initial
        # data change for every one of them, carrying whatever the tree was
        # built with. Relaying that burst south writes 0 into all eleven
        # setpoints, which zeroes min_gap and is the gap collapse attack.
        # So the handler stays disarmed until the first successful poll has
        # put real register values into self.expected.
        if not self.armed:
            return
        if self.expected.get(node.nodeid) == val:
            return
        key = self.ns.rw_key.get(node.nodeid, str(node.nodeid))
        addr = self.ns.rw_addr.get(node.nodeid)
        if addr is not None:
            log.warning("UA write %s = %s -> PLC HR %d", key, val, addr)
            asyncio.create_task(self._relay(addr, val, key, False))
            return
        coil = self.ns.rw_coil.get(node.nodeid)
        if coil is not None:
            log.warning("UA write %s = %s -> PLC coil %d", key, val, coil)
            asyncio.create_task(self._relay(coil, val, key, True))

    async def _relay(self, addr, val, key, is_coil):
        try:
            ok = await (self.link.write_coil(addr, val) if is_coil
                        else self.link.write(addr, val))
        except Exception as e:
            log.error("relay failed: %s = %s -> %d (%s)", key, val, addr, e)
            return
        if not ok:
            log.error("relay rejected: %s = %s -> %d", key, val, addr)


# ----------------------------------------------------------------- poller

async def push(ns, handler, snap):
    sp, ib, ob, co, dr = (snap["sp"], snap["ib"], snap["ob"],
                          snap["coils"], snap["drives"])

    async def w(key, value):
        node = ns.ro.get(key) or ns.rw.get(key)
        if node is None:
            return
        if node.nodeid in ns.rw_addr or node.nodeid in ns.rw_coil:
            handler.expected[node.nodeid] = value
        await node.write_value(
            ua.Variant(value,
                       ua.VariantType.Boolean if isinstance(value, bool)
                       else ua.VariantType.Int16))

    for i in range(3):
        await w(f"induct{i}.speed_sp", sp[SP["speed_sp"] + i])
        await w(f"induct{i}.rate_sp", sp[SP["rate_sp"] + i])
        await w(f"induct{i}.scan", sp[SP["scan"] + i])
        await w(f"induct{i}.speed_fb", dr[i][DRV["speed_fb"]])
        await w(f"outbound{i}.speed_sp", sp[SP["ob_speed"] + i])
        await w(f"outbound{i}.speed_fb", dr[3 + i][DRV["speed_fb"]])

    await w("scan_ct", sp[SP["scan_ct"]])
    await w("master_seed", sp[SP["master_seed"]])
    await w("run_id", sp[SP["run_id"]])
    await w("prog_hash", sp[SP["prog_hash"]])
    for key in ("scanner_state", "scanner_fault_mask", "scanner_ack_mask", "scanner_wait"):
        await w(key, sp[SP[key]])
    await w("xle_heartbeat_age", snap["liveness"][0])
    await w("xle_liveness", snap["liveness"][1])
    for i in range(3):
        await w(f"shift_ct{i}", sp[SP["shift_ct"] + i])
    # reset_cmd is self-clearing in the PLC: it is true for one scan and the
    # poll will nearly always read it false. Publishing the polled value keeps
    # the node honest rather than latching whatever a client last wrote.
    await w("reset_cmd", bool(co[COIL["reset_cmd"]]))
    await w("fault_reset", bool(co[COIL["fault_reset"]]))
    await w("scanner_fault_ack", bool(co[COIL["scanner_fault_ack"]]))
    await w("scanner_retry", bool(co[COIL["scanner_retry"]]))
    await w("xle_fault_ack", bool(co[COIL["xle_fault_ack"]]))
    await w("xle_retry", bool(co[COIL["xle_retry"]]))

    await w("min_gap", sp[SP["min_gap"]])
    await w("noread_sp", sp[SP["noread_sp"]])
    for key in ("noread_ct", "coll_ct", "jam_ct", "missort_ct",
                "recirc_ct", "nohome_ct", "inducted_ct"):
        await w(key, sp[SP[key]])

    for key in ("run", "auto", "jam_alarm", "coll_alarm",
                "noread_alarm", "nohome_alarm"):
        await w(key, bool(co[COIL[key]]))
    for i in range(3):
        await w(f"lane_run{i}", bool(co[COIL["lane_run"] + i]))
        await w(f"ob_run{i}", bool(co[COIL["ob_run"] + i]))

    for i in range(3):
        await ns.ro[f"ib{i}.cells"].write_value(
            ua.Variant(ib[i * 20:(i + 1) * 20], ua.VariantType.Int16))
        await ns.ro[f"ob{i}.cells"].write_value(
            ua.Variant(ob[i * 20:(i + 1) * 20], ua.VariantType.Int16))

    for i in range(9):
        await w(f"trailer{i}.loaded", sp[SP["trailer"] + i])
        await w(f"trailer{i}.bad", sp[SP["bad"] + i])

    for i in range(len(DRIVES)):
        await w(f"drive{i}.cmd", dr[i][DRV["cmd"]])
        await w(f"drive{i}.ref", dr[i][DRV["ref"]])
        await w(f"drive{i}.belt_load", dr[i][DRV["belt_load"]])
        await w(f"drive{i}.status", dr[i][DRV["status"]])
        await w(f"drive{i}.speed_fb", dr[i][DRV["speed_fb"]])
        await w(f"drive{i}.thermal", dr[i][DRV["thermal"]])
        await w(f"drive{i}.out_freq", dr[i][DRV["out_freq"]])
        await w(f"drive{i}.current", dr[i][DRV["current"]])
        await w(f"drive{i}.fault", dr[i][DRV["fault"]])


async def poll_loop(ns, handler, link):
    down = False
    while True:
        try:
            snap = await link.read_all()
            await push(ns, handler, snap)
            if not handler.armed:
                handler.armed = True
                log.info("write relay armed")
            if down:
                log.info("southbound recovered")
                down = False
        except Exception as e:
            if not down:
                log.error("southbound poll failed: %s", e)
                down = True
        await asyncio.sleep(POLL_SEC)


# ------------------------------------------------------------------- main

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("asyncua").setLevel(logging.WARNING)

    server = Server()
    await server.init()
    server.set_endpoint(ENDPOINT)
    server.set_server_name("Sorter Level 2 Supervisory")

    if SECURITY == "none":
        # Milestone 1/3 build state: what is realistic to find in the field.
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    else:
        # Milestone 5: same server, same namespace, same attack scripts.
        await server.load_certificate(CERT)
        await server.load_private_key(KEY)
        server.set_security_policy(
            [ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])
        server.set_security_IDs(["Username"])
        server.trust_certificate(TRUSTLIST)
    log.info("security policy: %s", SECURITY)

    idx = await server.register_namespace(URI)
    ns = Namespace(idx)
    await ns.build(server)

    link = ModbusLink()
    handler = WriteHandler(ns, link)
    sub = await server.create_subscription(100, handler)
    await sub.subscribe_data_change(list(ns.rw.values()))

    async with server:
        log.info("serving %s (%d writable tags)", ENDPOINT, len(ns.rw))
        await poll_loop(ns, handler, link)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
