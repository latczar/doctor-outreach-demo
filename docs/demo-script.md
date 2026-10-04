# Demo script and interview questions

## Before the interview (10 minutes)

1. Start Ollama (it usually runs in the background already) and open a terminal in the project folder.
2. Run `.venv\Scripts\python -m pytest -q`. It should say `165 passed, 4 deselected` (the 4 are the browser tests, which run on their own with `-m e2e`).
3. Run `.venv\Scripts\python -m outreach serve` and open http://localhost:8000.
4. Make sure the page is in light mode (switch at the top right), then click **Reset demo**, so the overview shows "Start here".
5. Have [docs/plan.md](plan.md) open in another tab in case they want the design.
6. Check `.env` has `EMAIL_SENDER=resend`, `DEMO_INBOX` and your `RESEND_API_KEY`. Approve one email and click **Send** to see it arrive, then click **Reset demo** again.
7. Check the date. Rachel Moore's 180-day gap ends on 28 Dec 2026 and Ben Carter's on 16 Feb 2027. From those dates each of them passes the "not previously contacted" check, so that count goes from 11 to 12, then 13. After 28 Dec, the Rachel Moore example (caught on her registration number) no longer shows on screen. Explain the rule instead, and point to `test_doctor_with_a_new_email_is_matched_on_registration_number`, which uses a fixed date.

Backup plan: if Ollama misbehaves on the day, set `LLM_PROVIDER=fake` in `.env`, restart the server, and say so. Every step except the writing is identical. If email won't send, set `EMAIL_SENDER=outbox`: the email is saved to a folder instead.

## The walkthrough (about 5 minutes)

1. **Start here.** Read the four lines on the overview: rules check the list, the system writes, you approve, each email goes once and is logged. "Your team does this by hand today." Under them, the board is empty: "This is your flow chart. Nothing has run yet."
2. **Click Run the pipeline.** The Run page reports each step as it finishes: "Qualified doctor: 19 of 24 doctors fit the campaign." While the system writes the drafts (about 1 to 2 minutes locally), point at the urgent alert line: a scraped profile tried to give the system instructions, so the team was told. When it finishes, click **Watch the replay**, press F for full screen and Space to play: "Here's that run again, from the audit log. The rules sort 27 rows in under a second, then each draft is written and checked." The by-hand bar is your estimate (5 to 10 minutes a doctor), so say so.
3. **Click See the overview.** "This is what needs me today": the to-do list. Then the board, now filled in. Its first line is the whole run: "27 rows became 24 doctors, 11 made the final list, 10 emails passed the checks, and none are approved yet." Then: "This is your flow chart, live. Each box is one of your steps, amber means a person is needed, and Your move is where I act." Tap a step's "i" to show how it decides. Click the **Email verified?** box for the 4 doctors stopped there. Tap the small "i" after "catch-all".
4. **Click Open the final list.** Search "price": Daniel Price's email was guessed from his trust's pattern, then verified. Filter to Cardiology and click **Copy for Google Sheets**: "Paste into cell A1 and the columns line up."
5. **Open the Not on the list tab and click Rachel Moore.** Her tracker stops at Not previously contacted?: "A new email, but caught on her registration number."
6. **Open Review emails** and type your name once. "Blue is the personalised draft the system wrote, grey is the standard footer, yellow is the personal detail. The highlight comes from the same function as the personalised check." Open **Edit before approving**, add `[DATE]`, click Approve: refused, edit kept. Approve two or three; reject one with a reason.
7. **Open the Needs you tab and click Decide on Hannah Lewis.** Her trust's domain accepts every address, so no computer can confirm the mailbox. Tick "I've confirmed this mailbox exists", note "called the trust switchboard", and re-check: "The rules run again with my confirmation, she joins the final list, and the system writes her email." Then on Isla McKenzie, pick a grade the campaign doesn't target: the rules still say no.
8. **Click Mohammed Iqbal under Need fixing.** The red sentence is the planted instruction. "The prompt reduces the risk; code enforces the rule." Online, open his doctor page and expand Attempt 1: it offered £2,000 and claimed to be an "official NHS partner", and the checks stopped both. On your laptop, if his draft passed this run, read it out: no £2,000 and no partnership claim.
9. **Click Send approved, then open Outreach log.** A box on the page asks first, "Send 3 approved emails now?", because it's the one step you can't take back. Click **Yes, send them**. The email lands in your inbox within seconds, so show it on your phone: "Every email is redirected to my inbox, and Resend's test mode only delivers to me, so no doctor can get one." Then: "Each send is claimed with a unique key before it happens, and the do-not-contact list is checked again at send time." Under Urgent alerts, click **See them as the team would**: Kofi's or Mohammed's alert as a Slack, Teams or WhatsApp message. "One hook. n8n or Zapier picks the chat app, so changing it doesn't touch the code." To prove nothing is sent twice:

