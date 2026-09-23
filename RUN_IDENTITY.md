# Durable identity for lane 1 multi-package mode

## Risk reproduced from fd2e9d8

The PLC's private `reset_nonce` and `token_next` start at zero on a cold
runtime restart. With seed 137, the first package of two cold runs has nonce
1, slot token 1, scanner sequence 1, serial 1, and barcode 6001. The old
SQLite key omitted any boot identity. A controlled reproduction using the old
`OutcomeJournal` printed:

```text
first cold run True
same-seed cold restart False
journal row [('1:1:1:1', '{"actual_trailer": 2}')]
```

The second outcome was silently ignored. An old ASX answer could also match a
new row if nonce and slot identity were reused while XLe stayed running.

## Protocol

XLe allocates a monotonically increasing run epoch in its SQLite `run_epochs`
table and commits it with `synchronous=FULL` before offering it to the PLC.
An epoch is a positive number below 900 million, encoded as two base-30000
words. Exhaustion is an explicit error. The outcome key is now
`epoch:nonce:token:serial:scanner_sequence`; package ID is
`l1-EPOCH-NONCE-TOKEN-SERIAL`. ASX receives both that ID and `run_epoch`, plus
its per-lookup UUID `request_id`. XLe accepts a response only when the UUID
and package ID match, and rechecks epoch, nonce, and slot row before command.
The PLC checks the command's epoch and full slot identity again. XLe cancels
pending work on either epoch or nonce change. The two-slot limit remains.

| PLC holding register | Meaning |
| --- | --- |
| 555–556 | XLe offered epoch, low and high base-30000 words |
| 557 | XLe offer commit ID, written last |
| 558–559 | PLC active epoch, low and high words |
| 560 | PLC echo of processed offer ID |
| 561 | PLC external identity fault: 0 ready, 1 no identity, 2 XLe fault |
| 562–563 | XLe command epoch, written before the existing 520–528 command |
| 564 | XLe fault request: 2 fail closed, 0 clear |

On PLC initialization or operator reset, the active epoch clears, and the
PLC ignores the offer commit ID already present at startup. The PLC accepts
a changed, nonzero offer only while stopped and after scanner reset. Until
then, register 561 is 1 and multi-mode lane 1 induction is inhibited. XLe
stops the sorter before offering an identity and waits at most two seconds
for the matching echo and active value. A journal that cannot be opened or
written, an active PLC epoch absent from the journal, or a rejected offer
stops the sorter, requests fault 2, emits `external_sort_fault`, and exits.
The operator should resolve the journal/PLC mismatch while stopped; a normal
PLC reset then permits a fresh handshake. XLe never adopts an unknown active
epoch. Existing single-package registers 500–512 and the default route are
unchanged.

The journal is part of the identity authority: preserve or back it up along
with this deployment. If both PLC state and the journal are lost, old package
identities cannot be reconstructed. A missing journal while a PLC epoch is
active is detected and fails closed. SQLite's committed epoch counter bounds
nonreuse to 899,999,999 runs per intact journal.

## Verification

