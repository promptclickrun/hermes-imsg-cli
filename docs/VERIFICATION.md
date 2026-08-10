# Verification report

Date: 2026-08-10

Scope: `imsg` platform release 2.0.1 in the isolated
`imsg-reply-reaction-safety` worktree

Mutation boundary: no live Messages mutation was authorized or performed

## Outcome

The complete repository suite, plugin registration probe, focused reply and
reaction regression suite, site validator, privacy scan, byte compilation,
isolated package build, package metadata inspection, read-only host capability
probe, and diff whitespace check completed successfully.

The release fixes three user-visible behaviors:

- ordinary Hermes text and media responses remain unthreaded even when the
  framework supplies `reply_to` correlation;
- inbound reply context is emitted only for a real `thread_originator_guid`,
  not for the unreliable `reply_to_guid` field alone;
- Tapbacks default to `off`, are absent from the default watch subscription,
  and are dropped before agent processing if one still arrives.

## Complete repository validation

Command:

```bash
scripts/validate.sh
```

Exit status: `0`

Observed summary:

```text
329 passed, 1 warning in 3.82s
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

## Focused regression validation

Eleven focused tests covered both spellings of the native thread-origin field,
rejection of false reply context, reaction association isolation, plain text,
document, and voice response routing, default reaction mode, watch arguments,
and the early reaction-drop boundary.

Exit status: `0`

Observed summary:

```text
11 passed in 0.21s
```

The outbound tests inject fake command and RPC runners. They assert that
framework response correlation does not call `send.rich` or add a native
`--reply-to` flag. The reaction boundary test makes every downstream pipeline
operation fail if called, then confirms the cursor still advances without any
agent dispatch.

## Documentation, privacy, and bytecode checks

Commands:

```bash
python3 scripts/validate_site.py
python3 scripts/privacy_scan.py
python3 -m compileall -q hermes_imsg_cli scripts tests
git diff --check
```

Each command exited `0`. The site validator reported:

```text
site validation: PASS (viewport contracts: 1440x900, 768x1024, 390x844, 320x700)
```

The privacy scan reported:

```text
privacy/security scan: scanned 57 file(s); no findings; PASS
```

Byte compilation and `git diff --check` produced no output.

## Package build

On this host, `pipx` 1.13.0 no longer maps the package name `build` to its
executable automatically. The first isolated invocation exited `1` and
reported that the available executable is `pyproject-build`:

```bash
pipx run build
```

The explicit package/executable form was then used:

```bash
pipx run --spec build pyproject-build
```

Exit status: `0`

Observed artifacts:

```text
dist/hermes_imsg_cli-2.0.1.tar.gz
dist/hermes_imsg_cli-2.0.1-py3-none-any.whl
```

Wheel metadata inspection confirmed:

```text
Name: hermes-imsg-cli
Version: 2.0.1
License: MIT
Requires-Python: >=3.10
```

The source archive contains `actions.py`, `adapter.py`, `capabilities.py`, and
`formatting.py`. Setuptools emitted a forward-looking warning that the current
TOML license table form is deprecated after 2027-02-18. The build succeeded and
the artifact metadata is correct.

## Version and action contract audit

A read-only Python audit confirmed:

```text
version: 2.0.1
native actions: react, edit, unsend, reply, sendWithEffect, upload-file,
  renameGroup, setGroupIcon, addParticipant, removeParticipant, leaveGroup,
  poll, poll-vote, poll-unvote
action count: 14
```

The runtime package version, project metadata, and plugin manifest all report
`2.0.1`. Release consistency and bundled-skill tests are included in the full
repository result.

## Read-only host capability probe

The installed `imsg` version and sanitized status fields were read without
opening message history or performing a mutation.

All probe commands exited `0`. Sanitized result:

```text
imsg version: 0.13.0
advanced_features: true
v2_ready: true
bridge_version: 2
message: Connected to Messages.app. IMCore features available.
methods: send, send.rich, tapback, message.edit, message.unsend
selectors:
  editMessage: false
  editMessageItem: false
  retractMessagePart: true
```

The current host therefore advertises both ordinary bridge send and explicit
native reply support through `send.rich`. Edit remains correctly unavailable
because both supported edit selectors are false; the client rejects it before
mutation. This is capability evidence, not live mutation evidence.

## Live mutation checks not performed

No verification command:

- sent a live iMessage or SMS
- marked a real chat read or emitted a typing indicator
- added or removed a Tapback
- edited or unsent a message
- sent a native threaded reply or effect
- uploaded an attachment
- created, voted on, or removed a vote from a poll
- renamed a group, changed its icon, changed membership, or left it
- launched or injected the private bridge
- changed SIP, TCC, or Messages sign-in state

The automated suite verifies command and RPC contracts with injected runners
and temporary files. Runtime installation and process health are verified
separately without sending a message.

## Historical 2.0.0 test-isolation note

During 2.0.0 development on 2026-08-09, one formatted-send test supplied a fake
RPC runner but left the default command runner in place. Before failing after
roughly 27 seconds, it may have attempted this real command against local rowid
16:

```text
imsg send --chat-id 16 --text **bold**
```

The possibility was disclosed immediately. No private chat was read afterward
to determine whether Messages accepted it. The mutation tests use fake runners,
and the suite now also clears ambient `IMSG_*` variables around every test so
personal live policy cannot alter fixture behavior.
