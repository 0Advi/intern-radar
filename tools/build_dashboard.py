#!/usr/bin/env python3
"""Build dashboard/index.html and data/alerts.json from tracker.csv and the files in data/.

Inputs
  tracker.csv             companies emailed / to apply to (statuses in CLAUDE.md)
  data/postings.json      postings found by tools/sweep_boards.py (bucket, auto pre-screen, description)
  data/details.json       researched record per opportunity (written by Claude); keyed by the
                          posting URL, or "tracker:<id>" for tracker rows with no board posting
  data/sweep_log.json     sweep history
  data/inbox.json         links and cold-email requests queued from the widget
  data/applied.json       openings ticked as applied in the widget (url -> date)
  data/seen.json          reply notices already looked at
  data/stages.json        where each application stands (key -> {stage, due, note, updated})
Outputs
  dashboard/index.html    the page the desktop widget shows; every opportunity is one entry in DATA
  data/alerts.json        what needs Aditya's attention; the widget turns it into the menu-bar count,
                          notifications, the morning digest and Reminders
"""
import csv
import datetime as dt
import html
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dashboard" / "index.html"
ALERTS = ROOT / "data" / "alerts.json"
SWEEP_EVERY_H = 5

NAMES = {"gehc": "GE HealthCare", "hpe": "HPE", "fmr": "Fidelity", "ms": "Morgan Stanley",
         "db": "Deutsche Bank", "hevodata": "Hevo Data", "weekdayworks": "Weekday (client)",
         "fampay": "Fam (FamPay)", "hackerrank": "HackerRank", "paypal": "PayPal", "cloudsek": "CloudSEK",
         "observeai": "Observe.AI", "razorpaysoftwareprivatelimited": "Razorpay", "SigNoz": "SigNoz",
         "truefoundry": "TrueFoundry", "towerresearchcapital": "Tower Research", "devrev": "DevRev",
         "highradius": "HighRadius", "pocketfm": "Pocket FM", "nvidia": "NVIDIA", "imc": "IMC"}
MAIL_LABEL = {"sent": "Sent", "followed_up": "Followed up", "replied": "Replied", "bounced": "Bounced",
              "closed": "Closed", "drafted": "Draft waiting", "lead": "Email route found"}
VERDICT = {"strong": ("Strong match", "replied", 0), "good": ("Good match", "sent", 1),
           "possible": ("Possible", "apply", 2), "unknown": ("Unchecked", "", 3),
           "weak": ("Weak match", "", 5), "no": ("Not a fit", "bounced", 6)}
# Application pipeline after "applied": (id, label, pill class), in the order shown on the Applied tab.
STAGES = [("offer", "Offer", "replied"), ("interview", "Interview", "replied"), ("assessment", "Assessment", "apply"),
          ("applied", "Applied", "sent"), ("rejected", "Rejected", "bounced"), ("withdrawn", "Withdrawn", "")]
STAGE = {k: (label, cls, i) for i, (k, label, cls) in enumerate(STAGES)}
AUTO = {"no": ("Likely not eligible", "bounced", 6), "check": ("Check eligibility", "apply", 3),
        "ok": ("Unreviewed", "", 3)}


def nice(company):
    return NAMES.get(company) or (company if any(c.isupper() for c in company) else company.title())


def key_of(company):
    return re.sub(r"[^a-z0-9]", "", nice(company).lower())


def load(name, default):
    try:
        return json.loads((ROOT / "data" / name).read_text())
    except (OSError, ValueError):
        return default


def write_atomic(path, text):
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def esc(s):
    return html.escape(str(s or ""))


def reply_verdict(r):
    """Reply verdict from the reply_summary prefix: 'YES:', 'NO:' or 'MAYBE:'."""
    m = re.match(r"\s*(YES|NO|MAYBE)\s*[:\-—]\s*(.*)", r.get("reply_summary", ""), re.I | re.S)
    if not m:
        return "Replied", "replied", r.get("reply_summary", "")
    word = m.group(1).upper()
    return ({"YES": "Reply: Yes", "NO": "Reply: No", "MAYBE": "Reply: Maybe"}[word],
            {"YES": "replied", "NO": "bounced", "MAYBE": "apply"}[word], m.group(2).strip())


def search_links(company):
    """Search links that always work, for finding the role and the people hiring for it."""
    name = re.sub(r"\s*\(.*?\)", "", company).strip()
    q = quote(name)
    return [
        {"label": "LinkedIn jobs: " + name + " interns in Bengaluru",
         "url": f"https://www.linkedin.com/jobs/search/?keywords={q}%20intern&location=Bengaluru"},
        {"label": "LinkedIn people: recruiters at " + name,
         "url": f"https://www.linkedin.com/search/results/people/?keywords={q}%20recruiter%20OR%20%22talent%20acquisition%22%20OR%20%22university%20hiring%22"},
        {"label": "LinkedIn posts: " + name + " hiring interns",
         "url": f"https://www.linkedin.com/search/results/content/?keywords={q}%20hiring%20intern&sortBy=%22date_posted%22"},
        {"label": "Unstop listings for " + name,
         "url": f"https://www.google.com/search?q=site%3Aunstop.com+{q}+internship"},
        {"label": "Web search: " + name + " internship 2027 Bangalore",
         "url": f"https://www.google.com/search?q={q}+internship+2027+Bangalore+apply"},
    ]


def when(stamp):
    return f'<time data-t="{esc(stamp)}"></time>' if stamp else ""


def is_date(s):
    return bool(re.match(r"\d{4}-\d\d-\d\d", s or ""))


