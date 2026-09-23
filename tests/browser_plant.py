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
                     plant_status:[1,0],plant_age:[0,0],plant_lane:[1,0],plant_fault:0};
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
        if case in ("fault", "fault_three"):
            first = snapshot(session)
            expected = {"l1", "l2", "l3"} if case == "fault_three" else {"l1", "l2"}
            assert len(first["nodes"]) == len(expected), first
            assert {n["id"].split("-")[0] for n in first["nodes"]} == expected
            assert all(n["status"] == "stale" for n in first["nodes"]), first
            assert "PLANT FAULT 1" in first["body"].upper(), first["body"][-1000:]
            time.sleep(2)
            second = snapshot(session)
            assert [(n["id"], n["position"], n["transform"]) for n in first["nodes"]] == [
                (n["id"], n["position"], n["transform"]) for n in second["nodes"]]
            capture(session, "plant-three-service-stopped.png" if case == "fault_three"
                    else "plant-lanes-service-stopped.png")
            print(json.dumps({"first": first["nodes"], "second": second["nodes"],
                              "state": second["state"]}, sort_keys=True), flush=True)
            return
        if case == "three_shared_four":
            old_ids = {node["id"] for node in snapshot(session)["nodes"]}
            request(HMI + "/test/three_slot_run/shared_four", "POST")
            request(HMI + "/test/plant_start", "POST")
            deadline = time.monotonic() + 195
            started = False
            concurrent = False
            waited = False
            outbound = False
            fourth = False
            while time.monotonic() < deadline:
                state = snapshot(session)
                nodes = state["nodes"]
                if not started:
                    started = any(node["id"] not in old_ids for node in nodes)
                    if not started:
                        time.sleep(.35)
                        continue
                for node in nodes:
                    history = observations.setdefault(node["id"], [])
                    entry = (node["belt"], node["position"], node["event"], node["status"])
                    if not history or history[-1] != entry:
                        history.append(entry)
                first_three = {n["id"].split("-")[0] for n in nodes}
                if len(nodes) == 3 and first_three == {"l1", "l2", "l3"} and not concurrent:
                    capture(session, "plant-three-concurrent.png")
                    concurrent = True
                if len(nodes) == 3 and any(n["belt"].startswith("ib") and
                                           n["position"] == 14 for n in nodes) and not waited:
                    capture(session, "plant-three-shared-wait.png")
                    waited = True
                if sum(n["belt"] == "ob0" for n in nodes) >= 2 and not outbound:
                    capture(session, "plant-three-shared-outbound.png")
                    outbound = True
                if any(n["id"].split("-")[4] == "4" for n in nodes) and not fourth:
                    capture(session, "plant-three-fourth-token.png")
                    fourth = True
                if concurrent and waited and outbound and fourth and all(
                        any(item[2] == 4 for item in h) for h in observations.values()):
                    break
                time.sleep(.35)
            assert concurrent and waited and outbound and fourth, (concurrent, waited, outbound, fourth)
            print(json.dumps({"case": case, "packages": observations,
                              "screenshots": sorted(p.name for p in Path(__file__).resolve().parent.joinpath("artifacts").glob("plant-three-*.png"))},
                             sort_keys=True), flush=True)
            return
        if case.startswith("lane3_"):
            scenario = case.removeprefix("lane3_")
            expected_lanes = {"all": {"l1", "l2", "l3"},
                              "shared": {"l2", "l3"},
                              "failure": {"l3"}}[scenario]
            request(HMI + "/test/lane3_run/" + scenario, "POST")
            request(HMI + "/test/plant_start", "POST")
            deadline = time.monotonic() + 195
            while time.monotonic() < deadline:
                state = snapshot(session)
                nodes = state["nodes"]
                for node in nodes:
                    history = observations.setdefault(node["id"], [])
                    entry = (node["belt"], node["position"], node["event"], node["status"])
                    if not history or history[-1] != entry:
                        history.append(entry)
                lanes = {key.split("-")[0] for key in observations}
                if scenario == "all" and "l3" in lanes and "lane3_induct" not in saved:
                    capture(session, "plant-lane3-induct.png"); saved.add("lane3_induct")
                if scenario == "shared":
                    if (any(n["id"].startswith("l3-") and n["belt"] == "ib2"
                            and n["position"] == 14 for n in nodes) and
                            "merge_wait" not in saved):
                        capture(session, "plant-lane3-merge-wait.png"); saved.add("merge_wait")
                    if (len(nodes) == 2 and all(n["belt"] == "ob0" for n in nodes)
                            and "shared_outbound" not in saved):
                        capture(session, "plant-lane3-shared-outbound.png")
                        saved.add("shared_outbound")
                if scenario == "failure" and any(n["event"] == 6 for n in nodes):
                    assert "LANE 3 FAILED CONFIRMATION" in state["body"].upper()
                    capture(session, "plant-lane3-failed-confirmation.png")
                    saved.add("failure")
                    break
                if (lanes == expected_lanes and all(
                        any(item[2] == 4 for item in h) for h in observations.values())):
                    capture(session, "plant-lane3-trailers.png")
                    saved.add("trailers")
                    break
                time.sleep(.35)
            assert {key.split("-")[0] for key in observations} == expected_lanes, observations
            for identity, history in observations.items():
                lane = int(identity[1])
                induct = {1: "ib0", 2: "ib1", 3: "ib2"}[lane]
                assert any(belt == induct and pos > 0 for belt, pos, _, _ in history)
                assert any(belt.startswith("ob") for belt, _, _, _ in history)
                assert any(event == 2 for _, _, event, _ in history), (identity, history)
                assert any(event == 3 for _, _, event, _ in history), (identity, history)
                terminal = 6 if scenario == "failure" else 4
                assert any(event == terminal for _, _, event, _ in history), (identity, history)
            if scenario == "shared":
                assert "merge_wait" in saved and "shared_outbound" in saved, saved
            if scenario == "failure":
                assert "failure" in saved
            print(json.dumps({"case": case, "packages": observations,
                              "final_state": state["state"], "screenshots": sorted(saved)},
                             sort_keys=True), flush=True)
            return
        route = ("/test/lane2_run/" + ("failure" if case == "lane2_failure" else case)
                 if case in ("shared", "lane2_failure") else "/test/plant_run/" + case)
        request(HMI + route, "POST")
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
            if case in ("two", "shared", "lane2_failure"):
                if len(nodes) == 2 and "two_induct" not in saved:
                    capture(session, "plant-lanes-induct.png" if case != "two" else "plant-two-induct.png")
                    saved.add("two_induct")
                if case != "two" and any(n["id"].startswith("l2-") and n["belt"] == "ib1"
                                         and n["position"] == 14 for n in nodes) and "merge_wait" not in saved:
                    capture(session, "plant-lanes-merge-wait.png"); saved.add("merge_wait")
                if len(nodes) == 2 and all(n["belt"].startswith("ob") for n in nodes) and "two_outbound" not in saved:
                    capture(session, "plant-lanes-shared-outbound.png" if case != "two" else "plant-two-outbound.png")
                    saved.add("two_outbound")
                if case == "lane2_failure" and any(n["id"].startswith("l2-") and n["event"] == 6 for n in nodes):
                    capture(session, "plant-lanes-failed-confirmation.png")
                    break
                if (len(observations) >= 2 and
                    all(any(item[2] == 4 for item in h) for h in observations.values())):
                    capture(session, "plant-lanes-shared-trailers.png" if case != "two" else "plant-two-trailer.png")
                    break
            else:
                if any(n["event"] == 6 for n in nodes):
                    capture(session, "plant-failed-confirmation.png")
                    break
            time.sleep(.35)
        expected = 2 if case in ("two", "shared", "lane2_failure") else 1
        assert len(observations) == expected, observations
        for identity, history in observations.items():
            induct = "ib1" if identity.startswith("l2-") else "ib0"
            assert any(belt == induct and position > 0 for belt, position, _, _ in history), (identity, history)
            assert any(belt.startswith("ob") and position > 0 for belt, position, _, _ in history), (identity, history)
            assert any(event == 2 for _, _, event, _ in history), (identity, history)
            assert any(event == 3 for _, _, event, _ in history), (identity, history)
            terminal = 6 if case == "failure" or (case == "lane2_failure" and identity.startswith("l2-")) else 4
            assert any(event == terminal for _, _, event, _ in history), (identity, history)
        if case == "two":
            assert "two_induct" in saved and "two_outbound" in saved, saved
            assert len({next(b for b, _, _, _ in h if b.startswith("ob")) for h in observations.values()}) == 2
        elif case == "shared":
            assert "merge_wait" in saved and "two_outbound" in saved, saved
            assert all(any(belt == "ob0" for belt, _, _, _ in h) for h in observations.values())
        else:
            assert "NO HOME" in state["body"].upper() or "FAILED" in state["body"].upper(), state["body"][-1000:]
            if case == "lane2_failure":
                assert "LANE 2 FAILED CONFIRMATION" in state["body"].upper(), state["body"][-1000:]
        print(json.dumps({"case": case, "packages": observations,
                          "final_state": state["state"], "screenshots": sorted(saved)},
                         sort_keys=True), flush=True)
    finally:
        wd(session, "", "DELETE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=("two", "failure", "stale", "fault", "fault_three", "shared",
                                         "lane2_failure", "lane3_all", "lane3_shared",
                                         "lane3_failure", "three_shared_four"))
    main(parser.parse_args().case)
