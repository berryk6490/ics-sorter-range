#!/usr/bin/env python3
"""Level 2 operator HMI for the induct sorter, as an OPC UA client.

Screen follows high-performance HMI convention: muted field, color reserved
for abnormal state, plan-view belt schematic. Package motion is derived from
drive speed feedback, so it tracks the belt rather than approximating it.

Same screen as before. What changed is the protocol underneath it: instead
of polling the PLC and six drives over Modbus, this subscribes to the UA
server on scada:4840 and is pushed changes as they happen.

Per opcua_plan.md section 7 step 2. The page markup is unchanged from the
Modbus-backed version apart from the connection status line, so step 3 is
a real parity check: the same scenario should produce the same numbers.

    ~/opcua/bin/python hmi_app.py      # shares the opcua venv
"""
import asyncio
import collections
import threading
import time

from asyncua import Client, ua
from flask import Flask, jsonify, render_template_string

ENDPOINT = "opc.tcp://10.10.2.10:4840/sorter/"
URI = "urn:sorter:level2"
RATE_WINDOW = 60        # seconds of history for throughput rates
RATE_SAMPLE = 0.5       # how often to sample counters for the rate window
POLL_MS = 100           # page refresh; matches the server's southbound poll

DRIVE_NAMES = ["induct 1", "induct 2", "induct 3",
               "outbnd 1", "outbnd 2", "outbnd 3"]
DRIVE_NODES = ["Induct1", "Induct2", "Induct3",
               "Outbound1", "Outbound2", "Outbound3"]

# Every tag the screen renders, as a browse path under Sorter. The key is
# what the /api handler reads; the path is what gets subscribed.
def _tagmap():
    t = {}
    for i in range(3):
        t[f"speed_sp{i}"] = [f"Induct{i+1}", "SpeedSetpoint"]
        t[f"rate_sp{i}"] = [f"Induct{i+1}", "RateSetpoint"]
        t[f"scan{i}"] = [f"Induct{i+1}", "ScanCode"]
        t[f"ob_speed{i}"] = [f"Outbound{i+1}", "SpeedSetpoint"]
    for key, name in (("min_gap", "MinGapSetpoint"),
                      ("noread_sp", "NoReadRateSetpoint"),
                      ("inducted", "InductedCount"),
                      ("missort", "MissortCount"),
                      ("coll", "CollisionCount"),
                      ("jam", "JamCount"),
                      ("nohome", "NoHomeCount"),
                      ("noread", "NoReadCount"),
                      ("recirc", "RecircCount")):
        t[key] = ["Process", name]
    for key, name in (("run", "Run"), ("auto", "Auto"),
                      ("a_jam", "JamAlarm"), ("a_coll", "CollisionAlarm"),
                      ("a_noread", "NoReadAlarm"), ("a_nohome", "NoHomeAlarm")):
        t[key] = ["Process", "Status", name]
    for key, name in (("scanner_state", "ScannerResetState"),
                      ("scanner_fault_mask", "ScannerFaultMask"),
                      ("scanner_ack_mask", "ScannerAckMask"),
                      ("scanner_wait", "ScannerWaitScans"),
                      ("scanner_fault_ack", "ScannerFaultAck"),
                      ("scanner_retry", "ScannerRetry")):
        t[key] = ["Process", "Status", name]
    for key, name in (("xle_heartbeat_age", "XLeHeartbeatAge"),
                      ("xle_liveness", "XLeLivenessState"),
                      ("xle_fault_ack", "XLeFaultAck"),
                      ("xle_retry", "XLeRetry")):
        t[key] = ["Process", "Status", name]
    t["plant_fault"] = ["Process", "Status", "PlantFault"]
    t["plant_failed_count"] = ["Process", "Status", "PlantFailedConfirmationCount"]
    t["plant_failed_lane"] = ["Process", "Status", "PlantFailedLane"]
    t["plant_heartbeat_age"] = ["Process", "Status", "PlantHeartbeatAge"]
    t["plant_mode"] = ["Process", "Status", "PlantMode"]
    t["photoeye_mode"] = ["Process", "Status", "PhotoeyeMode"]
    for key, name in (("accumulation_mode", "AccumulationMode"),
                      ("zone_fault", "ZoneFault"),
                      ("zone_ready_mask", "ZoneInductReadyMask"),
                      ("zone_age", "ZoneAgeScans")):
        t[key] = ["Process", "Status", name]
    for key, name in (("photoeye_fault_mask", "PhotoeyeFaultMask"),
                      ("photoeye_fault_sensor", "PhotoeyeFaultSensor"),
                      ("photoeye_fault_lane", "PhotoeyeFaultLane")):
        t[key] = ["Process", "Status", name]
    for i in range(3):
        slot = ["Process", "Plant", f"Slot{i+1}"]
        t[f"plant{i}"] = slot + ["Telemetry"]
        t[f"photoeyes{i}"] = slot + ["Photoeyes"]
        t[f"zone{i}"] = slot + ["ValidatedZone"]
        t[f"plant_status{i}"] = slot + ["Status"]
        t[f"plant_age{i}"] = slot + ["AgeScans"]
        t[f"plant_lane{i}"] = slot + ["Lane"]
    for i in range(3):
        t[f"lane_run{i}"] = ["Process", "Status", f"Induct{i+1}Running"]
        t[f"ob_run{i}"] = ["Process", "Status", f"Outbound{i+1}Running"]
        t[f"ib{i}"] = ["Belts", f"Induct{i+1}", "Cells"]
        t[f"ob{i}"] = ["Belts", f"Outbound{i+1}", "Cells"]
    for b in range(3):
        for d in range(3):
            i = b * 3 + d
            t[f"trailer{i}"] = ["Trailers", f"Trailer_{b+1}_{d+1}", "LoadedCount"]
            t[f"bad{i}"] = ["Trailers", f"Trailer_{b+1}_{d+1}", "BadCount"]
    for i, node in enumerate(DRIVE_NODES):
        for key, name in (("cmd", "CommandWord"), ("ref", "SpeedReference"),
                          ("belt_load", "BeltLoad"), ("status", "StatusWord"),
                          ("rpm", "SpeedFeedback"), ("hz", "OutputFreq"),
                          ("amps", "Current"), ("fault", "FaultCode"),
                          ("thermal", "ThermalLoad")):
            t[f"drive{i}.{key}"] = ["Drives", node, name]
    return t


