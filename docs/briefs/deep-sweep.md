# Brief: deep sweep (scheduled once a day, 8 PM)

Purpose: find openings the free board script cannot see, and give every unreviewed posting a verdict.
No email, no Gmail.

1. `python3 tools/precheck.py --task deep-sweep --sweep-if-stale` (runs the free board sweep first if
   the last one is over 6 h old). Work only from the list it prints.
2. **Unreviewed postings in the list:** for each, read its text (`python3 tools/fetch_details.py --show
   | grep -A60 '<url>'`; if that prints nothing the posting has no stored text, so open the URL), check batch / degree / CGPA / dates / pay, and write a details.json entry
   with a verdict (strong / good / possible / weak / no). `strong`/`good` is what alerts Aditya, so
   be accurate. Format: PLAYBOOK "Opportunity detail pages".
   If the list has **Tier-1 cards**, use them to order your work (highest score first) and read the
   verified facts instead of the whole posting where they settle the question; still record your own
   verdict (trial mode). Glance at a few Tier-1 rejects and say in the report if any looks wrong.
3. **Web search** (budget: about 8 searches, 20 minutes; fewer when the Tavily search already ran —
   its hits arrive as cards): queries from `sources.md`, current month and
   year; small startups, company career pages, founder hiring posts, Unstop / Wellfound / YC. Skip
   companies in the "already known" list. Verify each candidate on the company's own page.
   To find published hiring emails cheaply use `python3 tools/find_contacts.py domain1 domain2 …`.
4. New qualifying openings → tracker row (`lead` if a published hiring email exists, else `form_only`)
   plus a details.json entry. If a company's jobs are on Greenhouse / Lever / Ashby / SmartRecruiters /
   Workday, add the token to `tools/boards.json` (`python3 tools/sweep_boards.py --probe name`).
5. `python3 tools/build_dashboard.py`, then `python3 tools/precheck.py --log "task=deep-sweep result=..."`.
6. Report: new openings (company, role, how to apply, pay evidence) and which Aditya must apply to himself.
