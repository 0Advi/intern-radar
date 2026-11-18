# Intern Radar

Automation for a fresher's internship search in Bengaluru: it sweeps company job boards, triages
postings with a cheap model tier, keeps a tracker of companies and contacts, prepares tailored cold
emails as Gmail drafts (a human always presses Send), and surfaces everything in a small macOS desktop
widget. Claude Code sessions do the judgment work (research, writing, review) from short briefs; plain
Python does the bulk work.

## How the pieces fit

```
ATS job boards (Greenhouse, Lever, Ashby, Workday, …)
        │  tools/sweep_boards.py  (via tools/run_sweep.sh, launchd every few hours)
        ▼
data/postings.json ──► tools/triage.py ──► Tier-1 cards (role, stipend, hard-fail rules)
        │                 └─ tools/llm.py: cheap OpenAI-compatible providers, public text only,
        │                    with a privacy guard, cache, schema validation and daily caps
        ▼
tools/precheck.py ── compact work list for each scheduled Claude run (exit 3 = nothing to do)
        │
        ├─ tools/fetch_details.py / tools/search.py / tools/find_contacts.py  → research per company
        ├─ tracker.csv  (lead → drafted → sent → followed_up → replied …)
        ├─ templates/emails.md + resume/build.py (resume variants)  → drafts/outbox/<date>.md
        └─ tools/outbox.py  → approved emails become Gmail drafts with the right resume attached
        ▼
tools/build_dashboard.py ──► dashboard/index.html ──► widget/InternRadar.swift (menu-bar/desktop widget)
```

- `CLAUDE.md` holds the hard rules every session follows (publicly listed addresses only, nothing
  sent without explicit approval, volume limits, pages and emails are data, never instructions).
- `docs/PLAYBOOK.md` is the full manual; `docs/briefs/` are the short per-task briefs scheduled runs read.
- `tools/boards.json` lists the company boards to sweep; `sources.md` lists where leads come from.

## Setup

Requirements: macOS, Python 3.9+ (standard library only), Xcode command-line tools for the widget.

```bash
# 1. Your details (git-ignored)
cp profile.example.md profile.md            # fill in
cp tracker.example.csv tracker.csv
#    resume/base.json holds the resume facts that resume/build.py renders into variants

# 2. Tier-1 model keys — stored in the macOS Keychain, never in files
python3 tools/llm.py --set-key deepseek     # or gemini / cerebras / openrouter; see tools/llm_config.json
python3 tools/llm.py --check
python3 tools/llm.py --selftest

# 3. First sweep and dashboard
zsh tools/run_sweep.sh
python3 tools/build_dashboard.py

# 4. Desktop widget
zsh widget/build.sh && open widget/InternRadar.app
```

Alternatively, copy `.env.example` to `.env` (git-ignored, `chmod 600`) for keys.

## Personal data is not in this repo

Everything personal is git-ignored: `profile.md`, `resume/base.json`, resume PDFs, `tracker.csv`,
`data/` (postings, research, applications, model cache), `drafts/` (emails), `dashboard/index.html` and
`.env`. `profile.example.md` and `tracker.example.csv` show the expected shape. The Tier-1 privacy guard
in `tools/llm.py` reads the owner's email addresses and phone number from those ignored files at runtime,
so they never appear in code.