TAGS = _tagmap()

state = {k: ([0] * 20 if k.startswith(("ib", "ob")) and len(k) == 3
             else [0] * 10 if k.startswith("plant") and len(k) == 6
             else [0] * 8 if k.startswith("photoeyes")
             else [0] * 6 if k.startswith("zone") and len(k) == 5 else 0)
         for k in TAGS}
state["connected"] = False
hist = collections.deque(maxlen=int(RATE_WINDOW / RATE_SAMPLE))
lock = threading.Lock()


class SubHandler:
    """The server pushes; this just files the value under its key."""

    def __init__(self, keys):
        self.keys = keys        # nodeid -> key

    def datachange_notification(self, node, val, data):
        key = self.keys.get(node.nodeid)
        if key is None:
            return
        with lock:
            state[key] = list(val) if isinstance(val, (list, tuple)) else val


async def sample_rates():
    """A subscription only fires on change, so the rate window is sampled on
    its own clock rather than off the notifications.

    Two discontinuities have to be handled or the figure is nonsense. Sampling
    waits for real values, because seeding the window with pre-subscription
    zeros reads as a jump from nothing to the whole counter. And the counters
    reset to zero whenever the PLC program restarts, which inside a live UA
    session looks like every package un-inducting at once; the window is
    dropped when a counter goes backwards."""
    while True:
        with lock:
            ind = state["inducted"]
            load = sum(state[f"trailer{i}"] for i in range(9))
            if hist and (ind < hist[-1][1] or load < hist[-1][2]):
                hist.clear()                      # PLC restarted
            if ind or load:
                hist.append((time.time(), ind, load))
        await asyncio.sleep(RATE_SAMPLE)


CONTROL = {"run", "auto", "lane_run0", "lane_run1", "lane_run2",
           "ob_run0", "ob_run1", "ob_run2", "scanner_fault_ack", "scanner_retry",
           "xle_fault_ack", "xle_retry"}
ua_ctx = {"loop": None, "nodes": {}}     # set once a session is established


async def session():
    """One UA session: resolve the tags, subscribe, then sit idle."""
    async with Client(ENDPOINT) as client:
        idx = await client.get_namespace_index(URI)
        sorter = await client.nodes.objects.get_child([f"{idx}:Sorter"])

        nodes, keys = [], {}
        for key, path in TAGS.items():
            node = await sorter.get_child([f"{idx}:{p}" for p in path])
            nodes.append(node)
            keys[node.nodeid] = key

        handler = SubHandler(keys)
        sub = await client.create_subscription(200, handler)
        await sub.subscribe_data_change(nodes)

        ua_ctx["loop"] = asyncio.get_running_loop()
        ua_ctx["nodes"] = {k: n for k, n in zip(TAGS.keys(), nodes)
                           if k in CONTROL}
        with lock:
            state["connected"] = True
            hist.clear()
        rates = asyncio.create_task(sample_rates())
        try:
            while True:                     # keepalive, no polling
                await client.check_connection()
                await asyncio.sleep(2)
        finally:
            rates.cancel()
            ua_ctx["loop"] = None
            ua_ctx["nodes"] = {}
            with lock:
                state["connected"] = False


async def ua_loop():
    while True:
        try:
            await session()
        except Exception as e:
            with lock:
                state["connected"] = False
            print(f"UA session ended: {e}")
        await asyncio.sleep(2)


RATE_MIN_SPAN = 5.0     # seconds of history before a rate is meaningful


def rates():
    """Packages per minute over the history window. A window shorter than a
    few seconds divides a small count by a smaller interval and produces a
    figure in the tens of thousands, so it reports nothing until the window
    has real span."""
    with lock:
        h = list(hist)
    if len(h) < 4:
        return 0.0, 0.0
    dt = h[-1][0] - h[0][0]
    if dt < RATE_MIN_SPAN:
        return 0.0, 0.0
    return (max(0.0, (h[-1][1] - h[0][1]) * 60.0 / dt),
            max(0.0, (h[-1][2] - h[0][2]) * 60.0 / dt))


app = Flask(__name__)


