---
name: Coast Markdown
version: 0.1.0
extends: Coast 0.1.2
description: Markdown authoring and rendering layer for Coast. Prose scale, code chrome, 14 container types across seven callout tones, task lists, collapsibles, Mermaid theming, and the save-state and focus tokens a document editor needs.
colors:
  tone-info: "#1E5F8C"
  tone-info-bg: "#E8F0F6"
  tone-info-ink: "#16496B"
  tone-tip: "#4A97A8"
  tone-tip-bg: "#E4F2F4"
  tone-tip-ink: "#266B78"
  tone-success: "#3E8E6E"
  tone-success-bg: "#E5F2ED"
  tone-success-ink: "#2F7358"
  tone-warning: "#C88A2E"
  tone-warning-bg: "#FAF0DC"
  tone-warning-ink: "#8F621C"
  tone-danger: "#C1503E"
  tone-danger-bg: "#FAE8E5"
  tone-danger-ink: "#9A3D2D"
  tone-accent: "#7A5C9E"
  tone-accent-bg: "#F0EAF6"
  tone-accent-ink: "#5F4680"
  tone-muted: "#6B7785"
  tone-muted-bg: "#F0EFEA"
  tone-muted-ink: "#4E5865"
  status-saved: "#3E8E6E"
  status-saving: "#6B7785"
  status-dirty: "#C88A2E"
  status-error: "#C1503E"
  prose-rule: "#E3DCC9"
  prose-code-bg: "#F1EDE1"
  prose-mark-bg: "#F5DFC8"
  prose-selection: "#CFE2EE"
  focus-ring: "#5BA8B8"
colors-dark:
  tone-info: "#76B5D9"
  tone-info-bg: "#12283C"
  tone-info-ink: "#A8D2EA"
  tone-tip: "#7DC4CE"
  tone-tip-bg: "#122C33"
  tone-tip-ink: "#A9DCE3"
  tone-success: "#66B894"
  tone-success-bg: "#122E26"
  tone-success-ink: "#9AD6BA"
  tone-warning: "#E0AE5E"
  tone-warning-bg: "#2E2415"
  tone-warning-ink: "#EFCE96"
  tone-danger: "#E08574"
  tone-danger-bg: "#311915"
  tone-danger-ink: "#F0B3A6"
  tone-accent: "#A98FC9"
  tone-accent-bg: "#231C33"
  tone-accent-ink: "#C9B6E0"
  tone-muted: "#8B97A5"
  tone-muted-bg: "#16202C"
  tone-muted-ink: "#B4BEC9"
  status-saved: "#66B894"
  status-saving: "#8B97A5"
  status-dirty: "#E0AE5E"
  status-error: "#E08574"
  prose-rule: "#1E3550"
  prose-code-bg: "#101F30"
  prose-mark-bg: "#3D2E1C"
  prose-selection: "#1B3D57"
  focus-ring: "#7DC4CE"
typography:
  headline-sm:
    fontSize: 21px
    fontFamily: Frank Ruhl Libre, serif
    fontWeight: 600
    lineHeight: 1.3
  title-md:
    fontSize: 18px
    fontFamily: Heebo, sans-serif
    fontWeight: 600
    lineHeight: 1.4
  title-sm:
    fontSize: 16px
    fontFamily: Heebo, sans-serif
    fontWeight: 600
    lineHeight: 1.4
  label-md:
    fontSize: 13px
    fontFamily: Heebo, sans-serif
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: 0.06em
  code-inline:
    fontSize: 0.9em
    fontFamily: JetBrains Mono, IBM Plex Mono, ui-monospace, Miriam Mono CLM, monospace
    fontWeight: 400
    lineHeight: 1.5
  code-block:
    fontSize: 14px
    fontFamily: JetBrains Mono, IBM Plex Mono, ui-monospace, Miriam Mono CLM, monospace
    fontWeight: 400
    lineHeight: 1.65
spacing:
  compact-xs: 4px
  compact-sm: 8px
  compact-md: 12px
  compact-lg: 16px
  compact-xl: 24px
rounded:
  xs: 4px