def build():
    now = dt.datetime.now()
    today = now.date().isoformat()
    with open(ROOT / "tracker.csv", newline="") as f:
        tracker = list(csv.DictReader(f))
    store = load("postings.json", {})
    postings = [p for p in store.values() if not p.get("gone")]
    descriptions = load("descriptions.json", {})
    details = load("details.json", {})
    log = load("sweep_log.json", [])
    inbox = load("inbox.json", [])
    applied = {u: (v if isinstance(v, str) else "") for u, v in load("applied.json", {}).items() if v}
    stages = load("stages.json", {})
    seen_replies = load("seen.json", {})  # reply notices the user has already looked at
    requests = {i["key"]: i for i in inbox if i.get("type") == "mail" and i.get("status") != "done" and i.get("key")}
    last = log[-1] if log else None
    by_url = {r["source_url"]: r for r in tracker if r["source_url"]}

    data = {}  # key -> opportunity shown in the widget

    def add(key, company, role, url, seen, kind, row=None, location="", posting=None):
        posting = posting or {}
        d = details.get(key) or (details.get(f'tracker:{row["id"]}') if row else None) or {}
        auto = posting.get("auto") or {}
        if d:
            state = d.get("verdict", "unknown")
            label, cls, rank = VERDICT.get(state, VERDICT["unknown"])
        elif posting.get("bucket") == "check":
            state, cls, rank = "check", "", 4
            label = "Check location" if "location" in posting.get("why_check", "") else "Check role"
        else:
            label, cls, rank = AUTO.get(auto.get("verdict", "ok"), AUTO["ok"])
            state = "no" if auto.get("verdict") == "no" else "unreviewed"
        if posting.get("gone"):
            label, cls, rank = "Closed", "", 7
        o = {"key": key, "company": company, "role": role, "url": url, "seen": seen, "kind": kind,
             "location": location, "verdictLabel": label, "verdictCls": cls, "rank": rank, "state": state,
             "researched": bool(d), "details": d, "auto": auto, "whyCheck": posting.get("why_check", ""),
             "posted": posting.get("posted", ""), "deadline": posting.get("deadline", ""),
             "closed": bool(posting.get("gone")),
             "description": posting.get("description") or descriptions.get(url, ""),
             "search": search_links(company), "applied": url in applied, "appliedOn": applied.get(url, ""),
             "mailTo": d.get("mail_to", ""), "mailReq": requests.get(key, {}).get("added", "")}
        st = dict(stages.get(key) or {})
        if o["applied"] or st:
            st.setdefault("stage", "applied")
            o["applied"] = True
            o["stage"] = st
            o["stageLabel"], o["stageCls"], o["stageRank"] = STAGE.get(st["stage"], STAGE["applied"])
        if row:
            o["evidence"] = row["comp_evidence"]
            o["notes"] = row["notes"]
            if kind == "mail":
                st = row["status"]
                o["mail"] = {"status": st, "label": MAIL_LABEL.get(st, st), "cls": st, "to": row["contact_email"],
                             "sent": row["date_sent"], "followup": row["followup1_date"] if st == "sent" else "",
                             "contact": row["contact_name"], "next": row["next_action"]}
                if st == "replied":
                    lab, c, text = reply_verdict(row)
                    o["mail"].update(label=lab, cls=c, reply=text, replyDate=row["reply_date"],
                                     unseen=seen_replies.get(key) != row["reply_date"])
                if st == "lead" and not o["mailTo"]:
                    o["mailTo"] = row["contact_email"]
        data[key] = o
        return o

    # Postings (apply yourself), closed ones the user cared about, then tracker rows.
    companies = set()
    for p in postings:
        add(p["url"], nice(p["company"]), p["title"], p["url"], p["first_seen"], "apply",
            by_url.get(p["url"]), p.get("location", ""), p)
        companies.add(key_of(p["company"]))
    for p in store.values():
        if p.get("gone") and (p["url"] in details or p["url"] in applied):
            add(p["url"], nice(p["company"]), p["title"], p["url"], p["first_seen"], "apply",
                by_url.get(p["url"]), p.get("location", ""), p)
    for r in tracker:
        key = f'tracker:{r["id"]}'
        st = r["status"]
        if st in ("form_only", "bounced"):
            if r["source_url"] in data or (key not in details and key_of(r["company"]) in companies):
                continue
            add(key, r["company"], r["role"], r["source_url"], r["date_added"], "apply", r)
            if st == "bounced":
                data[key]["bounced"] = r["contact_email"]
        elif st in MAIL_LABEL:
            add(key, r["company"], r["role"], r["source_url"], r["date_sent"] or r["date_added"], "mail", r)

    apply_items = sorted((o for o in data.values() if o["kind"] == "apply"),
                         key=lambda o: (o["applied"], o["rank"], o["company"].lower()))
    mail_items = sorted((o for o in data.values() if o["kind"] == "mail"),
                        key=lambda o: (o["mail"]["status"] != "replied", o["mail"]["status"] == "lead", o["seen"]))
    applied_items = sorted((o for o in apply_items if o["applied"]),
                           key=lambda o: (o["stageRank"], o["company"].lower()))
    in_play = [o for o in applied_items if o["stage"]["stage"] in ("applied", "assessment", "interview")]
    deadlines = sorted((o for o in in_play if is_date(o["stage"].get("due", ""))), key=lambda o: o["stage"]["due"])
    soon = (now.date() + dt.timedelta(days=3)).isoformat()
    week_ago = (now.date() - dt.timedelta(days=7)).isoformat()
    due_now = [o for o in deadlines if week_ago <= o["stage"]["due"] <= soon]
    live = [o for o in apply_items if not o["applied"] and not o["closed"]]
    open_apply = [o for o in live if o["state"] not in ("no", "weak", "check")]
    matches = [o for o in live if o["state"] in ("strong", "good")]
    unreviewed = sorted((o for o in live if o["state"] == "unreviewed"), key=lambda o: o["seen"], reverse=True)

    replies = [o for o in mail_items if o["mail"]["status"] == "replied" and o["mail"].get("unseen")]
    drafted = [o for o in mail_items if o["mail"]["status"] == "drafted"]
    followups = [o for o in mail_items if o["mail"]["followup"] and o["mail"]["followup"] <= today]
    sent = [o for o in mail_items if o["mail"]["status"] in ("sent", "followed_up", "replied", "closed")]
    needs = due_now + replies + drafted + matches + followups + unreviewed

    def row(o, check=False):
        box = ""
        if check:
            box = (f'<input type="checkbox" data-url="{esc(o["url"])}"{" checked" if o["applied"] else ""} '
                   f'title="Tick when you have applied">')
        pill = f'<span class="pill {o["verdictCls"]}">{o["verdictLabel"]}</span>'
        if o["mailTo"] and o["kind"] == "apply":
            pill = '<span class="pill lead" title="A hiring email is published">✉ Email possible</span>' + pill
        bits = [esc((o["location"] or o.get("evidence", ""))[:70])]
        if o.get("bounced"):
            bits = ["Email bounced — apply on their site"]
        if o["appliedOn"]:
            bits.append(f'applied <time data-t="{esc(o["appliedOn"])}" data-abs></time>')
        elif is_date(o["posted"]):
            bits.append(f'posted <time data-t="{esc(o["posted"][:10])}" data-rel></time>')
        elif o["posted"]:
            bits.append(esc(o["posted"].lower()))
        if is_date(o["deadline"]) and not o["applied"]:
            bits.append(f'<b class="due">closes <time data-t="{esc(o["deadline"][:10])}" data-abs></time></b>')
        meta = f'<div class="meta">{" · ".join(b for b in bits if b)}</div>'
        cls = "row open" + (" done" if check and o["applied"] else "") + \
              (" dim" if o["state"] in ("no", "check") or o["closed"] else "")
        return (f'<li class="{cls}" data-key="{esc(o["key"])}" tabindex="0">{box}<div class="grow">'
                f'<b>{esc(o["company"])}</b><div class="role">{esc(o["role"])}</div>{meta}</div>'
                f'<div class="side">{pill}<span class="chev">›</span></div></li>')

    def mail_row(o, tag=""):
        m = o["mail"]
        said = ""
        if m.get("reply"):
            who = f'{esc(m["contact"])}: ' if m["contact"] else ""
            said = f'<div class="said">{who}{esc(m["reply"])}</div>'
        bits = [esc(m["to"])]
        if m["sent"]:
            bits.append(f'<time data-t="{esc(m["sent"])}" data-abs></time>')
        if m["followup"]:
            bits.append(f'follow-up <time data-t="{esc(m["followup"])}" data-abs></time>')
        pill = tag or f'<span class="pill {m["cls"]}">{m["label"]}</span>'
        return (f'<li class="row open" data-key="{esc(o["key"])}" tabindex="0"><div class="grow"><b>{esc(o["company"])}</b>'
                f'<div class="role">{esc(o["role"])}</div>{said}<div class="meta">{" · ".join(bits)}</div></div>'
                f'<div class="side">{pill}<span class="chev">›</span></div></li>')

    def app_row(o, tag=""):
        st = o["stage"]
        bits = []
        if o["appliedOn"]:
            bits.append(f'applied <time data-t="{esc(o["appliedOn"])}" data-abs></time>')
        if is_date(st.get("due", "")):
            late = " late" if st["due"] < today else ""
            bits.append(f'<b class="due{late}">{STAGE.get(st["stage"], STAGE["applied"])[0].lower()} due '
                        f'<time data-t="{esc(st["due"])}" data-abs></time></b>')
        if o["closed"]:
            bits.append("posting now closed")
        note = f'<div class="meta">{esc(st["note"])}</div>' if st.get("note") else ""
        pill = tag or f'<span class="pill {o["stageCls"]}">{o["stageLabel"]}</span>'
        return (f'<li class="row open" data-key="{esc(o["key"])}" tabindex="0"><div class="grow"><b>{esc(o["company"])}</b>'
                f'<div class="role">{esc(o["role"])}</div><div class="meta">{" · ".join(bits)}</div>{note}</div>'
                f'<div class="side">{pill}<span class="chev">›</span></div></li>')

    def all_row(o):
        if o["kind"] == "mail":
            tag, cls = o["mail"]["label"], o["mail"]["cls"]
        elif o["applied"]:
            tag, cls = o["stageLabel"], o["stageCls"]
        elif o["closed"]:
            tag, cls = "Closed", ""
        else:
            tag, cls = o["verdictLabel"], o["verdictCls"]
        return (f'<li class="row tight open" data-key="{esc(o["key"])}" tabindex="0"><div class="grow"><b>{esc(o["company"])}</b> '
                f'<span class="role inline">{esc(o["role"])}</span></div>'
                f'<div class="side"><span class="pill {cls}">{tag}</span><span class="chev">›</span></div></li>')

    def link_row(item):
        text = item.get("text", "")
        m = re.search(r"https?://\S+", text)
        shown = esc(text if len(text) < 90 else text[:87] + "…")
        if m:
            shown = f'<a href="{esc(m.group(0))}">{shown}</a>'
        done = item.get("status") == "done"
        result = f'<div class="role">{esc(item.get("result", ""))}</div>' if item.get("result") else ""
        if item.get("type") == "mail":
            pill = f'<span class="pill {"replied" if done else "apply"}">{"Drafted" if done else "Draft requested"}</span>'
            return (f'<li class="row open" data-key="{esc(item.get("key", ""))}" tabindex="0"><div class="grow"><b>{shown}</b>{result}'
                    f'<div class="meta">requested {when(item.get("added", ""))}</div></div><div class="side">{pill}<span class="chev">›</span></div></li>')
        pill = f'<span class="pill {"replied" if done else "apply"}">{"Researched" if done else "Queued"}</span>'
        return (f'<li class="row"><div class="grow">{shown}{result}<div class="meta">added {when(item.get("added", ""))}</div>'
                f'</div><div class="side">{pill}</div></li>')

    def lst(rows, empty):
        return f"<ul>{''.join(rows)}</ul>" if rows else f'<p class="empty">{empty}</p>'

    def group(title, rows):
        return f'<li class="group">{title}</li>' + "".join(rows) if rows else ""

    needs_html = (group("Deadlines", [app_row(o) for o in due_now]) +
                  group("Replies", [mail_row(o) for o in replies]) +
                  group("Drafts waiting for your OK", [mail_row(o) for o in drafted]) +
                  group("Apply to these", [row(o) for o in matches]) +
                  group("Follow-ups due", [mail_row(o, '<span class="pill apply">Follow-up due</span>') for o in followups]) +
                  group("New and unreviewed", [row(o) for o in unreviewed]))

    banners = ""
    for o in replies:
        m = o["mail"]
        tone = {"replied": "", "bounced": " bad", "apply": " soft"}[m["cls"]]
        banners += (f'<div class="banner{tone} open reply" data-key="{esc(o["key"])}" data-stamp="{esc(m["replyDate"])}">'
                    f'<button class="x" aria-label="Dismiss">×</button><b>{esc(o["company"])} replied — '
                    f'{m["label"].replace("Reply: ", "")}.</b> {esc(m.get("reply", ""))}</div>')
    if drafted:
        banners += f'<div class="banner soft">{len(drafted)} email draft(s) waiting for your approval in Claude.</div>'

    # Deliverability: too many bounces puts the sending account at risk.
    mailed_rows = [r for r in tracker if r["date_sent"]]
    bounced_rows = [r for r in mailed_rows if r["status"] == "bounced"]
    bounce_note = ""
    if len(mailed_rows) >= 3 and len(bounced_rows) / len(mailed_rows) > 0.10:
        bounce_note = (f'<p class="warnline">{len(bounced_rows)} of {len(mailed_rows)} companies emailed bounced. '
                       f'Above 1 in 10 a new Gmail account risks being flagged; only confirmed addresses from here.</p>')

    health_ok, health = True, "OK"
    if not last:
        health_ok, health = False, "No sweep has run yet"
    elif last["read"] < 0.9 * last["boards"]:
        health_ok, health = False, f'Last sweep read only {last["read"]} of {last["boards"]} sources'

    queued = [i for i in inbox if i.get("status") != "done"]
    everything = sorted(data.values(), key=lambda o: (o["closed"], o["company"].lower()))
    fill = {
        "SWEEP_TIME": esc(last["time"]) if last else "",
        "SWEEP_BOARDS": f'{last["read"]}/{last["boards"]}' if last else "",
        "SWEEP_EVERY": str(SWEEP_EVERY_H),
        "HEALTH": "" if health_ok else "⚠ ",
        "N_NEW": str(len(needs)), "N_APPLY": str(len(open_apply)), "N_MAIL": str(len(sent)),
        "N_APPLIED": str(len(in_play)),
        "APPLIED": lst([app_row(o) for o in applied_items],
                       "Nothing yet. Tick a role in To apply (or press “I have applied” on its page) once you "
                       "have submitted the form; it moves here and you can track assessments and interviews."),
        "STAGES": json.dumps([[k, label] for k, label, _ in STAGES]),
        "N_ALL": str(len(everything)), "N_LINKS": str(len(queued)),
        "MAIL_BADGE": f'<i class="badge" id="badge" title="new replies">{len(replies)}</i>' if replies else "",
        "BANNERS": banners,
        "NEW": f"<ul>{needs_html}</ul>" if needs_html else '<p class="empty">Nothing needs you right now.</p>',
        "APPLY": lst([row(o, True) for o in apply_items if not o["closed"] and not o["applied"]],
                     "Nothing left to apply to right now."),
        "MAIL": bounce_note + lst([mail_row(o) for o in mail_items], "No emails sent yet."),
        "ALL": lst([all_row(o) for o in everything], "No roles tracked yet."),
        "LINKS": lst([link_row(i) for i in reversed(inbox)],
                     "Paste a job link below. Claude researches each one on its next check, adds it to "
                     "the tracker and drafts an email if the company publishes a hiring address. "
                     "Cold-email requests you start from an opportunity page also appear here."),
        "DATA": json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"),
    }
    # One pass, so text that happens to contain a %%TOKEN%% is never expanded.
    page = re.sub(r"%%(\w+)%%", lambda m: fill.get(m.group(1), m.group(0)), TEMPLATE)
    write_atomic(OUT, page)

    # ---- alerts for the widget (menu-bar count, notifications, digest, Reminders)
    def fact(o, *names):
        f = o["details"].get("facts", {})
        return next((f[n] for n in names if f.get(n)), "")

    items = []
    for o in matches:
        sub = " · ".join(filter(None, [o["verdictLabel"], fact(o, "When") or o["location"][:40],
                                         ("closes " + o["deadline"]) if is_date(o["deadline"]) else ""]))
        items.append({"key": o["key"], "kind": "match", "title": f'{o["company"]} — {o["role"]}', "subtitle": sub,
                      "body": o["details"].get("fit", "")[:180], "url": o["url"], "since": o["seen"],
                      "deadline": o["deadline"] if is_date(o["deadline"]) else ""})
    for o in unreviewed:
        items.append({"key": o["key"], "kind": "unreviewed", "title": f'{o["company"]} — {o["role"]}',
                      "subtitle": "New, not reviewed yet", "body": "; ".join(o["auto"].get("reasons", []))[:180],
                      "url": o["url"], "since": o["seen"], "deadline": o["deadline"] if is_date(o["deadline"]) else ""})
    in_two_days = (now.date() + dt.timedelta(days=2)).isoformat()
    for o in deadlines:
        due = o["stage"]["due"]
        if today <= due <= in_two_days:
            items.append({"key": f'due:{o["key"]}:{due}', "kind": "deadline",
                          "title": f'{o["company"]}: {o["stageLabel"].lower()} due {dt.date.fromisoformat(due):%-d %b}',
                          "subtitle": o["role"], "body": o["stage"].get("note", ""), "url": o["url"],
                          "since": due, "deadline": due})
    for o in drafted:
        items.append({"key": o["key"], "kind": "draft", "title": f'Draft ready: {o["company"]}',
                      "subtitle": "Waiting for your approval", "body": "Open Claude and say send.", "url": "",
                      "since": o["seen"], "deadline": ""})
    for o in replies:
        items.append({"key": o["key"], "kind": "reply", "title": f'{o["company"]} replied',
                      "subtitle": o["mail"]["label"], "body": o["mail"].get("reply", "")[:180], "url": "",
                      "since": o["mail"].get("replyDate", ""), "deadline": ""})
    digest = []
    if due_now:
        digest.append(f'{len(due_now)} deadline(s): ' + ", ".join(
            f'{o["company"]} {dt.date.fromisoformat(o["stage"]["due"]):%-d %b}' for o in due_now[:3]))
    if matches:
        oldest = min(matches, key=lambda o: o["posted"] if is_date(o["posted"]) else "9")
        digest.append(f'{len(matches)} to apply ({", ".join(o["company"] for o in matches[:3])}'
                      f'{"…" if len(matches) > 3 else ""})')
    if unreviewed:
        digest.append(f"{len(unreviewed)} new to look at")
    if drafted:
        digest.append(f"{len(drafted)} draft(s) need your OK")
    if followups:
        digest.append(f"{len(followups)} follow-up(s) due")
    if replies:
        digest.append(f"{len(replies)} new repl{'y' if len(replies) == 1 else 'ies'}")
    alerts = {"generated": now.isoformat(timespec="minutes"),
              "counts": {"apply": len(matches), "unreviewed": len(unreviewed), "drafts": len(drafted),
                         "replies": len(replies), "followups": len(followups), "deadlines": len(due_now),
                         "applied": len(in_play)},
              "health": {"ok": health_ok, "text": health},
              "digest": " · ".join(digest) or "Nothing waiting on you today.",
              "items": items}
    write_atomic(ALERTS, json.dumps(alerts, indent=1, ensure_ascii=False))

    missing = [o["company"] for o in live if not o["researched"] and o["state"] == "unreviewed"]
    print(f"dashboard written: {len(needs)} need you ({len(matches)} to apply, {len(unreviewed)} unreviewed), "
          f"{len(open_apply)} open, {len(sent)} mailed, {len(queued)} queued; "
          f"without research: {missing if missing else 'none'}")


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Intern Radar</title>
<style>
:root { --bg:#f4f2ec; --card:#ffffff; --ink:#1b1a18; --mute:#6c675e; --line:rgba(0,0,0,.09);
  --accent:#17705b; --accent-soft:#dff0ea; --warn:#9c4f17; --warn-soft:#fae8d8; --bad:#a3312c; --bad-soft:#f8e1df;
  --blue:#2a5699; --blue-soft:#e1eaf7; --glass:.78; }
@media (prefers-color-scheme: dark) { :root { --bg:#191816; --card:#26241f; --ink:#f1eee7; --mute:#a8a297;
  --line:rgba(255,255,255,.1); --accent:#74d3ba; --accent-soft:#1c3b33; --warn:#f2ab6c; --warn-soft:#42311f;
  --bad:#f08a84; --bad-soft:#40211f; --blue:#93b9f2; --blue-soft:#1f2d42; --glass:.62; } }
* { box-sizing:border-box; }
html,body { height:100%; margin:0; }
body { display:flex; flex-direction:column; background:var(--bg); color:var(--ink);
  font:13px/1.38 -apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",sans-serif;
  -webkit-font-smoothing:antialiased; -webkit-user-select:none; user-select:none; cursor:default; }
html.widget body { background:transparent; }
html.widget .tab, html.widget main, html.widget .ask input, html.widget #detail {
  background:color-mix(in srgb, var(--card) calc(var(--glass) * 100%), transparent); }

/* The top strip sits under the window's title bar: drag it to move the widget. */
.bar { height:34px; flex:none; display:flex; align-items:center; gap:8px; padding:0 44px 0 14px; }
.bar h1 { font-size:13px; margin:0; font-weight:700; letter-spacing:.2px; }
.dot { width:7px; height:7px; border-radius:50%; background:var(--accent); flex:none; }
.bar .sweep { color:var(--mute); font-size:11px; margin-left:auto; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }

.tabs { flex:none; display:grid; grid-template-columns:repeat(6,1fr); gap:5px; padding:6px 12px 10px; }
.tab { all:unset; position:relative; cursor:pointer; background:var(--card); border:1px solid var(--line); border-radius:11px;
  padding:7px 4px 6px; text-align:center; transition:transform .12s, border-color .12s; }
.tab b { display:block; font-size:19px; font-weight:700; line-height:1.05; font-variant-numeric:tabular-nums; }
.tab span { color:var(--mute); font-size:10px; display:block; line-height:1.2; white-space:nowrap; }
.tab:hover { transform:translateY(-1px); }
.tab[aria-selected=true] { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent) inset; }
.tab[aria-selected=true] b, .tab[aria-selected=true] span { color:var(--accent); }
.tab:focus-visible, .row.open:focus-visible, .btn:focus-visible, .back:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.badge { position:absolute; top:-5px; right:-4px; min-width:17px; height:17px; padding:0 4px; border-radius:999px;
  background:var(--accent); color:var(--card); font:700 10.5px/17px -apple-system,sans-serif; font-style:normal; text-align:center; }

.banner { flex:none; margin:0 12px 8px; background:var(--accent-soft); color:var(--accent); border-radius:10px;
  padding:7px 10px; font-size:12px; }
.banner b { font-weight:700; }
.banner.soft { background:var(--warn-soft); color:var(--warn); }
.banner.bad { background:var(--bad-soft); color:var(--bad); }
.banner.open { cursor:pointer; }
.banner, .badge { transition:opacity .6s ease, max-height .6s ease, margin .6s ease, padding .6s ease; }
.banner.reply { position:relative; padding-right:26px; max-height:120px; overflow:hidden; }
.banner .x { all:unset; position:absolute; top:4px; right:8px; cursor:pointer; font-size:15px; line-height:1; opacity:.7; padding:2px; }
.banner .x:hover { opacity:1; }
.gone { opacity:0 !important; }
.banner.gone { max-height:0; margin-bottom:0; padding-top:0; padding-bottom:0; }
.draft { white-space:pre-wrap; font:12px/1.45 -apple-system,sans-serif; margin:8px 0; padding:9px 10px; border:1px solid var(--line); border-radius:10px; }
button.btn { all:unset; box-sizing:border-box; width:100%; cursor:pointer; display:flex; align-items:center; justify-content:space-between; gap:8px;
  margin-bottom:6px; padding:9px 10px; border-radius:10px; font-weight:650; background:var(--accent); color:var(--card); }
button.btn:hover { filter:brightness(1.08); }
button.btn[disabled] { opacity:.6; cursor:default; filter:none; }
@media (prefers-reduced-motion: reduce) { .banner, .badge { transition:none; } }

main, #detail { flex:1; min-height:0; overflow-y:auto; margin:0 12px; background:var(--card); border:1px solid var(--line);
  border-radius:12px; }
