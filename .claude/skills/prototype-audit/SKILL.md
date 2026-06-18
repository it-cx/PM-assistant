---
name: prototype-audit
description: Pixel-level layout/style validation of a generated HTML prototype. Runs headless Chromium, measures overflow / overlap / truncation / unstyled-defaults / contrast / zero-size / pageerror / console-error / failed-requests, and emits strict structured JSON for an upstream reflection loop. Use after any HTML prototype is generated, when the user says "审计原型 / validate this HTML / pixel check / 看看布局有没有问题 / output JSON for the fix loop". Does NOT judge aesthetics — only objective, measurable violations.
---

# Prototype Audit

Runs Playwright headless Chromium against a generated HTML prototype and emits a strict JSON report of objective layout/style violations. Designed as the **check node** in a generate → check → fix loop (max 5 iterations).

## When to Use

- Right after a design / prototype / ui-styling skill produces an HTML file
- User asks to "audit / validate / check" a prototype for layout problems
- Upstream reflection loop needs machine-readable issues to feed back to the generator
- After applying a fix and re-verifying

## Prerequisites (one-time)

```bash
pip install --user -r .claude/skills/prototype-audit/scripts/requirements.txt
python -m playwright install chromium
```

If Playwright is not installed, the script outputs `{"pass": false, "error": "playwright_not_installed", "fix": "..."}` and exits 2 — surface that fix command to the user.

## Quick Start

```bash
# default viewport 1920x1080, single-line JSON for piping
python .claude/skills/prototype-audit/scripts/audit.py dashboard/index.html

# custom viewport
python .claude/skills/prototype-audit/scripts/audit.py dashboard/index.html --viewport 1440x900

# pretty-print for human inspection
python .claude/skills/prototype-audit/scripts/audit.py dashboard/index.html --pretty
```

Exit codes: `0` = pass, `1` = issues found, `2` = environment error.

## What It Checks

All checks are **objective and pixel-level** — no aesthetic judgment.

| Type | Trigger | Default severity |
|------|---------|------------------|
| `overflow` (x) | Element extends beyond viewport right edge | high |
| `overflow` (x-clip) | `overflow-x: hidden` clips content (`scrollWidth > clientWidth`) | medium |
| `truncation` | `overflow-y: hidden` clips text (`scrollHeight > clientHeight`) | medium |
| `truncation` (ellipsis) | `text-overflow: ellipsis` actively clipping | low |
| `unstyled` | Color is `rgb(0,0,238)` on non-link (browser default link blue) | medium |
| `unstyled` | Font-family includes `Times` / `serif` (browser default) | low |
| `zero-size` | Container with children has width or height of 0 | high |
| `overlap` | Two visible siblings intersect by > 4 px², same z-index | medium (high if > 1000 px²) |
| `contrast` | Text contrast ratio below 4.5 (WCAG AA), YIQ formula | medium (high if < 3) |
| `pageerror` | Uncaught JS exception | high |
| `console-error` | `console.error(...)` call | medium |
| `resource-error` | HTTP 4xx/5xx response, or requestfailed (DNS / network) | medium |

## Output JSON Schema

```json
{
  "file": "dashboard/index.html",
  "viewport": { "w": 1920, "h": 1080 },
  "duration_ms": 3187,
  "iteration": null,
  "pass": false,
  "summary": {
    "total": 4,
    "high": 1, "medium": 2, "low": 1,
    "by_type": { "overflow": 1, "contrast": 2, "unstyled": 1 }
  },
  "issues": [
    {
      "type": "overflow",
      "severity": "high",
      "selector": "div.panel.fleet-strip > div.fleet-strip-inner",
      "geometry": { "right": 1980, "viewport_right": 1920, "overflow_px": 60 },
      "evidence": "extends 60px beyond viewport right",
      "suggestion": "reduce width, constrain parent, or set overflow-x:hidden on the right container"
    }
  ],
  "report_path": "E:\\CCproject\\.scratch\\audit-reports\\dashboard_20260618-131915.json"
}
```

