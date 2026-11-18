# Brief: daily outreach (scheduled 10 AM)

1. `python3 tools/precheck.py --task daily`. Work from its list (queued items, unreviewed postings,
   follow-ups due, drafts waiting, emails sent today, companies already known).
2. **Replies (Gmail connector, no browser):** `search_threads` with
   `query: "in:inbox newer_than:3d -from:me"`, `view: THREAD_VIEW_SUMMARY`. Ignore newsletters and Google
   notices. Open a thread with `get_thread` (`messageFormat: PLAIN_TEXT`) only if it is a reply to one of
   our emails, a bounce ("Mail Delivery Subsystem"), or an application email (assessment / interview /
   rejection).
   - reply → tracker `replied`, `reply_date`, `contact_name`, `reply_summary` starting `YES:` / `NO:` /
     `MAYBE:`; clear follow-up dates; draft an answer to `drafts/replies/<date>-<company>.md` (not sent)
   - bounce → tracker `bounced`, reason in notes
   - assessment / interview / rejection for a role he applied to → `data/stages.json`
   - **Sent check:** for each row in `python3 tools/outbox.py pending`, `search_threads`
     `in:sent to:<address> newer_than:7d`; if found, `python3 tools/outbox.py sent <id> <date>`
     (add `--followup` for a follow-up).
   If the connector errors twice, fall back to the browser and read pages as text (`get_page_text`),
   never typing a password.
3. **Follow-ups due** (from the list): draft them per `templates/emails.md` (same thread, short).
4. **New cold emails:** only if there are fewer drafts waiting than the daily cap allows. Candidates come
   from tracker rows with status `lead` and the precheck list; do not run a web-wide search here (that
   is the deep sweep's job). One checked company fact each; facts about Aditya only from `profile.md`.
5. Save all drafts to `drafts/outbox/<date>.md` in the usual format (`## N. Company — address (NEW|FOLLOW-UP 1)`,
   `**Subject:**` line, body; follow-ups get a `Thread: "<original subject>"` line); tracker rows →
   `drafted`. Check them with `python3 tools/outbox.py prepare <file>` (must print no PROBLEMS).
   Nothing is sent and no Gmail draft is created in this run.
6. Queued inbox items, if any: handle as in `docs/briefs/link-inbox.md`.
7. `python3 tools/build_dashboard.py`, then `python3 tools/precheck.py --log "task=daily result=..."`.
8. Report: replies (with draft answers), follow-ups drafted, new drafts and to whom, form-only roles to
   apply to, and ask Aditya to approve the batch.