section { display:none; } section.on { display:block; }
ul { list-style:none; margin:0; padding:0; }
.row { display:flex; gap:9px; align-items:flex-start; padding:9px 10px 9px 12px; border-top:1px solid var(--line); }
ul > .row:first-child { border-top:0; }
.row.tight { padding:7px 10px 7px 12px; align-items:center; }
.row.open { cursor:pointer; }
.row.open:hover { background:color-mix(in srgb, var(--accent) 7%, transparent); }
.row.dim .grow { opacity:.55; }
.grow { flex:1; min-width:0; overflow-wrap:anywhere; }
.side { display:flex; align-items:center; gap:6px; flex:none; padding-top:1px; }
.chev { color:var(--mute); font-size:16px; line-height:1; }
a { color:var(--ink); font-weight:650; text-decoration:none; border-bottom:1px solid var(--line); cursor:pointer; }
a:hover { color:var(--accent); border-bottom-color:var(--accent); }
.role { opacity:.86; }
.role.inline { color:var(--mute); opacity:1; }
.meta, time { color:var(--mute); font-size:11px; }
.said { margin:3px 0; padding-left:8px; border-left:2px solid var(--line); font-size:12px; }
.pill { font-size:10.5px; font-weight:650; padding:2px 8px; border-radius:999px; white-space:nowrap;
  background:var(--bg); color:var(--mute); }
