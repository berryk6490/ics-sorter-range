"""Rendered HMI proof using the existing serial-only localhost proxy."""
import argparse
import base64
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

WD = "http://127.0.0.1:4445"
HMI = "http://127.0.0.1:18000"


def request(url, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    with urlopen(Request(url, data=data, method=method,
                         headers={"Content-Type": "application/json"} if data else {}),
                 timeout=20) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def wd(session, path, method="GET", payload=None):
    return request(f"{WD}/session/{session}{path}", method, payload)["value"]


def snapshot(session):
    return wd(session, "/execute/sync", "POST", {"script": """
      return {state:document.querySelector('#plantstate').innerText,
        nodes:[...document.querySelectorAll('.pkg.plant')].map(e=>({
          id:e.dataset.packageId, zone:e.dataset.zone, motion:e.dataset.motion,
          hold:e.dataset.hold, dwell:e.dataset.dwell, position:e.dataset.position,
          status:e.dataset.status, className:e.className,
          transform:e.style.transform, transition:e.style.transition}))};
      """, "args": []})


def capture(session, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(wd(session, "/screenshot")))
    return str(path)


def run(case, output_dir):
    created = request(WD + "/session", "POST", {"capabilities": {"alwaysMatch": {
        "browserName": "firefox", "moz:firefoxOptions": {"args": ["-headless"]}}}})
    session = created["value"]["sessionId"]
    try:
        wd(session, "/url", "POST", {"url": HMI + "/"})
        if case == "stale":
            result = wd(session, "/execute/sync", "POST", {"script": """
              for(let i=1;i<1000;i++) clearInterval(i);
              const row=[1,0,8,41,2,1,83,2,0,3];
              const empty=[0,0,0,0,0,0,0,0,0,0];
              const d={connected:true,plant_rows:[row,empty,empty],
                plant_status:[1,0,0],plant_age:[0,0,0],plant_lane:[1,0,0],
                plant_fault:0,photoeye_mode:false,accumulation_mode:true,
                zone_rows:[[2,2,1,25,3,1],[0,0,0,0,0,0],[0,0,0,0,0,0]],
                zone_age:0};
              window.eval('currentPlantMode=true'); paintPlant(d);
              const node=document.querySelector('.pkg.plant');
              const before=node.style.transform;
              d.plant_status[0]=2; d.plant_age[0]=16;
              d.zone_rows[0][5]=2; d.zone_age=16;
              paintPlant(d);
              return {before,after:node.style.transform,
                className:node.className,status:node.dataset.status,
                transition:node.style.transition,
                text:document.querySelector('#plantstate').innerText};
              """, "args": []})
            assert result["before"] == result["after"]
            assert result["status"] == "stale" and "stale" in result["className"]
            assert "held" not in result["className"] and "none" in result["transition"]
            path = output_dir / "stale.png"
            capture(session, path)
            print(json.dumps({"case": case, "screenshot": str(path),
                              "observation": result}, sort_keys=True), flush=True)
            return result
        deadline = time.monotonic() + 75
        observation = None
        while time.monotonic() < deadline:
            observation = snapshot(session)
            nodes = observation["nodes"]
            if case == "lane_hold":
                held = [n for n in nodes if n["motion"] == "2" and
                        n["zone"] in ("1", "2", "3") and
                        int(n["dwell"] or 0) >= 10]
                if held and all("held" in n["className"] and n["status"] == "live"
                                for n in held):
                    break
            elif case == "merge_hold":
                held = [n for n in nodes if n["motion"] == "3" and
                        n["zone"] == "4" and n["status"] == "live"]
                if len(held) == 3 and len({n["id"].split("-")[0] for n in held}) == 3:
                    break
            elif case == "drive_stop":
                stopped = [n for n in nodes if n["motion"] == "4" and
                           n["status"] == "live" and n["id"]]
                if stopped:
                    break
            else:
                raise ValueError(case)
            time.sleep(.35)
        else:
            raise AssertionError(f"no rendered {case} observation: {observation}")
        path = output_dir / f"{case}.png"
        capture(session, path)
        print(json.dumps({"case": case, "screenshot": str(path),
                          "observation": observation}, sort_keys=True), flush=True)
        return observation
    finally:
        try:
            request(f"{WD}/session/{session}", "DELETE")
        except OSError:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=("lane_hold", "merge_hold", "drive_stop", "stale"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args.case, args.output_dir)
