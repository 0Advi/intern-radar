#!/usr/bin/env python3
"""Find email addresses a company publishes on its own site (home page + careers / jobs pages).

  python3 tools/find_contacts.py plane.so appsmith.com keploy.io
  python3 tools/find_contacts.py --file domains.txt

Prints one block per domain: each address, the page it is on, where it is visible ([link] = a mailto
link, [text] = shown on the page, [code-only] = only in scripts/data — never use those), and the words
around it, so you can tell a hiring address from support / sales / privacy. Reads public pages only. An address listed
here is still unverified: it can bounce (Matrice AI's did), and only addresses meant for hiring or
applications may be used for cold email.
"""
import html
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
JUNK = re.compile(r"\.(png|jpe?g|svg|webp|gif|js|css|woff2?)$|sentry|example\.|wixpress|w3\.org|schema\.org|@2x|"
                  r"domain\.com|email\.com|yourcompany|company\.com|u003e", re.I)
CAREERS = re.compile(r"career|jobs?\b|join-?us|joinus|hiring|work-with|we-are-hiring|open-?positions|life-at", re.I)
HIRING = re.compile(r"career|job|hiring|hr@|talent|recruit|people|join|intern|apply|work", re.I)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) contact-page-reader",
                                               "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.geturl(), r.read(600_000).decode("utf-8", "replace")


def visible_flags(page):
    """Addresses a visitor can actually see: in a mailto link whose target matches, or in page text
    outside <script>/<style>/JSON. Addresses only in code or behind a link to another address are risky."""
    mailto = {m.lower() for m in re.findall(r'href=["\']mailto:([^"\'?]+)', page, re.I)}
    body = re.sub(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>", " ", page)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    shown = {m.lower() for m in EMAIL.findall(html.unescape(body))}
    return mailto, shown


def emails_in(page):
    text = html.unescape(page)
    out = {}
    for m in EMAIL.finditer(text):
        addr = m.group(0).strip(".").lower()
        if JUNK.search(addr) or len(addr) > 60:
            continue
        around = re.sub(r"<[^>]+>|\s+", " ", text[max(0, m.start() - 110):m.end() + 60]).strip()
        out.setdefault(addr, around[-170:])
    return out


def scan(domain):
    domain = domain.strip().removeprefix("https://").removeprefix("http://").strip("/")
    found, pages, error = {}, [], ""
    try:
        base, home = fetch(f"https://{domain}")
    except Exception as e:  # noqa: BLE001
        try:
            base, home = fetch(f"https://www.{domain}")
        except Exception as e2:  # noqa: BLE001
            return domain, {}, [], f"{type(e2).__name__}"
    host = urlparse(base).netloc
    links = {urljoin(base, h) for h in re.findall(r'href=["\']([^"\'#]+)', home) if CAREERS.search(h)}
    links |= {urljoin(base, p) for p in ("/careers", "/jobs", "/about", "/contact")}
    same_site = [l for l in links if urlparse(l).netloc.replace("www.", "") == host.replace("www.", "")][:8]
    vis = {}
    def note_visibility(page):
        mailto, shown = visible_flags(page)
        for a in mailto | shown:
            v = vis.setdefault(a, set())
            if a in mailto:
                v.add("link")
            if a in shown:
                v.add("text")
    note_visibility(home)
    for addr, ctx in emails_in(home).items():
        found[addr] = ("/", ctx)
    for link in same_site:
        try:
            _, page = fetch(link)
        except Exception:  # noqa: BLE001
            continue
        path = urlparse(link).path or "/"
        pages.append(path)
        note_visibility(page)
        for addr, ctx in emails_in(page).items():
            if addr not in found or (CAREERS.search(path) and not CAREERS.search(found[addr][0])):
                found[addr] = (path, ctx)
    external = sorted({urlparse(l).netloc for l in links if urlparse(l).netloc.replace("www.", "") != host.replace("www.", "")})
    found = {a: (p, c, ",".join(sorted(vis.get(a, ()))) or "code-only") for a, (p, c) in found.items()}
    return domain, found, external[:4], error


def main():
    args = sys.argv[1:]
    if args[:1] == ["--file"]:
        args = [l.strip() for l in open(args[1]) if l.strip() and not l.startswith("#")]
    with ThreadPoolExecutor(12) as ex:
        for domain, found, external, error in ex.map(scan, args):
            hiring = {a: v for a, v in found.items() if (HIRING.search(a) or HIRING.search(v[0]) or HIRING.search(v[1]))
                      and v[2] != "code-only"}
            mark = "HIRING?" if hiring else ("other" if found else ("unreadable: " + error if error else "none"))
            print(f"\n== {domain}  [{mark}]" + (f"  jobs hosted on: {', '.join(external)}" if external else ""))
            for addr, (path, ctx, seen) in sorted(found.items(), key=lambda x: x[0] not in hiring):
                print(f"   {'*' if addr in hiring else ' '} {addr}  ({path}) [{seen}]  …{ctx[-120:]}")


if __name__ == "__main__":
    main()