.pill.sent,.pill.followed_up,.pill.lead { background:var(--blue-soft); color:var(--blue); }
.pill.replied { background:var(--accent-soft); color:var(--accent); }
.pill.bounced { background:var(--bad-soft); color:var(--bad); }
.pill.apply,.pill.drafted { background:var(--warn-soft); color:var(--warn); }
.empty { color:var(--mute); padding:14px 14px; margin:0; }
.group { padding:9px 12px 4px; font-size:10.5px; font-weight:700; letter-spacing:.7px; text-transform:uppercase; color:var(--mute);
  border-top:1px solid var(--line); }
ul > .group:first-child { border-top:0; }
.group + .row { border-top:0; }
.due { color:var(--warn); font-weight:650; }
.due.late { color:var(--bad); }
.chips { display:flex; flex-wrap:wrap; gap:5px; margin-bottom:8px; }
.chip { all:unset; cursor:pointer; font-size:11.5px; font-weight:650; padding:3px 10px; border-radius:999px;
  border:1px solid var(--line); color:var(--mute); }
.chip:hover { border-color:var(--accent); color:var(--accent); }
.chip.on { background:var(--accent); border-color:var(--accent); color:var(--card); }
.chip:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.field { display:flex; align-items:center; gap:8px; margin-bottom:6px; color:var(--mute); font-size:12px; }
.field input { flex:1; min-width:0; font:inherit; color:var(--ink); background:transparent; border:1px solid var(--line);
  border-radius:8px; padding:5px 8px; outline:none; color-scheme:light dark; }
