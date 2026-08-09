# Verification report

Date: 2026-08-09

Scope: `imsg` platform release 2.0.0 in the isolated
`imsg-full-parity` worktree

Mutation boundary: no live verification mutation was authorized

## Outcome

The complete repository suite, plugin registration probe, site validator,
privacy scan, byte compilation, isolated package build, wheel metadata check,
read-only host capability probe, and diff whitespace check completed
successfully.

The current host advertises the private methods and selectors needed for every
implemented native action except edit. It advertises `message.edit`, but both
supported edit selectors are false. The client therefore rejects edit before
running its mutation command on this host.

## Complete repository validation

Command:

```bash
scripts/validate.sh
```

Exit status: `0`

Observed summary:

```text
305 passed, 1 warning in 2.70s
plugin tools: imsg_action, imsg_poll
requirements report: ok
validate: PASS
```

The warning comes from the installed third-party Discord dependency importing
Python's deprecated `audioop` module. It is not emitted by this plugin.

The registration probe confirmed:

- dynamic `Platform("imsg")` resolution without a built-in enum member
- standalone sender registration
- `IMSG_HOME_CHANNEL` cron delivery binding
- YAML and environment configuration hooks
- `imsg_action` followed by `imsg_poll`

## Focused documentation and release checks

Command:

```bash
$HOME/.hermes/hermes-agent/venv/bin/python3 -m pytest \
  tests/test_site_validation.py \
  tests/test_release_safety.py \
  tests/test_privacy_scan.py -q
```

Exit status: `0`

Observed summary:

```text
27 passed in 0.15s
```

Additional commands:

```bash
python3 scripts/validate_site.py
python3 scripts/privacy_scan.py
$HOME/.hermes/hermes-agent/venv/bin/python3 -m compileall -q \
  hermes_imsg_cli scripts tests
git diff --check
```

Each command exited `0`. The site validator covered the deterministic contracts
for `1440x900`, `768x1024`, `390x844`, and `320x700`. The privacy scan reported:

```text
privacy/security scan: scanned 47 file(s); no findings; PASS
```

Byte compilation and `git diff --check` produced no output.

## Package build

The requested Hermes-venv command could not start because that environment
does not contain the `build` module:

```text
$HOME/.hermes/hermes-agent/venv/bin/python3: No module named build
```

An isolated `pipx` invocation was then used without modifying the Hermes
environment:

```bash
pipx run build
```

Exit status: `0`

Observed artifacts:

```text
dist/hermes_imsg_cli-2.0.0.tar.gz
dist/hermes_imsg_cli-2.0.0-py3-none-any.whl
```

Wheel metadata inspection confirmed:

```text
Name: hermes-imsg-cli
Version: 2.0.0
License: MIT
Requires-Python: >=3.10
```

The source archive contains `actions.py`, `capabilities.py`, and
`formatting.py`. Setuptools emitted a forward-looking warning that the current
TOML license table form is deprecated after 2027-02-18. The build succeeded and
the artifact metadata is correct.

## Version and action contract audit

A read-only Python audit confirmed:

```text
version: 2.0.0
requires-python: >=3.10
native actions: react, edit, unsend, reply, sendWithEffect, upload-file,
  renameGroup, setGroupIcon, addParticipant, removeParticipant, leaveGroup,
  poll, poll-vote, poll-unvote
action count: 14
```

The runtime package version, project metadata, and plugin manifest all report
`2.0.0`. The tool schema action enum is identical to the capability model's
action list.

`docs/PARITY.md` contains the required supported-functionality sections for
messaging, native actions, polls, groups, inbound events, policy, recovery,
requirements, and verification. It contains no comparison ledger or status
table.

## Read-only host capability probe

Commands:

```bash
/opt/homebrew/bin/imsg --version
/opt/homebrew/bin/imsg status --json |
  jq '{advanced_features,v2_ready,bridge_version,rpc_methods,selectors,message}'
/opt/homebrew/bin/imsg rpc --help
```

All commands exited `0`. Sanitized result:

```text
imsg version: 0.13.0
advanced_features: true
v2_ready: true
bridge_version: 2
message: Connected to Messages.app. IMCore features available.
```

Relevant advertised methods:

```text
send
send.rich
send.attachment
tapback
typing
read
message.edit
message.unsend
poll.send
messages.poll.send
poll.vote
messages.poll.vote
poll.unvote
polls.unvote
messages.poll.unvote
group.rename
group.setIcon
group.addParticipant
group.removeParticipant
group.leave
```

Relevant selectors:

```text
editMessage: false
editMessageItem: false
retractMessagePart: true
pollPayloadMessage: true
pollVoteMessage: true
```

`imsg rpc --help` confirmed the stdin/stdout JSON-RPC command and its local
database, log-level, verbosity, and JSON output flags.

This is capability evidence, not mutation evidence. No message content or chat
history was read during the final probe.

## Test isolation incident

During early test-driven development, one formatted-send test supplied a fake
RPC runner but left the default command runner in place. Before failing after
roughly 27 seconds, it may have attempted this real command against local rowid
16:

```text
imsg send --chat-id 16 --text **bold**
```

The possibility was disclosed immediately. No private chat was read afterward
to determine whether Messages accepted it. The test harness was corrected so
every mutation test uses fake runners, and all subsequent automated runs stayed
inside that boundary.

## Live mutation checks not performed

No final verification command:

- sent a live iMessage or SMS
- marked a real chat read or emitted a typing indicator
- added or removed a tapback
- edited or unsent a message
- sent a native threaded reply or effect
- uploaded an attachment
- created, voted on, or removed a vote from a poll
- renamed a group, changed its icon, changed membership, or left it
- launched or injected the private bridge
- changed SIP, TCC, or Messages sign-in state

The automated suite verifies the command and RPC contracts with in-memory
runners and temporary files. Live behavior remains an operator-controlled test
against a deliberately selected disposable conversation.

## Repository state

All changes remain unstaged in the isolated worktree. No commit, push, pull
request, Pages deployment, Hermes restart, or live configuration change was
performed.
