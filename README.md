# BTree WhatsApp CRM (MVP)

Enquiry capture, WhatsApp templates, campaigns, live chat with team intervention, contacts with segments and lists, and a chatbot. Runs locally in **mock mode**, so no real messages are sent.

## Run
Double-click `start.bat` (or `cd backend && ..\.venv\Scripts\python -m uvicorn main:app --port 8000`), then open:
- Dashboard: http://localhost:8000
- Public enquiry form: http://localhost:8000/enquiry

Setup from scratch: `python -m venv .venv`, then `.venv\Scripts\pip install fastapi uvicorn httpx`.

## Live Chat, Contacts and Campaigns
**Live Chat** (every signed-in team member with the "Live Chat" permission)
- **Four stages.** A chat is always in one of them, shown as tabs with counts:
  - **Active**: the chatbot is handling the contact.
  - **Requesting**: the contact asked for a person (tapped "Talk to a counsellor", the AI agent handed over, or the chatbot could not understand them) and nobody has picked the chat up. The longest-waiting contact is listed first with its waiting time. The menu badge counts these.
  - **Intervened**: a team member has taken the chat. The tab is grouped into **Intervened by me** and, for managers, **Intervened by others**. Only the person handling a chat can reply; others see "This chat is Intervened By <name>".
  - **Closed**: the chat was resolved. A new message from the contact reopens it as Active.
- **Intervene** (from Active or Requesting) takes the chat: the bot pauses and the event reads "User Intervened by Arun (waited 4 min)". Replying to an unclaimed chat claims it. **Release to bot** gives it back (Active, the bot restarts at the welcome menu). **Resolve** closes it. **Reopen** brings a closed chat back.
- **Everything is tracked in the chat** as event lines with the person and time: moved to Requesting, intervened (and how long the contact waited), taken over from someone, released, closed by, reopened, opted out/in. The same list is in the profile's **Activity** section.
- **Who sees what.** Everyone with Live Chat sees Active and Requesting chats (a shared queue), the chats they intervened in, and the closed chats they handled. Seeing and taking over chats other people are handling, and the full closed history, needs the permission **"Live Chat: see & take over chats handled by others"**: managers and admins have it, agents do not (tick it per person under Users & Roles). Opening someone else's chat as an agent shows "being handled by <name>", and only the holder or a manager can release or close it.
- A strip of recently opened chats sits above the list; **Unread** filters the list.
- **Chat Profile** panel: status, who intervened, last active, template and session message counts, unresolved queries (things the bot could not answer), source, first message, whether the 24-hour conversation is active, and an **Opted In** switch. Collapsible sections edit the contact, tags, lists, custom attributes and notes, and show the campaigns the contact received.
- The same chat + profile opens from any contact in Contacts or any recipient in a campaign.
- A contact who replies **STOP** is opted out (sticky: later messages do not opt them back in) and gets a confirmation; **START** opts back in. Switching the Opted In switch off does the same.

**Contacts** (permission "Contacts"; "Contacts: import, export & bulk edit" for managers and admins)
- Table with search, filters (status, course, tag, list, segment, opt-in), sorting and paging; select rows (or all matching) for bulk actions: add/remove tag, add/remove list, set status, opt in/out, delete.
- **Segments**: saved, self-updating filters (AND/OR of conditions on course, status, source, tag, list, opt-in, replied, callback pending, last activity, when added, name/phone/email, or any custom attribute) with a live match count.
- **Lists**: fixed groups you fill yourself or from a CSV import. **Tags**: coloured labels.
- **Import CSV** (phone column required; name, email, course, status, source, tags, consent optional; extra columns become attributes; existing numbers are updated) and **Export CSV** of the current filter. People are imported as not opted in unless you confirm they agreed or the file has a consent column.
- Attributes work in templates and chatbot messages as `{{city}}` etc.

**Campaigns**
- Audience: any chosen lists, any chosen tags, a segment, course and status, always limited to opted-in contacts, with a live count and sample. Send now or schedule.
- Each campaign shows progress, sent / delivered / read / replied (a reply within 3 days counts) / failed, and a recipient table with the failure reason. Actions: pause, resume, cancel, **retry failed** (re-sends only what failed, nobody gets a duplicate), duplicate (as a draft), export CSV, delete.

