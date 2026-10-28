# Contributing

Small, focused changes are easier to review in this range because PLC logic,
Python services, deployment copies, and dated evidence may describe different
revisions. Start with the [documentation guide](docs/README.md) and state
which source or contract your change affects.

## Choose the right source

- [`Sorter.st`](Sorter.st) is the canonical PLC Structured Text source.
  [`gen_sorter.py`](gen_sorter.py) also contains Structured Text despite its
  `.py` suffix; do not assume it is a Python generator. [`sorter.st`](sorter.st)
  is legacy. Identify the intended source and deployed binding before
  proposing a PLC change.
- [`devices/`](devices/) holds the plant, VFD, and scanner Python sources;
  [`services/`](services/) holds XLe and ASX; [`scada/`](scada/) holds the
  OPC UA server and HMI. [`deploy/`](deploy/) contains deployment copies,
  unit files, policy, and the [manifest](deploy/deployment_manifest.json).
- Keep dated reports and captured evidence intact. Describe later source or
  guest differences separately rather than rewriting an older result as if
  it were measured again.

## Prepare a change

1. Explain the behavior or documentation gap and keep the patch narrow.
2. Use the [Python 3.11 host setup](TESTING.md) and run checks relevant to
   the changed files. Record the exact commands, results, and any checks
   that were not run. Host tests do not establish a deployed range result.
3. For code or deployment changes, identify the source version, applicable
   manifest entries, and whether any guest copy was actually changed.
   Keep credentials, private guest environments, and raw private artifacts
   out of the repository and review text.
4. In a pull request, summarize the behavior, scope, verification, and
   runtime/evidence impact. Preserve historical failures and limitations.

Guest, PLC, firewall, service, capture, and recovery actions require a
separate bounded operator-approved procedure. A contribution or host test
does not itself authorize live range work.