components:
  prose:
    measure: 68ch
    textColor: "{colors.on-surface}"
    typography: "{typography.body-lg}"
    blockSpacing: "{spacing.md}"
  code-block:
    padding: "{spacing.md}"
    rounded: "{rounded.md}"
    direction: ltr
    textColor: "{colors.on-surface}"
    typography: "{typography.code-block}"
    backgroundColor: "{colors.prose-code-bg}"
  code-inline:
    padding: 2px 6px
    rounded: "{rounded.xs}"
    typography: "{typography.code-inline}"
    backgroundColor: "{colors.prose-code-bg}"
  callout:
    padding: "{spacing.md}"
    rounded: "{rounded.md}"
    borderWidth: 0px
    titleTypography: "{typography.title-sm}"
    accentInlineStart: 3px
  details:
    padding: "{spacing.md}"
    rounded: "{rounded.md}"
    borderWidth: 1px
    borderColor: "{colors.prose-rule}"
    summaryTypography: "{typography.title-sm}"
    backgroundColor: "{colors.surface-container}"
  task-item:
    boxSize: 18px
    rounded: "{rounded.xs}"
    gap: "{spacing.compact-md}"
    checkedColor: "{colors.tone-success}"
    borderColor: "{colors.prose-rule}"
  mark:
    padding: 1px 4px
    rounded: "{rounded.xs}"
    textColor: "{colors.on-surface}"
    backgroundColor: "{colors.prose-mark-bg}"
  focus-ring:
    width: 2px
    offset: 2px
    color: "{colors.focus-ring}"
---

## Overview

Coast Markdown extends Coast 0.1.2 for a document system: writing markdown,
saving it, and rendering it back. It is additive. Every token here is a new key,
nothing in the base file is redefined, and every rule in the base `Localization`
chapter still applies without amendment.

Coast's base identity is editorial and airy, tuned for hospitality and travel
surfaces. A document tool is a different animal: denser, more stateful, read for
minutes rather than glanced at. This layer resolves that tension by keeping
Coast's palette, shapes, and Hebrew rules intact while adding a compact scale for
tool chrome and a prose scale for the reading surface itself.

Two surfaces, two spacing scales:

- **Reading surface** (the rendered document): base spacing. Airy, `md` between
  blocks, `lg` around sections. Coast as written.
- **Tool chrome** (toolbars, file lists, status bars, metadata rows): the
  `compact-*` scale. `compact-md` between controls, `compact-lg` around groups.

Never mix them on the same surface. A file list at `spacing.lg` wastes half the
viewport; a paragraph at `compact-md` reads cramped.

## Prose scale

Markdown produces six heading levels. Coast ships three. The extension fills the
rest and switches families partway down, which is the editorial convention: serif
for structural headings, sans for the working ones.

| Markdown | Token | Family |
| --- | --- | --- |
| Document title | `headline-display` | Frank Ruhl Libre 64 |
| `#` | `headline-lg` | Frank Ruhl Libre 40 |
| `##` | `headline-md` | Frank Ruhl Libre 26 |
| `###` | `headline-sm` | Frank Ruhl Libre 21 |
| `####` | `title-md` | Heebo 18 |
| `#####` | `title-sm` | Heebo 16 |
| `######` | `label-md` | Heebo 13, tracked |

`headline-display` is for the document title in a reader view, not for `#` inside
body content. A markdown file whose first line is `#` renders at `headline-lg`.

Apply the base file's Hebrew display overrides at `headline-sm` and above: weight
floor 500, `letterSpacing: 0` on Hebrew runs, line-height floor 1.15.

**Never apply `text-transform: uppercase` to `label-md`.** Hebrew has no letter
case, so the transform silently does nothing to Hebrew and shouts in Latin,
producing a heading level that looks inconsistent between scripts. Use the
letter-spacing and weight to carry the emphasis instead.

**Measure**: cap the reading column at `68ch`. Hebrew at 17px with 1.7
line-height reads comfortably at roughly 60 to 70 characters; wider than that and
the return sweep starts costing comprehension. Use `max-inline-size`, not
`max-width`, so a future LTR surface inherits it correctly.

## Links

Primary ocean, underlined, hover to seafoam. This mirrors the base file's
ocean to seafoam button transition, so links and buttons feel like the same
system.

```css
.prose a {
  color: var(--primary);
  text-decoration-line: underline;
  text-decoration-thickness: 1px;
  text-underline-offset: 0.25em;
  text-decoration-skip-ink: auto;
  transition: color 120ms ease;
}
.prose a:hover { color: var(--tertiary); }
```

`text-underline-offset: 0.25em` is a Hebrew requirement, not a preference. The
descenders ק, ן, ץ, ך drop below the baseline far enough to collide with a
default-offset underline, which turns a link into a smudge at body sizes. The
larger offset clears them.

Link URLs stay LTR inside Hebrew flow. That is already covered by the base file's
BiDi chapter: wrap the visible text in `<bdi>` when the label is itself a URL or
contains one.

