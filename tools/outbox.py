#!/usr/bin/env python3
"""Turn an approved outbox file into exact Gmail-draft payloads, and keep the tracker in step.

The browser/screenshot route for sending is retired: Claude creates Gmail drafts with the Gmail connector
(create_draft) from the payloads printed here, and Aditya presses Send himself in Gmail.

  python3 tools/outbox.py prepare drafts/outbox/2026-11-13.md [--only 1,3]
      Parses the file and prints one JSON payload per email (to, subject, body, attachment, reply info).
      Copy each payload's fields into create_draft unchanged: no retyping, so nothing gets mangled.
  python3 tools/outbox.py drafted <tracker_id> <gmail_draft_id>
      Records that the Gmail draft exists (tracker notes) after create_draft succeeded.
  python3 tools/outbox.py sent <tracker_id> [YYYY-MM-DD] [--followup]
      Marks the row sent (or followed_up) and sets the next follow-up date (3, then 5 working days).
  python3 tools/outbox.py pending
      Lists rows whose Gmail draft is waiting for Aditya to press Send (check Sent, then call `sent`).
"""
import csv
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACKER = ROOT / "tracker.csv"
VARIANTS = ("backend", "ai-ml", "data", "sre-platform")
HEAD = re.compile(r"^## (\d+)\. (.+?) — (\S+@\S+?)\s*(?:\((.+?)\))?\s*$")


def rows():
    with open(TRACKER, newline="") as f:
        r = list(csv.DictReader(f))
    return r, list(r[0].keys())


def save(r, fields):
    tmp = TRACKER.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(r)
    tmp.replace(TRACKER)
    import subprocess
    subprocess.run([sys.executable, str(ROOT / "tools/build_dashboard.py")], capture_output=True)


def working_days(start, n):
    d = start
    while n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def attachment_for(variant):
    variant = variant if variant in VARIANTS else "backend"
    pdf = ROOT / "resume" / f"Aditya_Kumar_Singh_Resume_{variant}.pdf"
    if not pdf.exists():
        return None
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    return {"filename": "Aditya_Kumar_Singh_Resume.pdf", "mimeType": "application/pdf",
            "content": f"sha256:{sha}:{pdf}"}


def parse(path):
    lines = Path(path).read_text().splitlines()
    out, cur = [], None
    for line in lines:
        m = HEAD.match(line)
        if m:
            cur = {"n": int(m.group(1)), "company": m.group(2).strip(), "to": m.group(3).strip(),
                   "kind": "followup" if "FOLLOW" in (m.group(4) or "").upper() else "new",
                   "subject": "", "thread": "", "body": []}
            out.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("---") or line.startswith("# "):
            cur = None
            continue
        if line.startswith("**Subject:**"):
            cur["subject"] = line.split("**Subject:**", 1)[1].strip()
            cur["body"] = []
            continue
        if line.startswith("Thread:"):
            t = re.search(r'"(.+?)"', line)
            cur["thread"] = t.group(1) if t else ""
            continue
        if line.startswith(("Note:", "Resume sent as", "Attachment:")) and not cur["body"]:
            continue
        cur["body"].append(line)
    for e in out:
        e["body"] = "\n".join(e["body"]).strip()
    return out


def prepare(path, only):
    tracker, _ = rows()
    by_email = {r["contact_email"].lower(): r for r in tracker}
    payloads, problems = [], []
    for e in parse(path):
        if only and e["n"] not in only:
            continue
        r = by_email.get(e["to"].lower())
        p = {"n": e["n"], "company": e["company"], "tracker_id": r["id"] if r else None,
             "create_draft": {"to": [e["to"]], "body": e["body"]}}
        if e["kind"] == "new":
            p["create_draft"]["subject"] = e["subject"]
            att = attachment_for(r["resume_variant"] if r else "")
            if att:
                p["create_draft"]["attachments"] = [att]
        else:
            p["reply_to"] = {"find_with": f'search_threads query: in:sent to:{e["to"]} subject:"{e["thread"][:40]}"',
                             "then": "pass the newest sent message id as replyToMessageId; leave subject empty"}
        if not e["body"]:
            problems.append(f'#{e["n"]} {e["company"]}: empty body')
        if e["kind"] == "new" and not e["subject"]:
            problems.append(f'#{e["n"]} {e["company"]}: no subject')
        if not r:
            problems.append(f'#{e["n"]} {e["company"]}: {e["to"]} is not in tracker.csv')
        elif r["status"] == "do_not_contact":
            problems.append(f'#{e["n"]} {e["company"]}: status do_not_contact — must not be emailed')
        if len(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", e["body"])) > 0:
            problems.append(f'#{e["n"]} {e["company"]}: an email address appears inside the body — check it')
        payloads.append(p)
    print(json.dumps(payloads, indent=1, ensure_ascii=False))
    if problems:
        print("\nPROBLEMS (fix before creating drafts):\n- " + "\n- ".join(problems))
        return 1
    print(f"\n{len(payloads)} payload(s) ready. Attachments use a file reference that the app fills in once "
          "Aditya approves the create_draft call.")
    return 0


def set_row(tid, **changes):
    r, fields = rows()
    hit = [x for x in r if x["id"] == str(tid)]
    if not hit:
        print(f"no tracker row {tid}")
        return 1
    hit[0].update(changes)
    save(r, fields)
    print(f"tracker {tid} ({hit[0]['company']}): " + ", ".join(f"{k}={v}" for k, v in changes.items() if k != "notes"))
    return 0


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 2
    cmd = a[0]
    if cmd == "prepare":
        only = set()
        if "--only" in a:
            only = {int(x) for x in a[a.index("--only") + 1].split(",")}
        return prepare(a[1], only)
    if cmd == "drafted":
        r, _ = rows()
        row = next((x for x in r if x["id"] == a[1]), None)
        note = (row["notes"] + "; " if row and row["notes"] else "") + f"Gmail draft {a[2]} created {dt.date.today()}"
        return set_row(a[1], status="drafted", next_action="Waiting for Aditya to press Send in Gmail Drafts",
                       notes=note)
    if cmd == "sent":
        day = dt.date.fromisoformat(a[2]) if len(a) > 2 and not a[2].startswith("--") else dt.date.today()
        if "--followup" in a:
            return set_row(a[1], status="followed_up", followup2_date=working_days(day, 5).isoformat(),
                           next_action=f"Final follow-up on {working_days(day, 5)} if no reply")
        f1 = working_days(day, 3)
        return set_row(a[1], status="sent", date_sent=day.isoformat(), followup1_date=f1.isoformat(),
                       followup2_date=working_days(f1, 5).isoformat(),
                       next_action=f"Follow-up 1 on {f1} if no reply")
    if cmd == "pending":
        r, _ = rows()
        for x in r:
            if x["status"] == "drafted" and "Gmail draft" in x["notes"]:
                print(f'{x["id"]} {x["company"]} <{x["contact_email"]}>')
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