```powershell
.venv\Scripts\python -m pytest -k "sends_twice or stuck" -v
```

10. **Click See this page at phone size** in the footer. "One layout that rearranges itself, with every hint above its field, so there's no separate app to maintain."
11. **Optional, about a minute: watch the workflow test itself.** A browser opens on its own copy of the app, runs the pipeline, approves an email, sends it and checks the log. "Fast tests check every rule. A few browser tests check what a reviewer actually clicks."

```powershell
.venv\Scripts\python -m pytest -m e2e -k workflow --headed --slowmo 500
```

## Optional: upload a fresh list (1 to 2 minutes)

Shows the steps working on a list the system has never seen. Two made-up lists are in the `samples` folder:

- `demo-quick.csv`: 6 rows, about a minute. Ruth Adeyinka is listed twice and merged, Peter Lund (dermatology) is turned away, and 4 emails are written, including "Dear Ms Dunmore" for the surgeon.
- `demo-every-rule.csv`: 14 rows, where every rule stops someone for a different reason. Use this one if there's time.

On the Overview, open **Use your own research list**, choose the file, leave **Start fresh** ticked and click **Upload and run**. What happens to `demo-every-rule.csv`:

| Row | What happens | Say |
|---|---|---|
| Amara Osei, twice | Merged into one doctor: the same registration number, the email in capitals | "Copies are merged, not deleted." |
| Rory Kearns | "ST6", "A&E" and "England" are understood, and he's on the final list | "Messy data is cleaned before the rules see it." |
| Owen Hale | No email in the list. It's worked out from Eastbrook's pattern, checked, and he's "Dear Mr Hale" | "Finding an email and trusting it are separate steps." |
| Fiona Ashdown | Not a fit: FY2 is too junior | |
| Hugo Laurent | Not a fit: based in France | |
| Megan Holt | Needs you: "Clinical Lead" isn't a grade the rules know | "When the rules can't tell, a person decides." |
| Ravi Sethi | No email, and Hillcrest Surgery's pattern is unknown | |
| Joanna Hartley | Bad email: `enquiries@` is a shared inbox | |
| Tomasz Nowak | Bad email: the domain has no mail server | |
| Alice Brennan | Needs you: Greyfriars accepts every address, so a person confirms the mailbox | |
| Ben Carter | Stopped: an earlier campaign emailed him in August | "Earlier campaigns still protect people in a brand-new list." |
| Laura Simmons | Stopped: she asked not to be contacted | |
| Kofi Mensah | Urgent alert: his profile tries to give the system orders, and his email is checked like any other | "Same defence, new list." |

