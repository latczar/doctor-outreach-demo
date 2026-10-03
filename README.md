# Doctor outreach pipeline (prototype)

A working prototype of the MedicPaths outreach workflow:

```
Qualified doctor -> Email available? -> Email verified? -> Not previously contacted?
  -> Generate personalised email -> Human approval -> Send -> Log outreach
```

A messy research spreadsheet goes in. Plain rules filter it step by step, an AI model writes a draft for each doctor who survives, a named person approves every email, and the system sends and logs it without ever emailing the same doctor twice.

All data is made up. Every email address uses the reserved `.example` domain, and the send step refuses anything else.

- **Plan and concepts:** [docs/plan.md](docs/plan.md)
- **Interview walkthrough and likely questions:** [docs/demo-script.md](docs/demo-script.md)

## Quick start (Windows, PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env
.venv\Scripts\python -m outreach reset
.venv\Scripts\python -m outreach run
.venv\Scripts\python -m outreach serve
```

Then open http://localhost:8000.

`.env` uses the local Ollama model (`qwen2.5:7b-instruct`). If Ollama isn't running, start it with `ollama serve`, or set `LLM_PROVIDER=fake` to run with no AI at all.

## Online demo (Vercel)

The same app runs on Vercel's free tier as an online prototype, so others can click through it.
- `index.py` re-exports the app; `vercel.json` sets the time limit.
- On Vercel (which sets `VERCEL=1`), the settings switch to demo mode:
  - files live in `/tmp`, the only writable place;
  - a fresh server starts from `data/demo-snapshot.db`, a snapshot of a real run with the local model, so the first drafts are real AI output;
  - new drafts use the template writer, because the local model isn't online;
  - runs finish in one go, because an online server can pause between requests;
  - real email is switched off: sending always saves to the outbox, so visitors can't send anything.
- Data resets when the server restarts, and Reset demo goes back to the snapshot rather than an empty list. A banner says so.

Live copy: https://doctor-outreach.vercel.app (pushes to `main` redeploy it). The screens say "the system" rather than AI.

To update it: `vercel deploy --prod`. To rebuild the snapshot, run the pipeline locally with `DATABASE_PATH=data/demo-snapshot.db`.

## What it does with the sample data

| Step | Result | Examples from the made-up list |
|---|---|---|
| Rows in the research list | 27 | |
| Tidy and remove duplicates | 24 unique | Sarah O'Neill matched on registration number, J Okafor on email, Chloe Barker on name and employer |
| Qualified doctor? | 19 | A foundation doctor, a dermatologist and a doctor in Canada are disqualified. "Clinical Fellow" and a row with no name go to a person |
| Email available? | 18 | Daniel Price's email is guessed from his trust's pattern. Ashdown Community Hospital's pattern is unknown |
| Email verified? | 14 | No mail server, a mailbox that no longer exists, an `info@` inbox. A catch-all domain goes to a person |
| Not previously contacted? | 11 | Contacted 42 days ago, opted out, and Rachel Moore, who has a new email but the same registration number |
| Draft passed checks | 10 with the fake model, about 9 with qwen2.5 7B | One profile contains a hidden prompt injection |
| Approved, sent, logged | Up to you | |

## Commands

| Command | What it does |
|---|---|
| `python -m outreach reset` | Empties the database and loads the made-up outreach history and do-not-contact list |
| `python -m outreach run` | Imports the research list, runs every check and writes drafts |
| `python -m outreach run --file my.csv` | The same for another CSV with the same columns |
| `python -m outreach funnel` | Prints how many doctors passed each step |
| `python -m outreach send` | Sends every approved email |
| `python -m outreach export [--all]` | Writes the final list to `exports/` as CSV |
| `python -m outreach serve` | Starts the dashboard on http://localhost:8000 |
| `python -m pytest` | Runs the tests (no AI calls; about 15 seconds) |
| `python -m pytest -m e2e` | Runs the 4 browser tests in Chromium (laptop only). Add `--headed --slowmo 500` to watch them |

Prefix each with `.venv\Scripts\` on Windows if the virtual environment isn't activated.

## Settings

All in `.env` (see `.env.example`):

| Setting | Options |
|---|---|
| `LLM_PROVIDER` | `fake` (template, no AI), `ollama` (local and free), `anthropic` (Claude: `pip install anthropic` and set `ANTHROPIC_API_KEY`) |
| `EMAIL_SENDER` | `outbox` writes `.eml` files to `outbox/`. `smtp` sends to `SMTP_HOST:SMTP_PORT`, for example Mailpit. `resend` sends real email through Resend (see below) |
| `DEMO_INBOX` | Optional. Every email goes here instead of the doctor's made-up address, with a line at the top saying who it was for |
| `RESEND_API_KEY` | Your Resend key, for `EMAIL_SENDER=resend`. It stays in `.env`, which never goes to GitHub |
| `ALLOWED_RECIPIENT_SUFFIXES` | The send step refuses any other address. Defaults to `.example,.test` |
| `ALERT_WEBHOOK_URL` | Optional. Urgent alerts are posted here as JSON, so n8n or Zapier can forward them to WhatsApp, Slack or Teams. Without it they go to `outbox/alerts.log` |

Campaign rules live in [config/campaign.json](config/campaign.json): target countries, specialties and grades, the offer facts the model may use, the sender, the 180-day gap between contacts, the daily send cap and the number of draft attempts.

To watch real emails arrive in a web inbox, run Mailpit (`docker run -p 8025:8025 -p 1025:1025 axllent/mailpit`), set `EMAIL_SENDER=smtp`, and open http://localhost:8025.

To get real emails on your phone, sign up to [Resend](https://resend.com) for free with your own address and create a key with sending access. Then set `EMAIL_SENDER=resend`, `DEMO_INBOX` (that address) and `RESEND_API_KEY` in `.env`. Every approved email then lands in your inbox, starting with a line that says which doctor it was for. Two things stop it reaching a doctor: the app redirects every email to your inbox, and Resend's test mode only delivers to the address you signed up with. The online copy ignores these settings.

To run your own research list on the laptop, open **Use your own research list** on the Overview, download the template, fill in people who've agreed to take part (up to 500), and click **Upload and run**. The rows go through the same steps and stay in the laptop's database. The live site can't take your own file, because anyone with the link would see it. It offers **Try the sample list** instead: 5 made-up doctors go through the same steps as an upload. Real domains get a real check that they have a mail server (an MX lookup, with dnspython). No free check can confirm a mailbox exists, so those doctors go to Needs you, where a person confirms it. A real address is only ever emailed through the demo inbox redirect; without it, the send step blocks it.

## How it's built

```
config/campaign.json     targeting rules, offer facts, sender, limits
data/seed/               made-up research list, history, opt-outs, email fixtures
prompts/v1/              system prompt and draft prompt (version saved on every draft)
outreach/
  ingest.py              tidy + dedupe (union-find over registration number, email, name + employer)
  gates.py               qualify, email available, verify, history: plain rules
  drafting.py            prompt building, model call, retries with feedback
  guardrails.py          the checks on each draft, and the safe tidy-ups
  review.py              approve / reject / regenerate, with conditional updates
  sending.py             claim-then-send idempotency, re-check at send time, daily cap, safety guard
  notify.py              urgent alerts through one hook (file locally, or a webhook for n8n or Zapier)
  override.py            a person decides what the rules couldn't, then the rules run again
  pipeline.py            runs the steps in order and works out the funnel
  llm/                   fake, ollama, anthropic behind one small interface
  web/                   FastAPI + Jinja pages: overview, doctors, review, doctor, log, run, replay, architecture, phone view
    present.py           plain English for the screens: the three lists, trackers, highlights, the small "i" explanations
    replay.py            the replay page: the last run, rebuilt from the audit log
