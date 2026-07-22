#!/usr/bin/env python3
"""
guestbook.py — turns an approved GitHub issue into a guestbook entry.

Reads from the environment (set by the workflow):
  ISSUE_TITLE, ISSUE_USER, ISSUE_NUMBER

Only runs after you apply the `approved` label, so nothing lands on your
profile without you seeing it first.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "guestbook.json"
IST = timezone(timedelta(hours=5, minutes=30))

MAX_LEN = 180
BLOCKLIST = re.compile(r"https?://|<[^>]+>|@[A-Za-z0-9_-]{2,}", re.I)


def clean(raw: str) -> str | None:
    """Strip the command prefix, reject anything with links, HTML or pings."""
    msg = re.sub(r"^\s*/sign\b[:\s]*", "", raw, flags=re.I).strip()
    if not msg:
        return None
    msg = " ".join(msg.split())[:MAX_LEN]
    if BLOCKLIST.search(msg):
        return None
    return msg


def main() -> int:
    title = os.environ.get("ISSUE_TITLE", "")
    user = os.environ.get("ISSUE_USER", "")
    number = os.environ.get("ISSUE_NUMBER", "")

    if not title.lower().lstrip().startswith("/sign"):
        print(f"issue #{number} is not a /sign command — skipping")
        return 0

    msg = clean(title)
    if not msg:
        print(f"issue #{number} rejected: empty, or contains a link/HTML/mention")
        return 0

    entries = json.loads(STORE.read_text()) if STORE.exists() else []
    entries = [e for e in entries if e["user"].lower() != user.lower()]  # one per person
    entries.append({
        "user": user,
        "message": msg,
        "date": datetime.now(IST).strftime("%d %b %Y"),
        "issue": number,
    })

    STORE.parent.mkdir(exist_ok=True)
    STORE.write_text(json.dumps(entries[-50:], indent=2) + "\n", encoding="utf-8")
    print(f"added entry from @{user}: {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
