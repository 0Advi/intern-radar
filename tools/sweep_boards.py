#!/usr/bin/env python3
"""Sweep public job sources for Bengaluru tech internships.

Sources (all public, unauthenticated, read-only):
  greenhouse / lever / ashby / smartrecruiters / workday  — company boards listed in tools/boards.json
  feed    — github.com/Sarthzk/internship-tracker data/listings.json (big companies on their own portals)
  unstop  — Unstop's public internship search (only source with structured stipend and deadline)

  python3 tools/sweep_boards.py              # sweep, update data/postings.json, print new postings
  python3 tools/sweep_boards.py --all        # also print every current match
  python3 tools/sweep_boards.py --probe a b  # which ATS hosts a board for these company names
  python3 tools/sweep_boards.py --selftest   # run the title / location / eligibility filter checks

State (written atomically):
  data/postings.json   url -> posting, with first_seen / last_seen / misses / gone / auto
  data/sweep_log.json  recent sweeps: time, counts, failures
"""
import datetime as dt
import html
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
POSTINGS = DATA / "postings.json"
LOG = DATA / "sweep_log.json"
GONE_AFTER = 3            # consecutive successful reads without the posting before it counts as closed
CGPA = 8.38               # Aditya's CGPA, for cut-off checks
GRAD_YEAR = 2027

# ---------------------------------------------------------------- filters

INTERN = re.compile(r"\bintern(ship)?s?\b|\btrainee\b|\bapprentice|\bco-?op\b", re.I)
PLACE = re.compile(r"beng[a]?l[uo]+r[uo]?|bang[a]?lore|banglore|\bblr\b", re.I)
# India-wide or unspecified: only when the location names no other city ("Gurgaon, Haryana, India" is a no).
MAYBE_PLACE = re.compile(r"^\W*((remote|hybrid|anywhere|pan)\W*(in\W*)?)?india(\W*(remote|hybrid))?\W*$|"
                         r"^\W*karnataka(\W*india)?\W*$|^\W*multiple locations\W*$|^\W*\d+\s+locations\W*$", re.I)
# Never a software role, whatever else the title says.
HARD_NO = re.compile(r"\bph\.?\s?d\b|doctoral|hardware|analog|silicon|mechanical|\bcivil\b|vlsi|\brf\b|physical design|"
                     r"electrical|structures|verification|supply chain|accounting|data entry|data annotation|\bmba\b|"
                     r"technical writ|technical support|market research|aerospace|avionics|manufactur|chemical|"
                     r"\bftm\b|biomedical|pharma|clinical", re.I)
# Clearly a software / data / ML role.
STRONG = re.compile(r"software|\bsde\b|\bswe\b|developer|back-?end|front-?end|full.?stack|machine learning|deep learning|"
                    r"\bml\b|\bai\b|\bgen-?ai\b|\bnlp\b|\bllm|computer vision|data (engineer|scien)|applied scien|"
                    r"site reliability|\bsre\b|devops|platform engineer|security engineer|research engineer|python|"
                    r"\bcloud\b|business intelligence", re.I)
# Only counts when nothing in SOFT_NO is present.
WEAK = re.compile(r"engineer|\bdata\b|research|technical|technology|\btech\b|scientist|infra|platform|security", re.I)
SOFT_NO = re.compile(r"marketing|\bsales\b|design|\bhr\b|recruit|finance|legal|content|business|\bgtm\b|"
                     r"product manag|program manag|project manag|operations|customer|talent|people|brand|social media", re.I)
GENERIC = re.compile(r"^\W*(college|university|student|graduate|external|summer|winter|technical|engineering)?\W*"
                     r"intern(ship)?\W*(20\d\d)?\W*$", re.I)


def title_bucket(title):
    """'match' for software/data/ML intern titles, 'check' for bare titles like 'College Intern', else None."""
    if not INTERN.search(title) or HARD_NO.search(title):
        return None
    if STRONG.search(title):
        return "match"
    if GENERIC.match(title):
        return "check"
    return "match" if WEAK.search(title) and not SOFT_NO.search(title) else None


def place_bucket(locations, *other):
    """'blr' if a location (or the title / URL) names Bengaluru; 'maybe' if a location is India-wide or
    hidden behind "N Locations"; else None."""
    if PLACE.search(" | ".join(t for t in list(locations) + list(other) if t)):
        return "blr"
    return "maybe" if any(MAYBE_PLACE.match(l) for l in locations if l) else None


