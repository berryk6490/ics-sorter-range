"""Detached whole-controller fixtures. NO guests, AF_PACKET or real tcpdump."""
import ast
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time
from unittest.mock import patch
from unittest.mock import mock_open
import unittest

import baseline_capture as capture
import m2_guest_capture as guest
import test_m2_guest_capture as old

WRITER=old.WRITER.replace("with open(path,'wb') as f:f.write(struct.pack('<IHHIIII',0xa1b2c3d4,2,4,0,0,65535,1))",
    "f=open(path,'wb');f.write(struct.pack('<IHHIIII',0xa1b2c3d4,2,4,0,0,65535,1));f.flush()")
WRITER=WRITER.replace("print(json.dumps(dict(fixture=True,pid=os.getpid())),flush=True)",
    "print(json.dumps(dict(fixture=True,pid=os.getpid())),flush=True)\n"
    "if sys.argv[2] not in ('header-only','early'):print('tcpdump: listening on lo, link-type EN10MB (Ethernet), snapshot length 262144 bytes',file=sys.stderr,flush=True)\n"
    "if sys.argv[2]=='early':raise SystemExit(0)")
WRITER=WRITER.replace('while not stop:time.sleep(.01)',
    "begin=time.monotonic()\nwhile not stop:\n time.sleep(.01)\n if sys.argv[2]=='cap' and time.monotonic()-begin>.4:break")
PROBE='''def packet_open_proof(point,argv,rows):
    # SYNTHETIC ONLY. Real private file FD substitutes at the kernel socket seam.
    if MODE in ('no-socket','header-only'):return None
    for row in rows:
        base=Path('/proc')/str(row['pid'])
        try:
            command=(base/'cmdline').read_bytes().rstrip(b'\\0').split(b'\\0')
            if command!=[v.encode() for v in argv[6:]]:continue
            for fd in (base/'fd').iterdir():
                if os.readlink(fd)==str(Path(argv[-2])):
                    inode=os.stat(fd).st_ino
                    return dict(kind='LINUX_PACKET_SOCKET',interface_index=1,socket_inode=inode,
                        packet_row=['SYNTHETIC','1','3','0003','1','1','0',str(row['uid']),str(inode)],
                        capture_identity=row,executable_sha256=sha(base/'exe'),fixture_only=True)
        except (FileNotFoundError,ProcessLookupError):continue
    return None
'''


