"""Rendered Firefox check of PLC-fed plant telemetry through the SCADA HMI.

Run alongside serial_hmi_proxy.py and geckodriver --port 4445. Start the
matching live_plant.py case on SCADA with --start-file /tmp/plant-test-start.
"""
import argparse
import base64
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

WD = "http://127.0.0.1:4445"
HMI = "http://127.0.0.1:18000"


def request(url, method="GET", payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    with urlopen(Request(url, data=body, headers=headers, method=method), timeout=30) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def wd(session, suffix, method="GET", payload=None):
    return request(f"{WD}/session/{session}{suffix}", method, payload)["value"]


def snapshot(session):
    script = """
    return {
      state: document.querySelector('#plantstate').innerText,
      nodes: [...document.querySelectorAll('.pkg.plant')].map(e => ({
        id:e.dataset.packageId, belt:e.dataset.belt,
        position:Number(e.dataset.position), event:Number(e.dataset.event),
        status:e.dataset.status, transform:e.style.transform
      })),
      body:document.body.innerText
    };"""
    return wd(session, "/execute/sync", "POST", {"script": script, "args": []})


def capture(session, name):
    path = Path(__file__).resolve().parent / "artifacts" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(base64.b64decode(wd(session, "/screenshot")))
    print("screenshot", path, flush=True)


def main(case):
    response = request(WD + "/session", "POST", {
        "capabilities": {"alwaysMatch": {
            "browserName": "firefox", "moz:firefoxOptions": {"args": ["-headless"]}}}})
    session = response["value"]["sessionId"]
    observations = {}
    saved = set()
    try:
        wd(session, "/url", "POST", {"url": HMI + "/"})
        time.sleep(2)
        if case == "stale":
            script = """
            for(let i=1;i<1000;i++) clearInterval(i);
            const row=[1,0,8,41,2,1,83,2,0,3];
            const d={connected:true,plant_rows:[row,[0,0,0,0,0,0,0,0,0,0]],
                     plant_status:[1,0],plant_age:[0,0],plant_fault:0};
            window.eval('currentPlantMode=true'); paintPlant(d);
            const node=document.querySelector('.pkg.plant');
            const before=node.style.transform;
            d.plant_status[0]=2; d.plant_age[0]=16; paintPlant(d);
            const stale={text:document.querySelector('#plantstate').innerText,
                         transform:node.style.transform, transition:node.style.transition,
                         status:node.dataset.status};
            freezePlant('PLANT TELEMETRY UNAVAILABLE — UA DISCONNECTED');
            const unavailable={text:document.querySelector('#plantstate').innerText,
                               transform:node.style.transform, transition:node.style.transition,
                               status:node.dataset.status};
            return {before,stale,unavailable};"""
            result = wd(session, "/execute/sync", "POST", {"script": script, "args": []})
            assert result["before"] == result["stale"]["transform"] == result["unavailable"]["transform"]
            assert result["stale"]["status"] == "stale" and "STALE" in result["stale"]["text"]
            assert result["unavailable"]["status"] == "unavailable"
            assert "UNAVAILABLE" in result["unavailable"]["text"]
            assert "none" in result["stale"]["transition"] and "none" in result["unavailable"]["transition"]
            capture(session, "plant-stale-unavailable.png")
            print(json.dumps(result, sort_keys=True), flush=True)
            return
        request(HMI + "/test/plant_run/" + case, "POST")
        request(HMI + "/test/plant_start", "POST")
        deadline = time.monotonic() + 175
        while time.monotonic() < deadline:
            state = snapshot(session)
            nodes = state["nodes"]
            for node in nodes:
                history = observations.setdefault(node["id"], [])
                entry = (node["belt"], node["position"], node["event"], node["status"])
                if not history or history[-1] != entry:
                    history.append(entry)
            if case == "two":
                if len(nodes) == 2 and "two_induct" not in saved:
                    capture(session, "plant-two-induct.png"); saved.add("two_induct")
                if len(nodes) == 2 and all(n["belt"].startswith("ob") for n in nodes) and "two_outbound" not in saved:
                    capture(session, "plant-two-outbound.png"); saved.add("two_outbound")
                if (len(observations) >= 2 and
                    all(any(item[2] == 4 for item in h) for h in observations.values())):
                    capture(session, "plant-two-trailer.png")
                    break
            else:
                if any(n["event"] == 6 for n in nodes):
                    capture(session, "plant-failed-confirmation.png")
                    break
            time.sleep(.35)
        expected = 2 if case == "two" else 1
        assert len(observations) == expected, observations
        for identity, history in observations.items():
            assert any(belt == "ib0" and position > 0 for belt, position, _, _ in history), (identity, history)
            assert any(belt.startswith("ob") and position > 0 for belt, position, _, _ in history), (identity, history)
            assert any(event == 2 for _, _, event, _ in history), (identity, history)
            assert any(event == 3 for _, _, event, _ in history), (identity, history)
            assert any(event == (6 if case == "failure" else 4) for _, _, event, _ in history), (identity, history)
        if case == "two":
            assert "two_induct" in saved and "two_outbound" in saved, saved
            assert len({next(b for b, _, _, _ in h if b.startswith("ob")) for h in observations.values()}) == 2
        else:
            assert "NO HOME" in state["body"].upper() or "FAILED" in state["body"].upper(), state["body"][-1000:]
        print(json.dumps({"case": case, "packages": observations,
                          "final_state": state["state"], "screenshots": sorted(saved)},
                         sort_keys=True), flush=True)
    finally:
        wd(session, "", "DELETE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=("two", "failure", "stale"))
    main(parser.parse_args().case)
