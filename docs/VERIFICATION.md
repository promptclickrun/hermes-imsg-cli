# Verification report

**Date:** 2026-07-15
**Branch:** `security/gor-39-public-readiness`
**Scope:** GOR-39 current-tree public-readiness security/privacy remediation.
**Upstream audited (unchanged from prior review):** `openclaw/imsg@b5b7464bc748af482bfc3059b28d5dab0395da9e`

This report supersedes the prior (2026-07-13) verification for the security/
privacy remediation described below. It does not re-audit upstream `imsg`
parity, which is unchanged; see `PARITY.md`.

## What changed in this pass

- **Reply routing fix:** `send()` no longer attempts a threaded `send-rich`
  reply. A caller/metadata-supplied `chat_guid` could previously redirect a
  reply to a different chat than the rowid it was invoked with. Every reply
  now always plain-sends by rowid (`ARCHITECTURE.md` → "Reply routing: why
  rich/threaded replies are not attempted").
- **Fail-closed send confirmation:** a zero exit code from `imsg send` is no
  longer treated as success by itself; stdout must parse as a JSON
  confirmation object.
- **Outbound attachment safety:** symlinks and special files (directories,
  FIFOs, sockets, device nodes) are rejected before `imsg send --file` runs.
- **Inbound attachment path safety:** a bare `filename`/`file` attachment
  field is no longer expanded into a usable path; only `converted_path`/
  `path`/`cached_path` are used.
- **Deterministic multi-attachment reporting:** a partial failure across
  several attachments now reports `failed_index`, `attempted_attachments`,
  and `succeeded_message_ids` instead of an ambiguous single error.
- **Atomic, restrictive state persistence:** the resumable cursor file is
  written to a temp file and `os.replace()`d into place with `0600`
  permissions.
- **Chat-id target validation:** `--chat-id` send targets are validated as
  positive-integer rowids within a sane digit range (≤19 digits) before any
  subprocess runs.
- **Participant list type validation:** non-string/boolean entries in an
  event's `participants` field are dropped rather than coerced.
- **Bounded subprocess cleanup:** `cli.run_imsg`/`run_imsg_rpc` now
  terminate a cancelled/timed-out child first, then bound the wait before
  force-killing, instead of killing immediately.
- **Logging hardening:** raw `imsg watch` lines are never logged (only their
  length, on parse failure); `_redact()` now also strips home-directory
  paths and GUID-like identifiers, not just phone numbers and emails; every
  log call that could include a filesystem path or binary path is redacted.
- **`pii_safe` removed:** the platform registry entry no longer sets
  `pii_safe=True` — the agent-facing `imsg_poll` tool requires the LLM to
  pass back the literal rowid it was given, so hashing chat/user ids for the
  prompt would silently break polls for no privacy benefit.
- **Personal data neutralized:** all phone-number examples across source,
  tests, and docs now use the NANP reserved fictional range
  `+1 202-555-01xx`; personal names in test fixtures/docs were replaced with
  generic "Family Member"/"Owner" labels; the hardcoded personal
  `/Users/<name>/...` path in `scripts/validate.sh` and `docs/VERIFICATION.md`
  was replaced with `$HOME`-relative paths; unnecessary personal author
  metadata was removed from `plugin.yaml`/`pyproject.toml` (the `gordie`
  mention-pattern references remain — that is the product's own wake word,
  not author attribution).
- **New governance/CI:** `LICENSE` (MIT), `SECURITY.md`, `CONTRIBUTING.md`,
  `scripts/privacy_scan.py`, and a pinned-action macOS CI workflow
  (`.github/workflows/ci.yml`) were added.

## Automated evidence

All subprocess, filesystem, and Hermes boundaries in tests are mocked or use
temporary files, except two deliberately-real-subprocess tests that verify
bounded terminate-then-kill cleanup against short-lived, harmless local
`python -c` child processes (never `imsg`, never live Messages). The suite
performs no live sends, poll creation, votes, unvotes, or reactions.

### Full suite

```bash
HERMES_SRC=/path/to/hermes-agent \
  PYTHONPATH="/path/to/hermes-agent:$PWD" \
  $HOME/.hermes/hermes-agent/venv/bin/python3 -m pytest -q
```

Result:

```text
........................................................................ [ 34%]
........................................................................ [ 68%]
..................................................................       [100%]
210 passed, 6 warnings in 7.84s
```

The six warnings are transitive Hermes-environment deprecations (`pkg_resources`
namespace declarations), not plugin failures.

Coverage additions in this pass, on top of everything listed in the prior
report (source-correct builders, NDJSON parsing, rowid routing, allowlists/
rules, participant lookup, attachment conversion, standalone sends, reactions,
read/typing, poll CLI/RPC builders and capability gates):

- rich-reply routing regression tests (caller/cache-supplied `chat_guid` and
  `reply_to` never reach `send-rich`; the method itself no longer exists);
- fail-closed send confirmation (empty stdout, malformed JSON, non-object
  JSON on exit 0);
- outbound attachment safety (symlink, directory, FIFO, missing-file
  rejection; regular-file acceptance) for both the live adapter and the
  standalone sender;
- inbound `filename`-only attachments are never expanded into a path, while
  `converted_path`/`path` preference is preserved;
- deterministic multi-attachment partial-failure reporting;
- atomic state persistence with `0600` permissions, including cleanup of the
  temp file on a simulated write failure;
- participant list type validation (dict/list/int/bool/None entries dropped);
- bounded terminate-then-kill subprocess cleanup for both `run_imsg` and
  `run_imsg_rpc` against a real SIGTERM-ignoring child, and a real
  cooperative child completing well inside the grace period;
- `_redact()` sentinel tests for phone/email/home-path/GUID/chat-GUID
  patterns, individually and combined;
- watch-line handling never logs raw content on a JSON parse failure
  (only a redacted length is logged), verified via `caplog`;
- `chat_id` target syntax/range validation (non-numeric, zero, oversized,
  whitespace-only, and max-valid-length rowids);
- `pii_safe` is not set on the registry entry;
- the repository-wide privacy/security scanner's own detection rules
  (reserved-range phone numbers, personal home paths, secret shapes,
  non-example email domains) plus an end-to-end pass over the actual
  tracked+untracked repository content.

### Repository validation and registration probe

```bash
scripts/validate.sh
```

`scripts/validate.sh` no longer has a hardcoded personal default path; it
resolves `HERMES_SRC` from the environment or `$HOME/.hermes/hermes-agent`.

### Privacy/security scan

```bash
python3 scripts/privacy_scan.py
```

Result:

```text
privacy/security scan: scanned 29 file(s); no findings; PASS
```

### Syntax and package build

```bash
$HOME/.hermes/hermes-agent/venv/bin/python3 -m compileall -q hermes_imsg_cli tests scripts
bash -n scripts/validate.sh scripts/install-local.sh
$HOME/.hermes/hermes-agent/venv/bin/python3 -m pip wheel . --no-deps --no-build-isolation -w /tmp/hermes-imsg-wheel
```

Result: byte-compilation and both shell scripts pass syntax checks with no
output. Wheel build:

```text
Successfully built hermes-imsg-cli
Created wheel: hermes_imsg_cli-1.2.0-py3-none-any.whl
```

The build was run twice in this session to confirm reproducible success;
the SHA-256 differs between runs (`3dab75c1...506660d` and `a45d9b8e...bd26ca7`
were both observed) because wheel archives embed file timestamps, not
because content changed — verified by re-inspecting `METADATA` after each
build. Wheel metadata was inspected directly and contains no personal
author metadata, no non-reserved phone numbers, and correct `Project-URL`
entries pointing at the public repository.

### Diff hygiene

```bash
git diff --check
```

Result: exit 0, no whitespace errors (two trailing-blank-line issues
introduced while adding tests in this pass were found and fixed by this same
check before finalizing).

### CI

`.github/workflows/ci.yml` runs on `macos-14` with `actions/checkout` and
`actions/setup-python` pinned by full commit SHA (not a mutable tag), and
executes: `pytest -q`, `compileall`, a shell-script syntax check, the privacy/
security scanner, a PR-diff whitespace check, and a wheel build (uploaded as
an artifact). This was validated for YAML syntax and pinned-SHA correctness
(each SHA resolved against the GitHub API to confirm it is a real commit on
the expected tag) but has not run on GitHub's hosted runners as part of this
local session — no commit/push was made per the task's constraints.

## Read-only live prerequisite probe

No message, reaction, typing indicator, read receipt, SIP change, launch,
restart, or TCC action was performed. No native poll, caption, vote, or
unvote was performed. No live `imsg` binary was invoked in this session; all
subprocess boundaries are mocked or use short-lived, harmless local
`python -c` processes for the terminate-then-kill regression tests.

## Environment-gated verification left to operator

Unchanged from the prior report — these require a live macOS Messages
account and are intentionally not automated. See `OPERATIONS.md` for the
exact operator-only sequences (inbound DM/group tests, attachment/reaction
checks, and the poll create/vote/unvote sequence).

## Known risks / non-blockers

- Threaded/rich replies are no longer available at all (previously an
  environment-blocked best-effort feature with a plain fallback). This is an
  intentional, permanent trade of a cosmetic feature for a routing guarantee
  that cannot be subverted by any input the adapter does not itself control.
- `pii_safe` is not set, so this platform's session context is not eligible
  for the core PII-redaction path other built-in bridge platforms use; this
  is intentional (see above) and does not weaken this plugin's own routing/
  redaction guarantees, which are independent of that core mechanism.
- CI has been authored and locally validated (YAML syntax, pinned SHA
  authenticity) but not yet exercised on GitHub's infrastructure, since no
  commit/push was made in this session.
- No OpenClaw adapter, private IMCore implementation, core patch, service
  change, credential change, restart, release, commit, push, or merge was
  made.
