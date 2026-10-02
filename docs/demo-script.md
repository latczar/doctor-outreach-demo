# Demo script and interview questions

## Before the interview (10 minutes)

1. Start Ollama (it usually runs in the background already) and open a terminal in the project folder.
2. Run `.venv\Scripts\python -m pytest -q`. It should say `155 passed`.
3. Run `.venv\Scripts\python -m outreach serve` and open http://localhost:8000.
4. Make sure the page is in light mode (switch at the top right), then click **Reset demo**, so the overview shows "Start here".
5. Have [docs/plan.md](plan.md) open in another tab in case they want the design.
6. Check `.env` has `EMAIL_SENDER=resend`, `DEMO_INBOX` and your `RESEND_API_KEY`. Approve one email and click **Send** to see it arrive, then click **Reset demo** again.
7. Check the date. Rachel Moore's 180-day gap ends on 28 Dec 2026 and Ben Carter's on 16 Feb 2027. From those dates each of them passes the "not previously contacted" check, so that count goes from 11 to 12, then 13. After 28 Dec, the Rachel Moore example (caught on her registration number) no longer shows on screen. Explain the rule instead, and point to `test_doctor_with_a_new_email_is_matched_on_registration_number`, which uses a fixed date.

Backup plan: if Ollama misbehaves on the day, set `LLM_PROVIDER=fake` in `.env`, restart the server, and say so. Every step except the writing is identical. If email won't send, set `EMAIL_SENDER=outbox`: the email is saved to a folder instead.

## The walkthrough (about 5 minutes)

1. **Start here.** Read the four lines on the overview: rules check the list, the system writes, you approve, each email goes once and is logged. "Your team does this by hand today."
2. **Click Run the pipeline.** The Run page reports each step as it finishes: "Qualified doctor: 19 of 24 doctors fit the campaign." While the system writes the drafts (about 1 to 2 minutes locally), point at the urgent alert line: a scraped profile tried to give the system instructions, so the team was told. When it finishes, click **Watch the replay**, press F for full screen and Space to play: "Here's that run again, from the audit log. The rules sort 27 rows in under a second, then each draft is written and checked." The by-hand bar is your estimate (5 to 10 minutes a doctor), so say so.
3. **Click See the overview.** "This is what needs me today": the to-do list. Then the steps: "27 rows became 24 doctors", "19 of 24 fit the campaign". Tap the "i" after a step's name to show how it decides. Click **See who, and why** under Email verified? for the 4 doctors stopped there. Tap the small "i" after "catch-all".
4. **Click Open the final list.** Search "price": Daniel Price's email was guessed from his trust's pattern, then verified. Filter to Cardiology and click **Copy for Google Sheets**: "Paste into cell A1 and the columns line up."
5. **Open the Not on the list tab and click Rachel Moore.** Her tracker stops at Not previously contacted?: "A new email, but caught on her registration number."
6. **Open Review emails** and type your name once. "Blue is the personalised draft the system wrote, grey is the standard footer, yellow is the personal detail. The highlight comes from the same function as the personalised check." Open **Edit before approving**, add `[DATE]`, click Approve: refused, edit kept. Approve two or three; reject one with a reason.
7. **Open the Needs you tab and click Decide on Hannah Lewis.** Her trust's domain accepts every address, so no computer can confirm the mailbox. Tick "I've confirmed this mailbox exists", note "called the trust switchboard", and re-check: "The rules run again with my confirmation, she joins the final list, and the system writes her email." Then on Isla McKenzie, pick a grade the campaign doesn't target: the rules still say no.
8. **Click Mohammed Iqbal under Need fixing.** The red sentence is the planted instruction. "The prompt reduces the risk; code enforces the rule." Online, open his doctor page and expand Attempt 1: it offered £2,000 and claimed to be an "official NHS partner", and the checks stopped both. On your laptop, if his draft passed this run, read it out: no £2,000 and no partnership claim.
9. **Click Send approved, then open Outreach log.** The email lands in your inbox within seconds, so show it on your phone: "Every email is redirected to my inbox, and Resend's test mode only delivers to me, so no doctor can get one." Then: "Each send is claimed with a unique key before it happens, and the do-not-contact list is checked again at send time." To prove nothing is sent twice:

```powershell
.venv\Scripts\python -m pytest -k "sends_twice or stuck" -v
```

10. **Click See this page at phone size** in the footer. "One layout that rearranges itself, with every hint above its field, so there's no separate app to maintain."

## How I tested it (a good story to tell)

- 155 automated tests run in about 14 seconds with a fake model, so they never call an AI.
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
