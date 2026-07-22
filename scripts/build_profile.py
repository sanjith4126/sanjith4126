#!/usr/bin/env python3
"""
build_profile.py — renders sanjith4126's profile README from live GitHub data.

Two outputs:
  1. assets/status.svg   — a service-status board drawn from scratch (no third-party widget)
  2. README.md           — template with <!--BUILD:X--> blocks filled in

Run locally:   GITHUB_TOKEN=ghp_xxx python3 scripts/build_profile.py
Run in CI:     see .github/workflows/build-profile.yml
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent.parent
USER = os.environ.get("PROFILE_USER", "sanjith4126")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
API = "https://api.github.com"
IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------- design tokens
# Monitoring-console palette. Slate ink rather than pure black so it reads as a
# console, not a hacker-terminal cliche. One accent (amber) does all the work.
C = {
    "bg":     "#10151C",
    "panel":  "#161D26",
    "rule":   "#253040",
    "text":   "#C9D4E2",
    "dim":    "#6B7C91",
    "faint":  "#3D4B5C",
    "ok":     "#4FB286",
    "accent": "#E0A458",
    "hot":    "#C5566A",
}
MONO = "ui-monospace,'SF Mono','Cascadia Mono','DejaVu Sans Mono',Menlo,monospace"

LANG_TAG = {
    "C#": "c#", "Python": "py", "JavaScript": "js", "TypeScript": "ts",
    "HTML": "html", "CSS": "css", "Java": "java", "C": "c", "Shell": "sh",
    "Jupyter Notebook": "ipynb", "Dockerfile": "docker", None: "—",
}


# ---------------------------------------------------------------- http helpers
def gh(path: str) -> object:
    """GET the GitHub REST API. Returns parsed JSON, or None on failure."""
    req = Request(f"{API}{path}", headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USER}-profile-builder",
        **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
    })
    try:
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except (HTTPError, URLError, TimeoutError) as e:
        print(f"  ! {path} -> {e}", file=sys.stderr)
        return None


def esc(s: object) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def ago(iso: str | None) -> str:
    """'2026-07-19T10:00:00Z' -> '3d'."""
    if not iso:
        return "—"
    then = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    secs = (datetime.now(timezone.utc) - then).total_seconds()
    for div, unit in ((86400 * 365, "y"), (86400 * 30, "mo"), (86400, "d"), (3600, "h")):
        if secs >= div:
            return f"{int(secs // div)}{unit}"
    return f"{int(secs // 60)}m"


# ---------------------------------------------------------------- data fetch
def load_config() -> dict:
    cfg_path = ROOT / "data" / "config.json"
    if cfg_path.exists():
        return json.loads(cfg_path.read_text())
    return {"services": [], "max_services": 4}


def fetch_services(cfg: dict) -> list[dict]:
    """Repos to display as 'services', newest push first."""
    repos = gh(f"/users/{USER}/repos?per_page=100&sort=pushed") or []
    repos = [r for r in repos if not r.get("fork") and not r.get("archived")]
    by_name = {r["name"].lower(): r for r in repos}

    picked, wanted = [], [s.lower() for s in cfg.get("services", [])]
    for name in wanted:                       # explicit order from config.json
        if name in by_name:
            picked.append(by_name.pop(name))
    for r in repos:                           # fill remaining slots by recency
        if len(picked) >= cfg.get("max_services", 4):
            break
        if r["name"].lower() in by_name:
            picked.append(r)
            by_name.pop(r["name"].lower())

    out = []
    for r in picked[: cfg.get("max_services", 4)]:
        out.append({
            "name": r["name"],
            "lang": LANG_TAG.get(r.get("language"), (r.get("language") or "—").lower()),
            "pushed": r.get("pushed_at"),
            "stars": r.get("stargazers_count", 0),
            "desc": (r.get("description") or "").strip(),
        })
    return out


def fetch_activity(days: int = 30) -> tuple[list[int], int]:
    """Commits pushed per day from the public events feed. Returns (buckets, total)."""
    events: list[dict] = []
    for page in (1, 2, 3):
        chunk = gh(f"/users/{USER}/events/public?per_page=100&page={page}")
        if not chunk:
            break
        events.extend(chunk)
        if len(chunk) < 100:
            break

    today = datetime.now(timezone.utc).date()
    counts: Counter[int] = Counter()
    for e in events:
        if e.get("type") != "PushEvent":
            continue
        when = datetime.strptime(e["created_at"], "%Y-%m-%dT%H:%M:%SZ").date()
        offset = (today - when).days
        if 0 <= offset < days:
            counts[offset] += e.get("payload", {}).get("distinct_size", 0) or 0

    buckets = [counts.get(days - 1 - i, 0) for i in range(days)]  # oldest -> newest
    return buckets, sum(buckets)


# ---------------------------------------------------------------- svg render
def sparkbars(values: list[int], x: float, y: float, w: float, h: float) -> str:
    """A row of bars. y is the BASELINE (bars grow upward)."""
    if not values:
        return ""
    peak = max(values) or 1
    gap, n = 2.0, len(values)
    bw = (w - gap * (n - 1)) / n
    out = []
    for i, v in enumerate(values):
        bh = max(1.5, (v / peak) * h)
        bx = x + i * (bw + gap)
        fill = C["accent"] if v == peak and v > 0 else (C["ok"] if v else C["faint"])
        op = "1" if v else "0.55"
        out.append(
            f'<rect x="{bx:.1f}" y="{y - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
            f'rx="1" fill="{fill}" opacity="{op}"/>'
        )
    return "".join(out)


def render_svg(services: list[dict], buckets: list[int], total: int,
               ms: int, build: str) -> str:
    W, H = 860, 62 + 34 * max(len(services), 1) + 132
    now = datetime.now(IST)
    rows = []

    # --- response line: the signature element. This card IS an HTTP response.
    rows.append(
        f'<text x="28" y="42" font-family="{MONO}" font-size="15" font-weight="600">'
        f'<tspan fill="{C["accent"]}">GET</tspan>'
        f'<tspan fill="{C["text"]}" dx="10">/users/{esc(USER)}/status</tspan></text>'
    )
    rows.append(
        f'<g><circle cx="{W - 128}" cy="37" r="4" fill="{C["ok"]}">'
        f'<animate attributeName="opacity" values="1;0.25;1" dur="2.4s" repeatCount="indefinite"/>'
        f'</circle>'
        f'<text x="{W - 114}" y="42" font-family="{MONO}" font-size="14" fill="{C["ok"]}">200 OK</text>'
        f'<text x="{W - 28}" y="42" font-family="{MONO}" font-size="12" fill="{C["dim"]}" '
        f'text-anchor="end">{ms}ms</text></g>'
    )
    rows.append(f'<line x1="28" y1="60" x2="{W - 28}" y2="60" stroke="{C["rule"]}" stroke-width="1"/>')

    # --- services table
    y = 88
    rows.append(f'<text x="28" y="{y}" font-family="{MONO}" font-size="11" fill="{C["dim"]}" '
                f'letter-spacing="1.6">SERVICES</text>')
    rows.append(f'<text x="{W - 28}" y="{y}" font-family="{MONO}" font-size="11" fill="{C["dim"]}" '
                f'letter-spacing="1.6" text-anchor="end">LAST DEPLOY</text>')
    y += 26

    if not services:
        rows.append(f'<text x="28" y="{y}" font-family="{MONO}" font-size="13" fill="{C["dim"]}">'
                    f'no public repositories yet — push one to populate this board</text>')
        y += 34
    for s in services:
        fresh = s["pushed"] and (datetime.now(timezone.utc) - datetime.strptime(
            s["pushed"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).days < 30
        dot = C["ok"] if fresh else C["faint"]
        rows.append(f'<circle cx="34" cy="{y - 5}" r="3.5" fill="{dot}"/>')
        rows.append(f'<text x="50" y="{y}" font-family="{MONO}" font-size="13.5" '
                    f'fill="{C["text"]}">{esc(s["name"])}</text>')
        rows.append(f'<text x="330" y="{y}" font-family="{MONO}" font-size="12" '
                    f'fill="{C["accent"]}">{esc(s["lang"])}</text>')
        if s["desc"]:
            rows.append(f'<text x="400" y="{y}" font-family="{MONO}" font-size="11.5" '
                        f'fill="{C["dim"]}">{esc(s["desc"][:44])}</text>')
        rows.append(f'<text x="{W - 28}" y="{y}" font-family="{MONO}" font-size="12.5" '
                    f'fill="{C["dim"]}" text-anchor="end">{ago(s["pushed"])}</text>')
        y += 34

    rows.append(f'<line x1="28" y1="{y - 14}" x2="{W - 28}" y2="{y - 14}" '
                f'stroke="{C["rule"]}" stroke-width="1"/>')

    # --- activity strip
    y += 14
    rows.append(f'<text x="28" y="{y}" font-family="{MONO}" font-size="11" fill="{C["dim"]}" '
                f'letter-spacing="1.6">COMMITS PUSHED / 30d</text>')
    rows.append(f'<text x="{W - 28}" y="{y}" font-family="{MONO}" font-size="13" '
                f'fill="{C["text"]}" text-anchor="end">{total}</text>')
    rows.append(sparkbars(buckets, 28, y + 52, W - 56, 40))

    # --- response headers footer
    fy = y + 84
    rows.append(f'<line x1="28" y1="{fy - 18}" x2="{W - 28}" y2="{fy - 18}" '
                f'stroke="{C["rule"]}" stroke-width="1"/>')
    stamp = now.strftime("%Y-%m-%dT%H:%M IST")
    rows.append(
        f'<text x="28" y="{fy}" font-family="{MONO}" font-size="11" fill="{C["faint"]}">'
        f'<tspan fill="{C["dim"]}">x-generated-at:</tspan> {stamp}'
        f'<tspan fill="{C["dim"]}" dx="22">x-build:</tspan> #{esc(build)}'
        f'<tspan fill="{C["dim"]}" dx="22">cache-control:</tspan> max-age=21600</text>'
    )
    rows.append(
        f'<rect x="{W - 40}" y="{fy - 9}" width="8" height="12" fill="{C["accent"]}">'
        f'<animate attributeName="opacity" values="1;0;1" dur="1.1s" repeatCount="indefinite"/>'
        f'</rect>'
    )

    body = "\n  ".join(rows)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Live service status board for {esc(USER)}">
  <rect width="{W}" height="{H}" rx="10" fill="{C['bg']}"/>
  <rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="10" fill="none" stroke="{C['rule']}"/>
  {body}
</svg>
"""


