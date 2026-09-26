"""Genuine rendered HMI checkpoint and three distinct chute operator actions.

The host uses the existing serial HMI proxy. This script has no PLC or plant
connection. It fails if the browser, API, or identity evidence disagrees.
"""
import argparse
import json
from pathlib import Path
import time

import browser_accumulation as browser


def page(session):
    return browser.wd(session, "/execute/sync", "POST", {"script": """
      return {alarm:document.querySelector('#bar').innerText,
        chute:document.querySelectorAll('#doors .door')[1].innerText,
        packages:[...document.querySelectorAll('.pkg.plant')].map(e=>({
          id:e.dataset.packageId,zone:e.dataset.zone,motion:e.dataset.motion,
          hold:e.dataset.hold,position:e.dataset.position,status:e.dataset.status}))};
      """, "args": []})


def wait_for(predicate, label, seconds=90):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        api = browser.request(browser.HMI + "/api")
        if api.get("connected") and api.get("chute", {}).get("poll_fresh"):
            view = page(wait_for.session)
            if predicate(api, view):
                ua = browser.request(browser.HMI + "/ua")
                if (ua["ChuteState"] != api["chute"]["state"] or
                    ua["ChuteOccupied"] != api["chute"]["occupied"] or
                    ua["ChuteQuality"] != api["chute"]["quality"] or
                    ua["Trailer2LoadedCount"] != api["trailer"][1] or
                    ua["MeasuredChuteTrailer"] != api["chute"]["measured_trailer"]):
                    last = {"api": api, "page": view, "ua": ua,
                            "error": "OPC UA/HMI disagreement"}
                    time.sleep(.2)
                    continue
                last = {"api": api, "page": view, "ua": ua}
                return last
            last = {"api": api, "page": view}
        time.sleep(.2)
    raise TimeoutError(f"{label}: {last}")


def click(session, action):
    ok = browser.wd(session, "/execute/sync", "POST", {"script": """
      const button=document.querySelector('button[data-chute="'+arguments[0]+'"]');
      if(!button || button.disabled) return false;
      button.click(); return true;
      """, "args": [action]})
    if not ok:
        raise AssertionError(f"missing live HMI {action} button")


def run(output_dir):
    output_dir.mkdir(parents=True, exist_ok=False)
    created = browser.request(browser.WD + "/session", "POST", {
        "capabilities": {"alwaysMatch": {"browserName": "firefox",
                          "moz:firefoxOptions": {"args": ["-headless"]}}}})
    session = created["value"]["sessionId"]
    wait_for.session = session
    phases = {}
    try:
        browser.wd(session, "/url", "POST", {"url": browser.HMI + "/"})

        def held(api, view, state, occupied):
            if (api["chute"]["measured_trailer"] != 2 or
                api["chute"]["quality"] != 1 or
                api["chute"]["state"] != state or
                api["chute"]["occupied"] != occupied or
                api["trailer"][1] != 3):
                return False
            packages = [p for p in view["packages"] if p["hold"] == "5" and
                        p["status"] == "live" and p["id"]]
            return len(packages) == 1

        phases["full"] = wait_for(lambda a, v: held(a, v, 1, 3) and
                                  "TRAILER 2 CHUTE FULL" in v["alarm"], "full")
        package = next(p for p in phases["full"]["page"]["packages"]
                       if p["hold"] == "5")
        identity, held_position = package["id"], float(package["position"])
        phases["full"]["screenshot"] = browser.capture(session, output_dir / "full.png")
        (output_dir / "full.json").write_text(json.dumps(phases["full"], indent=2) + "\n")

        click(session, "acknowledge")
        phases["acknowledged"] = wait_for(lambda a, v: held(a, v, 2, 3) and
                                          any(p["id"] == identity and
                                              float(p["position"]) == held_position
                                              for p in v["packages"]), "acknowledge")
        phases["acknowledged"]["screenshot"] = browser.capture(
            session, output_dir / "acknowledged.png")
        (output_dir / "acknowledged.json").write_text(
            json.dumps(phases["acknowledged"], indent=2) + "\n")

        click(session, "empty")
        phases["empty"] = wait_for(lambda a, v: held(a, v, 3, 0) and
                                    any(p["id"] == identity and
                                        float(p["position"]) == held_position
                                        for p in v["packages"]), "empty and fresh clear")
        phases["empty"]["screenshot"] = browser.capture(session, output_dir / "empty.png")
        (output_dir / "empty.json").write_text(json.dumps(phases["empty"], indent=2) + "\n")

        click(session, "resume")
        phases["resumed"] = wait_for(lambda a, v: a["chute"]["state"] == 0 and
                                     a["chute"]["permissive"] == 1 and
                                     a["trailer"][1] == 3 and
                                     a["zone_fault"] == 0 and
                                     any(p["id"] == identity and p["status"] == "live" and
                                         p["hold"] != "5" and
                                         float(p["position"]) > held_position
                                         for p in v["packages"]),
                                     "resume movement")
        phases["resumed"]["screenshot"] = browser.capture(session, output_dir / "resumed.png")
        (output_dir / "resumed.json").write_text(json.dumps(phases["resumed"], indent=2) + "\n")
        phases["confirmed"] = wait_for(
            lambda a, _v: a["chute"]["state"] == 0 and
            a["chute"]["occupied"] == 1 and a["chute"]["quality"] == 1 and
            a["trailer"][1] == 4 and a["plant_fault"] == 0 and
            a["zone_fault"] == 0,
            "fourth matching physical confirmation", seconds=120)
        phases["confirmed"]["screenshot"] = browser.capture(
            session, output_dir / "confirmed.png")
        (output_dir / "confirmed.json").write_text(
            json.dumps(phases["confirmed"], indent=2) + "\n")
        print(json.dumps({"status": "PASS", "package_id": identity,
                          "phases": list(phases)}, sort_keys=True), flush=True)
        return phases
    finally:
        try:
            browser.request(browser.WD + "/session/" + session, "DELETE")
        except OSError:
            pass


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--output-dir", type=Path, required=True)
    cli.add_argument("--webdriver-url", default=browser.WD)
    cli.add_argument("--hmi-url", default=browser.HMI)
    args = cli.parse_args()
    browser.WD, browser.HMI = args.webdriver_url, args.hmi_url
    run(args.output_dir)
