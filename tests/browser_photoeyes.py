"""Rendered Firefox evidence of PLC-validated photoeye fields on the HMI."""
import base64
import json
from pathlib import Path
import time

from browser_plant import HMI, WD, request, wd


def main():
    response = request(WD + "/session", "POST", {
        "capabilities": {"alwaysMatch": {
            "browserName": "firefox", "moz:firefoxOptions": {"args": ["-headless"]}}}})
    session = response["value"]["sessionId"]
    try:
        wd(session, "/url", "POST", {"url": HMI + "/"})
        deadline = time.monotonic() + 75
        observed = []
        while time.monotonic() < deadline:
            data = wd(session, "/execute/sync", "POST", {"script": """
              return {body:document.body.innerText,
                state:document.querySelector('#plantstate').innerText,
                packages:[...document.querySelectorAll('.pkg.plant')].map(e=>({
                  id:e.dataset.packageId, raw:e.dataset.photoeyeRaw,
                  conditioned:e.dataset.photoeyeConditioned,
                  quality:e.dataset.photoeyeQuality}))};
            """, "args": []})
            if data["packages"] and data != (observed[-1] if observed else None):
                observed.append(data)
            if "PHOTOEYE LANE 1 SENSOR 2 FAULT" in data["body"]:
                assert any(p["id"].startswith("l1-") and
                           p["quality"] == "5" and
                           p["raw"] == "4" and p["conditioned"] == "4"
                           for p in data["packages"]), data
                screenshot = Path(__file__).resolve().parent / "artifacts" / "photoeye-missed-hmi.png"
                screenshot.parent.mkdir(exist_ok=True)
                screenshot.write_bytes(base64.b64decode(wd(session, "/screenshot")))
                print(json.dumps({"alarm": "PHOTOEYE LANE 1 SENSOR 2 FAULT",
                                  "state": data["state"],
                                  "packages": data["packages"],
                                  "observed_frames": len(observed),
                                  "screenshot": str(screenshot)}, sort_keys=True))
                return
            time.sleep(.25)
        raise AssertionError(f"photoeye alarm absent after {len(observed)} frames")
    finally:
        wd(session, "", "DELETE")


if __name__ == "__main__":
    main()