@app.route("/api")
def api():
    with lock:
        s = dict(state)
    ind_rate, load_rate = rates()
    return jsonify({
        "connected": s["connected"],
        "speed_sp":  [s[f"speed_sp{i}"] for i in range(3)],
        "ob_speed":  [s[f"ob_speed{i}"] for i in range(3)],
        "min_gap":   s["min_gap"],
        "rate_sp":   [s[f"rate_sp{i}"] for i in range(3)],
        "noread_sp": s["noread_sp"],
        "scan":      [s[f"scan{i}"] for i in range(3)],
        "noread": s["noread"], "coll": s["coll"], "jam": s["jam"],
        "missort": s["missort"], "recirc": s["recirc"],
        "nohome": s["nohome"], "inducted": s["inducted"],
        "trailer": [s[f"trailer{i}"] for i in range(9)],
        "bad": [s[f"bad{i}"] for i in range(9)],
        "ib": [s[f"ib{i}"] for i in range(3)],
        "ob": [s[f"ob{i}"] for i in range(3)],
        "run": s["run"], "auto": s["auto"],
        "scanner_state": s["scanner_state"],
        "scanner_fault_mask": s["scanner_fault_mask"],
        "scanner_ack_mask": s["scanner_ack_mask"],
        "scanner_wait": s["scanner_wait"],
        "xle_heartbeat_age": s["xle_heartbeat_age"],
        "xle_liveness": s["xle_liveness"],
        "plant_fault": s["plant_fault"], "plant_mode": s["plant_mode"],
        "photoeye_mode": s["photoeye_mode"],
        "accumulation_mode": s["accumulation_mode"],
        "zone_fault": s["zone_fault"],
        "zone_ready_mask": s["zone_ready_mask"],
        "zone_age": s["zone_age"],
        "zone_rows": [s[f"zone{i}"] for i in range(3)],
        "photoeye_fault_mask": s["photoeye_fault_mask"],
        "photoeye_fault_sensor": s["photoeye_fault_sensor"],
        "photoeye_fault_lane": s["photoeye_fault_lane"],
        "photoeye_rows": [s[f"photoeyes{i}"] for i in range(3)],
        "plant_failed_count": s["plant_failed_count"],
        "plant_failed_lane": s["plant_failed_lane"],
        "plant_heartbeat_age": s["plant_heartbeat_age"],
        "plant_rows": [s[f"plant{i}"] for i in range(3)],
        "plant_status": [s[f"plant_status{i}"] for i in range(3)],
        "plant_age": [s[f"plant_age{i}"] for i in range(3)],
        "plant_lane": [s[f"plant_lane{i}"] for i in range(3)],
        "lane_run": [s[f"lane_run{i}"] for i in range(3)],
        "ob_run": [s[f"ob_run{i}"] for i in range(3)],
        "alarms": {"jam": s["a_jam"], "coll": s["a_coll"],
                   "noread": s["a_noread"], "nohome": s["a_nohome"]},
        "drives": [{"name": DRIVE_NAMES[i],
                    "cmd": s[f"drive{i}.cmd"],
                    "ref": s[f"drive{i}.ref"],
                    "load": s[f"drive{i}.belt_load"],
                    "thermal": s[f"drive{i}.thermal"],
                    "status": s[f"drive{i}.status"],
                    "hz": s[f"drive{i}.hz"] / 10.0,
                    "rpm": s[f"drive{i}.rpm"],
                    "fault": s[f"drive{i}.fault"],
                    "amps": s[f"drive{i}.amps"] / 10.0}
                   for i in range(6)],
        "ind_rate": round(ind_rate, 1),
        "load_rate": round(load_rate, 1),
    })


@app.route("/cmd/<key>/<int:val>", methods=["POST"])
def cmd(key, val):
    """Operator control. The write leaves here as a UA write to a writable
    node, which the server relays down to the PLC coil. That is the same path
    an attacker with a UA session would use; nothing about this request is
    privileged, which is exactly the point the range is making."""
    if key not in CONTROL:
        return jsonify({"ok": False, "err": "unknown control"}), 400
    loop, node = ua_ctx["loop"], ua_ctx["nodes"].get(key)
    if loop is None or node is None:
        return jsonify({"ok": False, "err": "no UA session"}), 503
    try:
        fut = asyncio.run_coroutine_threadsafe(
            node.write_value(ua.Variant(bool(val), ua.VariantType.Boolean)), loop)
        fut.result(timeout=3)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "err": str(e)}), 500


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<title>Induct sorter</title><style>
:root{
  --bg:#13171a; --panel:#1c2126; --panel2:#242a30; --line:#333b42;
  --ink:#dde2e5; --dim:#8b9299; --faint:#5f686f;
  --alarm:#c9483b; --warn:#c08a28; --ok:#3f9e75;
  --d1:#4c6c8c; --d2:#5f7a52; --d3:#8a7045;
}
*{box-sizing:border-box}
body{font-family:"Segoe UI",system-ui,sans-serif;background:var(--bg);
  color:var(--ink);margin:0;padding:14px;font-size:13px}
h1{font-size:15px;font-weight:600;margin:0;letter-spacing:.2px}
h2{font-size:10px;font-weight:600;color:var(--dim);margin:0 0 9px;
  text-transform:uppercase;letter-spacing:.9px}
.top{display:flex;align-items:baseline;gap:14px;margin-bottom:10px}
.sub{color:var(--dim);font-size:11px}
.sub.bad{color:var(--alarm);font-weight:600}
.bar{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:12px}
.st{padding:3px 10px;border:1px solid var(--line);background:var(--panel);
  color:var(--faint);font-size:10px;letter-spacing:.5px;text-transform:uppercase}
