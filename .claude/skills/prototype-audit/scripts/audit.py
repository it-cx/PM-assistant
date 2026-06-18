#!/usr/bin/env python3
"""Prototype audit: pixel-level layout/style validation via headless Chromium.

Outputs strict JSON to stdout. Exit codes:
  0 = pass (no issues)
  1 = fail (issues found)
  2 = environment error (Playwright missing, file not found, navigation failed, ...)
"""

import argparse
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[3]
REQUIREMENTS_REL = ".claude/skills/prototype-audit/scripts/requirements.txt"


def emit(obj, pretty=False):
    if pretty:
        print(json.dumps(obj, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def env_error(error_kind, fix):
    emit({"pass": False, "error": error_kind, "fix": fix, "issues": []}, pretty=False)
    sys.exit(2)


def parse_viewport(s):
    try:
        w, h = s.lower().split("x")
        return int(w), int(h)
    except Exception:
        raise argparse.ArgumentTypeError(f"--viewport must be WxH, got {s!r}")


def normalize_target(target):
    if target.startswith(("http://", "https://", "file://")):
        return target
    p = Path(target).resolve()
    if not p.exists():
        env_error("file_not_found", f"target file does not exist: {target}")
    return p.as_uri()


SCAN_JS = r"""
() => {
  const VW = window.innerWidth, VH = window.innerHeight;
  const issues = [];

  const isVisible = (el) => {
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return s.display !== 'none'
        && s.visibility !== 'hidden'
        && parseFloat(s.opacity) > 0
        && r.width > 0 && r.height > 0;
  };

  const cssPath = (el) => {
    const parts = [];
    let cur = el;
    let depth = 0;
    while (cur && cur.nodeType === 1 && depth < 4) {
      let part = cur.tagName.toLowerCase();
      if (cur.id) part += '#' + cur.id;
      const cls = Array.from(cur.classList).slice(0, 2).join('.');
      if (cls) part += '.' + cls;
      parts.unshift(part);
      cur = cur.parentElement;
      depth++;
    }
    return parts.join(' > ');
  };

  const colorToRgb = (color) => {
    if (!color) return null;
    const m = color.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    return m[1].split(',').map(s => parseFloat(s.trim())).slice(0, 3);
  };

  const relLuminance = (rgb) => {
    const a = rgb.map(v => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
  };

  const contrastRatio = (fg, bg) => {
    const L1 = relLuminance(fg), L2 = relLuminance(bg);
    const hi = Math.max(L1, L2), lo = Math.min(L1, L2);
    return (hi + 0.05) / (lo + 0.05);
  };

  const findBg = (el) => {
    let cur = el;
    while (cur && cur.nodeType === 1) {
      const s = getComputedStyle(cur);
      const bg = colorToRgb(s.backgroundColor);
      if (bg && (bg[0] + bg[1] + bg[2]) > 0) return bg;
      cur = cur.parentElement;
    }
    return [255, 255, 255];
  };

  const all = Array.from(document.querySelectorAll('*'));
  const vis = all.filter(isVisible);

  // 1. overflow
  for (const el of vis) {
    const r = el.getBoundingClientRect();
    if (r.right > VW + 1) {
      issues.push({
        type: 'overflow', axis: 'x', severity: 'high',
        selector: cssPath(el),
        geometry: { right: Math.round(r.right), viewport_right: VW, overflow_px: Math.round(r.right - VW) },
        evidence: `extends ${Math.round(r.right - VW)}px beyond viewport right`,
        suggestion: 'reduce width, constrain parent, or set overflow-x:hidden on the right container'
      });
    }
    const s = getComputedStyle(el);
    if (s.overflowX === 'hidden' && el.scrollWidth > el.clientWidth + 1) {
      issues.push({
        type: 'overflow', axis: 'x-clip', severity: 'medium',
        selector: cssPath(el),
        geometry: { scroll_w: el.scrollWidth, client_w: el.clientWidth, clip_px: el.scrollWidth - el.clientWidth },
        evidence: `content ${el.scrollWidth}px clipped to ${el.clientWidth}px by overflow-x:hidden`,
        suggestion: 'widen container, reduce child widths, or allow horizontal scroll'
      });
    }
  }

  // 2. truncation
  for (const el of vis) {
    const childTags = Array.from(el.children).map(c => c.tagName.toLowerCase());
    const isTextLeaf = el.children.length === 0 || childTags.every(t => t === 'br');
    if (!isTextLeaf) continue;
    const txt = el.textContent && el.textContent.trim();
    if (!txt) continue;
    const s = getComputedStyle(el);
    if (s.overflowY === 'hidden' && el.scrollHeight > el.clientHeight + 1) {
      issues.push({
        type: 'truncation', severity: 'medium',
        selector: cssPath(el),
        geometry: { scroll_h: el.scrollHeight, client_h: el.clientHeight, clip_px: el.scrollHeight - el.clientHeight },
        evidence: `text ${el.scrollHeight}px clipped to ${el.clientHeight}px (overflow-y:hidden)`,
        suggestion: 'increase height, allow wrap, or reduce font/content'
      });
    }
    if (s.textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth + 1) {
      issues.push({
        type: 'truncation', kind: 'ellipsis', severity: 'low',
        selector: cssPath(el),
        geometry: { scroll_w: el.scrollWidth, client_w: el.clientWidth },
        evidence: 'text-overflow:ellipsis is actively clipping',
        suggestion: 'widen element or shorten content if full text matters'
      });
    }
  }

  // 3. unstyled (default browser styles)
  for (const el of vis) {
    const s = getComputedStyle(el);
    if (s.color === 'rgb(0, 0, 238)' && el.tagName !== 'A') {
      issues.push({
        type: 'unstyled', kind: 'default-link-blue', severity: 'medium',
        selector: cssPath(el),
        computed: { color: s.color },
        evidence: `color ${s.color} matches browser default for unvisited links`,
        suggestion: 'apply an explicit color'
      });
    }
    if (/Times|(?!sans-)\bserif\b/i.test(s.fontFamily.replace(/sans-serif/gi, '')) && !el.closest('[data-default-font]')) {
      issues.push({
        type: 'unstyled', kind: 'default-font', severity: 'low',
        selector: cssPath(el),
        computed: { font_family: s.fontFamily },
        evidence: 'font-family includes serif/Times (likely browser default)',
        suggestion: 'set explicit font-family'
      });
    }
  }

  // 4. zero-size: containers that collapsed despite having children
  for (const el of all) {
    if (el.children.length === 0) continue;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width >= 1 && r.height >= 1) continue;
    issues.push({
      type: 'zero-size', severity: 'high',
      selector: cssPath(el),
      geometry: { width: Math.round(r.width * 10) / 10, height: Math.round(r.height * 10) / 10 },
      computed: { display: s.display, position: s.position },
      evidence: `container collapsed to ${Math.round(r.width)}x${Math.round(r.height)} despite ${el.children.length} children`,
      suggestion: 'check display/position/width/height rules — flex/grid may have failed to apply'
    });
  }

  // 5. overlap: pairs of visible siblings whose rects intersect
  const parents = new Set();
  for (const el of vis) { if (el.parentElement) parents.add(el.parentElement); }
  for (const parent of parents) {
    const kids = Array.from(parent.children).filter(isVisible);
    for (let i = 0; i < kids.length; i++) {
      for (let j = i + 1; j < kids.length; j++) {
        const a = kids[i], b = kids[j];
        const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
        const ix = Math.max(0, Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left));
        const iy = Math.max(0, Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top));
        if (ix < 2 || iy < 2) continue;
        const area = ix * iy;
        if (area <= 4) continue;
        const za = getComputedStyle(a).zIndex, zb = getComputedStyle(b).zIndex;
        if (za !== 'auto' && zb !== 'auto' && za !== zb) continue;
        if (a.hasAttribute('data-overlay') || b.hasAttribute('data-overlay')) continue;
        issues.push({
          type: 'overlap', severity: area > 1000 ? 'high' : 'medium',
          selector: cssPath(a), selector_other: cssPath(b),
          geometry: { overlap_w: Math.round(ix), overlap_h: Math.round(iy), overlap_area: Math.round(area) },
          evidence: `two siblings overlap by ${Math.round(ix)}x${Math.round(iy)} px (${Math.round(area)} px²)`,
          suggestion: 'increase gap/margin, fix grid-column/row, or set explicit z-index if intentional'
        });
      }
    }
  }

  // 6. contrast: leaf-text elements below WCAG AA
  for (const el of vis) {
    if (!el.textContent || !el.textContent.trim()) continue;
    if (el.children.length > 0) {
      const directText = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim());
      if (!directText) continue;
    }
    const s = getComputedStyle(el);
    const fg = colorToRgb(s.color);
    if (!fg) continue;
    const bg = findBg(el);
    const ratio = contrastRatio(fg, bg);
    if (ratio < 4.5) {
      issues.push({
        type: 'contrast', severity: ratio < 3 ? 'high' : 'medium',
        selector: cssPath(el),
        computed: { fg: s.color, bg: `rgb(${bg.join(',')})`, ratio: Math.round(ratio * 10) / 10 },
        evidence: `text contrast ${ratio.toFixed(2)} below WCAG AA (4.5)`,
        suggestion: ratio < 3
          ? 'significantly increase text contrast (current ratio is dangerously low)'
          : 'increase text contrast to at least 4.5'
      });
    }
  }

  return issues;
}
"""


def run_audit(target, viewport, timeout, pretty, file_arg):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        env_error(
            "playwright_not_installed",
            f"pip install --user -r {REQUIREMENTS_REL} && python -m playwright install chromium",
        )

    start = time.time()
    page_errors = []
    console_errors = []
    failed_responses = []
    failed_requests = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
            page = context.new_page()

            page.on("pageerror", lambda e: page_errors.append({
                "message": str(e), "stack": getattr(e, "stack", None),
            }))
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
            page.on("response", lambda r: failed_responses.append((r.status, r.url)) if r.status >= 400 else None)
            page.on("requestfailed", lambda req: failed_requests.append((req.url, req.failure)) if req.failure else None)

            try:
                page.goto(target, wait_until="networkidle", timeout=timeout * 1000)
            except Exception as e:
                env_error("navigation_failed", f"page.goto({target!r}) failed: {e}")

            try:
                page.wait_for_timeout(800)
            except Exception:
                pass

            dom_issues = page.evaluate(SCAN_JS)
        finally:
            browser.close()

    duration_ms = int((time.time() - start) * 1000)

    runtime_issues = []
    for e in page_errors:
        runtime_issues.append({
            "type": "pageerror", "severity": "high", "selector": None,
            "evidence": e["message"], "stack": e.get("stack"),
            "suggestion": "fix uncaught exception before further UI checks are meaningful",
        })
    for msg in console_errors:
        runtime_issues.append({
            "type": "console-error", "severity": "medium", "selector": None,
            "evidence": msg, "suggestion": "investigate console.error call",
        })
    for status, url in failed_responses:
        runtime_issues.append({
            "type": "resource-error", "severity": "medium", "selector": None,
            "kind": f"http-{status}", "url": url,
            "evidence": f"HTTP {status} on {url}",
            "suggestion": "fix URL, swap CDN, or remove the reference",
        })
    for url, err in failed_requests:
        runtime_issues.append({
            "type": "resource-error", "severity": "medium", "selector": None,
            "kind": "request-failed", "url": url, "error": err,
            "evidence": f"network request failed: {url} ({err})",
            "suggestion": "check URL/network, or remove the reference",
        })

    all_issues = dom_issues + runtime_issues

    sev_rank = {"high": 3, "medium": 2, "low": 1}
    dedup = {}
    for it in all_issues:
        key = (it["type"], it.get("selector") or "")
        if key not in dedup or sev_rank[it["severity"]] > sev_rank[dedup[key]["severity"]]:
            dedup[key] = it
    issues = list(dedup.values())
    issues.sort(key=lambda it: -sev_rank[it["severity"]])

    by_type = {}
    for it in issues:
        by_type[it["type"]] = by_type.get(it["type"], 0) + 1

    output = {
        "file": file_arg,
        "viewport": {"w": viewport[0], "h": viewport[1]},
        "duration_ms": duration_ms,
        "iteration": None,
        "pass": len(issues) == 0,
        "summary": {
            "total": len(issues),
            "high": sum(1 for it in issues if it["severity"] == "high"),
            "medium": sum(1 for it in issues if it["severity"] == "medium"),
            "low": sum(1 for it in issues if it["severity"] == "low"),
            "by_type": by_type,
        },
        "issues": issues,
    }
    emit(output, pretty=pretty)
    return 0 if output["pass"] else 1


def main():
    ap = argparse.ArgumentParser(description="Pixel-level layout/style audit of an HTML prototype.")
    ap.add_argument("target", help="HTML file path or URL")
    ap.add_argument("--viewport", type=parse_viewport, default=(1920, 1080),
                    help="viewport size as WxH (default 1920x1080)")
    ap.add_argument("--timeout", type=int, default=15,
                    help="navigation timeout in seconds (default 15)")
    ap.add_argument("--pretty", action="store_true",
                    help="pretty-print JSON for human inspection")
    args = ap.parse_args()

    target = normalize_target(args.target)
    sys.exit(run_audit(target, args.viewport, args.timeout, args.pretty, args.target))


if __name__ == "__main__":
    main()