class OverlapTests(unittest.TestCase):
    setUp=old.GuestCaptureTests.setUp
    read_fixture=old.GuestCaptureTests.read_fixture
    wait_report=old.GuestCaptureTests.wait_report
    assert_gone=old.GuestCaptureTests.assert_gone

    def tearDown(self):
        evidence=os.environ.get('M2_OVERLAP_FIXTURE_OUTPUT')
        if evidence:
            dest=Path(evidence)/self._testMethodName
            shutil.copytree(self.root,dest,symlinks=True)
            (dest/'FIXTURE_ONLY.json').write_text(json.dumps({'synthetic':True,'AF_PACKET_opened':False,
                'real_tcpdump':False,'kernel_socket_seam':'instrumented archive substitutes private regular FD'},indent=2)+'\n')
        old.GuestCaptureTests.tearDown(self)

    def fixture(self,mode='normal',seconds=2):
        location='scada' if os.getuid()==1000 else 'xle';name=location+'-local'
        sources={n:Path(guest.__file__).with_name(n).read_bytes() for n in guest.MODULES}
        raw=sources['baseline_capture.py'].decode();tree=ast.parse(raw);lines=raw.splitlines(True)
        replacements={'capture_argv':'def capture_argv(point,output,packets):\n    return [sys.executable,"-u","-c",'+repr(WRITER)+',str(output),'+repr(mode)+']\n',
                      'packet_open_proof':PROBE.replace('MODE',repr(mode))}
        for fn in sorted([n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in replacements],key=lambda n:n.lineno,reverse=True):
            lines[fn.lineno-1:fn.end_lineno]=[replacements[fn.name]]
        sources['baseline_capture.py']=''.join(lines).encode()
        sources['m2_guest_capture.py']=sources['m2_guest_capture.py'].replace(b'import socket\n',
            b'import socket\n# SYNTHETIC HOST FIXTURE ONLY\nsocket.gethostname=lambda:'+repr(location).encode()+b'\n')
        archive=self.root/(mode+'.pyz');info=guest.build(archive,sources=sources)
        config=capture.load_config(capture.DEFAULT_CONFIG)
        identity={'kind':'SYNTHETIC_FIXTURE','selected_source_commit':config['selected_source_commit'],
                  'manifest_sha256':config['selected_manifest_sha256']}
        c=guest.plan(config,[name],location,str(self.root/('capture-'+mode)),'fixture-'+mode,str(archive),
            info['archive_sha256'],os.getuid(),seconds,100,4096,.15,host_identity=identity,module_pins=info['module_sha256'])
        c.update(status='PASS',checks={k:'PASS' for k in guest.REVIEW_KEYS},guest_contract_path=str(self.root/(mode+'.json')))
        path=Path(c['guest_contract_path']);path.write_bytes(guest.encode(c));pin=guest.digest(path.read_bytes())
        fake=lambda point,output,packets:[sys.executable,'-u','-c',WRITER,str(output),mode]
        patcher=patch.object(capture,'capture_argv',fake);patcher.start();self.addCleanup(patcher.stop)
        return c,path,pin

    def start(self,c,p,h):return guest.remote_start(p,h,self.root/'start',read=self.read_fixture)
    def join(self,c,p,h):return guest.remote_join(p,h,self.root/'start/capture-start.json',self.root/'join',read=self.read_fixture)

    def test_detached_whole_controller_open_console_reuse_join_and_offline_export(self):
        c,p,h=self.fixture(seconds=2);t=time.monotonic();started=self.start(c,p,h)
        self.assertEqual(started['status'],'OPEN',started);self.assertLess(time.monotonic()-t,1.5)
        cp=started['result']['checkpoint'];pid=cp['controller_identity']['pid']
        self.assertTrue(capture.same_process(cp['controller_identity'],capture.process_identity(pid)))
        for n in (0,1,2):self.assertEqual(os.readlink('/proc/'+str(pid)+'/fd/'+str(n)),'/dev/null')
        self.assertEqual(capture.process_identity(pid)['sid'],pid)
        # First subprocess exited (console released), controller remains alive.
        traces=[];read=guest.serial_call(c,"/usr/bin/python3 -B -c 'import json; print(json.dumps({\"fixture_sequential_read\":True}))'",
            'SYNTHETIC-scenario-read',time.monotonic()+5,traces,read=self.read_fixture)
        self.assertTrue(read['fixture_sequential_read']);self.assertEqual(len(traces),2)
        self.assertTrue(capture.same_process(cp['controller_identity'],capture.process_identity(pid)))
        joined=self.join(c,p,h);self.assertEqual(joined['status'],'COMPLETE',joined)
        report=self.wait_report(c,lambda r:r.get('terminal'));self.assert_gone(report)
        exported=guest.export_capture(p,h,self.root/'join/capture-transport.json',self.root/'export',seconds=30,read=self.read_fixture)
        self.assertEqual(exported['status'],'COMPLETE',exported)
        self.assertEqual(guest.verify_export(self.root/'export')['status'],'COMPLETE')
        self.assertEqual(json.loads((self.root/'export/raw/capture-open.json').read_text()),cp)
        # Resealing changed bytes cannot make a foreign checkpoint canonical.
        bad=deepcopy(started);bad['result']['checkpoint']['points'][c['selected_points'][0]]['proof']['capture_identity']['pid']+=1
        transport=deepcopy(joined);transport['launch']=bad
        with self.assertRaises(ValueError):guest.verify_detached_files(c,h,transport,self.root/'export/raw')

    def test_header_or_listening_without_socket_and_early_exit_never_open_or_relaunch(self):
        for mode in ('header-only','no-socket','early'):
            # Each is a separate one-use synthetic contract/destination.
            c,p,h=self.fixture(mode=mode,seconds=.7)
            result=guest.remote_start(p,h,self.root/('start-'+mode),read=self.read_fixture)
            self.assertEqual(result['status'],'INCOMPLETE',result)
            report=self.wait_report(c,lambda r:r.get('terminal'));self.assert_gone(report)
            with self.assertRaises(Exception):
                guest.guest_launch(c,{},h,guest.launch_request('f'*32))

    def test_controller_loss_cleans_without_join_adoption_and_cannot_pass(self):
        c,p,h=self.fixture(seconds=2);started=self.start(c,p,h);self.assertEqual(started['status'],'OPEN',started)
        identity=started['result']['checkpoint']['controller_identity'];observed=capture.process_identity(identity['pid'])
        self.assertEqual(observed['ppid'],os.getpid())  # fixture subreaper owns adopted child
        held=os.pidfd_open(identity['pid'])
        try:
            self.assertTrue(capture.same_process(identity,capture.process_identity(identity['pid'])))
            signal.pidfd_send_signal(held,signal.SIGKILL)
        finally:os.close(held)
        report=self.wait_report(c,lambda r:r.get('terminal'));self.assert_gone(report)
        self.assertEqual(report['cleanup'],'PASS');self.assertIn('controller_lost',report['primary_error'])
        with patch.object(guest.time,'monotonic',return_value=1e15),self.assertRaises(TimeoutError):
            guest.guest_join(c,h,{'launch_token':started['launch_token'],
                'checkpoint_sha256':started['result']['checkpoint_sha256']})

    def test_bound_token_runtime_identity_receipts_and_terminal_controls_reject(self):
        c,p,h=self.fixture(seconds=1.5);start=self.start(c,p,h);self.assertEqual(start['status'],'OPEN',start)
        for fault in ('token','archive','contract','uid','boot','process','point','deadline','missing-alive','order','nonce','partial'):
            bad=deepcopy(start);cp=bad['result']['checkpoint'];row=next(iter(cp['points'].values()))
            if fault=='token':bad['launch_token']='0'*32
            elif fault=='archive':cp['binding']['archive_sha256']='0'*64
            elif fault=='contract':cp['binding']['contract_sha256']='0'*64
            elif fault=='uid':cp['controller_identity']['uid']=True
            elif fault=='boot':cp['binding']['boot_id']='0'*36
            elif fault=='process':row['proof']['capture_identity']['start_ticks']=False
            elif fault=='point':row['point']['filter']='tcp'
            elif fault=='deadline':row['deadline_monotonic_ns']+=1
            elif fault=='missing-alive':bad['traces'].pop()
            elif fault=='order':bad['traces'].reverse()
            elif fault=='nonce':bad['traces'][1]['argv'][7]='0'*32
            else:bad['status']='INCOMPLETE'
            bad['result']['checkpoint_sha256']=guest.digest(guest.encode(cp))
            with self.subTest(fault=fault),self.assertRaises((ValueError,KeyError)):
                guest.check_start_transport(c,h,bad)
        joined=self.join(c,p,h);self.assertEqual(joined['status'],'COMPLETE',joined)
        for fault in ('missing-cleanup','foreign-identity','late','missing-join-alive'):
            bad=deepcopy(joined)
            if fault=='missing-cleanup':bad['result']['cleanup']='INCOMPLETE'
            elif fault=='foreign-identity':bad['join_result']['controller_postflight']['expected']['pid']+=1
            elif fault=='late':bad['join_result']['joined_monotonic_ns']=10**30
            else:bad['traces'].pop()
            if fault=='missing-cleanup':
                data=json.loads(bad['traces'][0]['stdout']);data['output']=json.dumps(bad['join_result'])
                bad['traces'][0]['stdout']=json.dumps(data)  # recompute the outer receipt too
            with self.subTest(fault=fault),self.assertRaises((ValueError,KeyError)):
                guest.check_detached_transport(c,h,bad)

    def test_prelaunch_failure_spends_reservation_without_retry_or_child_creation(self):
        c,p,h=self.fixture(mode='reservation',seconds=1)
        runtime={'path':str(self.root/'reservation.pyz'),'archive_sha256':c['bundle_sha256']}
        with patch.object(guest.subprocess,'Popen',side_effect=OSError('SYNTHETIC exec unavailable')) as popen:
            failed=guest.guest_launch(c,runtime,h,guest.launch_request('1'*32))
            self.assertEqual(failed['status'],'INCOMPLETE');self.assertIn('exec unavailable',failed['errors']['primary'])
            self.assertFalse(Path(c['output']).exists())
            with self.assertRaises(FileExistsError):guest.guest_launch(c,runtime,h,guest.launch_request('2'*32))
            self.assertEqual(popen.call_count,1)

    def test_inherited_console_refused_before_controller_and_schedule_no_extension(self):
        c,p,h=self.fixture(seconds=2)
        launch={'launch_token':'1'*32,'binding':guest.open_binding(c,h,capture.BOOT_ID),
                'launch_monotonic_ns':time.monotonic_ns(),'stdio':'DEVNULL; close_fds; new session'}
        with patch.object(guest.os,'getsid',return_value=os.getpid()),patch.object(guest.os,'readlink',return_value='/dev/pts/9'), \
                patch.object(guest,'guest_collect') as collect,self.assertRaisesRegex(ValueError,'stdio/session'):
            guest.detached_controller(c,{},h,launch)
        collect.assert_not_called()
        start=self.start(c,p,h);self.assertEqual(start['status'],'OPEN',start)
        with self.assertRaisesRegex(TimeoutError,'insufficient remaining'):
            guest.overlap_schedule([(c,h,start)],now=start['ended_monotonic'])
        self.assertEqual(self.join(c,p,h)['status'],'COMPLETE')

    def test_cap_failure_and_missing_terminal_remain_incomplete(self):
        c,p,h=self.fixture(mode='cap',seconds=2);start=self.start(c,p,h)
        self.assertEqual(start['status'],'OPEN',start)
        joined=self.join(c,p,h);self.assertEqual(joined['status'],'INCOMPLETE',joined)
        report=self.wait_report(c,lambda r:r.get('terminal'));self.assert_gone(report)
        self.assertIn('early or failed',report['primary_error'])
        self.assertEqual(report['cleanup'],'PASS')
        # join/read code has no process-signal or relaunch call at all.
        tree=ast.parse(__import__('inspect').getsource(guest.guest_join))
        names={n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute)}
        self.assertFalse(names&{'Popen','kill','send_signal','pidfd_send_signal'})

    def test_fixed_earliest_start_schedule_accepts_margin_and_rejects_too_late(self):
        c={'seconds':600};a={'started_monotonic':100.,'ended_monotonic':102.,'host_boot_id':capture.BOOT_ID}
        b={**a,'started_monotonic':110.,'ended_monotonic':112.}
        # Already-validated receipts are a seam for this pure arithmetic fixture.
        with patch.object(guest,'check_start_transport',return_value=set()):
            self.assertEqual(guest.overlap_schedule([(c,'FIXTURE',a),(c,'FIXTURE',b)],now=155.)['latest_case_start_monotonic'],155.)
            with self.assertRaises(TimeoutError):guest.overlap_schedule([(c,'FIXTURE',a),(c,'FIXTURE',b)],now=155.001)

    def test_missing_start_liveness_never_authorizes_join_or_relaunch(self):
        c,p,h=self.fixture(seconds=1.2);calls=[]
        def missing(vm,command,label,**options):
            calls.append(label);trace=options['trace']
            options['trace']=lambda row:None if row['probe'] else trace(row)
            return self.read_fixture(vm,command,label,**options)
        start=guest.remote_start(p,h,self.root/'start',read=missing)
        self.assertEqual(start['status'],'INCOMPLETE');self.assertEqual(calls,['m2-guest-launch'])
        with self.assertRaises(ValueError):self.join(c,p,h)
        report=self.wait_report(c,lambda r:r.get('terminal'));self.assert_gone(report)

    def test_production_packet_probe_requires_owned_fd_interface_protocol_and_executable(self):
        row={'pid':4242,'start_ticks':123,'uid':os.getuid(),'boot_id':capture.BOOT_ID,
             'ppid':1,'sid':4242,'pgid':4242,'state':'S'}
        point={'interface':'lo','filter':'tcp'};argv=capture.bounded_argv(point,Path('/FIXTURE.pcap'),100,10,3)
        expected=argv[6:];header='sk RefCnt Type Proto Iface R Rmem User Inode\n'
        for fault in ('none','fd','interface','protocol','exe','denied'):
            line='ptr 1 3 '+('0800' if fault=='protocol' else '0003')+' '+('2' if fault=='interface' else '1')+' 1 0 '+str(os.getuid())+' 999\n'
            opened=mock_open(read_data=(header+line).encode())
            with patch.object(capture.socket,'if_nametoindex',return_value=1),patch.object(capture,'process_identity',return_value=row), \
                    patch.object(Path,'read_bytes',return_value=b'\0'.join(v.encode() for v in expected)+b'\0'), \
                    patch.object(Path,'samefile',return_value=fault!='exe'), \
                    patch.object(Path,'iterdir',return_value=iter([Path('/proc/4242/fd/9')])), \
                    patch.object(capture.os,'readlink',return_value='socket:[998]' if fault=='fd' else 'socket:[999]'), \
                    patch.object(Path,'open',side_effect=PermissionError('SYNTHETIC proc denied') if fault=='denied' else opened), \
                    patch.object(capture,'sha',return_value='e'*64):
                if fault in ('exe','denied'):
                    with self.subTest(fault=fault),self.assertRaises((ValueError,PermissionError)):
                        capture.packet_open_proof(point,argv,[row])
                else:
                    proof=capture.packet_open_proof(point,argv,[row])
                    self.assertEqual(proof is not None,fault=='none')


if __name__=='__main__':unittest.main()