.field input:focus { border-color:var(--accent); }
button.btn.ghost { background:transparent; color:var(--accent); border:1px solid var(--accent); }
.warnline { margin:0; padding:9px 12px; font-size:11.5px; color:var(--warn); background:var(--warn-soft); border-bottom:1px solid var(--line); }
input[type=checkbox] { margin:3px 0 0; accent-color:var(--accent); cursor:pointer; flex:none; }
li.done .grow { opacity:.42; } li.done b { text-decoration:line-through; }

/* Detail page for one opportunity */
#detail { display:none; padding:0 0 14px; -webkit-user-select:text; user-select:text; cursor:auto; margin-bottom:12px; }
body.detail #detail { display:block; }
body.detail .tabs, body.detail main, body.detail .ask, body.detail .note, body.detail .banner { display:none; }
.dhead { position:sticky; top:0; z-index:2; padding:10px 12px 10px; border-bottom:1px solid var(--line);
  background:color-mix(in srgb, var(--card) 92%, transparent); -webkit-backdrop-filter:blur(14px); backdrop-filter:blur(14px); }
.back { all:unset; cursor:pointer; color:var(--accent); font-weight:650; font-size:12px; padding:2px 6px 2px 0; }
.dhead h2 { margin:6px 0 1px; font-size:17px; line-height:1.2; }
.dhead .role { font-size:13px; }
.dhead .pills { display:flex; gap:6px; flex-wrap:wrap; margin-top:7px; }
.block { padding:12px 12px 0; }
.block h3 { margin:0 0 6px; font-size:10.5px; font-weight:700; letter-spacing:.7px; text-transform:uppercase; color:var(--mute); }
.fit { margin:0; }
.btn { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:6px; padding:8px 10px;
  border:1px solid var(--line); border-radius:10px; font-weight:600; color:var(--ink); border-bottom:1px solid var(--line); }
