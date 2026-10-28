# Testing

## Host unit tests

From the repository root, create a Python 3.11 virtual environment before
installing dependencies so `pip` does not write to the system Python:

```sh
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python -m unittest discover -s tests -p 'test_*.py' -v
```

The dependency file is [requirements-dev.txt](requirements-dev.txt). CI uses
Python 3.11 and runs the same install and test commands on pushes and pull
requests; see [.github/workflows/host-tests.yml](.github/workflows/host-tests.yml).

## Not covered by CI

A passing host run means the host-side logic passed. It does not mean the range works.

- tests/run_first_package.sh needs the matiec compiler to build the C harnesses.
- live_*, run_*_host.py, browser_*, and network reachability scripts need the running
  lab guests. Follow their documented preflight and restoration procedures.
- Deployment preflight (tests/deployment_preflight.py --live) needs logged-in guests.

Record commit, date, exact command results, and VM states for any live run separately.
Do not reuse old check counts as results of a new run.

Return to the [repository overview](README.md) or the
[documentation guide](docs/README.md).