Each issue includes:
- `type` and `severity` (`high` / `medium` / `low`)
- `selector` — CSS path up to 4 levels deep (`tag#id.cls1.cls2 > ...`)
- `geometry` or `computed` — exact pixel measurements or computed-style values that triggered the issue
- `evidence` — human-readable one-liner of what was measured
- `suggestion` — machine-actionable hint to feed back to the generator

## Report Persistence

**When `pass: false`** (issues found), the full JSON is also saved to disk:

- **Location**: `<repo>/.scratch/audit-reports/` (gitignored — ephemeral, not committed)
- **Filename**: `<input-stem>_<YYYYMMDD-HHMMSS>.json` (e.g., `dashboard_20260618-131915.json`)
- **Path is surfaced three ways**:
  1. `report_path` field appended to the JSON output
  2. `Report saved: <path>` line printed to **stderr** (so stdout stays pure JSON for piping)
  3. The file itself on disk for later inspection

**When `pass: true`** — nothing is saved, no `report_path` field, no stderr line. A clean run leaves no trace.

This means each failed audit produces a timestamped artifact you can diff against the next iteration's report, or hand to the upstream reflection loop as a file rather than a pipe.

## Workflow (Generate → Check → Fix loop)

When invoked as part of a closed loop with an upstream generator skill:

1. Receive the generated HTML path from upstream.
2. Run `audit.py` and parse the JSON.
3. **If `pass: true`** → return `{"pass": true, "iterations": N}` to upstream. Loop ends.
4. **If `pass: false` and `iterations < 5`** → package the `issues` array (sorted by severity, `suggestion` preserved) as a fix directive and hand back to upstream. Upstream translates them into generator-skill instructions, regenerates, returns to step 1.
5. **If `iterations == 5` and still failing** → return `{"pass": false, "iterations": 5, "remaining_issues": [...]}` and surface to the human for judgment.

Between iterations, **deduplicate** issues by `(type, selector)` — if the same selector triggers the same issue across runs, it counts as one persistent problem (not a new one). Take the highest-severity version.

## Marking Intentional Overlays (`data-overlay`)

Many designs have elements that **intentionally** overlap or extend beyond the viewport — a centered label on top of a ring chart, decorative corner brackets, a horizontally scrolling card strip, a scanline animation overlay. Without context the auditor reports these as violations.

To suppress false positives, add the `data-overlay` attribute to the element (or any ancestor):

```html
<div class="fleet-strip" data-overlay> ... </div>      <!-- covers all child cards -->
<div class="scanline" data-overlay></div>
<span class="corner tl" data-overlay></span>
```

The auditor checks `el.closest('[data-overlay]')` — so marking a container suppresses checks for everything inside it. Applies to both `overflow` and `overlap` checks.

Use sparingly: only for elements whose out-of-flow position is by design. If you find yourself adding `data-overlay` to "make the audit pass", that's a signal the layout actually has a problem.

## Anti-patterns (explicitly NOT in scope)

- ❌ Aesthetic judgments — "this looks ugly", "this color is unappealing"
- ❌ Spec compliance — "does this match the PRD?" (use the `review` skill)
- ❌ Visual regression against a baseline screenshot
- ❌ Multi-viewport sweeps in one call (invoke the script multiple times instead)
- ❌ Full a11y audit (alt text, ARIA roles, keyboard nav) — only contrast is checked
- ❌ Human-readable Markdown reports — **always JSON**, by design
- ❌ Mutating the HTML — the generator skill fixes, this skill only judges

## Routing

When the user says "审计 / validate / pixel-check the prototype at PATH":

1. Run `python .claude/skills/prototype-audit/scripts/audit.py PATH`.
2. If exit 2 (environment error): show the JSON `fix` field to the user and stop.
3. Otherwise: hand the full JSON to upstream or to the user. Do **not** summarize as prose unless asked — the JSON is the contract.
