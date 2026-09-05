# Security policy

This plugin drives a live macOS Messages account through the local `imsg`
CLI. Treat message content, chat rowids/GUIDs, attachment paths, and handles
as personal data. This document covers how to report a vulnerability and the
security properties this repository is expected to preserve.

## Supported versions

Only the latest commit on the default branch is supported. There are no
maintained release branches; apply fixes by updating to the latest revision.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for a suspected vulnerability.

1. Use GitHub's private vulnerability reporting for this repository
   ("Security" tab → "Report a vulnerability"), or open a draft security
   advisory.
2. If neither is available, open a normal issue asking a maintainer to open a
   private channel; do not include exploit details, real phone numbers,
   chat rowids/GUIDs, message content, or file paths from your own Messages
   database in that issue.
3. Include: affected file(s)/function(s), a minimal reproduction using
   synthetic data only (e.g. NANP reserved `+1 202-555-01xx` numbers, generic
   rowids, `/path/to/...` placeholders), and the impact you believe it has.

We will acknowledge reports on a best-effort basis and aim to fix
high-severity issues (anything that could redirect a reply/send to the wrong
chat, leak PII into logs, or execute/read attachments outside the intended
scope) as the top priority.

## Security properties this plugin must preserve

Any change that weakens one of these is a regression, not a feature:

- **Reply routing never guesses.** Inbound replies always route to the exact
  Messages chat rowid captured from the inbound event through the
  capability-selected plain-send path. No GUID resolution, participant
  matching, or caller-supplied metadata can redirect a reply. Framework
  `reply_to` correlation never requests an Apple Messages thread; only the
  explicit native `reply` action can do so after a same-chat lookup.
- **Quoted context requires validated same-chat evidence.** A non-empty
  `thread_originator_guid` remains authoritative. For a short confirmation
  without one, the adapter performs a bounded history read and exposes context
  only when the current row points to a bot-authored question in the exact
  source chat. Raw `reply_to_guid` fields alone remain untrusted because they
  can identify an unrelated recent message.
- **Tapbacks are silent by default.** With reaction notifications `off`, the
  watch subscription omits reactions and any reaction that still arrives is
  dropped after cursor persistence but before authorization, history lookup,
  read receipts, conversion, or agent dispatch. Modes `own` and `all` are
  explicit opt-ins that permit Tapbacks to start agent turns.
- **Fail closed, not open.** Malformed/empty command output, invalid targets,
  unresolved chat rowids, and unsafe attachments are treated as failures, not
  successes.
- **No PII in logs.** Raw `imsg watch` lines are never logged. Errors and
  diagnostics are redacted (phone numbers, emails, home directory paths, and
  GUID-like identifiers) before they reach a log line or an agent-visible
  error message. Plugin-owned rule, participant, and action logs omit the known
  chat rowid.
- **Outbound attachments are regular files only.** Symlinks and special files
  (directories, FIFOs, sockets, device nodes) are rejected before being
  handed to `imsg send --file`.
- **Inbound attachment paths are never guessed.** A bare filename with no
  resolvable path is not treated as a path.
- **Local state is written safely.** The resumable cursor file is written
  atomically with owner-only (`0600`) permissions.
- **No permission automation.** The plugin never edits TCC, disables SIP,
  signs/injects a dylib, or signs into Messages on your behalf.

## Scope

In scope: this repository's Python source (`hermes_imsg_cli/`), its tests,
and its install/validation/CI scripts.

Out of scope: the upstream `imsg` executable itself, macOS Messages.app,
and the Hermes Agent core (`gateway`/`hermes_cli`) this plugin is loaded
into. Please report issues in those projects upstream.