Commands on the host:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/run_first_package.sh
```

The Python suite passed 24 tests, including same-seed cold restart with two
distinct journal outcomes, an unknown active epoch stopping the sorter with
fault 2, unreadable journal fail-closed handling, and an old ASX answer after
epoch change. The PLC suite passed the
default first-package path, scanner reset faults, one-package external cases,
and all four multi-package cases. Its multi fixture proves zero induction and
fault 1 before identity provisioning, then rejects a command with a wrong
epoch. The final suites and post-commit whitespace check are recorded with
the commit.

Live tests used only `plc`, `drives`, `fw`, and `scada`, with their existing
isolated bridges and firewall rules. Source was copied by serial console;
the PLC compiled successfully, then its runtime was restarted. On SCADA,
`~/opcua/bin/python ~/sorter-services/live_xle_epoch.py first` ran against
the existing default ASX plan and journal path. The PLC runtime was cold
restarted without deleting that journal, then the same command with `second`
ran. Both used seed 137:

| Phase | Epoch | Nonce / token / serial / scan | Barcode | ASX decision | PLC outcome | Journal rows |
| --- | ---: | --- | ---: | ---: | --- | ---: |
| First | 1 | 1 / 1 / 1 / 1 | 6001 | trailer 2 | trailer 2, state 5, command 1, scan→divert 3200 ms | 0→1 |
| Cold restart | 2 | 1 / 1 / 1 / 1 | 6001 | trailer 2 | trailer 2, state 5, command 1, scan→divert 3300 ms | 1→2 |

The IDs were `l1-1-1-1-1` and `l1-2-1-1-1`. Both scanner/ASX request IDs,
XLe commands, terminal PLC slot rows, trailer 2 counters, and the two unique
SQLite keys were checked. This is a cold PLC **process** restart, not a full
VM power cycle; the PLC's nonce and token reset proves the collision case.

For the bounded ten-package run, the drives VM's deployed scanner source
had a different checksum from the repository version and lacked the optional
repeat fixture. Its service source was left untouched. Only
`scanner@tunnel1` was temporarily stopped; the repository scanner ran from
a separate test path with `SORTER_REPEAT_BARCODE=1`. It was stopped and the
original service restarted afterward. The SCADA command was
`~/opcua/bin/python ~/sorter-services/live_xle_epoch.py batch`, using the
editable [test plan](tests/epoch_batch_plan.json). The scanner log reported
sequences and serials 1–10, all barcode 6001 (status 4 for 2–10). ASX plan
overrides routed by barcode **and serial**, with a 1200 ms delay on serial 6.
The plan's answer arrived too late for XLe's 800 ms timeout.

| Serial / token | Slot | ASX plan | PLC command | PLC actual / reason | Scan→divert |
| ---: | ---: | ---: | ---: | --- | ---: |
| 1 | 0 | 2 | 3 | trailer 2 / 0 | 3300 ms |
| 2 | 1 | 5 | 4 | trailer 5 / 0 | 3200 ms |
| 3 | 0 | 8 | 6 | trailer 8 / 0 | 3300 ms |
| 4 | 1 | 2 | 9 | trailer 2 / 0 | 3300 ms |
| 5 | 0 | 5 | 10 | trailer 5 / 0 | 3300 ms |
| 6 | 1 | 8, late | none | recirculated / 1 | 3300 ms |
| 7 | 0 | 8 | 14 | trailer 8 / 0 | 3300 ms |
| 8 | 1 | 2 | 15 | trailer 2 / 0 | 3300 ms |
| 9 | 0 | 5 | 18 | trailer 5 / 0 | 3300 ms |
| 10 | 1 | 8 | 19 | trailer 8 / 0 | 3300 ms |

This was epoch 3, nonce 2. All ten had unique package IDs
`l1-3-2-TOKEN-SERIAL` and matching scanner sequence, barcode, ASX request,
XLe scan/decision or fallback, PLC command (when applicable), terminal row,
and journal outcome. SQLite outcome rows increased 2→12. All ten terminal
slots were released; trailer counters were `[0,3,0,0,3,0,0,3,0]`, with one
recirculation and induction count ten. The two slots alternated across ten
tokens, demonstrating repeated reuse without a PLC reset. The decision
timeout again fit the observed 3200–3300 ms scan-to-divert interval at the
tested belt settings.

The runner restored the original enable coils and setpoints after every
phase. Final Modbus read: sorter run false; enables 881–887 true; external
mode 914/915 false; setpoints
`[200,200,200,233,233,233,2,14,14,14,30]`; seed 137; both slot token
registers zero. All three scanner services were active. The scanner,
conveyors, ASX, and trailer physics remain simulated; SCADA-to-PLC Modbus,
serial deployment, and the VM zone path were live. A full VM power-cycle
replay and live journal-corruption fault injection were not performed; the
same-seed process restart and the focused fail-closed tests cover those
specific software paths.
