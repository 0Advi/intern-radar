#!/usr/bin/env python3
"""Fixed web searches (Tavily) for openings the job-board sweep cannot see. No model involved.

  python3 tools/search.py            # run the queries if the last run is over 20 hours old
  python3 tools/search.py --force    # run now
  python3 tools/search.py --list     # show stored hits

Each query costs ~1 Tavily credit (free plan: ~1,000 a month). Hits whose page is already known
(job boards, tracker, details) are dropped; the rest go to data/search_hits.json with status "new",
and tools/triage.py turns them into cards. Without a TAVILY key this does nothing.
"""
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm  # noqa: E402  (for get_key only)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HITS = DATA / "search_hits.json"
STATE = DATA / "search_state.json"

YEAR = dt.date.today().year
QUERIES = [
    f"software engineer intern Bengaluru January {YEAR + 1} stipend PPO",
    f"backend intern Bangalore {YEAR + 1} batch 6 month internship stipend",
    f"SDE intern Bengaluru 2027 graduates apply",
    f"machine learning intern Bengaluru {YEAR + 1} startup stipend",
    f"AI engineer intern Bangalore startup hiring {dt.date.today():%B %Y}",
    f"data engineer intern Bengaluru 2027 batch",
    f"site:wellfound.com intern Bengaluru engineering",
    f"site:ycombinator.com/companies jobs intern Bengaluru",
    f"site:unstop.com internship software Bengaluru stipend",
    f"\"hiring interns\" Bengaluru startup engineering {dt.date.today():%B %Y}",
    f"Bengaluru startup raised Series A {dt.date.today():%B %Y} hiring engineers",
    f"site:jobs.ashbyhq.com intern Bengaluru",
    f"site:jobs.lever.co intern Bangalore",
    f"site:boards.greenhouse.io intern Bengaluru",
]
SKIP_HOSTS = re.compile(r"linkedin\.com|naukri\.com|indeed\.|glassdoor\.|internshala\.com|facebook\.com|"
                        r"youtube\.com|reddit\.com|quora\.com|twitter\.com|x\.com|instagram\.com", re.I)


def load(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False))
    tmp.replace(path)


def tavily(key, query):
    body = json.dumps({"query": query, "max_results": 8, "search_depth": "basic", "topic": "general"}).encode()
    req = urllib.request.Request("https://api.tavily.com/search", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("results", [])


def known_urls():
    urls = set(load(DATA / "postings.json", {})) | set(load(DATA / "details.json", {}))
    urls |= set(load(HITS, {}))
    return {u.rstrip("/") for u in urls}


def run(force=False):
    key = llm.get_key("tavily")
    if not key:
        print("search: no TAVILY key; skipped")
        return 0
    state = load(STATE, {})
    last = state.get("last_run")
    if last and not force and dt.datetime.now() - dt.datetime.fromisoformat(last) < dt.timedelta(hours=20):
        print(f"search: last run {last}; not due")
        return 0
    hits, known = load(HITS, {}), known_urls()
    added = errors = 0
    for q in QUERIES:
        try:
            for r in tavily(key, q):
                url = (r.get("url") or "").split("#")[0]
                if not url or SKIP_HOSTS.search(urlparse(url).netloc) or url.rstrip("/") in known:
                    continue
                hits[url] = {"title": (r.get("title") or "")[:200], "snippet": (r.get("content") or "")[:400],
                             "query": q, "found": dt.date.today().isoformat(), "status": "new"}
                known.add(url.rstrip("/"))
                added += 1
        except (urllib.error.URLError, OSError, ValueError) as e:
            errors += 1
            print(f"  query failed ({type(e).__name__}): {q}")
    state["last_run"] = dt.datetime.now().isoformat(timespec="minutes")
    save(HITS, hits)
    save(STATE, state)
    with open(DATA / "usage.jsonl", "a") as f:
        f.write(json.dumps({"time": state["last_run"], "tier": 0, "task": "search", "provider": "tavily",
                            "queries": len(QUERIES), "added": added, "errors": errors}) + "\n")
    print(f"search: {added} new page(s) from {len(QUERIES)} queries, {errors} failed")
    return 0


def main():
    if "--list" in sys.argv:
        for url, h in load(HITS, {}).items():
            print(f'[{h["status"]}] {h["title"][:80]} | {url}')
        return 0
    return run(force="--force" in sys.argv)


if __name__ == "__main__":
    sys.exit(main())