def plain(text):
    text = html.unescape(html.unescape(text or ""))
    text = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div|/ul)\s*/?>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "• ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def screen(title, text):
    """Cheap eligibility pre-screen of a posting's own words. Returns {'verdict', 'reasons', 'stipend'}.

    verdict: 'no' (states a rule Aditya fails), 'check' (something to confirm), 'ok' (nothing found).
    """
    blob = f"{title}\n{text or ''}"
    low = blob.lower()
    reasons, verdict = [], "ok"

    years = set()
    for m in re.finditer(r"graduat\w*\s+(?:in|by|during|year)?\s*(?:of\s+)?(?:\w+\s+)?(20[2-3]\d)", low):
        years.add(int(m.group(1)))
    for m in re.finditer(r"(20[2-3]\d)\s*(?:batch|graduat\w+|pass[\s-]?outs?|passing)", low):
        years.add(int(m.group(1)))
    for m in re.finditer(r"(?:batch|class)\s+(?:of\s+)?(20[2-3]\d)", low):
        years.add(int(m.group(1)))
    if years and GRAD_YEAR not in years:
        verdict = "no"
        reasons.append("asks for " + "/".join(str(y) for y in sorted(years)) + f" graduates (you are {GRAD_YEAR})")
    elif GRAD_YEAR in years:
        reasons.append(f"open to {GRAD_YEAR} graduates")

    wants_pg = re.search(r"\bph\.?\s?d\b|doctoral|\bm\.?\s?tech\b|\bm\.\s?s\.?(?=\W)|master'?s", low)
    wants_ug = re.search(r"bachelor|\bb\.?\s?tech\b|\bb\.e\b|undergrad", low)   # not a bare "be"
    if wants_pg and not wants_ug:
        verdict = "no"
        reasons.append("asks for Master's / PhD students")

    for m in re.finditer(r"cgpa\s*(?:of|:|-|>=|≥|above|minimum)?\s*(\d(?:\.\d+)?)|(\d(?:\.\d+)?)\s*(?:\+|and above|or above)?\s*cgpa", low):
        cut = float(m.group(1) or m.group(2))
        if 5 <= cut <= 10:
            if cut > CGPA:
                verdict = "no"
                reasons.append(f"CGPA cut-off {cut} is above yours ({CGPA})")
            else:
                reasons.append(f"CGPA cut-off {cut} (you have {CGPA})")
            break

    if verdict != "no":
        if re.search(r"pre-?final|penultimate|third[- ]year|3rd[- ]year", low):
            verdict = "check"
            reasons.append("mentions pre-final-year students")
        if re.search(r"tier[\s-]?(1|i|one)\b", low):
            verdict = "check"
            reasons.append("asks for a Tier 1 college")
        if re.search(r"summer\s+20\d\d", low) and not re.search(r"jan|winter|6[\s-]month|six[\s-]month", low):
            verdict = "check"
            reasons.append("looks like a summer internship; confirm dates")

    stipend = ""
    m = re.search(r"[^.\n]{0,60}(?:₹|\binr\b|rs\.?\s?\d|stipend)[^.\n]{0,90}", blob, re.I)
    if m and re.search(r"\d", m.group(0)):
        stipend = re.sub(r"\s+", " ", m.group(0)).strip()
    return {"verdict": verdict, "reasons": reasons, "stipend": stipend}


# ---------------------------------------------------------------- fetching

NET_ERRORS = (urllib.error.URLError, http.client.HTTPException, OSError, ValueError, KeyError)