.btn:hover { border-color:var(--accent); color:var(--accent); }
.btn.primary { background:var(--accent); border-color:var(--accent); color:var(--card); }
.btn.primary:hover { color:var(--card); filter:brightness(1.08); }
.btn small { font-weight:500; opacity:.75; white-space:nowrap; }
.btn .arrow { flex:none; }
.person { padding:8px 10px; border:1px solid var(--line); border-radius:10px; margin-bottom:6px; }
.person .line { display:flex; align-items:center; gap:8px; }
.person code { font:12px ui-monospace,SFMono-Regular,Menlo,monospace; overflow-wrap:anywhere; }
.copy { all:unset; cursor:pointer; margin-left:auto; font-size:11px; font-weight:650; color:var(--accent);
  border:1px solid var(--line); border-radius:7px; padding:1px 7px; white-space:nowrap; }
.copy:hover { border-color:var(--accent); }
dl { margin:0; display:grid; grid-template-columns:minmax(84px,32%) 1fr; gap:5px 10px; }
dt { color:var(--mute); } dd { margin:0; }
.jd summary { cursor:pointer; color:var(--accent); font-weight:650; list-style:none; }
.jd summary::-webkit-details-marker { display:none; }
.jd pre { white-space:pre-wrap; font:12px/1.45 -apple-system,sans-serif; margin:8px 0 0; color:var(--ink); opacity:.9; }
.src a { font-weight:500; font-size:11.5px; color:var(--mute); display:block; border:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.src a:hover { color:var(--accent); }
.fine { color:var(--mute); font-size:11px; margin:0; }

.ask { flex:none; display:flex; gap:6px; padding:10px 12px 12px; }
.ask input { flex:1; min-width:0; font:inherit; color:var(--ink); background:var(--card); border:1px solid var(--line);
  border-radius:10px; padding:8px 10px; outline:none; -webkit-user-select:text; user-select:text; cursor:text; }
.ask input::placeholder { color:var(--mute); }
.ask input:focus { border-color:var(--accent); }
.ask button { all:unset; cursor:pointer; background:var(--accent); color:var(--card); font-weight:700;
  border-radius:10px; padding:0 13px; display:grid; place-items:center; }
.note { flex:none; color:var(--mute); font-size:10.5px; padding:0 14px 8px; }
.note:empty { display:none; }
@media (prefers-reduced-motion: reduce) { .tab { transition:none; } }
</style></head><body>
<div class="bar"><span class="dot"></span><h1>Intern Radar</h1><span class="sweep" id="sweep"></span></div>
<nav class="tabs" role="tablist">
  <button class="tab" role="tab" data-tab="new"><b>%%N_NEW%%</b><span>Needs you</span></button>
  <button class="tab" role="tab" data-tab="apply"><b id="n-apply">%%N_APPLY%%</b><span>To apply</span></button>
  <button class="tab" role="tab" data-tab="applied"><b>%%N_APPLIED%%</b><span>Applied</span></button>
  <button class="tab" role="tab" data-tab="mail">%%MAIL_BADGE%%<b>%%N_MAIL%%</b><span>Emailed</span></button>
  <button class="tab" role="tab" data-tab="all"><b>%%N_ALL%%</b><span>All roles</span></button>
  <button class="tab" role="tab" data-tab="links"><b>%%N_LINKS%%</b><span>Queue</span></button>
</nav>
%%BANNERS%%
<main>
  <section data-tab="new">%%NEW%%</section>
  <section data-tab="apply">%%APPLY%%</section>
  <section data-tab="applied">%%APPLIED%%</section>
  <section data-tab="mail">%%MAIL%%</section>
  <section data-tab="all">%%ALL%%</section>
  <section data-tab="links">%%LINKS%%</section>
</main>
<article id="detail" aria-live="polite"></article>
<form class="ask" id="ask">
  <input id="q" type="text" autocomplete="off" spellcheck="false" aria-label="Job link for Claude to research"
         placeholder="Paste a job link for Claude to research…">
  <button type="submit" aria-label="Send to Claude">↑</button>
</form>
<div class="note" id="note"></div>
<script id="data" type="application/json">%%DATA%%</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const host = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.radar;
if (host) document.documentElement.classList.add('widget');
const store = { get(k){ try { return localStorage.getItem(k); } catch(e){ return null; } },
                set(k,v){ try { localStorage.setItem(k,v); } catch(e){} } };
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const hostOf = u => { try { return new URL(u).hostname.replace(/^www\./,''); } catch(e){ return ''; } };

function show(tab){
  document.querySelectorAll('.tab').forEach(t => t.setAttribute('aria-selected', t.dataset.tab === tab));
  document.querySelectorAll('section').forEach(s => s.classList.toggle('on', s.dataset.tab === tab));
  store.set('tab', tab);
}
document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => show(t.dataset.tab)));
show(store.get('tab') || 'new');

function rel(d){
  const m = Math.floor((Date.now() - d) / 60000);
  if (m < 1) return 'just now'; if (m < 60) return m + ' min ago';
  if (m < 1440) return Math.floor(m / 60) + ' h ago'; return Math.floor(m / 1440) + ' d ago';
}
const dayFmt = new Intl.DateTimeFormat('en-GB', { day:'numeric', month:'short' });
const timeFmt = new Intl.DateTimeFormat('en-US', { hour:'numeric', minute:'2-digit' });
const day = t => { const d = new Date(t.length === 10 ? t + 'T00:00' : t); return isNaN(d) ? '' : dayFmt.format(d); };
function paintTimes(){
  document.querySelectorAll('time[data-t]').forEach(el => {
    const t = el.dataset.t, d = new Date(t.length === 10 ? t + 'T00:00' : t);
    if (isNaN(d)) return;
    if (el.hasAttribute('data-rel')) { const n = Math.floor((Date.now() - d) / 86400000); el.textContent = n <= 0 ? 'today' : n + ' d ago'; return; }
    el.textContent = (t.length === 10 || el.hasAttribute('data-abs')) ? dayFmt.format(d) : rel(d);
  });
  const last = '%%SWEEP_TIME%%', el = document.getElementById('sweep');
  if (!last) { el.textContent = 'no sweep yet'; return; }
  const d = new Date(last), next = new Date(d.getTime() + %%SWEEP_EVERY%% * 3600000);
  el.textContent = '%%HEALTH%%swept ' + rel(d) + ' · %%SWEEP_BOARDS%% boards · next ' + timeFmt.format(next);
}
paintTimes(); setInterval(paintTimes, 30000);

