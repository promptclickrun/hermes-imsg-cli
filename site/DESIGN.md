# Site design authority

This file defines the shipped presentation for `site/`. The site is an editorial product document for a security-sensitive open-source integration. Clarity, source fidelity, and operational confidence take priority over decoration.

## Visual principles

1. **Technical content leads.** Product identity comes from precise language, typography, spacing, and alignment.
2. **Structure is visible.** Rules, table rows, sequence numbers, and consistent baselines show relationships without ornamental graphics.
3. **Restraint builds trust.** Surfaces are neutral, corners are modest, and one blue accent identifies actions, focus, and routing state.
4. **Components fit their information.** Capabilities use a comparison table, security properties use a definition list, installation uses a numbered procedure, and documentation uses an index.
5. **Claims stay specific.** The interface avoids performance numbers, endorsements, user counts, and claims that are not present in repository documentation.

## Palette

Color tokens are defined in `styles.css`.

| Role | Token | Value |
| --- | --- | --- |
| Primary surface | `--surface` | `#ffffff` |
| Secondary surface | `--surface-subtle` | `#f8fafc` |
| Primary text | `--text` | `#101828` |
| Secondary text | `--text-secondary` | `#475467` |
| Standard border | `--border` | `#d0d5dd` |
| Primary accent | `--accent` | `#175cd3` |
| Dark section | `--dark` | `#101828` |

Blue has three jobs only: primary actions, keyboard focus, and exact-route state. Neutral values carry all other hierarchy. Text contrast is maintained on every surface, including the dark installation section.

## Typography

- The local system sans stack handles all interface and editorial copy. It renders consistently with the host operating system and adds no network dependency.
- The local monospace stack identifies commands, configuration, rowids, status labels, and the project name.
- Headings use compact tracking and moderate weight. Body copy uses a 1.6 line height and a controlled reading measure.
- Type size uses fluid `clamp()` values where scale matters and fixed accessible sizes for controls and supporting text.
- Headings use sentence case. Uppercase is limited to short metadata labels.

## Composition

- Content is capped at 1200px with fluid side spacing.
- The opening view uses a two-column statement and routing sequence on wide screens, then becomes one column below 980px.
- Sections have generous vertical space and are separated by solid borders or a deliberate neutral surface change.
- Information patterns are not interchangeable. Procedures remain ordered, comparisons remain tabular, and properties remain term-and-description rows.
- Repeated elements use consistent 4px to 8px corner radii. The interface does not rely on depth effects.
- Every grid uses `minmax(0, 1fr)` where content could otherwise force overflow. Long code stays inside horizontally scrollable `pre` elements.

## Interaction

- Native buttons control the routing example and command-copy helpers.
- Route choices expose `aria-pressed`. Selection updates the visible chat, message, source rowid, destination command, and polite live region through `textContent`.
- The route confirmation is a brief linear progress treatment tied directly to the selected rowid.
- Hover and focus changes use color and borders. Motion is minimal and never required to understand state.
- The page performs no network requests, storage, tracking, form submission, or third-party runtime execution.

## Accessibility

- Semantic `header`, `nav`, `main`, `section`, `figure`, `table`, `aside`, and `footer` elements establish a reliable reading order.
- A skip link targets `#main-content`.
- Buttons and primary links provide at least a 44px target in every responsive layout.
- Keyboard focus uses a high-contrast blue outline with separation from the focused control.
- Selected state is communicated by border, background, text, and `aria-pressed`, not color alone.
- Narrow layouts are defined through 320px. Tables reflow, code scrolls within its own region, and the document prevents page-level horizontal overflow.
- `prefers-reduced-motion: reduce` removes smooth scrolling and meaningful animation duration.
- The default routing explanation remains complete when JavaScript is unavailable.

## Content authority

Product copy must remain traceable to `README.md`, `SECURITY.md`, or documents under `docs/`. Exact-rowid routing, macOS requirements, the basic and private capability boundary, installation behavior, security properties, and test evidence must not be broadened beyond those sources. A dated verification result must retain its date and report context. Links point to the repository or named upstream projects only.