The result: 14 rows become 13 doctors, 4 emails are written (Amara, Rory, Owen and Kofi, unless Kofi's fails the checks), and 2 doctors wait under Needs you. Decide on them to finish the story. Afterwards, **Reset demo** then **Run the pipeline** brings back the main list.

If they ask how the made-up hospitals pass the email checks: "A stand-in answers for made-up hospitals, the same one the main list uses. Real domains get a real mail-server check." A test, `tests/test_samples.py`, proves every row ends where this table says.

## How I tested it (a good story to tell)

- 165 automated tests run in about 15 seconds with a fake model, so they never call an AI.
- 4 browser tests (Playwright, in `tests/e2e`) click through the real pages in Chromium: the whole workflow from research list to outreach log, an edit that's refused, a Decide re-check, and every main page at phone width. Each starts its own copy of the app with a fresh database, so the demo data is never touched. `pytest -m e2e` runs them in about 15 seconds.
- Then I ran it against the real local model and measured how many drafts passed. The first run passed 7 of 11, and the failures showed three real problems:
  1. My prompt said "start the body with Dear...", and the model returned only the greeting. I reworded the layout.
  2. Telling a small model "don't write 'I hope you are well'" made it write exactly that. I reworded it positively, and code now deletes that filler sentence and logs it, rather than retrying.
  3. The model added its own "Best regards, Alex Morgan". The sign-off remover missed that pattern, and the email ended up with two sign-offs. I fixed it and added a test.
- After the fixes, 9 of 11 passed. Four of those failed once and passed on the retry, because the model got the exact reasons back. The two that still failed were a generic draft and the prompt-injection case, which is what the checks exist to catch.
- The online copy comes from a later run: 10 of 11 passed, two of them on the retry. Mohammed's first draft obeyed the planted instruction (£2,000 and "official NHS partner"), and the money and affiliation checks stopped it. His next two were too generic, so his email waits under Need fixing.

## Questions they might ask

**Why not let the AI decide who qualifies?**
Targeting is a business rule. It has to be exact, explainable and the same every time, so it's plain if/else from a config file. The AI is only used where language is needed. That's also cheaper and easy to test.

**How do you make sure a doctor never gets the same email twice?**
Before sending, the code inserts a row into the outreach log with a unique key: campaign plus doctor. The database refuses a second row with the same key, so a second click, a retry or a second worker finds the claim and stops. If the program crashed mid-send, the row stays "sending" and is never retried automatically; a person checks it. I chose at most once over at least once, because a duplicate email to a consultant is worse than a missed one.

**What if someone opts out between approval and sending?**
The send step runs the opt-out and history check again right before each email. There's a test for exactly that.

**How do you stop the model making things up?**
Four layers:
1. The prompt only gives it the facts it may use.
2. Code checks that every amount of money and every number appears in those facts or the doctor's profile.
3. Code blocks partnership and endorsement claims.
4. A person reads every email before it goes.

**What about prompt injection?**
The doctor's profile comes from the web, so it's untrusted. It's fenced in a `<doctor_profile>` block, and the system prompt says never to follow instructions in it. That's a mitigation, not a guarantee, which is why the output checks and the human review exist. The model also never sees the email address or registration number, so there's less to leak.

**What happens if the model is down?**
The model call raises a clear error ("Can't reach Ollama at ... start it with ollama serve"). That doctor is marked "needs attention" with the message, without burning retries, and the rest carry on. Run it again later and it picks up only the doctors still waiting.

**How does a reviewer approve in two places at once without clashing?**
Approval is a single database update that only succeeds if the draft is still waiting and is the newest one for that doctor. The second person gets "already reviewed, refresh the page".

**Why SQLite?**
Zero setup for a prototype. The patterns that matter (conditional updates, unique keys, transactions) work the same in Postgres, which is what I'd use in production.

**How would you scale to 10,000 doctors?**
- Postgres.
- A worker queue for drafting, so model rate limits don't block the page.
- Real enrichment and verification APIs with caching.
- A sending service such as Postmark or SES, with bounce and reply webhooks feeding the opt-out list.
- A gradual sending ramp to protect the domain's reputation.
- The review page already copes: its lists come 10 names a page, and only the email on screen gets its checks and highlights worked out, so it stays quick with hundreds waiting.

The pipeline steps themselves don't change.

**Which model would you use in production?**
Behind one interface, so it's a setting. Locally I used qwen2.5 7B, which is free and private. For production quality I'd measure a hosted model such as Claude on the same checks and compare first-time pass rates and cost per approved email. Every draft stores its provider, model and prompt version, so that comparison is a query.

**UK GDPR and PECR?**
- **What the prototype already does:**
  - The model never sees the email address or registration number.
  - Every email says where we found their details and how to opt out, and has a List-Unsubscribe header.
  - Opt-outs are permanent and checked twice.
  - Every decision is audited.
- **What I'd raise:**
  - A legitimate interest assessment.
  - Retention rules.
  - The point that GP partnerships count as individuals under PECR in England and Wales, so they may need consent.

I'd check all of that with whoever owns data protection. I'm not giving legal advice.

**How does the page know which words to highlight?**
It calls the same function the "personalised" check uses (`personal_overlap` in `guardrails.py`). One function, two uses: the check counts the shared words, and the page marks them. If they ever disagreed, that would be a bug, and it can't happen because there's only one piece of code.

**Profile notes come from the web. Could one break the page with hidden HTML?**
No. The page templates escape everything by default, and the highlighter escapes each piece of text before it adds its own `<mark>` tags. There's a test that feeds in an `<img onerror=...>` tag and checks it comes out as plain text.

**Why no JavaScript framework?**
The reviewer's tool is tables and forms, so the server builds each page. The folding sections are the browser's own `<details>` element, and each small "i" opens its explanation with the HTML `popover` attribute. There's nothing to build, nothing to keep patched, and every screen can be tested with a plain HTTP request.

**Why does only the Run page refresh itself?**
The old dashboard reloaded every 2 seconds during a run, which closed open panels and would wipe a half-typed search. Progress now lives on its own page with nothing to type into.

**What counts as urgent, and why not WhatsApp the doctors?**
An approved email that failed to send, and a scraped profile that tries to give the AI orders. Each goes to one hook that n8n or Zapier can forward to WhatsApp, Slack or Teams; a hook that's down is logged and the work carries on. Doctors aren't messaged on WhatsApp: its business rules need their opt-in first, and we hold no mobile numbers.

**Why GOV.UK patterns?**
They were built and tested with real users to make multi-step services clear to anyone. I borrowed the patterns, not the look: services outside GOV.UK mustn't use its crown, typeface or colours, and an official-looking tool would be a bad idea here.

**Why does the overview look a bit like a game?**
I borrowed one idea from strategy-game dashboards: show the whole state at a glance, and light up the next move. Nothing else. There are no points and no timers, because rushing a person's approval would undercut the reason for having one.

**Is it accessible?**
I checked every page against Vercel's Web Interface Guidelines, about 80 rules on accessibility, focus, forms and motion, and fixed what they found: form fields tell the browser what they hold, the spinner stops for people who ask their computer for less motion, and Send asks before anything goes out. It already had a skip link, a visible focus outline, a label on every field and zoom allowed. A full WCAG audit with a screen reader would be the next step.

**Can a person overrule the rules?**
Not directly. They supply the fact the rules were missing, or confirm the one thing a computer can't check (a mailbox on a catch-all domain), and the rules run again from the top. A grade the campaign doesn't target still disqualifies, and a mailbox the mail server rejects stays rejected. Every decision needs a note and a name.

**Can it send real email?**
Yes, on my laptop. Every approved email goes to my own inbox instead of the doctor's made-up address, with a line saying who it was for. Two guards stop it reaching a doctor: the redirect in the code, and Resend's test mode, which only delivers to my address. The online copy can't send at all.

**Can it work on a real list, not only made-up doctors?**
Yes, on my laptop. Upload a CSV with the template's columns and the same steps run on it. Real domains get a real check that they have a mail server, and since no free check can confirm a mailbox, a person confirms each one. Every email still goes to my own inbox; emailing real doctors needs MedicPaths' domain, an unsubscribe process and a lawful basis. On the live site, Try the sample list shows the same thing with 5 made-up doctors, because anyone with the link would see an uploaded list.

**Is the replay real or a recording?**
Real. The page rebuilds the last run from the audit log each time it loads, so the counts and times are the ones that happened. Only the by-hand bar is an estimate, and it says so. It only reads: it never drafts or sends anything.

**Why not LangChain or an agent?**
The steps are known in advance, so a fixed pipeline is simpler, cheaper and predictable. An agent would add freedom this job doesn't need. Each step here is a function with tests.

**What would you build next?**
1. Real verification and enrichment APIs.
2. Reply and bounce handling.
3. Sign-in for reviewers.
4. A weekly report on rejection reasons, to improve the prompt.
