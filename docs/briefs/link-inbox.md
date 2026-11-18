# Brief: link inbox (scheduled every 3 h)

1. `python3 tools/precheck.py --task link-inbox`. Exit 3 → stop now, nothing else.
2. For each queued item in the printed list:
   - **Pasted link:** open it (web fetch; browser only if the page needs JavaScript). Find company, role,
     location/work mode, dates, stipend, conversion, batch rules, how to apply. One or two web searches
     at most to check pay. Judge against the goal in CLAUDE.md.
     - qualifies + published hiring email → tracker row `lead`, details.json entry with `mail_to`
     - qualifies, form only → tracker row `form_only`, details.json entry
     - not a fit / unverifiable → no row
     Check the "companies already known" idea by grepping `tracker.csv` for the company name only.
   - **Cold-email request** (`type: mail`): write the email (110–160 words, `templates/emails.md`, facts
     only from `profile.md`, one checked company fact, Drive resume link from PLAYBOOK "Sending
     mechanics", never "attached") to `drafts/outbox/<date>-links.md`; tracker row → `drafted`; put
     `draft: {to, subject, body, drafted}` in its details.json entry. Do not send. Do not open Gmail.
3. Mark each item `done` with a one-line `result` in `data/inbox.json` (keep other fields).
4. `python3 tools/build_dashboard.py`, then `python3 tools/precheck.py --log "task=link-inbox result=..."`.
5. Report: each item, verdict, full text of any draft, ask Aditya to approve.
details.json format: PLAYBOOK "Opportunity detail pages". Edit JSON with a small Python snippet that
loads, updates one key and writes back — never print the whole file.
