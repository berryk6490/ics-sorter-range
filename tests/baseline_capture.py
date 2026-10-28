"""Canonical capture-only controller/supervisor. Default CLI collects host taps.

The pinned m2_guest_capture entry reuses this lifecycle in a guest namespace.
Scenario, serial authentication, approval, deployment and recovery remain separate.
"""
import argparse
import select
import socket
import sys
import tempfile
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import resource
import shlex
import signal
import subprocess
import time

from pcap_modbus_summary import summarize, MAX_BYTES, MAX_PACKETS

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(__file__).with_name('milestone2_capture.json')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}')


def utc():
    return datetime.now(timezone.utc).isoformat()


def save(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent,
                                         prefix='.' + path.name, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            original = sys.exc_info()[1]
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                if original is None:
                    raise
                note=f'temporary artifact cleanup failed: {exc}'
                if callable(getattr(original,'add_note',None)):original.add_note(note)
                else:original.__notes__=[*getattr(original,'__notes__',[]),note]


def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            result.update(block)
    return result.hexdigest()


def statistics(text):
    """Missing statistics stay unknown, including on permission failures."""
    labels = {'captured': r'(\d+) packets? captured',
              'received': r'(\d+) packets? received by filter',
              'kernel_dropped': r'(\d+) packets? dropped by kernel',
              'interface_dropped': r'(\d+) packets? dropped by interface'}
    return {key: int(found[-1]) if (found := re.findall(pattern, text)) else None
            for key, pattern in labels.items()}


def load_config(path):
    return validate_config(json.loads(Path(path).read_text()))


def validate_config(config):
    if config.get('schema_version') != 1 or not config.get('points'):
        raise ValueError('unsupported or empty capture configuration')
    for name, point in config['points'].items():
        if not NAME.fullmatch(name) or not NAME.fullmatch(point['interface']):
            raise ValueError('invalid point or interface')
        if point['location'] not in ('host', 'fw', 'plc', 'drives', 'xle', 'scada'):
            raise ValueError('invalid capture location')
        if not point['filter'] or '\n' in point['filter']:
            raise ValueError('explicit single-line filter required')
    return config


def limits(seconds, packets, max_bytes, grace):
    if not 0 < seconds <= 600 or not 1 <= packets <= MAX_PACKETS:
        raise ValueError('duration must be 0..600 seconds and packets 1..200000')
    if not 4096 <= max_bytes <= MAX_BYTES or not 0 < grace <= 5:
        raise ValueError('bytes must be 4096..67108864 and stop grace 0..5 seconds')


def identity(config):
    """Actual host sources, not an attestation of guest deployment."""
    manifest_path = ROOT / 'deploy/deployment_manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if sha(manifest_path) != config['selected_manifest_sha256']:
        raise ValueError('selected manifest differs; document and select explicitly')
    def git(*args, root=ROOT):
        return subprocess.run(['git', '-c', f'safe.directory={root}', '-C', str(root), *args], capture_output=True,
                              text=True, check=True, timeout=10).stdout.strip()
    paths = {row['repository_source'] for row in manifest['components']}
    paths.update(('tests/baseline_capture.py', 'tests/pcap_modbus_summary.py',
                  'tests/milestone2_results.json', 'MILESTONE2_BASELINE.md'))
    head = git('rev-parse', 'HEAD')
    if head != config['selected_source_commit']:
        raise ValueError('selected source commit differs; select explicitly')
    runtime = Path(config['measurement_source_worktree']).resolve()
    if (git('rev-parse', 'HEAD', root=runtime) != config['selected_source_commit'] or
            git('status', '--porcelain=v1', '-uall', root=runtime) or
            sha(runtime / 'deploy/deployment_manifest.json') != config['selected_manifest_sha256']):
        raise ValueError('measurement source worktree is not the selected clean revision')
    runtime_hashes = {row['repository_source']: sha(runtime / row['repository_source'])
                      for row in manifest['components']}
    if any(runtime_hashes[row['repository_source']] != row['sha256'] for row in manifest['components']):
        raise ValueError('measurement source component hash differs')
    host_differences = sorted({row['repository_source'] for row in manifest['components']
                               if sha(ROOT / row['repository_source']) != row['sha256']})
    return {'kind': 'HOST_SOURCE_ONLY', 'git_head': head,
            'git_status': git('status', '--porcelain=v1', '-uall'),
            'git_diff_sha256': hashlib.sha256(git('diff', '--binary', 'HEAD').encode()).hexdigest(),
            'selected_source_commit': config['selected_source_commit'],
            'manifest_sha256': sha(manifest_path),
            'manifest_component_count': len(manifest['components']),
            'declared_plc_identity': manifest['plc_program_identity'],
            'source_sha256': {name: sha(ROOT / name) for name in sorted(paths)},
            'measurement_source_worktree': str(runtime),
            'measurement_component_sha256': runtime_hashes,
            'undeployed_host_component_differences': host_differences,
            'handoff': config['handoff'], 'paper': config['paper'],
            'guest_equality': 'UNVERIFIED'}


def capture_argv(point, output, packets):
    # -Z explicitly retains the invoking identity; this helper never elevates.
    return ['/usr/bin/tcpdump', '-Z', pwd.getpwuid(os.geteuid()).pw_name,
            '-p', '-i', point['interface'], '-s', '0', '-nn', '-U', '-c', str(packets),
            '-w', str(output), point['filter']]


def bounded_argv(point, output, packets, seconds, grace):
    # Secondary bound only; the independent subreaper owns descendant cleanup.
    return ['/usr/bin/timeout', '--verbose', '--preserve-status', '--signal=INT',
            f'--kill-after={grace}s', f'{seconds}s',
            *capture_argv(point, output, packets)]


# Linux process ownership is established by ancestry under this supervisor,
# then pinned by pidfd plus boot/PID/start/UID identity. Names are never targets.
BOOT_ID = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
REAP_SECONDS = 1.0


def process_identity(pid):
    try:
        text = Path(f'/proc/{pid}/stat').read_text()
        fields = text[text.rfind(')') + 2:].split()
        return {'pid': int(pid), 'state': fields[0], 'ppid': int(fields[1]),
                'pgid': int(fields[2]), 'sid': int(fields[3]),
                'start_ticks': int(fields[19]),
                'uid': int(re.search(r'(?m)^Uid:\s+(\d+)', Path(f'/proc/{pid}/status').read_text())[1]),
                'boot_id': BOOT_ID}
    except (FileNotFoundError, ProcessLookupError):
        return None


def same_process(old, new):
    return new is not None and all(old[k] == new[k] for k in ('pid', 'start_ticks', 'uid', 'boot_id'))


def process_children(pid):
    """Kernel child edges, including children created by nonleader threads.

    Disappearing tasks are normal races. Access failures are not empty lists.
    Unrelated /proc rows are never read by descendant discovery.
    """
    try:
        tasks = list(Path(f'/proc/{pid}/task').iterdir())
    except (FileNotFoundError, ProcessLookupError):
        if process_identity(pid) is not None:
            raise ProcessDiscoveryError(f'required task visibility unavailable for live process {pid}')
        return []
    children = set()
    readable = False
    for task in tasks:
        try:
            children.update(map(int, (task / 'children').read_text().split()))
            readable = True
        except (FileNotFoundError, ProcessLookupError):
            if task.exists():
                raise ProcessDiscoveryError(f'required children visibility unavailable: {task}')
            continue
    if not readable and process_identity(pid) is not None:
        raise ProcessDiscoveryError(f'no readable task children for live process {pid}')
    return sorted(children)


class ProcessDiscoveryError(RuntimeError):
    pass


def process_key(row):
    return row['pid'], row['start_ticks']


def pidfd_alive(fd):
    # A pidfd stays readable after exit, including after numeric PID reuse.
    return not select.select([fd], [], [], 0)[0]


class OwnedProcesses:
    def __init__(self, root):
        if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
            raise RuntimeError('Linux pidfd support is required before capture launch')
        if root is None:
            raise RuntimeError('process identity unavailable')
        self.root = root
        self.records, self.fds, self.discovery_errors = {}, {}, []
        self.root_fd = None
        try:
            self.root_fd = os.pidfd_open(root['pid'])
            signal.pidfd_send_signal(self.root_fd, 0)
            # Required visibility is checked before any capture is launched.
            if not same_process(root, process_identity(root['pid'])):
                raise ProcessDiscoveryError('root incarnation changed before discovery')
            process_children(root['pid'])
            if not pidfd_alive(self.root_fd):
                raise ProcessDiscoveryError('root exited before discovery')
        except BaseException as exc:
            if self.root_fd is not None:
                os.close(self.root_fd)
            raise ProcessDiscoveryError(f'process visibility preflight: {type(exc).__name__}: {exc}') from exc

    def discovery_failed(self, context, exc):
        error = f'{context}: {type(exc).__name__}: {exc}'
        if error not in self.discovery_errors:
            self.discovery_errors.append(error)

    def scan(self):
        # Start only from proven incarnations. A global, non-atomic PPID
        # snapshot cannot prove ancestry: the numeric parent may be recycled.
        pending = [(self.root, self.root_fd), *[(row, self.fds[key])
                   for key, row in self.records.items()]]
        visited = set()
        while pending:
            parent, parent_fd = pending.pop()
            key = process_key(parent)
            if key in visited or not pidfd_alive(parent_fd):
                continue
            visited.add(key)
            try:
                before = process_identity(parent['pid'])
                if not same_process(parent, before):
                    if not pidfd_alive(parent_fd):
                        continue  # exited between the liveness and identity reads
                    raise ProcessDiscoveryError('parent incarnation unavailable or changed')
                children = process_children(parent['pid'])
            except (OSError, ValueError, ProcessDiscoveryError) as exc:
                self.discovery_failed(f'parent {key}', exc)
                continue
            for pid in children:
                fd = None
                try:
                    row = process_identity(pid)
                    if row is None or row['state'] == 'Z':
                        continue  # ordinary disappearing-child race
                    child_key = process_key(row)
                    if child_key in self.records:
                        continue  # already proven and pinned; may now be adopted
                    fd = os.pidfd_open(pid)
                    after = process_identity(pid)
                    # Re-read the kernel edge AFTER pinning the child. Both
                    # pinned incarnations must remain alive throughout proof.
                    confirmed_children = process_children(parent['pid'])
                    confirmed_parent = process_identity(parent['pid'])
                    if not pidfd_alive(fd):
                        continue
                    if (not same_process(row, after) or
                            not same_process(parent, confirmed_parent) or
                            not pidfd_alive(parent_fd) or
                            after['ppid'] != parent['pid'] or
                            pid not in confirmed_children or
                            after['start_ticks'] < parent['start_ticks']):
                        raise ProcessDiscoveryError(f'unproven parent incarnation/edge for child {pid}')
                    row['ownership_parent'] = {k: parent[k] for k in ('pid', 'start_ticks', 'uid', 'boot_id')}
                    self.records[child_key], self.fds[child_key] = row, fd
                    pending.append((row, fd))
                    fd = None  # ownership of this fd transferred to the tracker
                except (FileNotFoundError, ProcessLookupError):
                    pass
                except (OSError, ValueError, ProcessDiscoveryError) as exc:
                    self.discovery_failed(f'child {pid} of {key}', exc)
                finally:
                    if fd is not None:
                        os.close(fd)

    def live(self):
        # Ownership was established before registration. These pinned targets
        # remain safe to clean even if /proc becomes unreadable afterwards.
        return [row for key, row in self.records.items() if pidfd_alive(self.fds[key])]

    def send(self, sig, rows=None):
        for row in self.live() if rows is None else rows:
            key = process_key(row)
            if key not in self.fds or not pidfd_alive(self.fds[key]):
                continue
            try:
                signal.pidfd_send_signal(self.fds[key], sig)
            except ProcessLookupError:
                pass

    def close(self):
        for fd in [self.root_fd, *self.fds.values()]:
            os.close(fd)


def reap_children(wrappers):
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if not pid:
            return
        for process in wrappers.values():
            if process.pid == pid:
                process.returncode = os.waitstatus_to_exitcode(status)


def enable_subreaper():
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), 'cannot establish capture subreaper')