def get(url, body=None):
    headers = {"User-Agent": "job-board-reader", "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError:
            raise  # 404 and friends will not improve on retry
        except (urllib.error.URLError, http.client.HTTPException, OSError):
            if attempt == 2:
                raise
            time.sleep(2 + 3 * attempt)  # some hosts drop bursts of connections


def row(title, locations, url, posted="", description="", deadline="", company=None, stipend=""):
    return {"title": (title or "").strip(), "locations": [l for l in locations if l], "url": url or "",
            "posted": (posted or "")[:10], "description": description or "", "deadline": (deadline or "")[:10],
            "company": company, "stipend": stipend}


def greenhouse(tok):
    for j in get(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs?content=true").get("jobs", []):
        locs = [(j.get("location") or {}).get("name", "")]
        locs += [o.get("name", "") or "" for o in j.get("offices") or []]
        locs += [o.get("location") or "" for o in j.get("offices") or []]
        yield row(j.get("title"), locs, j.get("absolute_url"), j.get("first_published") or j.get("updated_at"),
                  plain(j.get("content")), j.get("application_deadline"))


def lever(tok):
    for j in get(f"https://api.lever.co/v0/postings/{tok}?mode=json"):
        c = j.get("categories") or {}
        locs = [c.get("location", "")] + list(c.get("allLocations") or [])
        posted = ""
        if j.get("createdAt"):
            posted = dt.datetime.fromtimestamp(j["createdAt"] / 1000).date().isoformat()
        parts = [j.get("descriptionPlain", "")]
        parts += [f'{l.get("text", "")}\n{plain(l.get("content", ""))}' for l in j.get("lists") or []]
        parts.append(j.get("additionalPlain", ""))
        r = row(j.get("text"), locs, j.get("hostedUrl"), posted, "\n".join(p for p in parts if p))
        r["extra"] = " / ".join(filter(None, [c.get("commitment"), j.get("workplaceType")]))
        yield r


def ashby(tok):
    for j in get(f"https://api.ashbyhq.com/posting-api/job-board/{tok}?includeCompensation=true").get("jobs", []):
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        r = row(j.get("title"), locs, j.get("jobUrl"), j.get("publishedAt"),
                j.get("descriptionPlain") or plain(j.get("descriptionHtml")), stipend=comp)
        r["extra"] = " / ".join(filter(None, [j.get("employmentType"), "Remote" if j.get("isRemote") else ""]))
        yield r


def smartrecruiters(tok):
    d = get(f"https://api.smartrecruiters.com/v1/companies/{tok}/postings?q=intern&country=in&limit=100")
    for j in d.get("content", []):
        loc = ", ".join(filter(None, [(j.get("location") or {}).get("city"), "India"]))
        yield row(j.get("name"), [loc], f"https://jobs.smartrecruiters.com/{tok}/{j['id']}", j.get("releasedDate"))


def workday(spec):
    """spec = 'tenant|wdN|site'. Searches 'intern', pages to the end, and opens multi-location rows."""
    tenant, wd, site = spec.split("|")
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}"
    seen = set()
    for term, pages in (("intern India", 3), ("intern", 10)):
        for page in range(pages):
            d = get(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": term})
            rows = d.get("jobPostings", [])
            for j in rows:
                path = j.get("externalPath", "")
                if not path or path in seen:
                    continue
                seen.add(path)
                title, loc = j.get("title", ""), j.get("locationsText", "")
                locs = [loc, path.split("/")[2] if path.count("/") >= 3 else ""]
                # "2 Locations": the list view hides them, so open the posting when the title qualifies.
                if re.search(r"\d+\s+locations", loc, re.I) and not PLACE.search(path) and title_bucket(title):
                    try:
                        info = get(f"{api}{path}").get("jobPostingInfo", {})
                        # Real locations known now, so drop the "N Locations" placeholder.
                        locs = [info.get("location", "")] + list(info.get("additionalLocations") or [])
                    except NET_ERRORS:
                        pass
                yield row(title, locs, f"{base}/{site}{path}", j.get("postedOn"))
            if len(rows) < 20:
                break


def feed(_):
    """Sarthzk/internship-tracker: India tech internships from company career portals, refreshed daily."""
    rows = get("https://raw.githubusercontent.com/Sarthzk/internship-tracker/main/data/listings.json")
    for j in rows if isinstance(rows, list) else rows.get("listings", []):
        if j.get("status") != "open":
            continue
        yield row(j.get("title"), j.get("locations") or [], j.get("url"), j.get("date_posted") or j.get("first_seen"),
                  company=j.get("company"))


def unstop(term):
    """Unstop internships paying at least ₹40k a month."""
    url = ("https://unstop.com/api/public/opportunity/search-result?opportunity=internships&oppstatus=open"
           f"&per_page=50&searchTerm={urllib.parse.quote(term)}")
    for j in ((get(url).get("data") or {}).get("data") or []):
        jd = j.get("jobDetail") or {}
        low = jd.get("min_salary") or 0
        if jd.get("pay_in") not in (None, "monthly") or low < 40000:
            continue
        locs = list(jd.get("locations") or []) + [l.get("city", "") if isinstance(l, dict) else str(l)
                                                    for l in j.get("locations") or []]
        high = jd.get("max_salary") or low
        pay = f"₹{low:,}" + (f"–₹{high:,}" if high != low else "") + " a month (Unstop listing)"
        yield row(j.get("title"), locs, j.get("seo_url"), j.get("updated_at"),
                  deadline=(j.get("regnRequirements") or {}).get("end_regn_dt") or j.get("end_date"),
                  company=(j.get("organisation") or {}).get("name"), stipend=pay)


FETCH = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "smartrecruiters": smartrecruiters,
         "workday": workday, "feed": feed, "unstop": unstop}
BUILT_IN = {"feed": ["sarthzk"], "unstop": ["software engineer", "backend developer", "machine learning", "data engineer"]}


def company_name(kind, tok):
    return tok.split("|")[0] if kind == "workday" else tok


def sweep(kind, tok):
    """Returns (hits, error). Never raises: one odd row or board must not stop the sweep."""
    try:
        rows = list(FETCH[kind](tok))
    except Exception as e:  # noqa: BLE001 - any failure here is "this source was unreadable"
        return None, f"{kind}:{tok} -> {type(e).__name__}: {e}"
    hits = {}
    for r in rows:
        try:
            title = r["title"]
            tb = title_bucket(title)
            pb = place_bucket(r["locations"], title, r["url"]) if tb else None
            if not tb or not pb or not r["url"]:
                continue
            location = " · ".join(dict.fromkeys(l.strip() for l in r["locations"] if l.strip()))
            if r.get("extra"):
                location = f'{location} / {r["extra"]}'
            hits[r["url"]] = {
                "company": r["company"] or company_name(kind, tok), "title": title, "location": location,
                "url": r["url"], "posted": r["posted"], "deadline": r["deadline"], "source": kind,
                "bucket": "match" if (tb == "match" and pb == "blr") else "check",
                "why_check": "" if (tb == "match" and pb == "blr") else
                             ("location not confirmed as Bengaluru" if pb != "blr" else "generic title; role unclear"),
                "description": r["description"][:9000],
                "auto": screen(title, r["description"]),
            }
            if r["stipend"]:
                hits[r["url"]]["auto"]["stipend"] = r["stipend"]
        except Exception as e:  # noqa: BLE001
            print(f"  skipped a row from {kind}:{tok}: {type(e).__name__}: {e}")
    return list(hits.values()), None


# ---------------------------------------------------------------- state

def read_json(path, default):
    """Load JSON; a corrupt file is set aside instead of crashing every later run."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except ValueError:
        bad = path.with_name(f"{path.name}.corrupt-{dt.datetime.now():%Y%m%d-%H%M%S}")
        path.rename(bad)
        print(f"  {path.name} was unreadable; moved to {bad.name} and started fresh")
        return default


def write_json(path, value):
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False))
    os.replace(tmp, path)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def probe(names):
    def one(args):
        kind, name = args
        try:
            return kind, name, len(list(FETCH[kind](name)))
        except Exception:  # noqa: BLE001
            return kind, name, None
    todo = [(k, n) for n in dict.fromkeys(names) for k in ("greenhouse", "lever", "ashby", "smartrecruiters")]
    found = {}
    with ThreadPoolExecutor(12) as ex:
        for kind, name, n in ex.map(one, todo):
            if n:
                found.setdefault(kind, []).append(name)
    print(json.dumps(found))


def selftest():
    keep = ["Software Engineer Intern - Salesforce Platform", "Software Engineering Intern, Design Systems",
            "Software Engineer Intern (Finance Engineering)", "Business Intelligence Engineer Intern",
            "SDE Intern - Content Platform", "Software Engineer Intern (University Recruiting)",
            "Applied Scientist Intern", "GenAI Intern", "Deep Learning Intern", "Computer Vision Intern",
            "NLP Intern", "Frontend Intern", "Site Reliability Intern", "Machine Learning Intern",
            "Associate Software Development Engineer (SDE) Intern", "Data Engineer Intern"]
    drop = ["Ph.D. Research Intern", "PhD Intern, Machine Learning", "Market Research Intern", "Civil Engineer Intern",
            "Technical Writer Intern", "Technical Support Intern", "Data Annotation Intern", "Marketing Intern",
            "Sales Intern", "Product Design Intern", "Analog Design Intern", "Senior Software Engineer",
            "HR Intern", "Legal Intern", "Internship - Structures"]
    check = ["College Intern", "Intern 2027", "Technical Internship 2027"]
    bad = [t for t in keep if title_bucket(t) != "match"] + [t for t in drop if title_bucket(t)] + \
          [t for t in check if title_bucket(t) != "check"]
    places = {"Bangalore": "blr", "Bengaluru, Karnataka": "blr", "Banglore": "blr", "Bengalore": "blr",
              "Karnataka, India": "maybe", "Remote - India": "maybe", "2 Locations": "maybe", "San Jose": None}
    places.update({"Gurgaon, Haryana, India": None, "India": "maybe", "Hybrid - India": "maybe"})
    bad += [f"place {p}" for p, want in places.items() if place_bucket([p]) != want]
    cases = [("x", "You must be graduating in 2028 with a Bachelor's degree", "no"),
             ("x", "2027 graduates of circuital branches only. CGPA 8 and above", "ok"),
             ("x", "research-oriented M.S./Ph.D. scholars", "no"),
             ("x", "Bachelor's or Master's degree in computer science", "ok"),
             ("x", "minimum CGPA of 9.0 required", "no"),
             ("x", "degree from a Tier 1 college", "check"),
             ("x", "Candidate graduated or graduating in 2025", "no"),
             ("x", "Currently pursuing a PhD in Computer Science; you will be part of the team", "no")]
    bad += [f"screen {t!r} -> {screen(a, t)['verdict']}" for a, t, want in cases if screen(a, t)["verdict"] != want]
    print("selftest:", "all filters behave" if not bad else f"{len(bad)} FAILED: {bad}")
    return 1 if bad else 0


def main():
    if "--probe" in sys.argv:
        return probe(sys.argv[sys.argv.index("--probe") + 1:])
    if "--selftest" in sys.argv:
        return selftest()
    boards = read_json(HERE / "boards.json", {})
    jobs = [(k, t) for k in FETCH for t in list(boards.get(k, [])) + BUILT_IN.get(k, [])]
    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(lambda a: sweep(*a), jobs))
    now = dt.datetime.now().isoformat(timespec="minutes")
    failed = [e for _, e in results if e]
    failed_kinds = {e.split(":", 1)[0] for e in failed if e.split(":", 1)[0] in BUILT_IN}
    failed_companies = {e.split(" -> ")[0].split(":", 1)[1].split("|")[0] for e in failed}

    # Company boards first, so a role also listed by the feed / Unstop keeps the official URL.
    current, taken = {}, set()
    for (kind, _), (hits, _) in sorted(zip(jobs, results), key=lambda x: x[0][0] in BUILT_IN):
        for h in hits or []:
            sig = (norm(h["company"]), norm(h["title"]))
            if h["url"] in current or (kind in BUILT_IN and sig in taken):
                continue
            current[h["url"]] = h
            taken.add(sig)

    store = read_json(POSTINGS, {})
    known = {(norm(p["company"]), norm(p["title"])) for p in store.values()}
    new = []
    for url, h in current.items():
        old = store.get(url)
        if old is None:
            h["first_seen"] = now
            if (norm(h["company"]), norm(h["title"])) not in known:   # same role under a new URL is not "new"
                new.append(h)
        else:
            h["first_seen"] = old["first_seen"]
            h["posted"] = h["posted"] or old.get("posted", "")
            h["description"] = h["description"] or old.get("description", "")
        h["last_seen"], h["gone"], h["misses"] = now, False, 0
        store[url] = h
    for url, h in store.items():
        if url in current:
            continue
        unreadable = h["company"] in failed_companies or h.get("source") in failed_kinds
        if not unreadable:
            h["misses"] = h.get("misses", 0) + 1
            if h["misses"] >= GONE_AFTER and not h.get("gone"):
                h["gone"], h["gone_on"] = True, now
    write_json(POSTINGS, store)

    log = read_json(LOG, [])
    log.append({"time": now, "boards": len(jobs), "read": len(jobs) - len(failed),
                "matches": sum(1 for h in current.values() if h["bucket"] == "match"),
                "check": sum(1 for h in current.values() if h["bucket"] == "check"),
                "new": [h["url"] for h in new], "failed": [e.split(" -> ")[0] for e in failed][:40]})
    write_json(LOG, log[-60:])

    show = list(current.values()) if "--all" in sys.argv else new
    for h in sorted(show, key=lambda h: (h["bucket"], h["company"].lower())):
        print(f'[{h["bucket"]}/{h["auto"]["verdict"]}] {h["company"]} | {h["title"]} | {h["location"][:60]} | '
              f'{h["posted"]} | {h["url"]}')
    print(f"\n{len(new)} new, {len(current)} current ({log[-1]['matches']} match, {log[-1]['check']} to check), "
          f"{len(jobs) - len(failed)}/{len(jobs)} sources read")
    for e in failed:
        print("  unreadable:", e[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
