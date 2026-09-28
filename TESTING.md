# Testing

## Host unit tests

Run from the repository root with Python 3.11:

    pip install -r requirements-dev.txt
    python3 -m unittest discover -s tests -p 'test_*.py'

CI runs the same command on every push and pull request
(.github/workflows/host-tests.yml).

## Not covered by CI

A passing host run means the host-side logic passed. It does not mean the range works.

- tests/run_first_package.sh needs the matiec compiler to build the C harnesses.
- live_*, run_*_host.py, browser_*, and network reachability scripts need the running
  lab guests. Follow their documented preflight and restoration procedures.
- Deployment preflight (tests/deployment_preflight.py --live) needs logged-in guests.

Record commit, date, exact command results, and VM states for any live run separately.
Do not reuse old check counts as results of a new run.
