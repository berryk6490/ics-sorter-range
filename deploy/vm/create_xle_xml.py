"""Derive the XLe domain from the local shut-off base VM definition.

The base disk is cloned independently before this script is run.  The output
intentionally omits UUID and NIC MAC so libvirt assigns fresh identities.
"""

from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def main(output):
    source = subprocess.check_output(
        ["virsh", "-c", "qemu:///system", "dumpxml", "base"], text=True
    )
    root = ET.fromstring(source)
    root.find("name").text = "xle"
    uuid = root.find("uuid")
    if uuid is not None:
        root.remove(uuid)
    root.find("vcpu").text = "1"
    disk = root.find("./devices/disk[@device='disk']/source")
    disk.set("file", "/var/lib/libvirt/images/xle.qcow2")
    interfaces = root.findall("./devices/interface")
    if len(interfaces) != 1:
        raise ValueError("base must have exactly one interface")
    interface = interfaces[0]
    interface.find("source").set("network", "ics-l2")
    mac = interface.find("mac")
    if mac is not None:
        interface.remove(mac)
    # A headless service VM needs its serial console, not SPICE or sound.
    devices = root.find("devices")
    for tag in ("graphics", "video", "sound", "audio", "redirdev"):
        for item in devices.findall(tag):
            devices.remove(item)
    for channel in devices.findall("channel"):
        if channel.get("type") == "spicevmc":
            devices.remove(channel)
    ET.indent(root, space="  ")
    Path(output).write_text(ET.tostring(root, encoding="unicode") + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: create_xle_xml.py OUTPUT")
    main(sys.argv[1])