External-link indicators, if used, go on the **end** side (left in RTL) and must
be `aria-hidden` with the meaning carried by the accessible name instead.

## Code

Inline code and fenced blocks both use the mono stack. The base file already
mandates the direction rule for both; this section supplies the chrome it did not
specify.

Mono stack: `JetBrains Mono, IBM Plex Mono, ui-monospace, Miriam Mono CLM,
monospace`.

Most monospace faces ship no Hebrew coverage at all, so a Hebrew comment inside a
code block falls back to a proportional face and breaks the grid mid-line. Miriam
Mono CLM sits in the stack to catch that. It will not match the Latin metrics
exactly, and it should not: a visible seam is better than silent fallback to
whatever the OS picks.

**Fenced blocks**: `prose-code-bg`, `rounded.md`, `spacing.md` padding,
`direction: ltr; text-align: start`, horizontal overflow scrolls inside the block
and never on the page. A language label, when shown, sits on the end side of the
block's top edge at `label-sm`.

**Inline code inside Hebrew** needs `<bdi>` per the base file. Give it
`padding: 2px 6px` and `rounded.xs`; anything larger disturbs the Hebrew line
rhythm at 1.7 leading.

## Blockquote and horizontal rule

Plain markdown `>` renders as an editorial quote, not a callout: no tinted
background, a 2px `prose-rule` bar on the **inline start** edge, `spacing.md`
inline-start padding, body text in `on-surface` at 90% opacity. Attribution lines
use `body-sm` in `tone-muted-ink`.

`---` renders as a 1px `prose-rule` line at `spacing.lg` block margins. No
shadow, no gradient, full measure width.

## Callouts

Fourteen container types, `::: type` through `:::`.

Thirteen of them are callouts. Fourteen distinct colors would destroy the
palette, and Coast is a four-color system that stays one, so those thirteen map
onto **seven tones**. Types sharing a tone are distinguished by icon and label,
not by hue. The fourteenth, `quote`, is not a callout at all and takes no tone.

| Type | Tone | Hebrew label |
| --- | --- | --- |
| `note` | `info` | הערה |
| `info` | `info` | מידע |
| `abstract` | `info` | תקציר |
| `tip` | `tip` | טיפ |
| `example` | `tip` | דוגמה |
| `success` | `success` | הצלחה |
| `warning` | `warning` | אזהרה |
| `experimental` | `warning` | ניסיוני |
| `danger` | `danger` | סכנה |
| `important` | `accent` | חשוב |
| `question` | `accent` | שאלה |
| `todo` | `muted` | לביצוע |
| `deprecated` | `muted` | מיושן |
| `quote` | none, see below | ציטוט |

Every value in the Tone column resolves to a real token triplet
(`tone-<name>`, `tone-<name>-bg`, `tone-<name>-ink`). There is no
`tone-editorial`, and there should not be: a renderer that looks up a tone for
`quote` is following the wrong branch.

The mapping is deliberate. `note` and `info` differing only by label is correct:
they carry the same weight and the reader should not be asked to decode two
blues. Reserve the loud tones. If everything is `danger`, nothing is.

### Anatomy

```text
┌ accent bar, 3px, inline-start edge
│  [icon] LABEL                 ← title row, title-sm, tone ink
│  body content                 ← body-md, on-surface
└ tone-*-bg background, rounded.md, spacing.md padding
```

The accent bar uses `border-inline-start`, never `border-left`. In RTL it lands
on the right edge, which is where a Hebrew reader's eye enters the block. Getting
this wrong puts the bar on the far side of the text and the whole block reads as
detached.

### Tone treatment

Each of the seven tones supplies three values: `tone-X` for the accent bar and
icon, `tone-X-bg` for the block background, `tone-X-ink` for the label text. Body
text inside a callout stays `on-surface`, never the tone ink. Tinting body copy
costs contrast and gains nothing.

Both light and dark values are defined in the frontmatter. The dark backgrounds
are near-black tints of the hue rather than lightened surfaces, so a callout
inside a dark reading view does not glow.

### `quote` is not a callout

It takes no tone triplet, and none is defined for it. `::: quote` renders in
Frank Ruhl Libre at `headline-sm`, no tint, no icon, a
hairline `prose-rule` bar on the inline-start edge, generous `spacing.lg` block
margins. It is the one container that gets Coast's editorial voice, and it is
where the system's serif earns its place inside a document.

A `>` blockquote nested inside `::: quote` is redundant. Render the inner
blockquote as plain paragraphs and let the container carry the semantics.

