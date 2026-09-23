# Lane 1 XLe liveness and operator recovery

## Intended behavior

The PLC's two slot rows and the journal-backed run epoch remain authoritative.
In multi mode XLe writes a heartbeat every 0.5 s: epoch words at 565–566,
then a changed sequence at 567. The PLC echoes a valid sequence at 573 and
publishes its age in scans at 568. It waits for the first valid heartbeat
before induction. If no valid change arrives for more than 50 PLC scans
(about five seconds at the tested 100 ms scan), state 569 latches **1: XLe
heartbeat lost**. New lane 1 inductions and new route commands are rejected.
The sorter and belts keep running so accepted routes finish at their assigned
trailer; scanned packages with no accepted route recirculate with PLC reason
1. Terminal slot rows remain until XLe returns, journals each outcome, and
releases them. The two-slot bound is unchanged.

The alarm does not clear when heartbeat traffic resumes:

| State at PLC register 569 | Meaning | Operator/XLe action |
| ---: | --- | --- |
| 0 | Healthy | Valid heartbeat and route commands permitted |
| 1 | Lost | Coil 916 **Acknowledge XLe** marks it seen |
| 2 | Acknowledged | Coil 917 **Retry XLe** starts recovery only with a fresh heartbeat |
| 3 | Retry, verifying | XLe checks SQLite `PRAGMA quick_check` and the active epoch against its journal, then writes epoch words 570–571 and a new commit sequence at 572 |

On Retry, the PLC records the proof sequence already present, so a stale
proof cannot clear state 3. A new proof with the active epoch clears the
alarm to 0. Another heartbeat expiry during retry relatches state 1. The
PLC accepts terminal-slot release commands during the fault, provided their
full package and epoch identity matches; route commands remain blocked. A
failed journal or identity check requests the existing external fault at
register 561 and stops the sorter. Acknowledge, Retry, and fault clear are
separate transitions. Coils 916–917 and registers 565–573 are appended;
existing one-package and default-route interfaces are unchanged.

The OPC UA server exposes `Process/Status/XLeHeartbeatAge` and
`XLeLivenessState`, with writable `XLeFaultAck` and `XLeRetry`. The HMI shows
the named alarm while states 1 or 2 are active, an acknowledged label after
Acknowledge, and a verifying label in state 3. It clears only when PLC state
returns to 0.

## Scanner source comparison before guest changes

`/home/kevin/scanner.py` on drives SHA-256:
`f5b32d2c2a7d83e318c57a66b3cd0b3ef3306be594f07f328c534600c2ac1419`.
Repository `devices/scanner.py` SHA-256:
`5f6f2d48b9424327e88b558a67924efe5bfc70fb061c8c13baa76cc18facd402`.
A read-only `diff -u` against the previously copied repository fixture at
`/home/kevin/scanner_epoch_fixture.py` found exactly four additions in the
repository version: a docstring paragraph for `SORTER_REPEAT_BARCODE=1`,
`import os`, an initialization flag reading that environment variable, and a
branch that gives serials after the first the first barcode with duplicate
status. No other scan/reset logic differs. The deployed `scanner@tunnel1`
service uses `/home/kevin/scanner.py` without that fixture. **All live tests
in this change used that deployed version**; no scanner source or service
was changed. Python scanner unit tests use the repository version.

## Verification

The host commands were:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/run_first_package.sh
python3 -m py_compile services/xle.py scada/hmi_ua.py scada/opcua_server.py tests/live_xle_liveness.py
```

The Python suite passed 26 tests, including no route while liveness is
faulted and a fresh proof after Retry. The compiled PLC suite passed default,
one-package, scanner-reset, and all multi-package cases; its heartbeat case
showed zero induction before the first package, ignored Retry before
Acknowledge and before XLe returned, then cleared only after a fresh proof.

Live tests ran over the existing SCADA-to-PLC Modbus path with only `plc`,
`drives`, `fw`, and `scada` powered. The PLC program compiled successfully on
the guest. `tests/live_xle_liveness.py` ran from SCADA with seed 137 and a
temporary SQLite journal that survived each XLe process stop/restart. The
runner restored settings in `finally`. Results:

| Case | At XLe stop | PLC fault and safe outcome | Recovery |
| --- | --- | --- | --- |
| Before first package | Valid epoch 1 and heartbeat, no occupied slots | State 1 after heartbeat age >50; induction 0, no trailer load | State 2 persisted after heartbeat returned; Retry and proof sequence 1 cleared state 0; journal 0 rows |
| Two accepted routes | Slot 0 token 1 terminal, command 23; slot 1 token 2 in motion with command 24 | State 1, induction held at 2; trailer 2 and trailer 5 each loaded once | State 2 persisted after return; Retry and a fresh proof cleared state 0; XLe issued no new route, journaled both outcomes once, released both slots |
| One undecided | Slot 0 token 1 terminal with command 27; slot 1 token 2 scanned but command 0 | State 1; first loaded trailer 2, second recirculated with reason 1 and no route | State 2 then proof cleared state 0; journal contains exactly two outcomes; both slots released |

The final accepted-route case recorded package IDs `l1-1-10-1-1` and
`l1-1-10-2-2`, barcodes 6001/5002, PLC terminal states 5/5, actual trailers
2/5, and counters `[0,1,0,0,1,0,0,0,0]`. The undecided case recorded IDs
`l1-1-12-1-1` and `l1-1-12-2-2`, terminal states 5/6, actual trailers 2/0,
one recirculation, and no second route command. Both scanner-to-divert
intervals were 3300 ms. The ASX decision for the second undecided package
was deliberately delayed by the [test plan](tests/liveness_sort_plan.json);
XLe was stopped while the slot was still in state 2, before a fallback
event. These are actual PLC slot rows, trailer counters, XLe event records,
and SQLite journal rows read by the SCADA runner.

The rendered Firefox HMI showed `XLE HEARTBEAT LOST`, then
`XLE HEARTBEAT LOST — ACKNOWLEDGED` after its Acknowledge button was clicked.
Its API still reported state 2 and heartbeat age 1 after XLe returned.
Clicking Retry led to a cleared alarm. Screenshots:
[lost](tests/artifacts/xle-heartbeat-lost.png),
[acknowledged](tests/artifacts/xle-heartbeat-acknowledged.png),
[cleared](tests/artifacts/xle-heartbeat-cleared.png).
The `agent-browser` CLI was unavailable, so the installed headless Firefox
and WebDriver were used for the rendered check, through the existing serial
HMI proxy. No bridge address or firewall rule changed.

An initial occupied-slot run timed out because a one-second terminal hold
released slot 0 before the observation condition was met. A second run
displayed and cleared the HMI alarm but its verifier missed a terminal row
after XLe released it. Neither was counted as a passing slot/journal test;
the final verifier held rows for observation and captured both physical
terminal rows before restarting XLe. A later check found that restoring the
normal induction interval after a long HMI interaction could induct a third
test package during teardown. The verifier now holds the interval at 32,000
scans, checks the observed count is exactly two, and performs a stopped PLC
reset after recording the journal and counter evidence. The final accepted
and undecided reruns both exited 0. Their cleanup left zero inducted and
zero occupied belt cells. No live journal-corruption injection or full VM
power cycle was performed.

Final Modbus read: sorter run false; enable coils 881–887 true; external
coils 914/915 false; setpoints
`[200,200,200,233,233,233,2,14,14,14,30]`; seed 137; induction count 0
after cleanup reset; both slot tokens 0; zero occupied belt cells.
`opcua-server` and `sorter-hmi` services were active. The scanner services
were not changed. `fw` and `scada` were returned to their initial shut-off
state after testing.
