"""Bounded offline classic-PCAP summary; no TCP reassembly or process inference."""
import argparse
from collections import Counter, defaultdict
import hashlib
import ipaddress
import json
from pathlib import Path
import struct

MAX_BYTES = 64 * 1024 * 1024
MAX_PACKETS = 200_000
SNAPLEN = 262144  # tcpdump -s 0


def summarize(path, *, max_bytes=MAX_BYTES, max_packets=MAX_PACKETS,
              allow_incomplete=False):
    """Count visible packets and complete Modbus ADUs, retaining explicit gaps.

    Rates use the packet timestamp span, not an assumed collection duration.
    Sequence/payload repeats are ambiguous retransmission candidates. TCP
    segments are not reassembled; missing replies do not prove a timeout.
    """
    if max_bytes < 24 or max_packets < 1:
        raise ValueError('positive packet and file bounds required')
    target = Path(path)
    if target.stat().st_size > max_bytes:
        raise ValueError('PCAP byte bound exceeded')
    counts, endpoints, registers = Counter(), Counter(), Counter()
    initiators, exceptions, ranges = Counter(), Counter(), Counter()
    polling = defaultdict(list)
    seen_payloads, syns, established = set(), set(), set()
    gaps = Counter()
    first = last = None
    complete = True
    digest = hashlib.sha256()
    bytes_read = 24

    def gap(reason):
        gaps[reason] += 1

    with target.open('rb') as stream:
        header = stream.read(24)
        digest.update(header)
        if len(header) != 24:
            raise ValueError('PCAP header missing')
        formats = {b'\xd4\xc3\xb2\xa1': ('<', 1_000_000),
                   b'\xa1\xb2\xc3\xd4': ('>', 1_000_000),
                   b'\x4d\x3c\xb2\xa1': ('<', 1_000_000_000),
                   b'\xa1\xb2\x3c\x4d': ('>', 1_000_000_000)}
        if header[:4] not in formats:
            raise ValueError('classic PCAP required (pcapng is unavailable)')
        endian, resolution = formats[header[:4]]
        major, minor, _, _, snaplen, link = struct.unpack(endian + 'HHIIII', header[4:])
        if (major, minor) != (2, 4) or not 1 <= snaplen <= SNAPLEN:
            raise ValueError('unsupported PCAP version or snaplen')
        if link != 1:
            raise ValueError('Ethernet PCAP required (use a named interface, not any)')
        while True:
            record = stream.read(16)
            digest.update(record)
            if not record:
                break
            if len(record) != 16:
                gap('truncated_record_header'); complete = False; break
            bytes_read += 16
            sec, fraction, size, original = struct.unpack(endian + 'IIII', record)
            if size > snaplen or size > original or fraction >= resolution:
                raise ValueError('invalid PCAP packet length or timestamp')
            if bytes_read + size > max_bytes:
                raise ValueError('PCAP byte bound exceeded during read')
            if counts['packets'] >= max_packets:
                raise ValueError('PCAP packet bound exceeded')
            frame = stream.read(size)
            bytes_read += len(frame)
            digest.update(frame)
            if len(frame) != size:
                gap('truncated_packet'); complete = False; break
            counts['packets'] += 1
            if size < original:
                gap('snaplen_truncation'); complete = False
            stamp = sec + fraction / resolution
            first = stamp if first is None else min(first, stamp)
            last = stamp if last is None else max(last, stamp)
            if len(frame) < 14:
                gap('short_ethernet'); continue
            kind, offset = frame[12:14], 14
            while kind in (b'\x81\x00', b'\x88\xa8') and len(frame) >= offset + 4:
                kind, offset = frame[offset + 2:offset + 4], offset + 4
            if kind != b'\x08\x00':
                counts['non_ipv4_packets'] += 1
                continue
            ip = frame[offset:]
            if len(ip) < 20 or ip[0] >> 4 != 4:
                gap('short_ipv4'); continue
            ihl, total = (ip[0] & 15) * 4, int.from_bytes(ip[2:4], 'big')
            if ihl < 20 or total < ihl or len(ip) < total:
                gap('invalid_or_incomplete_ipv4'); continue
            if int.from_bytes(ip[6:8], 'big') & 0x3fff:
                gap('ip_fragment_no_reassembly'); continue
            if ip[9] != 6:
                counts['non_tcp_ipv4_packets'] += 1
                continue
            tcp = ip[ihl:total]
            if len(tcp) < 20:
                gap('short_tcp'); continue
            hlen = (tcp[12] >> 4) * 4
            if hlen < 20 or hlen > len(tcp):
                gap('invalid_tcp_header'); continue
            source, target_ip = (str(ipaddress.IPv4Address(ip[x:x+4])) for x in (12, 16))
            sport, dport = struct.unpack_from('!HH', tcp)
            flow = (source, sport, target_ip, dport)
            reverse = (target_ip, dport, source, sport)
            label = f'{source}:{sport}->{target_ip}:{dport}'
            endpoints[label] += 1
            flags = tcp[13]
            if flags & 2 and not flags & 16:
                initiators[label] += 1
                if flow not in syns:
                    established.discard(flow); established.discard(reverse)
                syns.add(flow)
            if flags & 2 and flags & 16 and reverse in syns:
                established.update((flow, reverse))
            if flags & 4:
                counts['tcp_rst_packets'] += 1
                if reverse in syns and reverse not in established:
                    counts['tcp_reset_after_syn_connection_failure'] += 1
                syns.discard(flow); syns.discard(reverse)
                established.discard(flow); established.discard(reverse)
            if flags & 1:
                counts['tcp_fin_packets'] += 1
            if 8089 in (sport, dport):
                counts['network_asx_packets'] += 1
            payload = tcp[hlen:]
            if not payload:
                continue
            sequence = int.from_bytes(tcp[4:8], 'big')
            signature = (flow, sequence, hashlib.sha256(payload).digest())
            if signature in seen_payloads:
                counts['tcp_duplicate_sequence_payload_candidates'] += 1
                continue
            seen_payloads.add(signature)
            if 502 not in (sport, dport):
                continue
            cursor = 0
            while cursor < len(payload):
                adu = payload[cursor:]
                if len(adu) < 8:
                    gap('incomplete_mbap_or_tcp_segment'); break
                _, protocol, length = struct.unpack_from('!HHH', adu)
                if protocol or not 2 <= length <= 254:
                    gap('unrecognized_modbus_or_stream_continuation'); break
                if len(adu) < 6 + length:
                    gap('incomplete_modbus_adu_no_reassembly'); break
                adu = adu[:6 + length]
                cursor += len(adu)
                unit, function = adu[6:8]
                if sport == 502 and dport != 502:
                    if function & 128:
                        if len(adu) == 9:
                            exceptions[f'{label}:unit{unit}:fc{function & 127}:exception{adu[8]}'] += 1
                            counts['modbus_exception_responses'] += 1
                        else:
                            gap('invalid_modbus_exception_length')
                    else:
                        counts[f'modbus_response_fc{function}'] += 1
                    continue
                if dport != 502 or sport == 502 or function & 128:
                    gap('ambiguous_modbus_direction'); continue
                counts[f'modbus_request_fc{function}'] += 1
                if function not in (1, 2, 3, 4, 5, 6, 15, 16):
                    gap('function_address_semantics_unavailable'); continue
                if len(adu) < 12:
                    gap('short_modbus_request'); continue
                address, value = struct.unpack_from('!HH', adu, 8)
                quantity = 1 if function in (5, 6) else value
                valid = quantity > 0 and address + quantity <= 65536
                if function in (1, 2, 3, 4, 5, 6):
                    valid = valid and len(adu) == 12
                elif len(adu) < 13:
                    valid = False
                else:
                    expected = (quantity + 7) // 8 if function == 15 else quantity * 2
                    valid = valid and adu[12] == expected and len(adu) == 13 + expected
                if not valid:
                    gap('invalid_modbus_request_range_or_length'); continue
                space = ('coil' if function in (1, 5, 15) else 'discrete_input' if function == 2
                         else 'input_register' if function == 4 else 'holding_register')
                key = f'{source}->{target_ip}:{dport}:unit{unit}:fc{function}:{space}:{address}-{address+quantity-1}'
                ranges[key] += 1
                polling[key].append(stamp)
                if function in (5, 6, 15, 16):
                    registers[f'{source}:{function}:{address}'] += 1
    if not complete and not allow_incomplete:
        raise ValueError('truncated PCAP evidence (use --allow-incomplete for partial analysis)')
    duration = last - first if first is not None else 0
    profiles = {}
    for key, stamps in sorted(polling.items()):
        intervals = [b-a for a, b in zip(stamps, stamps[1:])]
        ordered = all(delta >= 0 for delta in intervals)
        profiles[key] = {
            'requests': len(stamps),
            'requests_per_packet_span_second': len(stamps)/duration if duration > 0 else None,
            'interval_samples': len(intervals),
            'interval_seconds_min': min(intervals) if intervals and ordered else None,
            'interval_seconds_max': max(intervals) if intervals and ordered else None,
            'interval_seconds_mean': sum(intervals)/len(intervals) if intervals and ordered else None,
            'interval_status': 'OBSERVED' if intervals and ordered else 'UNAVAILABLE'}
    return {'schema_version': 2, 'bytes': Path(path).stat().st_size,
            'sha256': digest.hexdigest(), 'file_complete': complete,
            'first_unix': first, 'last_unix': last, 'duration_seconds': round(duration, 9),
            'counts': dict(sorted(counts.items())), 'endpoints': dict(sorted(endpoints.items())),
            'syn_initiators': dict(sorted(initiators.items())),
            'write_registers': dict(sorted(registers.items())),
            'request_address_ranges': dict(sorted(ranges.items())),
            'modbus_exceptions': dict(sorted(exceptions.items())),
            'request_profiles': profiles, 'gaps': dict(sorted(gaps.items())),
            'limitations': [
                'IPv4 TCP over classic Ethernet PCAP only; other traffic is not decoded',
                'No TCP/IP reassembly; segmented ADUs and continuations are unavailable',
                'Initiator known only from SYN without ACK; no SYN means UNKNOWN',
                'Exact sequence/payload repeats are ambiguous retransmission candidates',
                'Rates use packet span; intervals combine connections for the same request profile',
                'No request-response matching, application latency, OPC UA or HTTP field decoding',
                'Missing replies do not establish TCP failure, timeout or Modbus exception',
                'No process attribution: correlate unit journals and socket/PID evidence',
                'File completeness does not establish capture coverage or zero packet drops']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pcap')
    parser.add_argument('--max-bytes', type=int, default=MAX_BYTES)
    parser.add_argument('--max-packets', type=int, default=MAX_PACKETS)
    parser.add_argument('--allow-incomplete', action='store_true')
    args = parser.parse_args()
    try:
        result = summarize(args.pcap, max_bytes=args.max_bytes,
                           max_packets=args.max_packets, allow_incomplete=args.allow_incomplete)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'analysis failed: {exc}\n')
    print(json.dumps(result, sort_keys=True))
    return 0 if result['file_complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