def child_pids():
    # Includes adopted zombies; successful cleanup must reap them too.
    return process_children(os.getpid())


def packet_open_proof(point, argv, rows):
    """Positive Linux packet-socket evidence from already-owned incarnations only."""
    expected=argv[6:]  # fixed bounded_argv prefix; not a caller-selected process
    index=socket.if_nametoindex(point['interface'])
    matches=[]
    for row in rows:
        pid=row['pid'];base=Path('/proc')/str(pid)
        try:
            if not same_process(row,process_identity(pid)):continue
            command=(base/'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
            if command!=[part.encode() for part in expected]:continue
            if not (base/'exe').samefile(expected[0]):raise ValueError('capture executable differs')
            inodes=set()
            for fd in (base/'fd').iterdir():
                target=os.readlink(fd)
                match=re.fullmatch(r'socket:\[(\d+)\]',target)
                if match:inodes.add(int(match[1]))
            with (base/'net/packet').open('rb') as stream:packet_data=stream.read(65537)
            if len(packet_data)>65536:raise ValueError('packet socket metadata exceeds bound')
            lines=packet_data.decode().splitlines()
            if not lines or lines[0].split()!=['sk','RefCnt','Type','Proto','Iface','R','Rmem','User','Inode']:
                raise ValueError('packet socket visibility/format unavailable')
            for line in lines[1:]:
                fields=line.split()
                if len(fields)!=9:raise ValueError('ambiguous packet socket row')
                if (int(fields[8]) in inodes and int(fields[4])==index and
                    int(fields[7])==row['uid'] and int(fields[2])==3 and int(fields[3],16)==3 and int(fields[5])==1):
                    matches.append({'kind':'LINUX_PACKET_SOCKET','interface_index':index,
                        'socket_inode':int(fields[8]),'packet_row':fields,
                        'capture_identity':row,'executable_sha256':sha(base/'exe')})
            if not same_process(row,process_identity(pid)):raise ValueError('capture incarnation changed during open proof')
        except (FileNotFoundError,ProcessLookupError):continue
    if len(matches)>1:raise ValueError('ambiguous owned packet sockets')
    return matches[0] if matches else None


def supervisor(plan, control_fd):
    """Independent capture supervisor, never scenario/guest/recovery control.

    EOF on the private controller pipe also occurs after controller SIGKILL.
    Only this surviving process can then write its own terminal cleanup record.
    """
    directory = Path(plan['directory'])
    report = {'schema_version': 2, 'terminal': False, 'status': 'IN_PROGRESS',
              'scenario_id': plan['scenario_id'], 'collection_token': plan['collection_token'], 'started_utc': utc(),
              'supervisor_identity': process_identity(os.getpid()),
              'points': {}, 'processes': [], 'primary_error': None,
              'cleanup_errors': [], 'artifact_errors': [], 'discovery_errors': [],
              'cleanup': 'INCOMPLETE'}
    wrappers, streams, tracker = {}, [], None
    stop_signal = []
    def interrupted(signum, frame):
        stop_signal.append(signum)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    def persist():
        save(directory / 'supervision.json', report)
    def reaped_scan():
        reap_children(wrappers)
        try:
            tracker.scan()
        except Exception as exc:
            # Discovery must never prevent signalling previously proven targets.
            tracker.discovery_failed('discovery scan', exc)
        report['processes'] = list(tracker.records.values())
        report['discovery_errors'] = list(tracker.discovery_errors)
    def remaining_children():
        try:
            return child_pids()
        except (OSError, ValueError, ProcessDiscoveryError) as exc:
            tracker.discovery_failed('final child visibility', exc)
            report['discovery_errors'] = list(tracker.discovery_errors)
            return None  # unknown is never an empty, verified child list
    try:
        enable_subreaper()
        tracker = OwnedProcesses(report['supervisor_identity'])
        persist()  # failure here must launch no capture
        for name, spec in plan['points'].items():
            if stop_signal or select.select([control_fd], [], [], 0)[0]:
                raise RuntimeError('controller or supervisor stopped before point launch')
            # Every opened stream is registered immediately, even if the next open fails.
            out = (directory / f'{name}.stdout').open('wb'); streams.append(out)
            err = (directory / f'{name}.stderr').open('wb'); streams.append(err)
            r = {**spec, 'started_utc': utc(), 'started_monotonic_ns': time.monotonic_ns(),
                 'exit_status': None, 'stop_reason': None, 'status': 'INCOMPLETE'}
            report['points'][name] = r
            def file_limit():
                resource.setrlimit(resource.RLIMIT_FSIZE, (plan['file_byte_limit'], plan['file_byte_limit']))
            process = subprocess.Popen(spec['argv'], stdout=out, stderr=err,
                                       env={**os.environ, 'LC_ALL': 'C'},
                                       start_new_session=True, preexec_fn=file_limit)
            wrappers[name] = process
            r['wrapper_identity'] = process_identity(process.pid)
            reaped_scan(); persist()
        while True:
            reaped_scan()
            ready, _, _ = select.select([control_fd], [], [], .02)
            if ready:
                message = os.read(control_fd, 1)
                raise RuntimeError('controller_lost' if not message else 'controller_aborted')
            if stop_signal:
                raise RuntimeError(f'supervisor_signal_{stop_signal[0]}')
            if plan.get('open_binding') and not (directory/'capture-open.json').exists():
                if tracker.discovery_errors:raise ProcessDiscoveryError('capture-open discovery incomplete')
                opened={}
                for name,spec in plan['points'].items():
                    r=report['points'][name]
                    with (directory/(name+'.stderr')).open('rb') as stream:raw=stream.read(65537)
                    if len(raw)>65536:raise ValueError('capture-open stderr exceeds bound')
                    lines=re.findall(r'(?m)^tcpdump: listening on '+re.escape(spec['point']['interface'])+
                        r', link-type [^\n]+, snapshot length 262144 bytes\s*$',raw.decode(errors='replace'))
                    if len(lines)>1:raise ValueError('ambiguous capture-open listening evidence')
                    proof=packet_open_proof(spec['point'],spec['argv'],
                        descendants_of(r['wrapper_identity'],tracker)) if lines else None
                    if proof is not None and not pidfd_alive(tracker.fds[process_key(proof['capture_identity'])]):proof=None
                    if proof is not None:
                        opened[name]={'point':spec['point'],'argv':spec['argv'],
                            'wrapper_identity':r['wrapper_identity'],'proof':proof,
                            'listening_line':lines[0].strip(),'started_monotonic_ns':r['started_monotonic_ns'],
                            'deadline_monotonic_ns':r['started_monotonic_ns']+int(plan['duration_limit_seconds']*1e9)}
                if len(opened)==len(plan['points']):
                    if any(p.poll() is not None for p in wrappers.values()):raise ValueError('early terminal before open checkpoint')
                    save(directory/'capture-open.json',{'schema_version':1,'status':'OPEN',
                        'binding':plan['open_binding'],'collection_token':plan['collection_token'],
                        'scenario_id':plan['scenario_id'],'controller_identity':plan['controller_identity'],
                        'supervisor_identity':report['supervisor_identity'],'points':opened,
                        'observed_monotonic_ns':time.monotonic_ns()})
            all_ended = True
            for name, process in wrappers.items():
                r = report['points'][name]
                elapsed = (time.monotonic_ns() - r['started_monotonic_ns']) / 1e9
                if process.returncode is not None:
                    record_end(r, process)
                    # GNU timeout may have enforced the same bound before our poll.
                    if r['stop_reason'] is None and elapsed >= plan['duration_limit_seconds']:
                        stderr = (directory / f'{name}.stderr').read_text(errors='replace')
                        if 'timeout: sending signal INT' in stderr:
                            r['stop_reason'] = 'duration_limit'
                    if process.returncode != 0 or r['stop_reason'] != 'duration_limit':
                        raise RuntimeError(f'{name}: wrapper exited early or failed ({process.returncode})')
                    continue
                all_ended = False
                if elapsed >= plan['duration_limit_seconds'] and r['stop_reason'] is None:
                    r['stop_reason'] = 'duration_limit'
                    # Groups are evidence only. pidfds target owned identities individually.
                    root = r['wrapper_identity']
                    subset = descendants_of(root, tracker)
                    tracker.send(signal.SIGINT, subset)
                if elapsed >= plan['duration_limit_seconds'] + plan['stop_grace_seconds']:
                    raise RuntimeError(f'{name}: forced_kill_required')
            if all_ended:
                break
            persist()
    except BaseException as exc:
        report['primary_error'] = f'{type(exc).__name__}: {exc}'
        if isinstance(exc, ProcessDiscoveryError):
            report['discovery_errors'].append(str(exc))
        report['artifact_errors'].extend(getattr(exc, '__notes__', []))
    finally:
        # Artifact/analysis/hash operations cannot bypass this independent cleanup.
        if tracker is not None:
            try:
                reaped_scan()
                if tracker.live():
                    tracker.send(signal.SIGINT)
                until = time.monotonic() + plan['stop_grace_seconds']
                if wrappers:
                    # Do not give an already-expired point a second grace window.
                    until = min(until, min(r['started_monotonic_ns'] / 1e9 +
                        plan['duration_limit_seconds'] + plan['stop_grace_seconds']
                        for name, r in report['points'].items() if name in wrappers))
                while tracker.live() and time.monotonic() < until:
                    reaped_scan(); time.sleep(.01)
                if tracker.live():
                    tracker.send(signal.SIGKILL)
                    if report['primary_error'] is None:
                        report['primary_error'] = 'surviving_descendants_required_SIGKILL'
                until = time.monotonic() + REAP_SECONDS
                while time.monotonic() < until:
                    reaped_scan()
                    if not tracker.live() and remaining_children() == [] and all(p.returncode is not None for p in wrappers.values()):
                        break
                    if tracker.live():
                        if report['primary_error'] is None:
                            report['primary_error'] = 'late_surviving_descendants_required_SIGKILL'
                        tracker.send(signal.SIGKILL)
                    time.sleep(.01)
                reaped_scan()
                report['remaining_processes'] = tracker.live()
                report['unreaped_child_pids'] = remaining_children()
                report['cleanup'] = ('PASS' if not tracker.live() and report['unreaped_child_pids'] == []
                                     and not report['discovery_errors']
                                     else 'INCOMPLETE')
            except BaseException as exc:
                report['cleanup_errors'].append(f'{type(exc).__name__}: {exc}')
            finally:
                tracker.close()
        for name, process in wrappers.items():
            record_end(report['points'][name], process)
        for stream in streams:
            try:
                stream.close()
            except OSError as exc:
                report['artifact_errors'].append(str(exc))
        report['terminal'] = True
        report['ended_utc'] = utc()
        report['status'] = ('COMPLETE' if report['primary_error'] is None and
                            not report['cleanup_errors'] and not report['artifact_errors'] and
                            report['cleanup'] == 'PASS' else 'INCOMPLETE')
        try:
            persist()
        except BaseException as exc:
            report['status'] = 'INCOMPLETE'
            report['artifact_errors'].append(f'terminal supervision receipt: {type(exc).__name__}: {exc}')
        try:
            print(json.dumps(report), flush=True)
        except OSError:
            pass
    return 0 if report['status'] == 'COMPLETE' else 2


def descendants_of(root, tracker):
    """Return the pinned wrapper and its known descendants (not a PGID kill)."""
    rows = tracker.live()
    owned = {process_key(root)}
    while True:
        added = {process_key(r) for r in tracker.records.values()
                 if process_key(r['ownership_parent']) in owned}
        if added <= owned:
            break
        owned.update(added)
    return [r for r in rows if process_key(r) in owned]


def finish_supervisor(process, control_fd, root, grace, discovery_errors=None):
    """Ask for cleanup, then bounded fallback against validated descendants."""
    errors = []
    if process is None:
        return errors
    if process.poll() is None:
        try:
            os.write(control_fd, b'X')
        except (BrokenPipeError, OSError):
            pass
    try:
        process.wait(timeout=grace + REAP_SECONDS + 2)
        return errors
    except subprocess.TimeoutExpired:
        errors.append('supervisor cleanup deadline exceeded')
    # The guardian is still alive, so ancestry is available even after wrapper loss.
    tracker = OwnedProcesses(root)
    def scan():
        try:
            tracker.scan()
        except Exception as exc:
            tracker.discovery_failed('fallback discovery scan', exc)
    try:
        scan(); tracker.send(signal.SIGINT)
        until = time.monotonic() + grace
        while tracker.live() and time.monotonic() < until:
            scan(); time.sleep(.01)
        tracker.send(signal.SIGKILL)
        until = time.monotonic() + REAP_SECONDS
        while tracker.live() and time.monotonic() < until:
            scan(); tracker.send(signal.SIGKILL); time.sleep(.01)
        if tracker.live():
            errors.append(f'capture descendants remain: {tracker.live()}')
        if discovery_errors is not None:
            discovery_errors.extend(tracker.discovery_errors)
        else:
            errors.extend(tracker.discovery_errors)
    finally:
        tracker.close()
    # Exact supervisor identity only; never signal a recycled PID or broad group.
    if same_process(root, process_identity(process.pid)):
        fd = os.pidfd_open(process.pid)
        try:
            if same_process(root, process_identity(process.pid)):
                signal.pidfd_send_signal(fd, signal.SIGKILL)
        finally:
            os.close(fd)
    try:
        process.wait(timeout=REAP_SECONDS)
    except subprocess.TimeoutExpired:
        errors.append('supervisor did not exit after SIGKILL')
    return errors


def guest_command(point, directory, scenario, seconds, packets, max_bytes, grace, config):
    """Render shell text only; canonical serial tooling owns any later access."""
    directory = str(directory)
    if not directory.startswith('/') or not NAME.fullmatch(scenario):
        raise ValueError('absolute guest output and safe scenario ID required')
    q = shlex.quote
    argv = ['sudo', '-n', '/usr/bin/prlimit', f'--fsize={max_bytes}:{max_bytes}',
            '/usr/bin/timeout', '--verbose', '--preserve-status', '--signal=INT',
            f'--kill-after={grace}s', f'{seconds}s', '/usr/bin/tcpdump', '-Z', 'kevin',
            '-i', point['interface'], '-s', '0', '-nn', '-U', '-c', str(packets),
            '-w', directory + '/capture.pcap', point['filter']]
    metadata = {'schema_version': 1, 'scenario_id': scenario, 'point': point,
                'duration_limit_seconds': seconds, 'packet_limit': packets,
                'file_byte_limit': max_bytes, 'stop_grace_seconds': grace,
                'argv': argv, 'configuration': config,
                'clock_uncertainty': 'UNMEASURED; record bounds before cross-host correlation'}
    # stdout/stderr and separate start/end/exit artifacts survive nonzero exits.
    return (f'export LC_ALL=C; umask 077; mkdir -- {q(directory)} || exit 1; '
            f'cd -- {q(directory)} || exit 1; '
            f"printf '%s\\n' {q(json.dumps(metadata, sort_keys=True))} > configuration.json; "
            "date -u +%Y-%m-%dT%H:%M:%S.%NZ > started-utc.txt; "
            "date +%s.%N > started-unix.txt; "
            f'{shlex.join(argv)} > tcpdump.stdout 2> tcpdump.stderr; capture_rc=$?; '
            "date -u +%Y-%m-%dT%H:%M:%S.%NZ > ended-utc.txt; "
            "date +%s.%N > ended-unix.txt; "
            "printf '%s\\n' \"$capture_rc\" > wrapper-exit-status.txt; "
            "sha256sum -- configuration.json *-utc.txt *-unix.txt wrapper-exit-status.txt "
            "tcpdump.stdout tcpdump.stderr capture.pcap > SHA256SUMS; hash_rc=$?; "
            "test \"$capture_rc\" -eq 0 && test \"$hash_rc\" -eq 0")


def seal(directory):
    report = {'schema_version': 2, 'status': 'INCOMPLETE', 'errors': [], 'sealed_files': []}
    entries = []
    try:
        paths = sorted(directory.iterdir())
    except OSError as exc:
        paths = []
        report['errors'].append(f'enumeration: {type(exc).__name__}: {exc}')
    for path in paths:
        if path.name in ('SHA256SUMS', 'sealing.json'):
            continue
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError('not a regular artifact')
            entries.append(f'{sha(path)}  {path.name}\n')
            report['sealed_files'].append(path.name)
        except Exception as exc:
            report['errors'].append(f'{path.name}: {type(exc).__name__}: {exc}')
    try:
        (directory / 'SHA256SUMS').write_text(''.join(entries))
        report['manifest_sha256'] = sha(directory / 'SHA256SUMS')
    except Exception as exc:
        report['errors'].append(f'SHA256SUMS: {type(exc).__name__}: {exc}')
    report['status'] = 'COMPLETE' if not report['errors'] else 'INCOMPLETE'
    try:
        save(directory / 'sealing.json', report)
    except Exception as exc:
        report['status'] = 'INCOMPLETE'
        report['errors'].append(f'sealing.json: {type(exc).__name__}: {exc}')
    return report


def record_end(receipt, process):
    if receipt.get('ended_utc'):
        return
    receipt['exit_status'] = process.returncode
    receipt['ended_utc'] = utc()
    receipt['ended_monotonic_ns'] = time.monotonic_ns()
    receipt['elapsed_seconds'] = (receipt['ended_monotonic_ns'] - receipt['started_monotonic_ns']) / 1e9
    receipt['time_semantics'] = 'execution-namespace process-launch and observed-exit bounds; packet timestamps are separate'


def required_artifacts(result):
    extra={'detached-start.json','capture-open.json'} if result.get('detached_launch') else set()
    return extra | {'collection.json', 'configuration.json', 'identity.json', 'supervision.json',
            'supervision-plan.json', 'supervisor.stdout', 'supervisor.stderr'} | {
                f'{name}.{suffix}' for name in result['selected_points']
                for suffix in ('pcap', 'stdout', 'stderr')}


def verify(directory, *, guest_postflight=None):
    """Offline integrity and independent capture completeness; never guest reads."""
    directory = Path(directory)
    hashes = {}
    for line in (directory / 'SHA256SUMS').read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9_.-]+)', line)
        if not match or match[2] in hashes:
            raise ValueError('unsafe or duplicate checksum entry')
        digest, name = match.groups()
        path = directory / name
        if path.is_symlink() or not path.is_file() or sha(path) != digest:
            raise ValueError(f'missing or changed evidence: {name}')
        hashes[name] = digest
    sealing = json.loads((directory / 'sealing.json').read_text())
    if (sealing.get('status') != 'COMPLETE' or sealing.get('errors') or
            sealing.get('manifest_sha256') != sha(directory / 'SHA256SUMS')):
        raise ValueError('incomplete sealing evidence')
    result = json.loads((directory / 'collection.json').read_text())
    required = required_artifacts(result)
    if not required <= hashes.keys():
        raise ValueError('incomplete evidence: missing collection, configuration or identity')
    config = json.loads((directory / 'configuration.json').read_text())
    report = json.loads((directory / 'supervision.json').read_text())
    location=result.get('execution_location','host')
    if location!='host':
        # A guest PID must never be checked against the host /proc namespace.
        # The exporter obtains this fresh read through canonical guest_read.
        if guest_postflight is None:raise ValueError('fresh guest namespace postflight required')
        identity_fields={'pid','start_ticks','uid','boot_id','state','ppid','pgid','sid'}
        def checked_identity(row):
            if (not isinstance(row,dict) or not identity_fields<=row.keys() or
                    any(type(row[k]) is not int or row[k]<0 for k in
                        ('pid','start_ticks','uid','ppid','pgid','sid')) or
                    row['pid']==0 or row['start_ticks']==0 or
                    not isinstance(row['state'],str) or len(row['state'])!=1 or
                    not isinstance(row['boot_id'],str) or not re.fullmatch(
                        r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',row['boot_id'])):
                raise ValueError('malformed guest process identity')
            return process_key(row)
        canonical={}
        for row in [report['supervisor_identity'],*report.get('processes',[])]:
            key=checked_identity(row)
            if key in canonical:raise ValueError('duplicate supervision identity')
            canonical[key]=row
        checks=guest_postflight.get('checks',[])
        if (guest_postflight.get('location')!=location or
            guest_postflight.get('collection_token')!=result.get('collection_token') or
            guest_postflight.get('boot_id')!=report['supervisor_identity']['boot_id'] or
            type(guest_postflight.get('observed_monotonic_ns')) is not int or
            guest_postflight['observed_monotonic_ns']<max(
                row.get('ended_monotonic_ns',0) for row in result['points'].values()) or
            guest_postflight.get('errors')!=[] or not isinstance(checks,list) or len(checks)!=len(canonical)):
            raise ValueError('incomplete or mismatched guest namespace cleanup proof')
        seen=set()
        for row in checks:
            if not isinstance(row,dict) or set(row)!={'expected','observed'}:
                raise ValueError('malformed guest cleanup row')
            key=checked_identity(row['expected']);trusted=canonical.get(key)
            # Full, type-sensitive equality: nested postflight fields are never
            # authority to redefine the supervision incarnation.
            if (key in seen or trusted is None or
                    json.dumps(row['expected'],sort_keys=True)!=json.dumps(trusted,sort_keys=True)):
                raise ValueError('guest expected identity differs from supervision')
            seen.add(key);observed=row['observed']
            if observed is not None:
                checked_identity(observed)
                if (set(observed)!=identity_fields or observed['pid']!=trusted['pid'] or
                        observed['boot_id']!=guest_postflight['boot_id']):
                    raise ValueError('guest process observation scope differs')
                if same_process(trusted,observed) and observed['state']!='Z':
                    raise ValueError('live canonical guest capture identity')
        remaining=[]
    else:remaining=remaining_processes(report)
    if (not report.get('terminal') or report.get('status') != 'COMPLETE' or
            report.get('collection_token') != result.get('collection_token') or
            report.get('scenario_id') != result.get('scenario_id') or
            result.get('supervisor_exit_status') != 0 or
            report.get('cleanup') != 'PASS' or result.get('cleanup') != 'PASS' or
            report.get('discovery_errors') or
            any(result.get('errors', {}).values()) or remaining or
            report.get('remaining_processes') or report.get('unreaped_child_pids')!=[]):
        raise ValueError('incomplete supervision, cleanup or terminal evidence')
    if result.get('schema_version') != 2 or not result.get('terminal') or result.get('status') != 'COMPLETE' or not result.get('points'):
        raise ValueError('collection is incomplete or failed')
    if not result.get('scenario_id') or set(result['points']) != set(result['selected_points']):
        raise ValueError('incomplete scenario or selected point accounting')
    for name, receipt in result['points'].items():
        needed = {f'{name}.pcap', f'{name}.stderr', f'{name}.stdout'}
        if not needed <= hashes.keys():
            raise ValueError('incomplete evidence: missing point artifacts')
        if receipt.get('status') != 'COMPLETE' or receipt.get('exit_status') != 0:
            raise ValueError('incomplete capture process')
        if receipt.get('point') != config['points'].get(name):
            raise ValueError('capture configuration identity differs')
        if receipt.get('stop_reason') != 'duration_limit' or not receipt.get('ended_utc'):
            raise ValueError('capture window is incomplete')
        if receipt['elapsed_seconds'] < result['duration_limit_seconds']:
            raise ValueError('capture window is short')
        stats = statistics((directory / f'{name}.stderr').read_text())
        summary = summarize(directory / f'{name}.pcap', max_bytes=result['file_byte_limit'],
                            max_packets=result['packet_limit'])
        if (receipt['pcap_sha256'] != summary['sha256'] or receipt['statistics'] != stats or
                stats['captured'] != summary['counts'].get('packets', 0) or
                stats['received'] is None or stats['kernel_dropped'] != 0 or
                stats['interface_dropped'] not in (None, 0)):
            raise ValueError('capture statistics, hashes or drops do not agree')
    return {'status': 'COMPLETE', 'scenario_id': result['scenario_id'],
            'points': sorted(result['points']), 'baseline_result': 'NOT_EVALUATED'}


class CaptureInterrupted(Exception):
    pass


def collect(config, names, directory, scenario, seconds, packets, max_bytes, grace, clock_note,
            *, guest_execution=None):
    limits(seconds, packets, max_bytes, grace)
    if not NAME.fullmatch(scenario) or not names or len(names) != len(set(names)):
        raise ValueError('unique capture points and safe scenario ID required')
    points = {name: config['points'][name] for name in names}
    location='host' if guest_execution is None else guest_execution['location']
    if (location not in ('host','xle','scada','drives') or
        any(p['location'] != location or not p['verified_in_handoff'] for p in points.values())):
        raise ValueError('collect accepts verified host points only; guest context requires separate review'
                         if guest_execution is None else 'capture location/verified point mismatch')
    directory = Path(directory).resolve()
    if guest_execution is None and directory.is_relative_to(ROOT):
        raise ValueError('evidence must be outside the source worktree')
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    result = {'schema_version': 2, 'scenario_id': scenario, 'selected_points': names,
              'execution_location':location,
              'started_utc': utc(), 'status': 'IN_PROGRESS', 'terminal': False, 'points': {},
              'duration_limit_seconds': seconds, 'packet_limit': packets,
              'file_byte_limit': max_bytes, 'stop_grace_seconds': grace,
              'clock_note': clock_note, 'functional_outcome': 'NOT RUN',
              'collection_token': os.urandom(16).hex(), 'cleanup': 'INCOMPLETE',
              'errors': {'primary': None, 'cleanup': [], 'discovery': [],
                         'artifacts': [], 'analysis': [], 'sealing': []}}
    if guest_execution is not None and guest_execution.get('launch') is not None:
        result['detached_launch']=guest_execution['launch']
        result['controller_identity']=process_identity(os.getpid())
        result['collection_token']=guest_execution['launch']['launch_token']
    process = root = None
    control_read = control_write = None
    streams, previous = [], {}
    cleaning = False
    def interrupted(signum, frame):
        if not cleaning:
            raise CaptureInterrupted(f'controller signal {signum}')
    def failure(category, exc):
        text = f'{type(exc).__name__}: {exc}'
        result['errors']['artifacts'].extend(getattr(exc, '__notes__', []))
        if category == 'primary':
            if result['errors']['primary'] is None:
                result['errors']['primary'] = text
        else:
            result['errors'][category].append(text)
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[sig] = signal.signal(sig, interrupted)
        # pidfd feature checks precede capture launch.
        probe = OwnedProcesses(process_identity(os.getpid())); probe.close()
        save(directory / 'configuration.json', config)
        save(directory / 'identity.json', identity(config) if guest_execution is None else guest_execution['identity'])
        if result.get('detached_launch'):
            save(directory/'detached-start.json',{'launch':result['detached_launch'],
                'controller_identity':result['controller_identity']})
        save(directory / 'collection.json', result)
        plan = {key: result[key] for key in ('scenario_id', 'collection_token',
                'duration_limit_seconds', 'file_byte_limit', 'stop_grace_seconds')}
        plan['directory'] = str(directory)
        if guest_execution is not None:plan['guest_runtime']=guest_execution['runtime']
        if result.get('detached_launch'):
            plan['open_binding']=result['detached_launch']['binding']
            plan['controller_identity']=result['controller_identity']
        plan['points'] = {name: {'point': point, 'argv': bounded_argv(
            point, directory / f'{name}.pcap', packets, seconds, grace)} for name, point in points.items()}
        save(directory / 'supervision-plan.json', plan)
        control_read, control_write = os.pipe()
        out = (directory / 'supervisor.stdout').open('wb'); streams.append(out)
        err = (directory / 'supervisor.stderr').open('wb'); streams.append(err)
        entry=([sys.executable,str(Path(__file__).resolve())] if guest_execution is None else guest_execution['entry'])
        process = subprocess.Popen([*entry, '_supervise',
                    str(directory / 'supervision-plan.json'), str(control_read)],
                    pass_fds=(control_read,), stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                    start_new_session=True)
        os.close(control_read); control_read = None
        root = process_identity(process.pid)
        result['supervisor_identity'] = root
        save(directory / 'collection.json', result)
        # A separate deadline also bounds a stalled independent supervisor.
        process.wait(timeout=seconds + 2 * grace + REAP_SECONDS + 5)
    except (Exception, KeyboardInterrupt) as exc:
        failure('primary', exc)
        if isinstance(exc, ProcessDiscoveryError):
            failure('discovery', exc)
    finally:
        cleaning = True
        try:
            try:
                result['errors']['cleanup'].extend(finish_supervisor(
                    process, control_write, root, grace, result['errors']['discovery']))
            except BaseException as exc:
                failure('cleanup', exc)
            for fd in (control_read, control_write):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError as exc:
                        failure('cleanup', exc)
            for stream in streams:
                try:
                    stream.close()
                except OSError as exc:
                    failure('artifacts', exc)
            if process is not None:
                try:
                    report = json.loads((directory / 'supervision.json').read_text())
                    if (not report.get('terminal') or report.get('collection_token') != result['collection_token']):
                        raise ValueError('missing or mismatched terminal supervision evidence')
                    result['points'] = report['points']
                    result['cleanup'] = report['cleanup']
                    result['supervisor_exit_status'] = process.returncode
                    if report['status'] != 'COMPLETE' or process.returncode != 0:
                        failure('primary', RuntimeError(report.get('primary_error') or 'supervisor failed'))
                    result['errors']['cleanup'].extend(report['cleanup_errors'])
                    result['errors']['discovery'].extend(report.get('discovery_errors', []))
                    result['errors']['artifacts'].extend(report['artifact_errors'])
                    live = remaining_processes(report)
                    if live:
                        result['cleanup'] = 'INCOMPLETE'
                        failure('cleanup', RuntimeError(f'capture processes remain: {live}'))
                except BaseException as exc:
                    failure('artifacts', exc)
            else:
                result['cleanup'] = 'NOT_STARTED'
            # Parsing and hashing are strictly after bounded process cleanup.
            for name, r in result['points'].items():
                r['status'] = 'INCOMPLETE'
                try:
                    r['statistics'] = statistics((directory / f'{name}.stderr').read_text(errors='replace'))
                    summary = summarize(directory / f'{name}.pcap', max_bytes=max_bytes,
                                        max_packets=packets, allow_incomplete=True)
                    r['pcap_sha256'] = summary['sha256']
                    r['packets'] = summary['counts'].get('packets', 0)
                    r['file_complete'] = summary['file_complete']
                    stats = r['statistics']
                    if (r['exit_status'] == 0 and r['stop_reason'] == 'duration_limit' and
                            r['elapsed_seconds'] >= seconds and summary['file_complete'] and
                            stats['captured'] == r['packets'] and stats['received'] is not None and
                            stats['kernel_dropped'] == 0 and stats['interface_dropped'] in (None, 0)):
                        r['status'] = 'COMPLETE'
                except BaseException as exc:
                    r['analysis_error'] = f'{type(exc).__name__}: {exc}'
                    failure('analysis', exc)
            result['ended_utc'] = utc()
            result['terminal'] = True
            for name in sorted(required_artifacts(result) - {'collection.json'}):
                path = directory / name
                if not path.is_file() or path.is_symlink():
                    failure('artifacts', RuntimeError(f'missing required artifact: {name}'))
            result['status'] = ('COMPLETE' if not any(result['errors'].values()) and
                result['cleanup'] == 'PASS' and set(result['points']) == set(names) and
                all(r['status'] == 'COMPLETE' for r in result['points'].values()) else 'INCOMPLETE')
            try:
                save(directory / 'collection.json', result)
            except BaseException as exc:
                result['status'] = 'INCOMPLETE'; failure('artifacts', exc)
            if result['status'] != 'COMPLETE':
                try:
                    save(directory / 'failure.json', result)
                except BaseException as exc:
                    failure('artifacts', exc)
            sealing = seal(directory)
            if sealing['errors']:
                result['status'] = 'INCOMPLETE'
                result['errors']['sealing'].extend(sealing['errors'])
                # Preserve hash errors in the terminal result, then reseal that failure.
                try:
                    save(directory / 'collection.json', result)
                    save(directory / 'failure.json', result)
                except BaseException as exc:
                    failure('artifacts', exc)
                sealing = seal(directory)
            result['sealing'] = sealing
            if sealing['status'] != 'COMPLETE':
                result['status'] = 'INCOMPLETE'
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    return result


def remaining_processes(report):
    rows = report.get('processes', [])
    return [row for row in rows if same_process(row, current := process_identity(row['pid']))
            and current['state'] != 'Z']


def inspect(directory, wait=0):
    """Read-only failure evidence; never repair, reseal or promote a partial run."""
    until = time.monotonic() + wait
    report, collection, errors = {}, {}, []
    while True:
        for name, target in (('collection.json', collection), ('supervision.json', report)):
            try:
                target.clear(); target.update(json.loads((directory / name).read_text()))
            except (OSError, ValueError) as exc:
                errors.append(f'{name}: {exc}')
        if report.get('terminal') or time.monotonic() >= until:
            break
        time.sleep(.05)
    live = remaining_processes(report)
    return {'status': 'INCOMPLETE', 'acceptance': 'NOT_EVALUATED',
            'collection_terminal': collection.get('terminal', False),
            'collection': collection, 'supervision': report,
            'remaining_processes': live, 'read_errors': sorted(set(errors)),
            'warning': 'Failure inspection never constitutes acceptance or finalizes a dead controller receipt'}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '_supervise':
        with Path(sys.argv[2]).open() as stream:
            plan = json.load(stream)
        return supervisor(plan, int(sys.argv[3]))
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('plan', 'collect'):
        p = sub.add_parser(action)
        p.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
        p.add_argument('--point', action='append', required=True)
        p.add_argument('--output', type=Path, required=True)
        p.add_argument('--scenario-id', required=True)
        p.add_argument('--seconds', type=float, default=10)
        p.add_argument('--packets', type=int, default=5000)
        p.add_argument('--max-bytes', type=int, default=8 * 1024 * 1024)
        p.add_argument('--grace', type=float, default=3)
        if action == 'collect':
            p.add_argument('--clock-note', required=True)
    p = sub.add_parser('verify')
    p.add_argument('directory', type=Path)
    p = sub.add_parser('inspect')
    p.add_argument('directory', type=Path)
    p.add_argument('--wait', type=float, default=0)
    args = parser.parse_args()
    try:
        if args.action == 'inspect':
            if not 0 <= args.wait <= 620:
                raise ValueError('inspection wait must be 0..620 seconds')
            result = inspect(args.directory, args.wait)
        elif args.action == 'verify':
            result = verify(args.directory)
        else:
            config = load_config(args.config)
            limits(args.seconds, args.packets, args.max_bytes, args.grace)
            if not NAME.fullmatch(args.scenario_id):
                raise ValueError('safe scenario ID required')
            if args.action == 'collect':
                result = collect(config, args.point, args.output, args.scenario_id, args.seconds,
                                 args.packets, args.max_bytes, args.grace, args.clock_note)
            else:
                result = {'status': 'PLANNED_ONLY', 'commands': {}}
                for name in args.point:
                    point = config['points'][name]
                    if not point['verified_in_handoff']:
                        result['commands'][name] = {'status': 'BLOCKED: interface/route not verified'}
                    elif point['location'] == 'host':
                        result['commands'][name] = {'tcpdump_argv': bounded_argv(
                            point, args.output / f'{name}.pcap', args.packets, args.seconds, args.grace),
                            'limits_applied_by': 'collect; do not run this bare tcpdump argv'}
                    else:
                        result['commands'][name] = {'status':'UNSUPPORTED_LEGACY_GUEST_PATH',
                            'reason':'GNU-timeout-only renderer has no accepted independent guest lifecycle',
                            'replacement':'m2_guest_capture.py build/plan; separately reviewed pinned bundle and export'}
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0 if result['status'] in ('COMPLETE', 'PLANNED_ONLY') else 2
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(2, f'capture preparation/collection failed: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
