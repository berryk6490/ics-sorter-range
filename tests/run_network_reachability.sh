#!/usr/bin/env bash
# Run on the libvirt workstation after the four guest serial consoles are logged in.
set -euo pipefail
cd "$(dirname "$0")/.."
for guest in plc drives scada analyst; do
  python3 tests/serial_copy.py "$guest" \
    tests/network_reachability.py:/home/kevin/network_reachability.py \
    tests/network_flows.json:/home/kevin/network_flows.json
  python3 tests/serial_command.py "$guest" \
    "python3 /home/kevin/network_reachability.py $guest --matrix /home/kevin/network_flows.json"
done
