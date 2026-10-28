"""Synthetic packet fixtures only; no live/network or Milestone 2 evidence."""
import hashlib
from pathlib import Path
import socket
import struct
import tempfile
import unittest

from pcap_modbus_summary import summarize


def adu(function=3, address=10, count=2, tid=1, unit=1):
    body = bytes((unit, function)) + struct.pack('!HH', address, count)
    return struct.pack('!HHH', tid, 0, len(body)) + body


def packet(payload=b'', *, source='10.10.2.20', target='10.10.1.10',
           sport=40000, dport=502, seq=1, flags=0x18, padding=b'', vlan=False):
    tcp = struct.pack('!HHII', sport, dport, seq, 0) + bytes((0x50, flags)) + b'\0'*6 + payload
    ip = bytes((0x45, 0)) + struct.pack('!H', 20+len(tcp)) + b'\0'*4 + bytes((64, 6)) + b'\0'*2
    ip += socket.inet_aton(source) + socket.inet_aton(target) + tcp
    return b'\0'*12 + (b'\x81\0\0\1\x08\0' if vlan else b'\x08\0') + ip + padding


def pcap(frames, *, endian='<', nanos=False):
    magic = (b'\x4d\x3c\xb2\xa1' if nanos else b'\xd4\xc3\xb2\xa1')
    if endian == '>':
        magic = magic[::-1]
    data = magic + struct.pack(endian+'HHIIII', 2, 4, 0, 0, 262144, 1)
    for timestamp, frame in frames:
        fraction = round((timestamp-int(timestamp)) * (1e9 if nanos else 1e6))
        data += struct.pack(endian+'IIII', int(timestamp), fraction, len(frame), len(frame)) + frame
    return data


class SummaryTests(unittest.TestCase):
    def run_summary(self, data, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'synthetic.pcap'
            path.write_bytes(data)
            return summarize(path, **kwargs)

    def test_ranges_rates_intervals_coalescing_and_padding(self):
        write = adu(16, 520, 2) + b'\x04\0\1\0\2'
        write = write[:4] + struct.pack('!H', 11) + write[6:]
        data = pcap([(100, packet(adu()+adu(tid=2), padding=b'\xff'*30)),
                     (100.5, packet(adu(tid=3), seq=25, vlan=True)),
                     (101, packet(write, seq=37))])
        got = self.run_summary(data)
        self.assertEqual(got['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(got['counts']['modbus_request_fc3'], 3)
        profile = next(v for k,v in got['request_profiles'].items() if ':fc3:' in k)
        self.assertEqual(profile['requests_per_packet_span_second'], 3)
        self.assertEqual(profile['interval_seconds_mean'], .25)
        self.assertTrue(any('holding_register:520-521' in k for k in got['request_address_ranges']))
        self.assertEqual(got['counts']['modbus_request_fc16'], 1)
        self.assertEqual(got['gaps'], {})

    def test_connection_failure_is_not_modbus_exception(self):
        refused = self.run_summary(pcap([
            (100, packet(flags=2)),
            (101, packet(source='10.10.1.10', target='10.10.2.20', sport=502,
                         dport=40000, flags=0x14))]))
        self.assertEqual(refused['counts']['tcp_reset_after_syn_connection_failure'], 1)
        self.assertNotIn('modbus_exception_responses', refused['counts'])
        self.assertTrue(refused['syn_initiators'])
        exception = b'\0\1\0\0\0\3\1\x83\2'
        got = self.run_summary(pcap([(100, packet(exception, source='10.10.1.10',
                                                  target='10.10.2.20', sport=502, dport=40000))]))
        self.assertEqual(got['counts']['modbus_exception_responses'], 1)
        self.assertNotIn('tcp_reset_after_syn_connection_failure', got['counts'])
        self.assertEqual(got['syn_initiators'], {})
        self.assertEqual(len(got['modbus_exceptions']), 1)

    def test_established_reset_is_not_connection_refusal(self):
        got = self.run_summary(pcap([
            (100, packet(flags=2)),
            (101, packet(flags=0x12, source='10.10.1.10', target='10.10.2.20', sport=502, dport=40000)),
            (102, packet(flags=4, source='10.10.1.10', target='10.10.2.20', sport=502, dport=40000))]))
        self.assertEqual(got['counts']['tcp_rst_packets'], 1)
        self.assertNotIn('tcp_reset_after_syn_connection_failure', got['counts'])

    def test_retransmission_ambiguity_and_segmentation(self):
        frame = packet(adu())
        got = self.run_summary(pcap([(100, frame), (101, frame), (102, packet(adu()[:10], seq=13))]))
        self.assertEqual(got['counts']['tcp_duplicate_sequence_payload_candidates'], 1)
        self.assertEqual(got['counts']['modbus_request_fc3'], 1)
        self.assertEqual(got['gaps']['incomplete_modbus_adu_no_reassembly'], 1)
        self.assertEqual(next(iter(got['request_profiles'].values()))['interval_status'], 'UNAVAILABLE')

    def test_incomplete_file_is_never_a_complete_capture(self):
        data = pcap([(100, packet(adu()))])
        for truncated in (data[:-1], data+b'\0'):
            with self.assertRaisesRegex(ValueError, 'truncated'):
                self.run_summary(truncated)
            self.assertFalse(self.run_summary(truncated, allow_incomplete=True)['file_complete'])
        snap = bytearray(data)
        struct.pack_into('<I', snap, 36, len(packet(adu()))+1)
        self.assertFalse(self.run_summary(snap, allow_incomplete=True)['file_complete'])

    def test_byte_packet_header_and_link_bounds(self):
        data = pcap([(100, packet()), (101, packet())])
        with self.assertRaisesRegex(ValueError, 'byte bound'):
            self.run_summary(data, max_bytes=24)
        with self.assertRaisesRegex(ValueError, 'packet bound'):
            self.run_summary(data, max_packets=1)
        with self.assertRaisesRegex(ValueError, 'header missing'):
            self.run_summary(b'abc')
        cooked = bytearray(data); struct.pack_into('<I', cooked, 20, 113)
        with self.assertRaisesRegex(ValueError, 'Ethernet'):
            self.run_summary(cooked)

    def test_nanos_endian_empty_and_address_semantics(self):
        for endian in ('<', '>'):
            for nanos in (False, True):
                got = self.run_summary(pcap([(100.25, packet(adu(5, 100, 0xff00)))],
                                           endian=endian, nanos=nanos))
                self.assertEqual(got['first_unix'], 100.25)
                self.assertTrue(any(':coil:100-100' in k for k in got['request_profiles']))
        empty = self.run_summary(pcap([]))
        self.assertTrue(empty['file_complete'])
        self.assertEqual(empty['request_profiles'], {})
        bad = self.run_summary(pcap([(100,packet(adu(3,65535,2)))]))
        self.assertEqual(bad['request_address_ranges'], {})
        self.assertTrue(bad['gaps'])


if __name__ == '__main__':
    unittest.main()