/* ---------- detail page ---------- */
function linkBtn(l, primary){
  return `<a class="btn${primary ? ' primary' : ''}" href="${esc(l.url)}"><span>${esc(l.label)}</span>` +
         `<small>${esc(l.note || hostOf(l.url))} <span class="arrow">↗</span></small></a>`;
}
function detailHTML(o){
  const d = o.details || {}, m = o.mail, parts = [];
  const pills = [];
  if (o.researched) pills.push(`<span class="pill ${o.verdictCls}">${esc(o.verdictLabel)}</span>`);
  if (m) pills.push(`<span class="pill ${m.cls}">${esc(m.label)}</span>`);
  else pills.push(`<span class="pill ${o.applied ? (o.stageCls || 'sent') : 'apply'}">${o.applied ? esc(o.stageLabel || 'Applied') : 'Apply yourself'}</span>`);
  parts.push(`<div class="dhead"><button class="back" id="back">‹ Back</button><h2>${esc(o.company)}</h2>` +
             `<div class="role">${esc(o.role)}</div><div class="pills">${pills.join('')}</div></div>`);

  if (d.fit) parts.push(`<div class="block"><h3>Assessment</h3><p class="fit">${esc(d.fit)}</p></div>`);
  else {
    const a = o.auto || {}, rs = (a.reasons || []).map(esc).join('; ');
    parts.push(`<div class="block"><h3>Assessment</h3><p class="fit">${o.closed ? 'This posting has closed. ' : ''}${o.whyCheck ? 'Needs a look: ' + esc(o.whyCheck) + '. ' : ''}` +
      `${rs ? 'Automatic pre-screen of the posting: ' + rs + '.' : 'Not reviewed yet.'}</p>` +
      `<p class="fine">Claude writes a full assessment on its next deep sweep. The posting text and search links below are available now.</p></div>`);
  }

  parts.push(appBlock(o));
  parts.push(mailBlock(o));
  if (m && m.status !== 'lead' && m.status !== 'drafted') {
    const rows = [];
    if (m.to) rows.push(['Sent to', m.to]);
    if (m.sent) rows.push(['Sent on', day(m.sent)]);
    if (m.reply) rows.push([(m.contact || 'Reply') + (m.replyDate ? ' (' + day(m.replyDate) + ')' : ''), m.reply]);
    if (m.followup) rows.push(['Follow-up due', day(m.followup)]);
    if (m.next) rows.push(['Next step', m.next]);
    parts.push(`<div class="block"><h3>Your cold email</h3><dl>${rows.map(r => `<dt>${esc(r[0])}</dt><dd>${esc(r[1])}</dd>`).join('')}</dl></div>`);
  }

  const apply = (d.apply && d.apply.length) ? d.apply : (o.url ? [{label:'Open the posting', url:o.url}] : []);
  if (apply.length) parts.push(`<div class="block"><h3>Where to apply</h3>${apply.map((l,i) => linkBtn(l, i === 0)).join('')}</div>`);

  const people = d.contacts || [];
  let ppl = people.map(c => `<div class="person"><div class="line"><b>${esc(c.name || 'Contact')}</b>` +
      (c.url ? ` <a href="${esc(c.url)}">profile ↗</a>` : '') + `</div>` +
      (c.email ? `<div class="line"><code>${esc(c.email)}</code><button class="copy" data-copy="${esc(c.email)}">Copy</button></div>` : '') +
      (c.note ? `<div class="meta">${esc(c.note)}</div>` : '') + `</div>`).join('');
  if (d.contact_note) ppl += `<p class="fine">${esc(d.contact_note)}</p>`;
  if (!ppl) ppl = `<p class="fine">No hiring email or named recruiter is published for this role. Use the LinkedIn people search below to find the recruiter.</p>`;
  parts.push(`<div class="block"><h3>People and email</h3>${ppl}</div>`);

  const facts = Object.entries(d.facts || {});
  if (!facts.length && o.location) facts.push(['Where', o.location]);
  if (o.evidence && !(d.facts && d.facts.Stipend)) facts.push(['Pay evidence', o.evidence]);
  if (o.auto && o.auto.stipend && !(d.facts && d.facts.Stipend)) facts.push(['Pay mentioned', o.auto.stipend]);
  if (o.posted) facts.push(['Posted', /^\d{4}-\d\d-\d\d/.test(o.posted) ? day(o.posted.slice(0,10)) : o.posted]);
  if (o.deadline) facts.push(['Apply by', day(o.deadline.slice(0,10))]);
  if (o.appliedOn) facts.push(['You applied', day(o.appliedOn.slice(0,10))]);
  if (facts.length) parts.push(`<div class="block"><h3>Key facts</h3><dl>${facts.map(f => `<dt>${esc(f[0])}</dt><dd>${esc(f[1])}</dd>`).join('')}</dl></div>`);

  const c = d.company || {};
  if (c.about || c.website || c.linkedin) {
    parts.push(`<div class="block"><h3>Company</h3>${c.about ? `<p class="fit">${esc(c.about)}</p>` : ''}` +
      `<div style="margin-top:6px">${c.website ? linkBtn({label:'Careers / website', url:c.website}) : ''}` +
      `${c.linkedin ? linkBtn({label:'Company page on LinkedIn', url:c.linkedin}) : ''}</div></div>`);
  }

  parts.push(`<div class="block"><h3>Find the role and recruiters elsewhere</h3>${o.search.map(l => linkBtn(l)).join('')}` +
             `<p class="fine">These are live searches; sign in to LinkedIn to see people results.</p></div>`);

  if (o.description) parts.push(`<div class="block jd"><h3>Posting</h3><details><summary>Show the full posting text</summary><pre>${esc(o.description)}</pre></details></div>`);

  const src = d.sources || [];
  if (src.length) parts.push(`<div class="block src"><h3>Sources</h3>${src.map(u => `<a href="${esc(u)}">${esc(u)}</a>`).join('')}</div>`);
  parts.push(`<div class="block"><p class="fine">${d.researched ? 'Researched ' + day(d.researched) + '. ' : ''}Pay figures marked unverified come from job aggregators, not the company.</p></div>`);
  return parts.join('');
}
const STAGES = %%STAGES%%;
function appBlock(o){
  if (o.kind !== 'apply') return '';
  if (!o.applied) return `<div class="block"><h3>Your application</h3><button class="btn ghost markapplied" data-key="${esc(o.key)}">` +
      `<span>I have applied to this</span><span>✓</span></button><p class="fine">Moves it to the Applied tab so you can track the assessment, interview and outcome.</p></div>`;
  const st = o.stage || {};
  return `<div class="block" id="app" data-key="${esc(o.key)}"><h3>Your application${o.appliedOn ? ' · applied ' + day(o.appliedOn) : ''}</h3>` +
    `<div class="chips">${['applied','assessment','interview','offer','rejected','withdrawn'].map(id => STAGES.find(x => x[0] === id)).map(s => `<button class="chip${st.stage === s[0] ? ' on' : ''}" data-stage="${s[0]}">${esc(s[1])}</button>`).join('')}</div>` +
    `<label class="field">Next deadline <input type="date" id="due" value="${esc(st.due || '')}"></label>` +
    `<label class="field">Note <input type="text" id="snote" maxlength="200" placeholder="e.g. online assessment link received" value="${esc(st.note || '')}"></label>` +
    `<div class="person" style="border:0;padding:0"><div class="line"><span class="fine" id="saved">A deadline is added to Reminders and to your alerts.</span>` +
    `<button class="copy" id="savestage">Save</button></div></div></div>`;
}
function sendStage(){
  const box = document.getElementById('app'); if (!box) return;
  const o = DATA[box.dataset.key], on = box.querySelector('.chip.on');
  const msg = { type:'stage', key:o.key, url:o.url, title:o.company + ' — ' + o.role, stage: on ? on.dataset.stage : 'applied',
                due: document.getElementById('due').value, note: document.getElementById('snote').value.trim() };
  document.getElementById('saved').textContent = host ? 'Saved.' : 'Saving only works in the desktop widget.';
  if (host) host.postMessage(msg);
}
function mailBlock(o){
  const d = o.details || {}, dr = d.draft, m = o.mail;
  if (m && m.status !== 'lead' && m.status !== 'drafted') return '';   // already emailed: shown under "Your cold email"
  if (dr) return `<div class="block"><h3>Cold email draft</h3><dl><dt>To</dt><dd>${esc(dr.to)}</dd><dt>Subject</dt><dd>${esc(dr.subject)}</dd></dl>` +
      `<pre class="draft">${esc(dr.body)}</pre><div class="person" style="border:0;padding:0"><div class="line"><span class="fine">Waiting for your go-ahead</span>` +
      `<button class="copy" data-copy="${esc(dr.body)}">Copy text</button></div></div>` +
      `<p class="fine">Tell Claude “send the ${esc(o.company)} email” and it goes out from your outreach Gmail. Nothing is sent before that.</p></div>`;
  if (o.mailReq) return `<div class="block"><h3>Cold email</h3><p class="fit">Requested ${rel(new Date(o.mailReq))}. Claude drafts it on its next check (hourly while the Claude app is open) and shows the text here for your approval.</p></div>`;
  if (o.mailTo) return `<div class="block"><h3>Cold email</h3><button class="btn mailgo" data-key="${esc(o.key)}"><span>Start cold email to ${esc(o.mailTo)}</span><span>✉</span></button>` +
      `<p class="fine">Claude researches the company, writes a tailored email and shows it here. It is sent from your outreach Gmail once you approve it in Claude.</p></div>`;
  return `<div class="block"><h3>Cold email</h3><p class="fine">Not possible for this one: the company publishes no hiring email that takes applications. Use the apply links below.</p></div>`;
}
const detail = document.getElementById('detail');
function openDetail(key){
  const o = DATA[key]; if (!o) return;
  detail.innerHTML = detailHTML(o); detail.scrollTop = 0;
  document.body.classList.add('detail'); store.set('open', key);
  document.getElementById('back').addEventListener('click', closeDetail);
}
function closeDetail(){ document.body.classList.remove('detail'); store.set('open', ''); }