tests/                   157 tests, one file per step plus end-to-end, screen, alert, decision and web tests;
                         tests/e2e holds the 4 browser tests (Playwright)
```

Python, FastAPI, SQLite and server-rendered pages, styled with patterns from the GOV.UK Design System in our own look. No LangChain, no agents, no queue: each step is a function you can point at and test.

The screens: **Overview** (your to-do list, then each step as "19 of 24 doctors fit the campaign"), **Doctors** (search, filters, a tracker per doctor, a Decide button for doctors the rules couldn't place, Download CSV or Copy for Google Sheets), **Review emails** (one at a time, with the lists beside it 10 names a page), **Outreach log**, a **Run** page that reports progress, a **Replay** page that plays the last run back from the audit log (Space plays, F is full screen), a **Production architecture** page (footer link) and a **phone view**. Light by default, with a dark switch.

## Honest limitations

- Email finding and verification use local fixture files. Real services (Hunter, ZeroBounce and similar) would sit behind the same two interfaces.
- Reviewers type their name. A real version needs sign-in.
- The "personalised" check matches words between the draft and the profile. It catches generic drafts, but it can't judge whether a detail is used well; the human review does that.
- Run on a 7B local model, about 2 in 11 drafts fail the checks and go to a person. A larger model should pass more often. That's untested here.
- UK GDPR and PECR need a proper assessment before real outreach. One point to raise: in England and Wales, GP partnerships count as individuals under PECR, so they may need consent rather than legitimate interest. This prototype records the evidence (source, opt-outs, audit trail) but isn't legal advice.
