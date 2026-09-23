# XLe process restart recovery

## Contract and limits

The PLC's two slot rows remain authoritative. At startup XLe reconstructs
`(run epoch, run nonce, token, serial, scanner sequence)` and the stable package ID
`l1-EPOCH-NONCE-TOKEN-SERIAL` from each occupied row. A scanned row (state 2) gets
a fresh ASX request ID. XLe re-reads the nonce and complete slot identity
before committing a decision; the PLC checks them again. An accepted route
(state 3 or 4) is **not commanded again**. XLe waits for the physical outcome.
A terminal row (state 5, 6, or 7) is logged and released even if the new XLe
process never observed its scan.

The canonical outcome log is SQLite at
`~/sorter-services/xle-outcomes.sqlite3` on SCADA by default. A unique key
on the five-part PLC identity gives one durable outcome row across XLe
restarts. XLe commits the row with SQLite synchronous FULL before printing
`plc_outcome`. If it crashes between commit and stdout, the journal still
contains the outcome, though stdout can miss it. On restart XLe emits
`plc_outcome_already_recorded` and releases the terminal slot. Other progress
events may repeat. The generated journal is excluded from Git.

On a scanner run nonce change, XLe abandons old tasks, cancels queued ASX
work, and discards old answers. A worker already making an HTTP call may
finish, but its answer cannot command the new run. XLe has at most two
pending slots and two ASX workers. `--packages N` accepts any positive finite
count, including counts above two; `--packages 0` serves continuously.
`--multi --packages 1` runs a one-package recovery probe through the slot
protocol. The default one-package command still uses the prior 500..512 path.

Recovery has these boundaries:

- An ASX request ID and uncommitted decision are lost in a crash. XLe starts
  a new lookup if the package is still scanned and upstream of the first
  divert. A late command is rejected by the PLC, leaving recirculation.
- An accepted route and terminal outcome survive an XLe crash in the PLC
  slot. Once the slot is released, the journal retains the outcome. Aggregate
  trailer counters alone cannot reconstruct per-package identity.
- A PLC reset clears occupied slots and modeled packages. XLe can discard
  stale requests but cannot recover package state erased by that reset.
- A cold PLC process restart can reuse its private nonce and token counters.
  XLe now commits a new run epoch in the same SQLite journal before the PLC
  accepts external multi mode. See [RUN_IDENTITY.md](RUN_IDENTITY.md) for the
  handshake, fail-closed behavior, and live cold-restart evidence.

## Verification before the run-epoch change

Before the fix, focused host reproductions starting XLe against a state 3
slot and a state 5 slot both ended with `multi outcome timeout: 0/1` and no
release. After the fix, `python3 -m unittest discover -s tests -p 'test_*.py'`
passed 20 tests. They cover scanned, accepted, and terminal slot restarts;
one journaled outcome through a crash before release; and a nonce change
while an ASX request is in flight. The nonce-change case used a controlled
Modbus fake and was not repeated live. `bash tests/run_first_package.sh` passed
the default-route, scanner-reset, legacy one-package, and multi-package PLC
cases.

Live tests ran only `plc`, `drives`, `fw`, and `scada`, with the existing
isolated networks and SCADA-to-PLC Modbus rule. The restart runner killed
XLe processes and read the PLC slot rows over Modbus:

| Restart point | Package and PLC evidence | Restart behavior |
| --- | --- | --- |
| Route accepted | `l1-5-1-1`, token 1, barcode 6001, state 3, command 12 before crash | New XLe logged `plc_recovered`, then state 5/trailer 2, scan tick 223, accept 225, divert 256; no second route command; one journal row; slot released. |
| Terminal recirculation | `l1-6-1-1`, token 1, barcode 6001, state 6/reason 1 before restart | XLe logged one recirculated outcome (scan tick 223, divert 256) and was killed before release. Another instance logged `plc_outcome_already_recorded`, issued release command 14, and left one journal row. No route or trailer load. |

The finite live batch `xle.py --packages 3` used seed 137 **without a PLC
reset between packages**. Slot 0 was released after package 1 and reused by
package 3 with a new token:

| Package ID | Slot/token | Barcode | ASX → actual trailer | PLC command | Scan → first divert | Acceptance margin |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `l1-7-1-1` | 0 / 1 | 6001 | 2 → 2 | 15 | 3300 ms | 3100 ms |
| `l1-7-2-2` | 1 / 2 | 5002 | 5 → 5 | 16 | 3300 ms | 3100 ms |
| `l1-7-3-3` | 0 / 3 | 2003 | 8 → 8 | 18 | 3300 ms | 3100 ms |

PLC Modbus trailer counters were `[0,1,0,0,1,0,0,1,0]`, induction was three,
and recirculation was zero. ASX request and response IDs matched the XLe
scan, command, outcome, and release events for each package. The legacy
one-package live path also passed: barcode 6001 loaded trailer 2, state
3/reason 0, with 100 ms from scan to command acceptance. Continuous
`--packages 0` was not exercised live.

After the tests, a Modbus read showed sorter run false, enable coils
881..887 true, external coils 914/915 false, setpoints
`[200,200,200,233,233,233,2,14,14,14,30]`, seed 137, and scanner reset
state ready. Both live runners restore operator settings in `finally`.
