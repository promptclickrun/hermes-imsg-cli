# Contributing

Thanks for considering a contribution to `hermes-imsg-cli`. This is a small,
focused Hermes gateway platform plugin; the bar for changes is correctness,
tests, and privacy/security hygiene over feature breadth.

## Before you start

- Read `README.md` for the architecture/module boundary and the
  [safety invariant](README.md#safety-invariant): inbound replies always
  route to the exact Messages chat rowid, never a guessed GUID or handle.
- Read `SECURITY.md` for the properties every change must preserve.
- Check `docs/ARCHITECTURE.md` for module responsibilities and non-negotiable
  invariants before touching `adapter.py`, `cli.py`, `client.py`, or
  `rules.py`.

## Development setup

```bash
git clone https://github.com/promptclickrun/hermes-imsg-cli
cd hermes-imsg-cli
python3 -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
```

Some tests exercise the Hermes-independent pure modules (`cli.py`,
`rules.py`, `config.py`) and run anywhere. Adapter/registration tests
`pytest.importorskip("gateway")` and are skipped unless a Hermes Agent
checkout is on `PYTHONPATH`:

```bash
HERMES_SRC=/path/to/hermes-agent \
  PYTHONPATH="/path/to/hermes-agent:$PWD" python -m pytest -q
# or, if you have a local Hermes install:
scripts/validate.sh
```

## Making a change

1. Open an issue first for anything beyond a small, obvious fix.
2. Keep pure logic (`cli.py`, `rules.py`, `config.py`) free of `gateway`/
   `hermes_cli` imports so it stays testable without a Hermes checkout.
3. Never introduce a code path where a caller-supplied value (metadata,
   RPC/tool arguments, event fields) can select the destination chat for a
   reply. Replies route by the trusted rowid only.
4. Any new subprocess call goes through the single choke points in `cli.py`
   (`run_imsg` / `run_imsg_rpc`) so timeout and terminate-then-kill cleanup
   stay centralized.
5. Any new log statement must not include raw watch lines, raw message
   text/handles, or unredacted paths/GUIDs — run it through
   `rules._redact()` or omit the content entirely.
6. Add or update tests for behavior you change, including a regression test
   for any bug fix. Tests must never perform a live send, poll mutation,
   reaction, or read against a real Messages account — mock the subprocess
   boundary (`ImsgAdapter._run_imsg` / `_run_imsg_rpc`, or `ImsgClient`'s
   `runner`/`rpc_runner`).
7. Use only synthetic example data: phone numbers from the NANP reserved
   fictional range `+1 202-555-01xx` (`+12025550100`–`+12025550199`),
   generic chat rowids, generic "Family"/"Owner" style labels instead of
   real names, and `/path/to/...` rather than a real home directory.

## Running checks locally

```bash
python -m pytest -q
python -m compileall -q hermes_imsg_cli tests scripts
python scripts/privacy_scan.py
git diff --check
python -m pip wheel . --no-deps --no-build-isolation -w /tmp/hermes-imsg-wheel
```

All of the above (except the wheel build) also run in CI
(`.github/workflows/ci.yml`) on macOS, since some tests are platform-gated.

## Pull requests

- Keep PRs scoped to one concern; explain *why*, not just *what*, for
  anything touching routing, redaction, or attachment handling.
- Update the relevant doc (`README.md`, `docs/ARCHITECTURE.md`,
  `docs/PARITY.md`, `docs/OPERATIONS.md`, `docs/MIGRATION.md`) alongside a
  behavior change.
- Do not include real personal data (your own phone numbers, contact names,
  home directory paths, message content, or chat rowids/GUIDs) in code,
  tests, commit messages, or PR descriptions.

## Code of conduct

Be respectful and constructive. Assume good faith; disagree on substance,
not people.
