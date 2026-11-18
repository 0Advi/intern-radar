# Where to sweep each day

## Posting sources
- Company careers pages of tracked companies (re-check weekly)
- LinkedIn Jobs + posts: "hiring interns", "SDE intern 2027", "backend intern Bengaluru", "PPO"
- Wellfound (India, internship, engineering), YC Work at a Startup (India / remote)
- Instahyre, Cutshort, Unstop, Internshala (filter stipend ≥ 40k — most listings fail this)
- X / LinkedIn posts by founders and engineering managers announcing intern hiring
- Funding news (Entrackr, Inc42, YourStory, TechCrunch India): Series A–C companies usually hire soon after

## Search queries (rotate; add the current month/year)
- `"software engineer intern" 2027 Bengaluru stipend`
- `"backend intern" OR "SDE intern" "PPO" India`
- `"6 month internship" "full-time conversion" engineering India`
- `site:wellfound.com intern backend Bangalore`
- `site:jobs.lever.co OR site:boards.greenhouse.io OR site:jobs.ashbyhq.com intern India engineering`
- `"hiring interns" "Python" OR "FastAPI" Bangalore`
- `"AI engineer intern" OR "ML intern" India 2027`

## Company pools likely to meet ₹40–50k stipend and ₹10–12 LPA+ PPO
Verify each before mailing; record the evidence.
- Well-funded Indian product startups and unicorns (fintech, SaaS, devtools, logistics, commerce)
- India engineering centres of global product companies
- HFT / quant / trading-tech firms (high pay, hard bar — backend variant)
- Infra, observability, devtools and AI-agent startups (Faultline is a direct fit)
- Health-tech / bio-ML companies (BioVeritas is a direct fit)
- B2B retail-tech / ops SaaS (RetailExpansion is a direct fit)

## Checking compensation
Posting text → Glassdoor / AmbitionBox intern salaries → Levels.fyi → LinkedIn/Reddit/Leetcode
discuss threads. Service companies and mass recruiters rarely meet the bar; skip them.

## Sources the script already reads every sweep (do not repeat by hand)
- Company boards in `tools/boards.json` (Greenhouse, Lever, Ashby, SmartRecruiters, Workday)
- github.com/Sarthzk/internship-tracker `data/listings.json` — big companies on their own portals
- Unstop public internship search, kept only when the stipend is ₹40k+ a month
(Two other GitHub trackers listed here earlier were stale and have been dropped.)

## Not reachable by the script — the deep sweep's job
- Startups on Keka, Darwinbox, Zoho Recruit, Kula, Workable or their own careers pages
- LinkedIn, Wellfound, Instahyre, Cutshort, Internshala: best covered by job-alert emails sent to the
  outreach inbox (Aditya sets these up once); read them in the daily run
- Roles titled without the word "intern" (University Graduate, New Grad, Student Researcher)

## Contact-finding method that worked (10 Nov 2026)
Open the company's /careers page in the browser and read its mailto links. About 1 in 6 Bengaluru
startups publish a careers@ address; Ashby/Greenhouse/Lever boards never do — log those as `form_only`.
