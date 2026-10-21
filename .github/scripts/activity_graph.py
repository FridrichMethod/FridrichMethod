#!/usr/bin/env python3
"""Render a GitHub contribution activity graph as a static SVG.

Stands in for github-readme-activity-graph.vercel.app, which has been offline
since 2026-09. Mirrors its "github-compact" look and its default 31-day window.
Uses only the standard library; reads the token from the GITHUB_TOKEN env var.
On any error it exits non-zero without touching the output file.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from xml.sax.saxutils import escape

QUERY = """
query ($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    name
    login
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""

# "github-compact" theme from the original service.
LINE = "#26a641"
TEXT = "#8b949e"
WIDTH, HEIGHT = 1200, 420
LEFT, RIGHT, TOP, BOTTOM = 90, 1150, 80, 350


def fetch(login: str, days: int, token: str) -> tuple[str, list[tuple[date, int]]]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    variables = {
        "login": login,
        "from": (now - timedelta(days=days)).isoformat(),
        # One extra day in case the runner clock is behind GitHub's.
        "to": (now + timedelta(days=1)).isoformat(),
    }
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL errors: {payload['errors']}")
    user = (payload.get("data") or {}).get("user")
    if user is None:
        raise RuntimeError(f"user {login!r} not found")

    weeks = user["contributionsCollection"]["contributionCalendar"]["weeks"]
    contribs = sorted(
        (date.fromisoformat(d["date"]), d["contributionCount"])
        for w in weeks
        for d in w["contributionDays"]
    )
    if not contribs:
        raise RuntimeError("empty contribution calendar")
    # A trailing zero usually means "today" has not really started yet.
    if contribs[-1][1] == 0:
        contribs.pop()
    return user["name"] or user["login"], contribs[-days:]


def nice_step(max_value: int, max_ticks: int = 8) -> int:
    """Smallest integer step from {1, 2, 5} x 10^k giving at most max_ticks intervals."""
    raw = max(max_value, 1) / max_ticks
    exp = 10 ** math.floor(math.log10(raw)) if raw >= 1 else 1
    for m in (1, 2, 5, 10):
        if m * exp >= raw:
            return m * exp
    return 10 * exp


def render(name: str, contribs: list[tuple[date, int]]) -> str:
    counts = [c for _, c in contribs]
    step = nice_step(max(counts))
    y_max = max(step * math.ceil(max(counts) / step), step)
    n = len(contribs)

    def x(i: int) -> float:
        return LEFT + (RIGHT - LEFT) * (i / (n - 1) if n > 1 else 0.5)

    def y(v: float) -> float:
        return BOTTOM - (BOTTOM - TOP) * v / y_max

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" fill="none">',
        "<style>"
        "text{font-family:'Segoe UI',Ubuntu,Sans-Serif;fill:" + TEXT + "}"
        ".title{font-size:20px;font-weight:600}"
        ".label{font-size:12px}"
        ".axis{font-size:14px}"
        ".grid{stroke:" + TEXT + ";stroke-width:1;stroke-opacity:.3;stroke-dasharray:2}"
        "</style>",
        f'<text class="title" x="{WIDTH / 2}" y="40" text-anchor="middle">'
        f"{escape(name)}&apos;s Contribution Graph</text>",
    ]

    for v in range(0, y_max + 1, step):
        yy = y(v)
        out.append(f'<line class="grid" x1="{LEFT}" y1="{yy:.1f}" x2="{RIGHT}" y2="{yy:.1f}"/>')
        out.append(f'<text class="label" x="{LEFT - 10}" y="{yy + 4:.1f}" text-anchor="end">{v}</text>')
    for i, (d, _) in enumerate(contribs):
        xx = x(i)
        out.append(f'<line class="grid" x1="{xx:.1f}" y1="{TOP}" x2="{xx:.1f}" y2="{BOTTOM}"/>')
        out.append(f'<text class="label" x="{xx:.1f}" y="{BOTTOM + 20}" text-anchor="middle">{d.day}</text>')

    points = " ".join(f"{x(i):.1f},{y(c):.1f}" for i, c in enumerate(counts))
    out.append(
        f'<polygon points="{x(0):.1f},{BOTTOM} {points} {x(n - 1):.1f},{BOTTOM}" '
        f'fill="{LINE}" fill-opacity=".1"/>'
    )
    out.append(
        f'<polyline points="{points}" stroke="{LINE}" stroke-width="4" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
    )
    out.extend(f'<circle cx="{x(i):.1f}" cy="{y(c):.1f}" r="5" fill="{TEXT}"/>' for i, c in enumerate(counts))

    first, last = contribs[0][0], contribs[-1][0]
    out.append(
        f'<text class="axis" x="{(LEFT + RIGHT) / 2}" y="{HEIGHT - 20}" text-anchor="middle">'
        f"Days ({first:%b} {first.day} – {last:%b} {last.day}, {last.year})</text>"
    )
    mid = (TOP + BOTTOM) / 2
    out.append(
        f'<text class="axis" x="25" y="{mid}" text-anchor="middle" '
        f'transform="rotate(-90 25 {mid})">Contributions</text>'
    )
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--user", required=True)
    parser.add_argument("--days", type=int, default=31)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is not set", file=sys.stderr)
        return 1
    try:
        name, contribs = fetch(args.user, args.days, token)
    except Exception as exc:  # noqa: BLE001 - report and keep the previous SVG
        print(f"Failed to fetch contributions: {exc}", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render(name, contribs))
    print(f"Wrote {args.out} ({len(contribs)} days, {sum(c for _, c in contribs)} contributions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