## Test script
1. Submit the enquiry form with consent ticked. The welcome template is auto-sent, so check Live Chat.
2. In Live Chat (mock mode), use "Simulate an incoming message": try `hi`, then pick options. "Talk to a counsellor" moves the chat to Needs attention; click **Intervene** and reply by hand.
3. Contacts: add a few contacts, tag them, make a list and a segment, try the bulk actions and a CSV import.
4. Templates: create one using `{{name}}` / `{{course}}`, preview it, Submit (auto-approved in mock mode).
5. Campaigns: pick the template and an audience, send now, and watch the stats. Delivered and read statuses are simulated.
6. Contacts without consent are never messaged.

## Automated tests
`tests\` holds scripts that run against a throwaway copy of the database in mock mode (nothing is sent, the live data is untouched):
`.venv\Scripts\python -I tests\test_crm_modules.py` (contacts, segments, opt-out, intervention, campaigns), `tests\test_chatbot_and_templates.py`, and `powershell -File tests
un_browser_check.ps1` (drives the UI in headless Edge, with screenshots). `tests\preview.py <user> <port>` starts a demo copy to look at.

## Website widget -> WhatsApp (how conversations move into WhatsApp)
1. Add to your page, before `</body>`: `<script src="https://YOUR-SERVER/widget.js"></script>` (optionally `data-course="Full Stack Development"`). Preview: http://localhost:8000/demo-site.html
2. A visitor clicks the green button. WhatsApp opens with your number (`WHATSAPP_NUMBER` in `.env`) and a pre-filled message such as "Hi BTree Systems, I'd like to know about Full Stack Development. (via website)".
3. When they send it, Meta calls `/webhook`. A lead is created (source `web_widget`, course detected), the bot replies in WhatsApp with the course details, and staff can continue in Live Chat or send templates.
4. Messages and templates you send from the dashboard are delivered to the person's WhatsApp when `WA_MODE=cloud`.

Note: a number connected to the Cloud API is controlled by the API. Staff reply through this dashboard, and the same number cannot also be used in the regular WhatsApp Business app unless you enable Meta's "coexistence" feature.

## Option: OpenWA (unofficial gateway, no Meta setup)
Drives a normal WhatsApp account through [OpenWA](https://github.com/rmyndharis/OpenWA). It violates WhatsApp's ToS and the number can be banned, so use a spare SIM, not your main number. Not for regulated or EU users.
1. Install Docker Desktop, then `git clone https://github.com/rmyndharis/OpenWA`, `cd OpenWA`, `docker compose up -d`.
2. Get the key: `docker exec openwa-api cat /app/data/.api-key`.
3. In `.env`: `WA_MODE=openwa`, `OPENWA_API_KEY=<key>`. If OpenWA runs in Docker and this server on the host, also `OPENWA_CALLBACK_URL=http://host.docker.internal:8000`.
4. Restart, open the **Connect** tab, press **Create session + register webhook**, then **Show QR code** and scan it under WhatsApp > Linked devices.
5. Send a test to your own phone, then message the linked number from another phone.

Differences from cloud mode: templates are just rendered text (auto-approved, no Meta review), there is no 24-hour window rule, and broadcasts are paced by `OPENWA_SEND_DELAY` seconds (plus jitter) per message. The OpenWA integration was written from its docs and has not been run against a live OpenWA instance.

## Going live checklist (the Connect tab shows each status)
1. Meta Business account, WhatsApp number, permanent access token (System User token) -> `.env`: `WA_TOKEN`, `WA_PHONE_ID`, `WA_WABA_ID`, `WA_APP_SECRET`, `WHATSAPP_NUMBER`, then `WA_MODE=cloud`.
2. Make the server public over HTTPS. For local testing, run a tunnel, for example `cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000`, and put the https URL in `PUBLIC_URL`.
3. Meta App -> WhatsApp -> Configuration: callback URL `<PUBLIC_URL>/webhook`, verify token from `.env`, subscribe to `messages`.
4. Connect tab -> "Send a test" to your own phone. If hello_world arrives, the pipeline works.
5. Message your business number from another phone and watch the lead and bot reply appear.

## Going live (details)
Fill `.env` with `WA_TOKEN`, `WA_PHONE_ID`, `WA_WABA_ID`, `WA_APP_SECRET`, set `WA_MODE=cloud`. Expose the server over HTTPS (for example ngrok or a VPS) and set the Meta webhook callback to `https://<host>/webhook` with your `WA_VERIFY_TOKEN`, subscribed to `messages`. Templates are then submitted to Meta and stay PENDING until approved. Status updates come from the webhook (it does not poll Meta), so a template stays PENDING here until you press Submit again once Meta has approved it.