### Icons

Icons are chrome, so they are SVG at 18px in `tone-X`, set `aria-hidden`, and
inherit no text color from content.

Emoji in the authored content are **content**, not chrome. Do not strip them, do
not recolor them, and do not treat an author's 💡 as a substitute for the tip
icon. An emoji at the very start of a Hebrew line can push the BiDi algorithm
into reordering neighbouring punctuation, so a renderer that injects emoji itself
should place them after the label text, never before it.

### Nesting and titles

`::: type Custom Title` overrides the default label. The title row renders even
when empty; a callout with no label is a colored box with no meaning.

Nest at most two levels. Beyond that the inline-start bars stack and the content
column collapses, which on a narrow RTL viewport becomes unreadable fast.

## Details and collapsibles

`::: details Title` renders a native `<details>` and `<summary>`. Native, not a
JavaScript accordion: it survives print, keyboard, and screen readers with no
work.

- Summary row: `title-sm`, `compact-md` padding, chevron on the **end** side.
- Chevron flips per the base file's direction-aware glyph rule and rotates 90
  degrees on open, 120ms.
- Remove `::-webkit-details-marker` and `list-style` on the summary; the default
  triangle does not flip and cannot be styled.
- Container: `surface-container`, 1px `prose-rule` border, `rounded.md`.
- Open state adds a `prose-rule` hairline between summary and content.

Do not animate the height. Native `<details>` has no open transition that works
without JavaScript, and the half-built versions of it break the print view.

## Task lists

`- [ ]` and `- [x]`.

- `list-style: none`, the box is a real `<input type="checkbox">` styled with
  `appearance: none`, never a pseudo-element. Assistive tech needs the input.
- Box on the **start** side (right in RTL), `18px`, `rounded.xs`, 1px
  `prose-rule` border, `compact-md` gap to the label.
- Checked: fill `tone-success`, white checkmark, label drops to `tone-muted-ink`.
- **No strikethrough on completed items.** Struck Hebrew is materially harder to
  read than struck Latin: the line cuts through the x-height band where Hebrew
  carries nearly all of its letter-distinguishing detail, since there are no
  ascenders to survive above it. The muted color carries the state.
- Read-only rendering still needs `disabled` on the input, not `pointer-events:
  none`, so the state is announced rather than merely unclickable.

## Highlight

`==text==` renders `<mark>`.

Browser default `<mark>` is pure yellow and will not survive contact with Coast.
Override to `prose-mark-bg` (warm sand in light, a dark amber tint in dark) with
`on-surface` text.

```css
.prose mark {
  color: var(--on-surface);
  background-color: var(--prose-mark-bg);
  padding: 1px 4px;
  border-radius: var(--rounded-xs);
  box-decoration-break: clone;
  -webkit-box-decoration-break: clone;
}
```

`box-decoration-break: clone` is required. Without it, a highlight that wraps
across lines gets padding only on the first and last fragments, which in RTL
produces a ragged shape that reads as a rendering bug.

## Mermaid

Mermaid needs `theme: 'base'` for `themeVariables` to apply at all. Any other
theme value silently ignores them.

```js
mermaid.initialize({
  startOnLoad: true,
  theme: 'base',
  fontFamily: 'Heebo, sans-serif',
  flowchart: { htmlLabels: true, curve: 'basis' },
  themeVariables: {
    background:            '#F9F4E8',
    mainBkg:               '#FFFFFF',
    primaryColor:          '#E8F0F6',
    primaryTextColor:      '#2A3540',
    primaryBorderColor:    '#1E5F8C',
    secondaryColor:        '#FAF0DC',
    secondaryTextColor:    '#2A3540',
    secondaryBorderColor:  '#D4906B',
    tertiaryColor:         '#E4F2F4',
    tertiaryTextColor:     '#2A3540',
    tertiaryBorderColor:   '#5BA8B8',
    lineColor:             '#1E5F8C',
    textColor:             '#2A3540',
    nodeBorder:            '#1E5F8C',
    clusterBkg:            '#F1EDE1',
    clusterBorder:         '#E3DCC9',
    titleColor:            '#16496B',
    edgeLabelBackground:   '#F9F4E8',
    actorBkg:              '#FFFFFF',
    actorBorder:           '#1E5F8C',
    actorTextColor:        '#2A3540',
    signalColor:           '#1E5F8C',
    signalTextColor:       '#2A3540',
    noteBkgColor:          '#FAF0DC',
    noteTextColor:         '#2A3540',
    noteBorderColor:       '#D4906B',
  },
});
```