# ---------------------------------------------------------------- readme build
def inject(md: str, key: str, value: str) -> str:
    start, end = f"<!--BUILD:{key}-->", f"<!--/BUILD:{key}-->"
    if start not in md or end not in md:
        print(f"  ! marker {key} missing from template", file=sys.stderr)
        return md
    head = md.split(start)[0]
    tail = md.split(end)[1]
    return f"{head}{start}\n{value}\n{end}{tail}"


def main() -> int:
    t0 = time.perf_counter()
    cfg = load_config()
    print(f"-> building profile for {USER}")

    services = fetch_services(cfg)
    buckets, total = fetch_activity()
    ms = int((time.perf_counter() - t0) * 1000)
    build = os.environ.get("GITHUB_RUN_NUMBER", "local")

    (ROOT / "assets").mkdir(exist_ok=True)
    (ROOT / "assets" / "status.svg").write_text(
        render_svg(services, buckets, total, ms, build), encoding="utf-8")
    print(f"   assets/status.svg  ({len(services)} services, {total} commits/30d, {ms}ms)")

    tpl = ROOT / "README.template.md"
    if tpl.exists():
        md = tpl.read_text(encoding="utf-8")

        now_lines = "\n".join(
            f"- **{s['name']}** — {s['desc'] or 'no description set'} "
            f"`{s['lang']}` · last push {ago(s['pushed'])} ago"
            for s in services[:3]
        ) or "- nothing public yet"
        md = inject(md, "NOW", now_lines)

        gb = ROOT / "data" / "guestbook.json"
        entries = json.loads(gb.read_text()) if gb.exists() else []
        gb_md = "\n".join(
            f"> {e['message']}  \n> — [@{e['user']}](https://github.com/{e['user']}), {e['date']}"
            for e in entries[-5:][::-1]
        ) or "_No entries yet. Open an issue titled `/sign <your message>` to leave one._"
        md = inject(md, "GUESTBOOK", gb_md)

        md = inject(md, "STAMP",
                    f"<sub><code>last build: "
                    f"{datetime.now(IST).strftime('%d %b %Y, %H:%M IST')} "
                    f"&middot; run #{build} &middot; {ms}ms</code></sub>")

        (ROOT / "README.md").write_text(md, encoding="utf-8")
        print("   README.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