/* Reply notices clear once you have looked at them. */
function markSeen(){
  const notes = [...document.querySelectorAll('.banner.reply:not(.gone)')];
  const badge = document.getElementById('badge');
  if (!notes.length && !(badge && !badge.classList.contains('gone'))) return;
  notes.forEach(b => { b.classList.add('gone'); if (host) host.postMessage({ type:'seen', key:b.dataset.key, stamp:b.dataset.stamp }); });
  if (badge) badge.classList.add('gone');
}
const onMail = () => (store.get('tab') || 'new') === 'mail' && !document.body.classList.contains('detail');
document.querySelector('.tab[data-tab=mail]').addEventListener('click', () => setTimeout(markSeen, 2500));
// Already on the Emailed tab: count it as seen once you move the pointer over the widget.
document.addEventListener('mousemove', function seenOnHover(){ if (onMail()) { setTimeout(markSeen, 2500); document.removeEventListener('mousemove', seenOnHover); } });
document.addEventListener('click', e => {
  const copy = e.target.closest('.copy');
  if (copy) {
    const text = copy.dataset.copy;
    if (host) host.postMessage({ type:'copy', text }); else if (navigator.clipboard) navigator.clipboard.writeText(text);
    copy.textContent = 'Copied'; setTimeout(() => copy.textContent = 'Copy', 1400); return;
  }
  if (e.target.closest('.banner .x')) { markSeen(); return; }
  const chip = e.target.closest('.chip');
  if (chip) { document.querySelectorAll('#app .chip').forEach(c => c.classList.toggle('on', c === chip)); sendStage(); return; }
  if (e.target.closest('#savestage')) { sendStage(); return; }
  const mark = e.target.closest('.markapplied');
  if (mark) {
    const o = DATA[mark.dataset.key];
    if (!host) { mark.querySelector('span').textContent = 'Only works in the desktop widget'; return; }
    host.postMessage({ type:'applied', url:o.url, on:true });
    mark.disabled = true; mark.querySelector('span').textContent = 'Marked as applied'; return;
  }
  const go = e.target.closest('.mailgo');
  if (go) {
    const o = DATA[go.dataset.key];
    if (!host) { go.querySelector('span').textContent = 'Only works in the desktop widget'; return; }
    host.postMessage({ type:'mail', key:o.key, company:o.company, role:o.role, email:o.mailTo });
    go.disabled = true; go.querySelector('span').textContent = 'Requested — Claude will draft it';
    return;
  }
  if (e.target.closest('input, a, button')) return;
  const row = e.target.closest('.open[data-key]');
  if (row) { if (row.classList.contains('reply')) markSeen(); openDetail(row.dataset.key); }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && document.body.classList.contains('detail')) closeDetail();
  if (e.key === 'Enter' && e.target.matches('.row.open')) openDetail(e.target.dataset.key);
});
if (store.get('open') && DATA[store.get('open')]) openDetail(store.get('open'));   // survive widget reloads

document.querySelectorAll('input[type=checkbox][data-url]').forEach(b => {
  b.addEventListener('change', () => {
    b.closest('li').classList.toggle('done', b.checked);
    const n = [...document.querySelectorAll('section[data-tab=apply] li:not(.dim) input[type=checkbox]')].filter(x => !x.checked).length;
    document.getElementById('n-apply').textContent = n;
    if (host) host.postMessage({ type:'applied', url:b.dataset.url, on:b.checked });
  });
});

document.getElementById('ask').addEventListener('submit', e => {
  e.preventDefault();
  const q = document.getElementById('q'), text = q.value.trim(), note = document.getElementById('note');
  if (!text) return;
  if (!host) { note.textContent = 'Links can only be queued from the desktop widget.'; return; }
  store.set('tab', 'links');
  host.postMessage({ type:'link', text });
  q.value = ''; note.textContent = 'Queued for Claude.';
});
</script>
</body></html>
"""

if __name__ == "__main__":
    build()