Dark mode swaps these values:

```js
{
  background:         '#0A1828',
  mainBkg:            '#142A40',
  textColor:          '#F9F4E8',
  lineColor:          '#76B5D9',
  primaryColor:       '#12283C',
  primaryBorderColor: '#76B5D9',
  clusterBkg:         '#101F30',
  clusterBorder:      '#1E3550',
}
```

Re-render on theme change. Mermaid bakes colors into the SVG at render time and
does not react to a CSS variable flip.

### Mermaid in RTL

This is where Hebrew diagrams usually fall apart.

- **Flow direction**: prefer `flowchart RL` over `LR` for Hebrew content. A
  left-to-right flow asks a Hebrew reader to trace against their reading
  direction. `TD` is direction-neutral and is the safest default.
- **The rendered SVG container is LTR.** Set `direction: ltr` on the wrapper.
  Mermaid computes text anchoring assuming LTR, and an inherited `dir="rtl"`
  shifts labels out of their nodes.
- **Hebrew labels need explicit direction.** With `htmlLabels: true`, author
  labels as `A["<span dir='rtl'>שלב ראשון</span>"]`. Without the span, a label
  mixing Hebrew with a number or a Latin identifier reorders unpredictably, the
  same failure the base file's BiDi chapter describes for inline content.
- **`htmlLabels: true` requires `securityLevel` to permit HTML.** On untrusted
  input keep `securityLevel: 'strict'` and accept plain-text labels instead.
  Never relax the security level to fix a typography problem on content you did
  not author.
- The fenced ` ```mermaid ` source block, when shown rather than rendered,
  follows the code-block rules: LTR, mono, `prose-code-bg`.

## Tables

The base file covers direction. This covers appearance.

- Header row: `surface` background, `label-md`, `tone-muted-ink`.
- Cell padding `compact-md`, 1px `prose-rule` bottom border per row.
- No vertical rules, no zebra striping. Coast's chrome is shadow and space, not
  grid lines.
- Numeric columns: right-aligned in LTR, and in RTL still visually grouped by
  wrapping cells in `<bdi>` per the base file.
- The table scrolls inside its own `overflow-inline: auto` container. The page
  never scrolls horizontally.

## Editor states

A system that saves documents needs states Coast never defined. These are the
`status-*` tokens.

| State | Token | Surface treatment |
| --- | --- | --- |
| Saved | `status-saved` | Dot plus label, `label-md`, no animation |
| Saving | `status-saving` | Dot with 1.2s opacity pulse |
| Unsaved changes | `status-dirty` | Dot plus label |
| Save failed | `status-error` | Dot plus label plus retry affordance |

Never signal state by color alone. Each state carries a text label; the dot is
redundant reinforcement. A `status-saved` green dot with no words fails both
color-blind readers and anyone glancing at the tab.

`prose-selection` sets `::selection` inside the editor. The browser default
selection blue clashes with the bone surface and, over Hebrew, reduces contrast
enough to matter.

**Focus ring**: 2px `focus-ring` seafoam at 2px offset, on every interactive
element, via `:focus-visible` only. The base file's pill inputs have no border to
carry focus, so without this the keyboard path through a document list is
invisible.

## Authoring rules the renderer enforces

The base file's punctuation chapter is a set of content rules, and a markdown
editor is the one surface that can actually enforce them. Surface these as
non-blocking author hints, never as autocorrect:

- ASCII `"` or `'` inside a Hebrew run: suggest gershayim ״ or geresh ׳.
- ASCII hyphen between two Hebrew words: suggest maqaf ־.
- Em dash anywhere: flag it. The base file bans it system-wide.
- `mm/dd/yyyy` date shapes: flag as US format.
- `₪` before an amount: suggest moving it after, with a space.

Autocorrecting these silently is wrong. An author quoting English inside Hebrew
legitimately needs ASCII quotes, and a code sample legitimately needs a hyphen.
Hint, let the author decide.

## Examples

### Callout, Hebrew

- **HE**: `::: tip` / חסכו זמן: שמירה אוטומטית פועלת כל 30 שניות.
- **EN**: `::: tip` / Save time: autosave runs every 30 seconds.

### Save-state label

- **HE**: כל השינויים נשמרו
- **EN**: All changes saved

### Save error

- **HE**: השמירה נכשלה. הטיוטה נשמרה מקומית, לנסות שוב?
- **EN**: Save failed. Your draft is stored locally, try again?