.st.on{color:var(--ink);background:var(--panel2);border-color:#414a52}
.st.al{background:var(--alarm);border-color:var(--alarm);color:#1a1012;font-weight:700}
.panel{background:var(--panel);border:1px solid var(--line);padding:12px;margin-bottom:12px}
.ctl{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
.ctl .lbl{font-size:9px;color:var(--faint);text-transform:uppercase;
  letter-spacing:.7px;margin-right:3px}
.btn{padding:4px 11px;border:1px solid var(--line);background:var(--panel2);
  color:var(--dim);font-size:10px;letter-spacing:.5px;text-transform:uppercase;
  cursor:pointer;font-family:inherit}
.btn:hover{border-color:#4c5760;color:var(--ink)}
.btn.on{background:#1f3b2f;border-color:#2f5c47;color:#8fd6b6}
.btn.off{background:#3a2320;border-color:#5c3630;color:#e0a49c}
.btn:disabled{opacity:.4;cursor:default}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(104px,1fr));gap:1px;
  background:var(--line);border:1px solid var(--line);margin-bottom:12px}
.kpi{background:var(--panel);padding:8px 10px}
.kl{color:var(--dim);font-size:9px;text-transform:uppercase;letter-spacing:.7px}
.kv{font-size:21px;font-weight:600;margin-top:1px;font-variant-numeric:tabular-nums}
.kv.bad{color:var(--alarm)}
.spark{display:block;margin-top:4px}
.lane{display:flex;align-items:center;gap:9px;margin-bottom:6px}
.ln{width:58px;font-size:10px;color:var(--dim);text-align:right;flex-shrink:0;
  text-transform:uppercase;letter-spacing:.5px}
.track{position:relative;height:26px;background:var(--panel2);
  border:1px solid var(--line);flex-shrink:0}
.tick{position:absolute;top:0;bottom:0;width:1px;background:#2b3339}
.mk{position:absolute;top:-1px;bottom:-1px;width:2px}
.mk.tun{background:var(--dim)}
.mk.div{background:#4a545c}
.mkl{position:absolute;top:-13px;font-size:8px;color:var(--faint);
  letter-spacing:.4px;transform:translateX(-50%)}
.pkg{position:absolute;top:3px;height:18px;width:36px;
  display:flex;align-items:center;justify-content:center;
  font-size:9px;font-weight:600;color:#0e1114;
  transition:opacity .18s linear}
.pkg.g1{background:var(--d1)}.pkg.g2{background:var(--d2)}.pkg.g3{background:var(--d3)}
.pkg.plant{width:44px;border:1px solid #d7e8db;z-index:2;
  transition:transform .09s linear,opacity .18s linear}
.pkg.plant.stale{background:var(--warn);border-color:var(--warn);
  transition:none}
.pkg.plant.held{filter:saturate(.35);border-color:var(--dim)}
.plantstate{display:flex;gap:12px;margin:0 0 9px 67px;color:var(--dim);
  font-size:10px;font-variant-numeric:tabular-nums}
.plantstate .stale{color:var(--warn);font-weight:700}
.plantstate .unavailable{color:var(--alarm);font-weight:700}
.plantstate .held{color:var(--dim);font-weight:600}
.flow{height:14px;margin:2px 0 8px 67px;position:relative}
.flow div{position:absolute;top:6px;height:1px;background:var(--line)}
.doors{display:flex;gap:9px;margin-left:67px;margin-top:3px}
.door{flex:1;background:var(--panel2);border:1px solid var(--line);padding:5px 7px}
.door.err{border-color:var(--alarm)}
.dn{font-size:9px;color:var(--dim);letter-spacing:.4px}
.dv{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:1px}
.dbad{font-size:9px;color:var(--faint)}
.dbad.on{color:var(--alarm);font-weight:600}
.cols{display:grid;grid-template-columns:1fr 340px;gap:12px}
@media(max-width:1000px){.cols{grid-template-columns:1fr}}
.fp{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);
  border:1px solid var(--line)}
.f{background:var(--panel);padding:7px 9px}
.fn{font-size:9px;color:var(--dim);text-transform:uppercase;letter-spacing:.6px;
  display:flex;justify-content:space-between}
.fs{width:7px;height:7px;border-radius:50%;background:#3b444b;margin-top:2px}
.fs.run{background:var(--ok)}.fs.flt{background:var(--alarm)}
.fv{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:2px}
.fv small{font-size:9px;font-weight:400;color:var(--dim)}
.fbar{height:3px;background:#2b3339;margin-top:4px;position:relative}
.fbar i{position:absolute;left:0;top:0;bottom:0;background:#68757e;
  transition:width .5s linear}
.fbar u{position:absolute;top:-2px;bottom:-2px;width:1px;background:var(--ink)}
.fmeta{font-size:9px;color:var(--dim);margin-top:3px;display:flex;
  justify-content:space-between;font-variant-numeric:tabular-nums}
.fmeta .flt{color:var(--alarm);font-weight:600}
.therm{height:2px;background:#2b3339;margin-top:3px;position:relative}
.therm i{position:absolute;left:0;top:0;bottom:0;background:#4a5560;
  transition:width .4s linear}
.therm i.warn{background:var(--warn)}
.therm i.trip{background:var(--alarm)}
table{width:100%;border-collapse:collapse;font-size:11px}
td{padding:4px 0;border-bottom:1px solid #272e34;font-variant-numeric:tabular-nums}
td:not(:first-child){text-align:right}
th{font-size:9px;color:var(--dim);text-align:right;font-weight:600;
  padding-bottom:5px;text-transform:uppercase;letter-spacing:.6px}
th:first-child{text-align:left}
</style></head><body>

<div class="top"><h1>INDUCT SORTER</h1><div class="sub" id="conn">connecting…</div></div>
<div class="bar" id="bar"></div>
<div class="ctl" id="ctl"></div>

<div class="kpis">
  <div class="kpi"><div class="kl">Inducted</div><div class="kv" id="ind">0</div></div>
  <div class="kpi"><div class="kl">Loaded</div><div class="kv" id="loaded">0</div></div>
  <div class="kpi"><div class="kl">Induct /min</div><div class="kv" id="irate">0</div>
    <svg class="spark" id="sp1" width="96" height="16"></svg></div>
  <div class="kpi"><div class="kl">Load /min</div><div class="kv" id="lrate">0</div>
    <svg class="spark" id="sp2" width="96" height="16"></svg></div>
  <div class="kpi"><div class="kl">Missorts</div><div class="kv" id="ms">0</div></div>
  <div class="kpi"><div class="kl">Collisions</div><div class="kv" id="coll">0</div></div>
  <div class="kpi"><div class="kl">Jams</div><div class="kv" id="jam">0</div></div>
  <div class="kpi"><div class="kl">No-reads</div><div class="kv" id="nr">0</div></div>
  <div class="kpi"><div class="kl">Recirc</div><div class="kv" id="rc">0</div></div>
</div>

<div class="panel">
  <h2>Sortation</h2>
  <div class="plantstate" id="plantstate" style="display:none"></div>
  <div id="ibelts"></div>
  <div class="flow"><div style="left:0;right:0"></div></div>
  <div id="obelts"></div>
  <div class="doors" id="doors"></div>
</div>

<div class="cols">
  <div class="panel"><h2>Drives</h2><div class="fp" id="drives"></div></div>
  <div class="panel"><h2>Setpoints</h2>
    <table><thead><tr><th>Point</th><th>Speed</th><th>Interval</th><th>Last scan</th></tr></thead>
    <tbody id="sps"></tbody></table>
  </div>
</div>

<script>
const CELLS=20, CW=44, RATED=1750;
const TUN=10, DIV=[14,16,18], ENT=[2,5,8], DOOR=[12,15,18];
const hist1=[], hist2=[];
const seen={};      // belt -> {barcode: {el, pos, target}}
const pace={};      // belt -> {dist, norm, rpm, rate}
const BELT_DRIVE={ib0:0, ib1:1, ib2:2, ob0:3, ob1:4, ob2:5};

function track(id, marks){
  let h='<div class="track" style="width:'+(CELLS*CW)+'px" id="'+id+'">';
  for(let i=1;i<CELLS;i++) h+='<div class="tick" style="left:'+(i*CW)+'px"></div>';
  marks.forEach(m=>{
    h+='<div class="mk '+m.k+'" style="left:'+(m.c*CW+CW/2)+'px"></div>';
    if(m.t) h+='<div class="mkl" style="left:'+(m.c*CW+CW/2)+'px">'+m.t+'</div>';
  });
  return h+'</div>';
}
function grp(v){const d=Math.floor(v/1000); return d<=3?'g1':(d<=6?'g2':'g3');}

/* Motion comes from the drive, not from watching the cells.

   The PLC advances a belt when its position accumulator overflows, and that
   accumulator is fed by the drive's reported speed: acc += fb/1750 each scan.
   So cells per second is (fb/1750) x scans per second. The scan rate is the
   one unknown, and it is constant, so it is calibrated continuously by
   dividing total observed cell movement by the integral of fb/1750 over the
   same period. After a few seconds that converges and stays put.

   Deriving speed this way means the animation stops the moment the belt does,
   follows the drive down through its deceleration ramp, and changes pace if
   anyone writes a new speed — including an attacker. Nothing else has to
   detect any of those cases. */
function beltRate(belt){
  const p=pace[belt];
  if(!p || !p.rpm) return 0;
  const norm=p.rpm/1750;
  const scans = (p.norm>0.5 && p.dist>3) ? p.dist/p.norm : 22;
  return Math.min(60, norm*scans*p.trim);
}

function paint(belt, cells, rpm){
  const el=document.getElementById(belt);
  if(!el) return;
  if(!seen[belt]) seen[belt]={};
  if(!pace[belt]) pace[belt]={dist:0, norm:0, rpm:0, rate:0, trim:1};
  const live=seen[belt], p=pace[belt], now={};
  p.rpm=rpm;
  cells.forEach((v,i)=>{ if(v) now[String(v)]=i; });
  let errSum=0, errN=0;

  // observed movement, for the scan-rate calibration only
  for(const k in now){
    const d=live[k];
    if(d && now[k]>d.target){ p.dist += now[k]-d.target; break; }
  }
  p.rate=beltRate(belt);

  for(const k in now){
    const i=now[k];
    let d=live[k];
    if(!d){
      const e=document.createElement('div');
      e.className='pkg '+grp(Number(k));
      e.textContent=k;
      e.style.transform='translateX('+(i*CW+4)+'px)';
      e.style.opacity='0';
      el.appendChild(e);
      requestAnimationFrame(()=>{e.style.opacity='1';});
      live[k]={el:e, pos:i, target:i};
    } else {
      d.target=i;
      const err=i-d.pos;
      const snap=Math.max(3, p.rate*0.6);
      if(Math.abs(err)>snap){ d.pos=i; }
      else { d.pos+=err*0.06; errSum+=err; errN++; }
    }
  }
  // Correct the rate, not the position. Holding a package against a ceiling
  // produces the stutter it was meant to prevent: it stalls at the limit and
  // bursts when the next reading lands. Trimming the estimate by how far
  // packages run ahead or behind lets velocity converge on the belt instead.
  if(errN && p.rate>0.05){
    p.trim = Math.min(1.6, Math.max(0.6, p.trim + (errSum/errN)*0.012));
  }
  for(const k in live){
    if(!(k in now)){
      const d=live[k]; d.el.style.opacity='0';
      setTimeout(()=>d.el.remove(),220); delete live[k];
    }
  }
}

let lastFrame=performance.now();
function frame(t){
  const dt=Math.min(.25,(t-lastFrame)/1000); lastFrame=t;
  for(const belt in seen){
    const p=pace[belt]; if(!p) continue;
    p.norm += (p.rpm/1750)*dt;          // integral for the calibration
    const rate=p.rate, step=rate*dt;
    const live=seen[belt];
    const stopped = rate < 0.05;
    for(const k in live){
      const d=live[k];
      // A stopped belt is not a slow belt: park the package on its cell so
      // nothing creeps forward while the belt is standing still.
      if(stopped) d.pos=d.target;
      // No per-frame ceiling. The trim keeps position honest; this limit only
      // catches a runaway estimate, well outside normal drift.
      else d.pos=Math.min(d.target+3, Math.min(CELLS-1, d.pos+step));
      d.el.style.transform='translateX('+(d.pos*CW+4)+'px)';
    }
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

/* Plant mode consumes PLC-validated positions verbatim. It never integrates
   VFD speed in the browser, so a missed update cannot make a parcel drift. */
const plantSeen={};
let currentPlantMode=false;
const SENSOR_NAME=['WAIT','INDUCT','TUNNEL','DIVERT','TRAILER','RECIRC','FAILED CONFIRM'];
const ZONE_NAME=['NONE','APPROACH','DECISION','PREMERGE','MERGE','OUTBOUND','TERMINAL'];
const MOTION_NAME=['NONE','MOVING','HELD_DOWNSTREAM','HELD_MERGE','DRIVE_STOPPED',
  'AWAITING_ROUTE','OUTBOUND','TERMINAL','JAMMED'];
const HOLD_NAME=['NONE','ZONE_FULL','MERGE_CAPACITY','DRIVE_OFF','ROUTE_PENDING'];
function clearLegacy(belt){
  const live=seen[belt]||{};
  for(const key in live) live[key].el.remove();
  seen[belt]={}; delete pace[belt];
}
function clearPlant(){
  for(const key in plantSeen){plantSeen[key].remove(); delete plantSeen[key];}
}
function freezePlant(reason){
  for(const key in plantSeen){
    const el=plantSeen[key]; el.classList.add('stale');
    el.style.transition='none'; el.dataset.status='unavailable';
  }
  const line=document.getElementById('plantstate');
  if(currentPlantMode){line.style.display='flex';line.innerHTML='<span class="unavailable">'+reason+'</span>';}
}
function paintPlant(d){
  const line=document.getElementById('plantstate');
  line.style.display='flex';
  if(!d.connected){freezePlant('PLANT TELEMETRY UNAVAILABLE — UA DISCONNECTED');return;}
  const now={};
  const labels=[];
  for(let slot=0;slot<3;slot++){
    const r=d.plant_rows[slot], status=d.plant_status[slot], age=d.plant_age[slot];
    if(status===0){labels.push('SLOT '+(slot+1)+' EMPTY');continue;}
    if(status===3 || !r[3]){labels.push('<span class="unavailable">SLOT '+(slot+1)+' TELEMETRY UNAVAILABLE</span>');continue;}
    const lane=d.plant_lane[slot];
    if(lane!==1 && lane!==2 && lane!==3){labels.push('<span class="unavailable">SLOT '+(slot+1)+' LANE UNAVAILABLE</span>');continue;}
    const identity='l'+lane+'-'+(r[0]+r[1]*30000)+'-'+r[2]+'-'+r[3]+'-'+r[4];
    const belt=r[5], pos=r[6]/10, kind=r[7], actual=r[8];
    const beltId=belt===1?'ib0':belt===5?'ib1':belt===6?'ib2':('ob'+(belt-2));
    const pe=d.photoeye_mode ? d.photoeye_rows?.[slot] : null;
    const peValid=!d.photoeye_mode || (pe && pe[0]===r[0] && pe[1]===r[1] &&
      pe[2]===r[2] && pe[3]===r[3] && pe[4]===r[4]);
    const z=d.accumulation_mode ? d.zone_rows?.[slot] : null;
    const zValid=!d.accumulation_mode || (z && z[5]===1 && z[4]!==0);
    const held=zValid && d.accumulation_mode && [2,3,4,5].includes(z[1]);
    const stale=status!==1 || d.plant_fault!==0 || (d.photoeye_mode && !peValid) ||
      (d.accumulation_mode && (!zValid || d.zone_age>15));
    const state=stale?'STALE':'LIVE';
    labels.push('<span class="'+(stale?'stale':held?'held':'')+'">SLOT '+(slot+1)+' '+identity+
      ' '+beltId.toUpperCase()+' '+pos.toFixed(1)+' '+(SENSOR_NAME[kind]||'UNKNOWN')+
      (actual?' '+actual:'')+' '+state+' ('+age+')'+
      (d.accumulation_mode ? ' ZONE '+(zValid?ZONE_NAME[z[0]]:'UNAVAILABLE')+
       ' MOTION '+(zValid?MOTION_NAME[z[1]]:'UNAVAILABLE')+
       ' HOLD '+(zValid?HOLD_NAME[z[2]]:'UNAVAILABLE')+
       ' DWELL '+(zValid?(z[3]/10).toFixed(1)+'s':'?') : '')+
      (d.photoeye_mode ? ' PE RAW '+(peValid?pe[5]:'?')+
       ' FILTERED '+(peValid?pe[6]:'?')+
       ' QUALITY '+(peValid?pe[7]:'UNAVAILABLE') : '')+'</span>');
    if(!['ib0','ib1','ib2','ob0','ob1','ob2'].includes(beltId)) continue;
    now[identity]=true;
    let el=plantSeen[identity];
    if(!el){
      el=document.createElement('div'); el.className='pkg plant g'+lane;
      el.textContent=r[4]+'/'+r[3];
      el.dataset.packageId=identity;
      plantSeen[identity]=el;
    }
    const track=document.getElementById(beltId);
    if(el.parentElement!==track) track.appendChild(el);
    el.classList.toggle('stale',stale);
    el.classList.toggle('held',!!held && !stale);
    el.style.transition=stale?'none':'transform .09s linear,opacity .18s linear';
    el.style.transform='translateX('+(Math.max(0,Math.min(19,pos))*CW+4)+'px)';
    el.dataset.belt=beltId; el.dataset.position=String(pos);
    el.dataset.event=String(kind); el.dataset.status=stale?'stale':'live';
    if(d.accumulation_mode){
      el.dataset.zone=zValid?String(z[0]):'unavailable';
      el.dataset.motion=zValid?String(z[1]):'unavailable';
      el.dataset.hold=zValid?String(z[2]):'unavailable';
      el.dataset.dwell=zValid?String(z[3]):'unavailable';
    }
    if(d.photoeye_mode){
      el.dataset.photoeyeRaw=peValid?String(pe[5]):'unavailable';
      el.dataset.photoeyeConditioned=peValid?String(pe[6]):'unavailable';
      el.dataset.photoeyeQuality=peValid?String(pe[7]):'unavailable';
    }
  }
  for(const key in plantSeen){if(!now[key]){plantSeen[key].remove();delete plantSeen[key];}}
  line.innerHTML=labels.join(' · ');
}

function spark(id, arr){
  const s=document.getElementById(id); if(!s) return;
  if(arr.length<2){ s.innerHTML=''; return; }
  const w=96,h=16,mx=Math.max(...arr,1);
  const pts=arr.map((v,i)=>(i*(w/(arr.length-1)))+','+(h-(v/mx)*(h-2)-1)).join(' ');
  s.innerHTML='<polyline points="'+pts+'" fill="none" stroke="#68757e" stroke-width="1"/>';
}
function stat(t,on,al){return '<span class="st'+(al?' al':(on?' on':''))+'">'+t+'</span>';}

const CTLS=[['run','MASTER'],['auto','AUTO'],
            ['lane_run0','IND 1'],['lane_run1','IND 2'],['lane_run2','IND 3'],
            ['ob_run0','OUT 1'],['ob_run1','OUT 2'],['ob_run2','OUT 3']];
let ctlBuilt=false;
function buildCtl(){
  let h='<span class="lbl">control</span>';
  CTLS.forEach(([k,t])=>{ h+='<button class="btn" data-k="'+k+'" id="b_'+k+'">'+t+'</button>'; });
  const el=document.getElementById('ctl');
  el.innerHTML=h;
  el.querySelectorAll('.btn').forEach(b=>{
    b.onclick=async()=>{
      const k=b.dataset.k, next=b.classList.contains('on')?0:1;
      b.disabled=true;
      try{ await fetch('/cmd/'+k+'/'+next,{method:'POST'}); }
      catch(e){}
      setTimeout(()=>{b.disabled=false;},400);
    };
  });
  ctlBuilt=true;
}
function paintCtl(d){
  const vals={run:d.run, auto:d.auto,
              lane_run0:d.lane_run[0], lane_run1:d.lane_run[1], lane_run2:d.lane_run[2],
              ob_run0:d.ob_run[0], ob_run1:d.ob_run[1], ob_run2:d.ob_run[2]};
  CTLS.forEach(([k])=>{
    const b=document.getElementById('b_'+k); if(!b||b.disabled) return;
    b.className='btn '+(vals[k]?'on':'off');
  });
}

function build(){
  let h='';
  for(let i=0;i<3;i++){
    const m=[{c:TUN,k:'tun',t:'SCAN'}];
    DIV.forEach((c,j)=>m.push({c:c,k:'div',t:'D'+(j+1)}));
    h+='<div class="lane"><div class="ln">induct '+(i+1)+'</div>'+track('ib'+i,m)+'</div>';
  }
  document.getElementById('ibelts').innerHTML=h;
  h='';
  for(let i=0;i<3;i++){
    const m=[];
    ENT.forEach((c,j)=>m.push({c:c,k:'div',t:'L'+(j+1)}));
    DOOR.forEach((c,j)=>m.push({c:c,k:'tun',t:'DR'+(j+1)}));
    h+='<div class="lane"><div class="ln">outbnd '+(i+1)+'</div>'+track('ob'+i,m)+'</div>';
  }
  document.getElementById('obelts').innerHTML=h;
}

let lastBarMarkup="";
async function tick(){
  let d;
  try { d = await (await fetch('/api')).json(); }
  catch(e){ document.getElementById('conn').textContent='HMI polling error';
    freezePlant('PLANT TELEMETRY UNAVAILABLE — HMI POLLING ERROR'); return; }

  const c=document.getElementById('conn');
  c.textContent = d.connected ? 'connected to UA server 10.10.2.10:4840' : 'UA SERVER UNREACHABLE';
  c.className = d.connected ? 'sub' : 'sub bad';

  // The control row already shows run state for every belt; repeating it as
  // status pills said the same thing twice. This strip is alarms only, and
  // stays empty when nothing is wrong.
  let p='';
  if(d.alarms.jam) p+=stat('JAM',0,1);
  if(d.alarms.coll) p+=stat('COLLISION',0,1);
  if(d.alarms.noread) p+=stat('NO-READ',0,1);
  if(d.alarms.nohome) p+=stat('NO-HOME',0,1);
  if(d.plant_mode && d.plant_fault) p+=stat('PLANT FAULT '+d.plant_fault,0,1);
  if(d.accumulation_mode && d.zone_fault) p+=stat('ZONE VALIDATION FAULT '+d.zone_fault,0,1);
  if(d.photoeye_mode && d.photoeye_fault_mask)
    p+=stat('PHOTOEYE LANE '+d.photoeye_fault_lane+' SENSOR '+d.photoeye_fault_sensor+
      ' FAULT (SLOTS '+d.photoeye_fault_mask+')',0,1);
  if(d.plant_failed_count) p+=stat('LANE '+(d.plant_failed_lane||'?')+' FAILED CONFIRMATION',0,1);
  if(d.scanner_state===1 || d.scanner_state===2) p+=stat('SCANNER RESET WAIT',1,0);
  if(d.scanner_state===3 || d.scanner_state===4){
    const failed=[1,2,3].filter(i=>d.scanner_fault_mask & (1<<(i-1))).join(', ');
    p+=stat('SCANNER TUNNEL '+failed+' RESET FAULT'+(d.scanner_state===4?' — ACKNOWLEDGED':''),0,1);
    p+='<button class="btn" data-scanner="'+(d.scanner_state===3?'scanner_fault_ack':'scanner_retry')+'">'
      +(d.scanner_state===3?'ACKNOWLEDGE':'RETRY RESET')+'</button>';
  }
  if(d.xle_liveness===1 || d.xle_liveness===2){
    p+=stat('XLE HEARTBEAT LOST'+(d.xle_liveness===2?' — ACKNOWLEDGED':''),0,1);
    p+='<button class="btn" data-scanner="'+(d.xle_liveness===1?'xle_fault_ack':'xle_retry')+'">'
      +(d.xle_liveness===1?'ACKNOWLEDGE XLE':'RETRY XLE')+'</button>';
  } else if(d.xle_liveness===3) p+=stat('XLE RECOVERY VERIFYING',1,0);
  const bar=document.getElementById('bar');
  if(p!==lastBarMarkup){ bar.innerHTML=p; lastBarMarkup=p; }
  bar.onclick=async e=>{
    const key=e.target.dataset.scanner;
    if(!key) return;
    e.target.disabled=true;
    try { await fetch('/cmd/'+key+'/1',{method:'POST'}); } catch(err){}
  };
  bar.style.display = p ? 'flex' : 'none';
  if(!ctlBuilt) buildCtl();
  paintCtl(d);

  const loaded=d.trailer.reduce((a,b)=>a+b,0);
  document.getElementById('ind').textContent=d.inducted;
  document.getElementById('loaded').textContent=loaded;
  document.getElementById('irate').textContent=d.ind_rate;
  document.getElementById('lrate').textContent=d.load_rate;
  [['ms',d.missort],['coll',d.coll],['jam',d.jam]].forEach(([id,v])=>{
    const e=document.getElementById(id); e.textContent=v; e.className='kv'+(v?' bad':'');
  });
  document.getElementById('nr').textContent=d.noread;
  document.getElementById('rc').textContent=d.recirc;

  hist1.push(d.ind_rate); if(hist1.length>40) hist1.shift(); spark('sp1',hist1);
  hist2.push(d.load_rate); if(hist2.length>40) hist2.shift(); spark('sp2',hist2);

  if(d.plant_mode){
    if(!currentPlantMode){
      clearLegacy('ib0'); clearLegacy('ib1'); clearLegacy('ib2');
      for(let i=0;i<3;i++) clearLegacy('ob'+i);
    }
    currentPlantMode=true;
    paintPlant(d);
  } else {
    if(currentPlantMode){clearPlant();document.getElementById('plantstate').style.display='none';}
    currentPlantMode=false;
    d.ib.forEach((cells,i)=>paint('ib'+i,cells,d.drives[BELT_DRIVE['ib'+i]].rpm));
    d.ob.forEach((cells,i)=>paint('ob'+i,cells,d.drives[BELT_DRIVE['ob'+i]].rpm));
  }

  let t='';
  for(let i=0;i<9;i++){
    const b=Math.floor(i/3)+1, dr=(i%3)+1;
    t+='<div class="door'+(d.bad[i]?' err':'')+'"><div class="dn">TRAILER '+b+'-'+dr+'</div>'
      +'<div class="dv">'+d.trailer[i]+'</div>'
      +'<div class="dbad'+(d.bad[i]?' on':'')+'">'+d.bad[i]+' wrong</div></div>';
  }
  document.getElementById('doors').innerHTML=t;

  document.getElementById('drives').innerHTML=d.drives.map(v=>{
    const pct=Math.min(100,(v.rpm/RATED)*100), rp=Math.min(100,(v.ref/RATED)*100);
    const cls=v.fault?'flt':(v.rpm>0?'run':'');
    return '<div class="f"><div class="fn"><span>'+v.name+'</span>'
      +'<span class="fs '+cls+'"></span></div>'
      +'<div class="fv">'+v.rpm+' <small>rpm</small></div>'
      +'<div class="fbar"><i style="width:'+pct+'%"></i>'
      +'<u style="left:'+rp+'%"></u></div>'
      +'<div class="fmeta"><span>ref '+v.ref+'</span><span>'+v.hz.toFixed(1)+' Hz</span>'
      +'<span'+(v.amps>12?' class="flt"':'')+'>'+v.amps.toFixed(1)+' A</span>'
      +'<span>'+v.load+' pkg</span>'
      +'<span'+(v.fault?' class="flt"':'')+'>'+(v.fault?'F'+v.fault:'—')+'</span></div>'
      +'<div class="therm"><i style="width:'+Math.min(100,v.thermal)+'%" class="'
      +(v.thermal>=100?'trip':(v.thermal>60?'warn':''))+'"></i></div></div>';
  }).join('');

  document.getElementById('sps').innerHTML=[0,1,2].map(i=>
    '<tr><td>induct '+(i+1)+'</td><td>'+d.speed_sp[i]+'</td><td>'
    +d.rate_sp[i]+'</td><td>'+(d.scan[i]||'—')+'</td></tr>').join('')
    +[0,1,2].map(i=>'<tr><td>outbnd '+(i+1)+'</td><td>'+d.ob_speed[i]
    +'</td><td>—</td><td>—</td></tr>').join('')
    +'<tr><td>min gap</td><td>'+d.min_gap+'</td><td>—</td><td>—</td></tr>'
    +'<tr><td>no-read rate</td><td>'+d.noread_sp+' ‰</td><td>—</td><td>—</td></tr>';
}
build(); setInterval(tick,100); tick();
</script></body></html>"""


@app.route("/")
def index():
    return render_template_string(PAGE)


def ua_thread():
    asyncio.run(ua_loop())


def serve():
    """Flask exits the process if the port is busy, which on a cold boot can
    mean a leftover instance from a previous start owns it. Waiting is better
    than dying: systemd cannot tell a real fault from a transient one, and an
    exit here is what turns a slow boot into a failed unit."""
    for attempt in range(60):
        try:
            app.run(host="0.0.0.0", port=8000)
            return
        except OSError as e:
            print(f"bind failed ({e}); retrying in 5s")
            time.sleep(5)
    raise SystemExit("port 8000 never became available")


if __name__ == "__main__":
    threading.Thread(target=ua_thread, daemon=True).start()
    serve()
