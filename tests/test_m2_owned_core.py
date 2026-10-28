"""Synthetic ownership races and loopback fake RPC only. No PLC/guest core."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import m2_owned_core as o

TOKEN='a'*32


def auth():
    core=dict(pid=77,start_ticks=99,uid=1000,euid=1000,boot_id='SYNTHETIC',
              exe=o.EXE,exe_sha256=o.EXE_SHA,attempt=TOKEN)
    launch=dict(schema_version=1,token=TOKEN,parent_pid=12,issued_monotonic_ns=1_000_000_000,
                birth={**core,'ppid':12,'state':'S'})
    return dict(schema_version=2,token=TOKEN,launch=launch,core=core)


class Connection:
    def __init__(self,kernel):self.kernel=kernel;self.receiver=77;self.closed=False
    def getsockname(self):return ('127.0.0.1',40123)
    def getpeername(self):return ('127.0.0.1',o.PORT)
    def settimeout(self,n):pass
    def sendall(self,data):
        if self.kernel.fault=='after-final-check':self.kernel.listener_inode=33
        self.kernel.delivered.setdefault(self.receiver,[]).append(data)
    def recv(self,n):return b'OK\n'
    def close(self):self.closed=True


class Kernel:
    def __init__(self,fault=None):
        self.fault=fault;self.row={**auth()['core'],'state':'S'};self.delivered={}
        self.listener_inode=11;self.reads=0;self.fd_closed=False;self.connection=None
    def process(self,pid):
        self.reads+=1;r=self.row.copy()
        if self.fault=='reuse' or (self.fault=='reuse-before-send' and self.reads>=3):r['start_ticks']=101
        return r
    def cores(self):return [77,88] if self.fault=='unexpected-core' else [77]
    def sockets(self,pid):
        if self.fault=='denied':raise PermissionError('SYNTHETIC FD access denied')
        return {11} if self.fault=='wrong-accepted-owner' else {11,22}
    def tcp(self):
        if self.fault=='replace-before-send' and self.reads>=3:self.listener_inode=33
        return [dict(state='0A',local=('0.0.0.0',o.PORT),remote=('0.0.0.0',0),inode=self.listener_inode),
                dict(state='01',local=('127.0.0.1',o.PORT),remote=('127.0.0.1',40123),inode=22)]
    def pin(self,pid):return 5
    def alive(self,fd):return True
    def close(self,fd):self.fd_closed=True
    def connect(self,timeout):
        self.connection=Connection(self)
        if self.fault=='replace-before-connect':self.listener_inode=33;self.connection.receiver=88
        return self.connection
    def now(self):return 2
    def now_ns(self):return 2_000_000_000
    def sleep(self,n):raise AssertionError('fixture should establish ownership immediately')


class OwnershipTests(unittest.TestCase):
    def test_pid88_launch_vs_pid77_observation_never_authorizes(self):
        a=auth();a['launch']['birth']['pid']=88
        with self.assertRaisesRegex(ValueError,'does not match'):
            o.validate_observation(a['launch'],a['core'],TOKEN,1000)
        k=Kernel();r=o.owned_quit(a,TOKEN,k)
        self.assertEqual(r['status'],'REFUSED');self.assertFalse(r['rpc_attempted'])
        self.assertEqual(k.delivered,{})

    def test_missing_malformed_mismatched_stale_identity_refuses_without_bytes(self):
        cases=[None,{},auth(),auth(),auth(),auth()]
        cases[2]['token']='wrong'
        cases[3]['core']['uid']=0
        cases[4]['launch']['issued_monotonic_ns']=-1
        cases[5]['launch']['issued_monotonic_ns']=1
        for i,a in enumerate(cases):
            k=Kernel()
            if i==5:k.now_ns=lambda:632_000_000_000
            with self.subTest(case=i):
                r=o.owned_quit(a,TOKEN,k)
                self.assertEqual(r['status'],'REFUSED',r);self.assertEqual(k.delivered,{})

    def test_reuse_listener_replacement_unexpected_core_or_denied_proof_refuses(self):
        for fault in ('reuse','reuse-before-send','replace-before-connect','replace-before-send',
                      'unexpected-core','denied','wrong-accepted-owner'):
            k=Kernel(fault)
            with self.subTest(fault=fault):
                r=o.owned_quit(auth(),TOKEN,k)
                self.assertEqual(r['status'],'REFUSED',r)
                self.assertEqual(k.delivered,{})
                if k.connection:self.assertTrue(k.connection.closed)

    def test_valid_owned_core_is_sent_one_fixed_quit_on_proven_connection(self):
        k=Kernel();r=o.owned_quit(auth(),TOKEN,k)
        self.assertEqual(r['status'],'SENT',r)
        self.assertEqual(k.delivered,{77:[b'quit()\n']})
        self.assertTrue(k.fd_closed);self.assertTrue(k.connection.closed)

    def test_replacement_after_last_check_cannot_retarget_held_tcp_connection(self):
        k=Kernel('after-final-check');r=o.owned_quit(auth(),TOKEN,k)
        self.assertEqual(k.listener_inode,33)
        self.assertEqual(r['status'],'SENT')
        self.assertEqual(k.delivered,{77:[b'quit()\n']})
        self.assertNotIn(88,k.delivered)

    def test_launch_capture_uses_direct_unreaped_child_and_uid_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            row={**auth()['core'],'state':'S','ppid':os.getpid()}
            k=Kernel();k.process=lambda pid:row
            k.incarnation=lambda pid:{**row,'pid':pid,'start_ticks':99 if pid==77 else 98}
            k.sleep=lambda seconds:None
            child=type('Child',(),{'pid':77})()
            with patch.object(o,'Linux',return_value=k),patch.object(o.os,'getuid',return_value=1000), \
                    patch.object(o.os,'geteuid',return_value=1000),patch.object(o.subprocess,'Popen',return_value=child) as spawn:
                launch=o.launch(str(Path(directory)/'core.log'),TOKEN,1000)
                self.assertEqual(launch['birth']['pid'],77)
                self.assertEqual(spawn.call_count,1)
                self.assertEqual(spawn.call_args.kwargs['env'][o.ENV_KEY],TOKEN)
                self.assertEqual(spawn.call_args.args[0],['nohup','./core/openplc'])
            with patch.object(o.os,'getuid',return_value=0),patch.object(o.subprocess,'Popen') as spawn:
                with self.assertRaises(ValueError):o.launch(str(Path(directory)/'second.log'),TOKEN,1000)
                spawn.assert_not_called()


SERVER=r'''
import ctypes,json,os,pathlib,socket,sys
ctypes.CDLL(None).prctl(15,b'm2-core-fixture',0,0,0)
s=socket.socket();s.bind(('127.0.0.1',0));s.listen()
print(json.dumps({'port':s.getsockname()[1]}),flush=True)
c,_=s.accept();b=b''
while not b.endswith(b'\n'):
 r=c.recv(64)
 if not r:break
 b+=r
pathlib.Path(sys.argv[1]).write_bytes(b)
if b==b'quit()\n':c.sendall(b'OK\n')
c.close();s.close()
'''


class LoopbackTests(unittest.TestCase):
    def test_real_proc_accepted_socket_proof_and_fake_process_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            log=Path(directory)/'messages'
            p=subprocess.Popen([sys.executable,'-c',SERVER,str(log)],stdout=subprocess.PIPE,text=True,
                               env={**os.environ,o.ENV_KEY:TOKEN})
            try:
                port=json.loads(p.stdout.readline())['port']
                k=o.Linux();row=k.process(p.pid)
                a={'schema_version':2,'token':TOKEN,'core':{n:row[n] for n in o.CORE_FIELDS},
                   'launch':{'schema_version':1,'token':TOKEN,'parent_pid':os.getpid(),
                             'issued_monotonic_ns':time.monotonic_ns(),'birth':row}}
                with patch.object(o,'PORT',port),patch.object(o,'COMM','m2-core-fixture'), \
                        patch.object(o,'EXE',row['exe']),patch.object(o,'EXE_SHA',row['exe_sha256']):
                    result=o.owned_quit(a,TOKEN)
                self.assertEqual(result['status'],'SENT',result)
                p.wait(3)
                self.assertEqual(log.read_bytes(),b'quit()\n')
                self.assertEqual(p.returncode,0)
            finally:
                if p.poll() is None:p.kill()
                p.wait(3);p.stdout.close()


if __name__=='__main__':unittest.main()
