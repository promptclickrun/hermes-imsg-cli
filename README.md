# hermes-imsg-cli

Native iMessage and SMS support for [Hermes Agent](https://github.com/NousResearch/hermes-agent) on macOS, powered by the local [`imsg` CLI](https://github.com/openclaw/imsg).

`hermes-imsg-cli` is a third-party Hermes gateway platform plugin. It receives events from Messages, applies access-control policies, delivers approved messages to Hermes, and sends responses and attachments through Messages.app. It uses Hermes' standard plugin system and does not require changes to Hermes core.

## Features

- Receive iMessage and SMS conversations in Hermes, including direct messages and group chats.
- Route every inbound reply to the exact Messages chat that produced the event.
- Send text, images, video, audio, and file attachments.
- Deliver scheduled messages and `send_message` output without a live gateway adapter.
- Restrict access with separate DM and group allowlists, ordered rules, participant checks, and mention requirements.
- Resume the message stream from a locally persisted cursor after a restart.
- List chats, inspect group details, read history, search messages, and use standard reactions.
- Expose optional read receipts, typing indicators, targeted tapbacks, and native polls when the installed `imsg` bridge supports them.

> [!IMPORTANT]
> Replies always use the numeric Messages chat rowid captured from the inbound event. The plugin does not guess a destination from participants, phone numbers, or caller-supplied metadata. Threaded reply bubbles are not supported because replies remain bound to that source rowid.

## Requirements

- macOS 14 or newer with Messages.app signed in.
- A working [Hermes Agent](https://github.com/NousResearch/hermes-agent) installation.
- Python 3.9 or newer in the Hermes runtime.
- `imsg` installed on the Mac that runs the Hermes gateway.
- Full Disk Access for the complete process chain that launches Hermes and reads `~/Library/Messages/chat.db`.
- Automation permission for Messages.app when sending messages.
- Text Message Forwarding from an iPhone when sending or receiving SMS.
- Optional: `ffmpeg` for supported inbound attachment conversions.

The plugin does not sign in to Messages, grant macOS permissions, modify TCC, or change System Integrity Protection.

## Quick start

### 1. Install and verify `imsg`

```bash
brew install steipete/tap/imsg
imsg --version
imsg status --json
```

Grant Full Disk Access and Messages Automation permission in **System Settings > Privacy & Security**. Restart the affected terminal, launcher, or Hermes service after changing permissions.

Confirm that `imsg` can read the local Messages database:

```bash
imsg chats --limit 3 --json
```

See the upstream [permissions guide](https://github.com/openclaw/imsg/blob/main/docs/permissions.md) if the command cannot open `chat.db`.

### 2. Install the Hermes plugin

```bash
git clone https://github.com/promptclickrun/hermes-imsg-cli.git
cd hermes-imsg-cli
scripts/install-local.sh
```

The installer:

- Symlinks `hermes_imsg_cli/` to `~/.hermes/plugins/imsg-platform`.
- Adds `imsg-platform` to `plugins.enabled` in `~/.hermes/config.yaml`.
- Creates a timestamped backup before changing an existing config.
- Runs a local plugin discovery probe when the Hermes source checkout is available.

Keep the cloned repository in place because the installed plugin is a symlink. The installer does not install `imsg`, grant permissions, or restart Hermes. If `config.yaml` does not exist yet, create it through Hermes setup and enable the plugin manually.

### 3. Add a starter configuration

Add the following to `~/.hermes/config.yaml` and replace the example values:

```yaml
plugins:
  enabled:
    - imsg-platform

platforms:
  imsg:
    enabled: true
    extra:
      binary: /opt/homebrew/bin/imsg
      auto_launch: false
      send_read_receipts: false
      fallback_action: silent_drop

      # Direct-message senders
      allowed_handles:
        - "+12025550101"

      # Group access is configured separately
      group_allowed_handles:
        - "+12025550101"
      allowed_chats:
        - "42"
      require_mention: true
      mention_patterns:
        - '(?<![\w@])@?hermes\b[,:\-]?'

    # Optional default destination for cron and notifications
    home_channel:
      platform: imsg
      chat_id: "42"
      name: Messages
```

Use `command -v imsg` to find the correct binary path. Apple Silicon Homebrew normally uses `/opt/homebrew/bin/imsg`; Intel Homebrew commonly uses `/usr/local/bin/imsg`.

Find Messages chat rowids with:

```bash
imsg chats --limit 10 --json
```

Handles may be E.164 phone numbers or iMessage email addresses. The numbers above are reserved fictional examples.

### 4. Restart and verify Hermes

Restart a running gateway so it discovers the plugin, then verify the plugin and platform through your normal Hermes workflow:

```bash
hermes plugins list
hermes gateway status
```

The installed plugin should appear as `imsg-platform`, and the gateway should report the `imsg` platform when its configuration and binary are available.

## Configuration

YAML under `platforms.imsg` is the clearest way to manage the plugin. Environment variables are also supported and override YAML. An environment-only setup must set `IMSG_ENABLED=true`.

Common settings:

| Setting | Purpose |
| --- | --- |
| `allowed_handles` | Handles allowed to send direct messages to the agent. |
| `group_allowed_handles` | Handles allowed to invoke the agent in groups. |
| `allowed_chats` | Messages chat rowids allowed in group context. |
| `require_mention` | Requires a configured mention pattern in allowed groups. |
| `mention_patterns` | Regular expressions used to detect and remove the agent mention. |
| `rules` | Ordered access-control rules for more precise policies. |
| `fallback_action` | Action when no rule matches. Defaults to `silent_drop`. |
| `participant_validation` | Uses event participants in `fast` mode or a live group lookup in `strict` mode. |
| `auto_launch` | Attempts to arm the optional injected Messages bridge at connection time. |
| `send_read_receipts` | Marks approved inbound chats read when the bridge supports it. |
| `poll_vote_events` | Controls whether DM, group, and unvote poll events may reach the agent. |

See [Operations](docs/OPERATIONS.md) for the complete YAML schema, environment-variable reference, cursor management, and advanced configuration.

### Ordered rules

Flat allowlists work well for simple installations. If `rules` is non-empty, rules are evaluated from top to bottom and the first match determines the action. Flat allowlists are only synthesized when no rules are configured.

```yaml
platforms:
  imsg:
    extra:
      fallback_action: silent_drop
      mention_patterns:
        - '(?<![\w@])@?hermes\b[,:\-]?'
      rules:
        - name: approved-dm
          context: dm
          senders:
            - "+12025550101"
          action: allow

        - name: mentioned-in-approved-group
          context: group
          chats:
            - "42"
          senders: any
          require_mention: true
          action: allow
```

Supported actions are `allow`, `deny`, `prompt`, and `silent_drop`. Self-authored Messages events are always ignored. Keep `fallback_action: silent_drop` unless the Hermes pairing workflow is intentionally handling `prompt`.

## Routing and outbound delivery

For inbound events, Hermes replies to the exact source rowid. For explicit outbound delivery:

- Use `imsg:<chat_rowid>` for an existing conversation or group, such as `imsg:42`.
- Use `imsg:<phone_number>` for a direct phone destination, such as `imsg:12025550105`.
- Use `IMSG_HOME_CHANNEL` or `home_channel.chat_id` as the default cron and notification destination.

The plugin distinguishes short numeric rowids from phone-number-length targets and normalizes bare phone numbers before calling `imsg send --to`. Email handles are also supported for explicit outbound sends.

Outbound attachments must exist as regular files. Symlinks, directories, sockets, FIFOs, and device files are rejected. When Hermes supplies multiple attachments, the plugin sends them one at a time and reports any partial failure explicitly.

## Feature availability

| Capability | Availability |
| --- | --- |
| Chats, history, search, watch, and inbound attachments | Standard `imsg` features. Full Disk Access is required. |
| Text and file sending | Standard `imsg` features. Messages Automation is required. |
| SMS | Requires Text Message Forwarding from an iPhone. |
| Standard reactions | Uses public automation and may require both Accessibility and Messages Automation. |
| Read receipts and typing indicators | Bridge-dependent. These can be unavailable on current macOS releases. |
| Native poll creation, voting, and unvoting | Bridge-dependent. Hermes exposes these through the explicit `imsg_poll` tool after capability checks. |
| Threaded reply bubbles | Not supported. Replies use a plain send to the exact source rowid. |

Normal SIP-enabled installations should begin with `auto_launch: false` and `send_read_receipts: false`. Advanced IMCore features can require an injected helper and may remain unavailable because of library validation or private-entitlement restrictions. The plugin never disables SIP or installs the helper for you.

Before using native polls, inspect `imsg status --json` and follow the compatibility procedure in [Operations](docs/OPERATIONS.md). Do not retry an ambiguous poll mutation automatically.

## Updating

The installer points Hermes at the local checkout, so updating the checkout updates the plugin code:

```bash
cd /path/to/hermes-imsg-cli
git pull --ff-only
```

Restart the Hermes gateway after updating. Review [Migration](docs/MIGRATION.md) before changing the installed `imsg` version or moving between plugin versions.

## Uninstalling

Disable the plugin, remove its symlink, and restart Hermes:

```bash
hermes plugins disable imsg-platform
rm ~/.hermes/plugins/imsg-platform
```

If the Hermes CLI is unavailable, remove `imsg-platform` from `plugins.enabled` in `~/.hermes/config.yaml` manually.

## Troubleshooting

| Problem | Check |
| --- | --- |
| Plugin is missing | Confirm the symlink exists, `imsg-platform` is enabled, and Hermes was restarted after installation. |
| Messages cannot be read | Grant Full Disk Access to every parent process in the Hermes launcher chain, then restart those processes. |
| Sends fail or never appear | Confirm Messages is signed in, Automation permission is granted, and the same destination works with a deliberate direct `imsg send` test. |
| Inbound events stop | Run a direct `imsg watch --json` test, check the installed `imsg` version, and inspect gateway logs for permission or unsupported-flag errors. |
| Read receipts, typing, or polls fail | Inspect `imsg status --json`. Disable bridge-dependent features when the required methods or selectors are absent. |
| Strict participant checks deny every group | Run `imsg group --chat-id <rowid> --json` under the same launcher identity as Hermes. |

The full diagnostic guide is in [Operations](docs/OPERATIONS.md#troubleshooting).

## Documentation

- [Operations](docs/OPERATIONS.md): installation details, configuration reference, permissions, and troubleshooting
- [Architecture](docs/ARCHITECTURE.md): component boundaries, routing invariants, and design decisions
- [Command parity](docs/PARITY.md): supported `imsg` surfaces and intentional exclusions
- [Migration](docs/MIGRATION.md): compatibility, upgrades, rollback, and behavior changes
- [Verification](docs/VERIFICATION.md): test evidence and environment-gated checks
- [Security policy](SECURITY.md): security properties and private vulnerability reporting
- [Contributing](CONTRIBUTING.md): development setup, tests, and contribution requirements

## License

MIT. See [LICENSE](LICENSE).
