"""Summarize bounded classic-PCAP evidence without disclosing payloads."""

import argparse
from collections import Counter
import ipaddress
import json
from pathlib import Path
import struct


def summarize(path):
    data = Path(path).read_bytes()
    if len(data) < 24:
        raise ValueError("PCAP header missing")
    magic = data[:4]
    endian = {b"\xd4\xc3\xb2\xa1": "<", b"\xa1\xb2\xc3\xd4": ">"}.get(magic)
    if endian is None:
        raise ValueError("classic microsecond PCAP required")
    if struct.unpack_from(endian + "I", data, 20)[0] != 1:
        raise ValueError("Ethernet PCAP required")
    offset = 24
    counts = Counter()
    endpoints = Counter()
    registers = Counter()
    first = last = None
    while offset + 16 <= len(data):
        seconds, micros, size, _ = struct.unpack_from(endian + "IIII", data, offset)
        offset += 16
        if offset + size > len(data):
            raise ValueError("truncated PCAP packet")
        frame = data[offset:offset + size]
        offset += size
        stamp = seconds + micros / 1_000_000
        first = stamp if first is None else min(first, stamp)
        last = stamp if last is None else max(last, stamp)
        counts["packets"] += 1
        if len(frame) < 34 or frame[12:14] != b"\x08\x00":
            continue
        ip = frame[14:]
        ihl = (ip[0] & 15) * 4
        if len(ip) < ihl + 20 or ip[9] != 6:
            continue
        source = str(ipaddress.IPv4Address(ip[12:16]))
        target = str(ipaddress.IPv4Address(ip[16:20]))
        tcp = ip[ihl:]
        sport, dport = struct.unpack_from("!HH", tcp)
        header = ((tcp[12] >> 4) & 15) * 4
        payload = tcp[header:]
        endpoints[f"{source}:{sport}->{target}:{dport}"] += 1
        if dport == 8089 or sport == 8089:
            counts["network_asx_packets"] += 1
        if dport == 502 and len(payload) >= 10 and payload[2:4] == b"\x00\x00":
            function = payload[7]
            register = int.from_bytes(payload[8:10], "big")
            counts[f"modbus_request_fc{function}"] += 1
            if function in (5, 6, 15, 16):
                registers[f"{source}:{function}:{register}"] += 1
    if offset != len(data):
        raise ValueError("trailing truncated PCAP record")
    return {"bytes": len(data), "first_unix": first, "last_unix": last,
            "duration_seconds": round(last - first, 6) if first is not None else 0,
            "counts": dict(sorted(counts.items())),
            "endpoints": dict(sorted(endpoints.items())),
            "write_registers": dict(sorted(registers.items()))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pcap")
    args = parser.parse_args()
    print(json.dumps(summarize(args.pcap), sort_keys=True))