## Dashboard pages
Dashboard (stats + message analytics), Sessions (OpenWA: create, start, stop, unlink, delete, QR), Chats, Leads, Number Checker (is this number on WhatsApp, max 20 per check), Templates, Broadcasts, Courses, Webhooks (list, send test delivery, re-register) and Message Tester (send one real message and see the raw result). Sessions, Number Checker, Webhooks and Message Tester need `WA_MODE=openwa`.

## Users and roles
Everyone signs in with their own username and password. On first start after this change, the old shared `ADMIN_PASSWORD` became the account `admin` (change it under your name in the sidebar). If there are no accounts at all, `/login` offers to create the first admin.

Admins manage people under **Users & Roles**. Role presets, which can be fine-tuned per person with checkboxes:

| Area | Admin | Manager | Agent |
|---|---|---|---|
| Dashboard, Chats, Leads | yes | yes | yes |
| Number Checker, Templates, Broadcasts, Courses | yes | yes | no |
| Sessions, Webhooks, Message Tester | yes | no | no |
| Users & roles | yes | no | no |

Permissions are enforced by the server (a blocked call returns 403), not only hidden in the menu. Disabling an account or changing its password signs that person out. Messages sent from Chats record who sent them. There is always at least one active admin.

## Menu chatbot (default)
A menu-only bot: leads reply with option numbers (or the option text); there are no free-form answers. Edit it on the **Chatbot** page (admins and managers):
- **Start**: the welcome ("our counsellor will contact you promptly") + numbered options. Sent once per conversation; the lead is flagged **callback pending**. Returning to the main menu shows the shorter "main menu" text.
- **Course list**: built from the Courses page; picking a course sends its fee, duration, timings and syllabus, then the step's options.
- **Your own steps**: a message + up to 9 options, each pointing to another step.
- **Final hand-over**: the team-contact message; the bot pauses and a counsellor takes over. Every path should end here. Too many wrong replies also hand over.
Template reply buttons and the AI agent (if switched on) take priority; with the menu chatbot off, the old keyword bot answers. "Resume bot" in Chats restarts the lead at the welcome. **Try it** on the page tests unsaved changes without sending anything; options appear as boxes you can click, like a lead tapping them.
Options are plain text ("1. Courses, fees & syllabus", no emoji numbers). By default each one is a tap-to-reply link (`wa.me/<your number>?text=<option>`): the lead taps it, WhatsApp opens the chat with the option typed, and they press Send. Real WhatsApp buttons need Meta's Cloud API; the self-hosted gateway cannot send them. Typing the number or the option text also works. The setting "How leads pick an option" can switch to plain numbered options.

## AI auto-reply agent
Claude (`claude-opus-5-5`, low effort) answers incoming WhatsApp messages when switched on in **AI Agent** (needs `ANTHROPIC_API_KEY` in `.env` and `pip install anthropic`). Per conversation:
1. First message: the acknowledgement ("our counsellor will contact you promptly") and the lead is flagged **callback pending**.
2. Questions: answered from the Courses page plus the extra knowledge text. It does not invent facts; anything unknown is handed over.
3. Hand-over (lead is done, wants a person/admission/payment/demo, unknown question, or after N AI replies): the final message with the team contact, and the bot pauses so a counsellor continues. Clear the flag with **Mark as called** in Chats.
Template button replies still take priority. If the agent is off, the key is missing, or the API fails, the keyword bot (or just the acknowledgement) is used instead. Refusals fall back to another model automatically (`fallbacks: "default"`). "Try it" on the AI Agent page tests replies without sending anything on WhatsApp.

## Template reply buttons
Each template can have up to 3 buttons. For each one, set an automatic reply (variables like `{{name}}` work), optionally a new lead status, and optionally "hand over to a person" (pauses the bot). On Meta's Cloud API these are real buttons. The self-hosted gateway cannot send real buttons, so the options are listed as 1, 2, 3 and the lead replies with the number or the option text. Replies are matched against the last template sent to that lead within 7 days; anything else goes to the keyword bot as before.

## Known MVP gaps (next phase)
- Serve over HTTPS before exposing it to the internet.
- SQLite storage. Move to PostgreSQL, with Redis for the send queue, at scale.
- Meta template approval is not synced back automatically. It needs the `message_template_status_update` webhook field.
- Bot is keyword based. The `bot_reply` function in `backend/main.py` is where Claude would plug in.
- Media messages, CSV lead import and Razorpay links are not built yet.
- The cloud sender has not been tested against Meta, because there were no credentials. Only mock mode is verified.
