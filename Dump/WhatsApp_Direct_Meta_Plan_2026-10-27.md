# WhatsApp directly on Meta's Cloud API: the plan for our own inbox (27 Oct 2026)

Written for the project owner and for whoever builds this (an engineer or an AI model with no earlier context). **Decision (owner, 27 Oct 2026): no DoubleTick or any other BSP. The ERP integrates directly with Meta's WhatsApp Cloud API and we build our own inbox and workflows.** This document supersedes the DoubleTick-based parts of `WhatsApp_Integration_Report_and_Plan_2026-10-27.md`; that report's sections 1 (where we are), 2 (Meta's rules), 5 (what our product should be) and 8 (risks) still hold. **Nothing here has been built or changed.** Items marked **UNVERIFIED** could not be confirmed against a live Meta page on 27 Oct 2026; Meta's Help Center pages (business verification, billing) returned only titles.

---

## 0. The short version

1. **Going direct is possible and well documented for messaging, webhooks, templates and media.** Two things are not cleanly documented for a company on its own: moving a number off a BSP, and coexistence (using the WhatsApp Business app and the API on one number).
2. **Recommendation: register a NEW dedicated number first** and leave the current number on DoubleTick untouched until our inbox is proven. No downtime, no dependence on DoubleTick's cooperation. Migrate the old number later, only if it is safe (section 5).
3. **We build the inbox ourselves.** Everything DoubleTick's hosted inbox gave (shared inbox, assignment, notes, quick replies, templates screen, reporting) is ours to build; Meta's API gives none of it (section 6).
4. **Phase 1 needs no Meta account at all.** The message log, signed-webhook receiver, identity store and matching can be built and tested against fixtures built from Meta's documented payload shapes.
5. **The owner has slow tasks to start now:** Meta business verification, a payment method, INR billing, and choosing the new number (section 3).
6. **Configuration is multi-account from the start (added 28 Oct):** several WhatsApp Business Accounts, several numbers per account, several agents per number, all set up in the ERP without code changes. The full model, the admin setup flow, the routing rules and a 32-scenario check are in **section 12**; where section 2 says one settings record or one shared number, section 12 governs.

---

## 1. What changed from the earlier plan

| Earlier plan | Now |
|---|---|
| Provider adapter with a DoubleTick implementation | A **Meta Cloud API client**. The adapter interface stays, but only to allow a mock in tests. |
| DoubleTick's unsigned webhook, secret token | **Meta's signed webhook**: HMAC-SHA256 of the raw body with the app secret, in `X-Hub-Signature-256`, plus the GET verify handshake. |
| DoubleTick's inbox for agents | **Our own inbox**, in the ERP. |
| Meta's user-ID change as a risk to check | A **requirement**: Meta states BSUID support is required for directly integrated businesses. |
| DoubleTick's plan, seats, markup | **Meta's rates only** (India, INR, effective 1 Oct 2026: marketing 0.8631, utility 0.115, authentication 0.115, service 0.115 after 1,000 free a month per number) and no seat fees. |
| Ten questions to DoubleTick | Not needed. Replaced by the owner tasks in section 3. |

---

## 2. What we have to build (scope)

**A. Meta connection** (`splinh/whatsapp/`)
- A small client for the Graph API (version pinned in config; docs' examples use v26.0, released 29 Jul 2026; v25.0 expires 29 Jul 2028, v24.0 on 18 Feb 2028, v23.0 on 8 Oct 2027): send message (text, template, media, interactive), mark as read, upload and download media, template create / list / edit / delete, phone number registration, business profile.
- **Secrets** in Password fields or site config, never in client code or logs: the system-user access token, the app secret, the webhook verify token. **One set per WhatsApp Account record (section 12)**, so one account's token expiring never stops another. Alert on API error 190 (token expired).

**B. Signed webhook receiver** (guest endpoint, `splinh/api/whatsapp_webhook.py`)
- GET handshake: answer 200 with `hub.challenge` when `hub.verify_token` matches.
- POST: verify `X-Hub-Signature-256` against the **raw bytes** before parsing; reject without it. Store the raw event, return 200 at once, process in a queued job. A batch can hold up to 1,000 updates and arrive up to 7 days late and out of order; **deduplicate on message id plus status**, make status updates monotonic, tolerate duplicates.
- **With several accounts**: Meta allows **one webhook endpoint and one app secret per Meta app**, so the URL carries the key of the app that receives the event (for example `.../whatsapp_webhook?app=<key>`); the GET handshake checks that app's verify token and the POST signature is checked with that app's secret. Events are then routed to the right account by the WABA id (`entry[].id`) and to the right number by `metadata.phone_number_id` (section 12.2 and 12.8).
- Subscribe the fields: `messages` (messages and statuses), `message_template_status_update`, `message_template_quality_update`, `template_category_update`, `phone_number_quality_update`, `phone_number_name_update`, `account_update`, `account_alerts`, `user_preferences`, `business_username_updates`.
- The callback needs a valid TLS certificate (self-signed is not supported). mTLS is optional; Meta says its IP ranges change, so we do not allow-list by IP.
- **Meta keeps messages for 30 days at most and has no history API: our own log is the only history from day one.**

**C. Data (created by the config engine, not by hand in a UI)**
- `WhatsApp Account` (one per WABA; replaces the single settings record): see section 12.2.
- `WhatsApp Number` (many per account): see section 12.2.
- `WhatsApp Agent Access` and `WhatsApp Routing Rule` (who may work a number, and how new conversations are assigned): see section 12.2 and 12.4.
- `WhatsApp Message`: direction, status, `wamid` with a **unique index**, business number, customer number as an E.164 key and the raw value, type, body, caption, template and parameters, media as a private File, sent by, party (Dynamic Link to Lead / Contact / Customer), billing category and a billable flag from the status webhook, failure code and reason, the raw payload. Indexes on (party, time) and (number, time). Plan archiving.
- `WhatsApp Conversation`: per business number and customer: last inbound time (window state), party, assignee, status (open / done), unread count.
- `WhatsApp Identity`: phone-to-BSUID pairs, per portfolio, stored exactly as received (e.g. `IN.xxxxx`; changing the string makes requests fail).
- `WhatsApp Template`: mirror of name, language, category, status, quality, header type, synced from the API and the status webhooks.
- `WhatsApp Quick Reply`, `WhatsApp Note` (internal), and a failure log in the style of `Call Sync Failure`.
- Custom fields on Lead and Contact: **opt-in status, source, date, wording** (and the user-preference state from `user_preferences`).

**D. Our inbox** (Desk page + record timeline, `doctype_js` additive only so the vendor `sigzensfa` Lead and Customer scripts are not disturbed)
- Conversation list, unread counts, **live updates** when a message arrives (Frappe realtime), open / done, filters.
- **Assignment:** by default to the Lead's owner or the customer's Sales Person; an unassigned pool; manual reassign; a manager can see their hierarchy (the same Sales Person tree DILO uses).
- Compose with **window awareness**: free text and documents while the window is open, only approved templates once it is closed. Meta offers no API for window state, so we track the last inbound time ourselves and treat error 131047 as the safety net.
- Templates screen: create (with a document or image header via Meta's Resumable Upload API), see approval and quality, pick and fill variables from the record, send.
- Attachments: send a Quotation or invoice PDF by one click; convert images to JPEG or PNG under 5 MB; **download inbound media immediately** (the link lasts 5 minutes, the id 7 days) into a private File.
- Notes, quick replies, mark as read.

**E. Business value on top** (what DoubleTick could not give us)
- WhatsApp timeline on Lead / Customer / Opportunity, next to calls, quotations and visits.
- **KPI automation:** the WhatsApp Chat count and duration on Sales KPI Entry (typed by hand today) become system-generated; DILO can show WhatsApp touches.
- Opt-in enforcement: no template send without recorded opt-in; STOP and marketing opt-out honoured.
- **Cost view:** monthly billable messages per category against the 1,000 free service messages, from the `pricing` object in status webhooks; alert on billing failure (error 131042).
- Account health: `account_update` (restriction, violation, disabled) and `account_alerts` raise a notification at once, because a restricted account stops everyone.
- Triage list for numbers with no record or several; **no automatic Lead creation** without a decision.

**F. Changes elsewhere**
- Android call tracker: stop uploading WhatsApp **message** notifications, keep only call events (92.5% of the 1,315 WhatsApp rows in the 10 Oct analysis were not calls, and they carried chat titles and numbers from employees' phones).
- After cut-over only: disable the two DoubleTick Server Scripts (`doubletick_list_users`, `doubletick_validate_user`), the DoubleTick role and the disabled service account. This also closes the open user-list endpoint flagged earlier. **Nothing on production is changed without the owner's confirmation.**

---

## 3. Owner tasks, in order (these are the slow part; start now)

| # | Task | Who | Duration (UNVERIFIED unless stated) |
|---|---|---|---|
| 1 | In Meta Business Settings, find **which business portfolio owns the current WhatsApp account (WABA)**: Splashjet's own, or DoubleTick's. This decides whether the old number could ever be taken over. About 30 minutes. | Owner (portfolio admin) | 30 min |
| 2 | Confirm Splashjet has a Meta business portfolio with correct legal details; start **business verification** with GST certificate, certificate of incorporation or PAN (third-party sources; Meta's own page did not load). The legal name must match exactly. Verification is not needed to start sending but is one way to move the messaging limit from 250 to 2,000 unique recipients a day. | Owner | 1-5 days |
| 3 | Attach a **payment method** (card is documented; UPI and prepaid are unverified) to the WhatsApp account. Without one, service messages beyond the free tier stop (Meta required this by 30 Sep 2026). | Owner | 1 day |
| 4 | **Migrate billing to INR** before **31 Dec 2026**; from 1 Jan 2027 Meta stops delivering for eligible non-INR accounts. | Owner | 1 day |
| 5 | Choose the **new dedicated number** (mobile preferred; a landline works with voice OTP; it must not be registered on WhatsApp or the WhatsApp Business app, which would have to be deleted first). Decide who controls it long term. | Owner | - |
| 6 | Create the **system user** in Business Settings with Full control of the app and the WhatsApp account; the developer then generates the token with `business_management`, `whatsapp_business_messaging` and `whatsapp_business_management`. | Owner + developer | same day |

Developer tasks: create the Meta app with the WhatsApp use case; build the endpoint; register the number (`/request_code`, `/verify_code`, then `/register` with a 6-digit PIN; at most 10 registration requests per number per 72 hours); display-name approval (about a day, unverified); create templates.

---

## 4. Development phases

Effort is relative: S small, M medium, L large.

| Phase | Content | Needs a Meta account? | Effort |
|---|---|---|---|
| **1. Core, built offline** | Doctypes, Cloud API client with a mock, signed-webhook receiver (handshake, signature, idempotency, raw capture, queue), message log, identity store and matching (BSUID first, phone second, triage list), opt-in fields, error-code handling table, offline tests with fixtures from Meta's documented payload shapes. | **No** | L |
| **2. Live on UAT, text** | A Meta app with its own test number on UAT (a separate app from production, because an app has one callback URL; the free test number is UNVERIFIED in this research). Handshake, receive, send inside the window, status updates, a minimal conversation view and compose on the Lead / Contact / Customer form. | Yes (test) | M |
| **3. Templates and attachments** | Template create / sync / picker / send, document-header quotation send, inbound media into Files, image conversion. | Yes | M |
| **4. Full inbox** | Conversation list, realtime, assignment, unread, notes, quick replies, hierarchy visibility. | Yes | L |
| **5. KPI, opt-in, cost and health** | System-generated WhatsApp counts, opt-in enforcement and STOP handling, billing counters, account-health alerts, manager and export views, the call-tracker notification change. | Yes | M |
| **6. Pilot on the new dedicated number** | A small team, real customers who have opted in, a few weeks. | Yes (production) | - |
| **7. Old number** | Only after the pilot, and only if section 5's conditions hold. | - | decision |

Each phase ends the way the Duplicate Check and DILO work did: offline tests, a rolled-back test against real UAT data, a changelog entry, and a commit.

---

## 5. The existing number: why a new one first

- Meta documents **no migration path for a company moving its own number off a BSP**. Its partner-to-partner page now says number migration "isn't supported" for accounts on the new account model, and the Meta Business Suite off-boarding flow is started by a **Tech Provider**, not by the business. What exists is third-party, older-model BSP documentation, so treat it as UNVERIFIED.
- Migration would need DoubleTick to cooperate: two-step verification can only be turned off through an emailed link in WhatsApp Manager (no API), and DoubleTick's own guides warn that deleting a number in Business Manager can permanently lose templates, limits, verified status and webhook settings. Expect minutes to hours of downtime, and quality rating does not carry over in the Business Suite flow. **Chat history stays in DoubleTick; the Cloud API keeps nothing beyond 30 days.**
- **Reps' own WhatsApp Business apps:** Meta documents coexistence only for Solution Partners and Tech Providers. For a company integrating directly it is **UNVERIFIED and probably not available**, so a number on the Cloud API cannot also be used from the Business app. Reps would use the ERP inbox for the shared number and could keep their own apps on their own personal numbers.
- Therefore: **pilot on a new number; keep the old one on DoubleTick; migrate only if** the WABA is in Splashjet's own portfolio, DoubleTick confirms in writing that it will release the number and disable two-step verification, and the pilot has run for several weeks.

---

## 6. What we lose from DoubleTick and where each function comes from

| Function | Provided by Meta's platform? | Provided by us |
|---|---|---|
| Shared inbox, assignment, notes, tags | No | Yes (phase 4) |
| Quick replies | No | Yes |
| Templates screen | API plus Meta's own manager UI | Our screen, optional |
| Template approval status | Webhooks | Synced into the ERP |
| Broadcast and campaigns | API only | Not in scope for v1; needs queueing, throttling, opt-in segments |
| Chatbot / automated flows | Interactive messages and Flows exist | Not in scope for v1 |
| Reporting | Only status and pricing data | Our own analytics from the log |
| Opt-in and opt-out | Policy requires it; `user_preferences` gives marketing opt-outs | Consent records and STOP handling |
| Media preview | No | Download and store |
| Chat history | Not retained beyond 30 days | Our message log |

---

## 7. Error handling table (from Meta's error codes)

| Code | Meaning | Reaction |
|---|---|---|
| 131047 | More than 24 h since the user's last message | Switch to a template. |
| 131049 | Message withheld to keep the ecosystem healthy (often marketing frequency) | Do not retry within 24 h. |
| 130429 | Throughput reached (default 80 messages per second per number) | Back off and retry. |
| 131056 | Too many messages to the same user (about 1 per 6 s, burst 45) | Wait, retry that recipient only. |
| 131026 | Recipient cannot receive (not on WhatsApp, terms not accepted, old client) | Do not retry; use another channel. |
| 131051 / 131053 | Unsupported type / media upload or MIME problem | Fix the request. |
| 132000, 132001, 132012 | Template variable mismatch / not found or not approved / wrong format | Fix the request. |
| 132015 / 132016 | Template paused for quality / permanently disabled | Edit or replace the template. |
| 131042 | Payment or billing problem | Alert the owner at once. |
| 131031 / 368 | Account restricted or disabled | Alert at once; read `account_update`. |
| 190 | Access token expired | Alert; rotate the token. |
| 131062 | BSUID not supported for that message type | Send to the phone number. |

---

## 8. Risks

1. **Identity.** Users with a username may arrive without a phone number (shown only if there was an interaction in the previous 30 days). Mitigation: the identity store; matching by BSUID first; the triage list; `REQUEST_CONTACT_INFO` template button to obtain the number (B2B effectiveness UNVERIFIED). Whether usernames have reached India is UNVERIFIED.
2. **Opt-in.** Our 300k imported Leads almost certainly lack documented WhatsApp opt-in. Until it is collected: reply only inside an open window, no templates. India-specific TRAI and DPDP points are UNVERIFIED and the legal burden is ours.
3. **Account health.** A restricted or banned account stops every user at once. Template misclassification, spam complaints and low quality trigger escalating blocks. Mitigation: monitor `account_update` and `account_alerts`, plan template categories, honour opt-outs.
4. **Cost.** Marketing is about 7.5 times utility; service messages are charged again from 1 Oct 2026 after 1,000 a month per number; a failed payment stops delivery. The INR deadline is 31 Dec 2026.
5. **Messaging limit.** A new portfolio starts at 250 unique recipients a day for out-of-window templates (2,000 after verification or 2,000 delivered template messages to unique numbers in 30 days at high quality). Inbound traffic and in-window replies do not count. Do not plan any bulk campaign on the first tier.
6. **Token and signature mistakes break the inbox silently.** Monitor 190, signature failures and webhook delivery failures.
7. **Our log is the only history.** Back it up with the rest of the site; set retention with the owner.
8. **Vendor JS on Lead and Customer forms is brittle.** Our UI is additive only: unique button labels, never remove others' buttons, namespaced events.
9. **Product policy.** Whether ink and solvent products could count as restricted goods under WhatsApp's commerce policy is UNVERIFIED; check before any product messaging.

---

## 9. Still UNVERIFIED (check before relying on it)

Which portfolio owns the current WABA; business-verification documents and time (third-party); whether tokens expire and when; UPI or prepaid payment and GST invoices from Meta in India; the free test number and Live-mode requirement for UAT; the mark-as-read payload; coexistence for a direct business; the second half of the BSUID documentation; India messaging-tier details and rates for calling; TRAI and DPDP specifics.

---

## 10. Decisions needed from the owner

1. Which number for the pilot (new, dedicated, long-term controlled), and who owns the Meta account.
2. Approve tasks 1-6 in section 3 and start them.
3. **Inbox scope for the first release:** minimum (conversation and compose on the record, reply, send quotation) or the full inbox in phase 4.
4. **Default assignment rule:** to the Lead owner or Sales Person; unassigned pool for unknown numbers.
5. **Opt-in policy** (section 8, item 2) and how opt-in gets collected (proposal: an opt-in checkbox in the Duplicate Check dialog we already built, so every new Lead is recorded from day one).
6. Visibility: reps see their own conversations, managers their hierarchy, System Manager everything.
7. Message and media retention.
8. Approve starting **Phase 1 on UAT** now.

---

## 11. Sources

Our own files: `whatsapp_integration_analysis.md`, `PROJECT_CONTEXT.md` item 9, CHANGELOG entries of 25 Sep, `WhatsApp_CallLog_Analysis_2026-10-10.md`, `splinh/custom/phone_lookup.py`, `scorecard_schema.py`, `api/call_tracking.py`. Meta documentation under `developers.facebook.com/documentation/business-messaging/whatsapp/` fetched 27 Oct 2026 (pages undated): get-started, access-tokens, webhooks (overview, create-webhook-endpoint), messages/send-messages, templates (overview, template-management, template-media), business-phone-numbers (phone-numbers, registration, display-names, media, two-step-verification), messaging-limits, throughput, pricing (and non-template-messages, change-billing-currency), business-scoped-user-ids, embedded-signup/onboarding-business-app-users, solution-providers/support (migration pages), data-privacy-and-security, getting-opt-in, support/error-codes; plus `developers.facebook.com/docs/graph-api/changelog/versions`. Third-party BSP pages were used only where marked UNVERIFIED.

---

## 12. Configuration model: several WABAs, numbers and agents (added 28 Oct 2026)

**Requirement (owner, 28 Oct):** the ERP must support **several WhatsApp Business Accounts (WABAs)**, **several numbers under each**, and **several agents working each number**, all configured inside the ERP with no code change. This section is the design and the scenario check. The Meta facts it relies on were researched on 28 Oct (section 12.8); items still **UNVERIFIED** are listed there, and the design works whichever way they turn out.

### 12.1 Principles

1. **Agents exist only in the ERP.** Meta has no agent accounts; an agent is an ERP user who is given access to a number. No agent ever logs into Meta.
2. **Credentials are stored at the level where Meta scopes them** (verified 28 Oct): the **app secret and the webhook endpoint belong to the Meta app** (one of each per app), the **access token belongs to the portfolio** (one system user per portfolio covers all of its WABAs), and **templates, billing, payment method and spend limit belong to the WABA**. The ERP keeps a record for each level and links them, so any combination works: one app for everything, or one app per portfolio.
3. **A number belongs to the company, never to a person.** A "personal" number is a company-issued number assigned to one agent; if the agent leaves, the number and its history stay.
4. **A conversation belongs to one number and one customer**, with one assignee at a time. Customers appear on the record timeline from all numbers, labelled with the number.
5. **Everything is configured in the Desk**, validated against Meta's API where possible, and every change leaves an audit trail.

### 12.2 The model

```
Meta Portfolio   the company's Meta business account: access token, verification, number cap, sending limit
  |
  +-- WhatsApp Account   one per WABA ("Messaging account"): templates, billing, spend limit, currency
        |
        +-- WhatsApp Number   many per account: own display name, quality, profile
              +-- Agent Access   many users per number
              +-- Routing Rule   ordered rules per number

Meta App   one webhook endpoint and one app secret; serves every account it is subscribed to
           (each Account links to the Portfolio it belongs to and to the App that receives its events)

Conversation = (Number, customer)  ->  one assignee, status, unread, window state
```

**Terminology (Meta is mid-transition):** Meta now calls a WABA a "Messaging account"; the `waba_id` is unchanged. Its documentation describes a WhatsApp account that holds a phone number and a Messaging account that holds templates and billing, while older text says a WABA holds many numbers. In the ERP a **Number always belongs to an Account** (the owner of templates and billing), which fits either reading.

**WhatsApp Meta Portfolio** (one per Meta business portfolio)
- Label, portfolio id, enabled, **system-user access token** (Password; one token covers all WABAs of the portfolio; store the portfolio id with it), token notes (expiry preference chosen when generated).
- Read from Meta and shown: business verification status, the **number cap** and how many are used (2 until verified, then 20), the **messaging limit tier** (250, 2,000, ...), INR migration status.

**WhatsApp Meta App** (one per Meta app)
- Label, app id, **app secret** and **webhook verify token** (Password), a generated **webhook key** that goes in the callback URL, Live-mode flag. Shows the callback URL and verify token to paste into the Meta app. Lists the accounts it is subscribed to.

**WhatsApp Account** (one per WABA)
- Name, enabled, **environment** tag (UAT or production, shown prominently), link to its **Portfolio** and to its **Meta App**, WABA id, API version (pinned).
- Read from Meta: currency and time zone (fixed once a line of credit is attached), business verification and account review status. Payment status and spend limit (billing is per account).
- Health fields written by the ERP: token valid, last webhook received, last error, billing status, restricted flag.
- Buttons: **Test connection**, **Subscribe webhook** (`POST /{WABA_ID}/subscribed_apps`), **Sync numbers**, **Sync templates**.

**WhatsApp Number** (many per account)
- Account, phone number id, display number (E.164), display name and its approval status, quality rating, status (connected, offline, restricted), throughput.
- **Type: Shared or Personal.** For Personal: the owner agent. For Shared: team or region, supervisors.
- **Purposes** (tick any): customer conversations, send documents, notifications and reminders, campaigns. A personal number can be barred from campaigns.
- Business hours and the out-of-hours message, auto-reply on or off, **sticky days** (how long a conversation stays with its agent), default maximum open conversations per agent.
- Visibility mode for agents: **own conversations only**, or **all conversations on this number**.

**WhatsApp Agent Access** (one row per user and number)
- User, role on this number (**Agent** or **Supervisor**), maximum open conversations, availability (available / away), languages or skills (optional), may send templates, may send campaigns. A user can have rows on several numbers and accounts.

**WhatsApp Routing Rule** (ordered per number; first match wins): section 12.4.

**Records and linking:** the existing design in section 2.C (message log with a unique message id, conversation record with window state, identity store, template mirror, opt-in fields, failure log) all point to the **number**, which points to the **account**, so account is always known.

### 12.3 How an administrator configures it (setup flow)

A "WhatsApp Setup" workspace in the Desk, with these steps in order:

1. **Connect Meta, in three records.**
   - **Portfolio:** enter the portfolio id and the system-user access token; the ERP checks it and shows verification status, number cap and messaging tier.
   - **Meta App:** enter the app id, app secret and verify token; the ERP generates the webhook key and shows the callback URL and verify token to paste into the Meta app's configuration.
   - **Account (WABA):** enter the WABA id, pick its Portfolio and its Meta App, tag the environment. Press **Test connection** (reads the WABA through the Graph API: verification status, currency, time zone), then **Subscribe webhook**.
2. **Sync numbers.** The ERP lists the numbers of that WABA from Meta (with quality, tier and display-name status) and creates a number record for each, switched off until configured. **Adding a new number** (add, voice or SMS code, register with a PIN) is done in Meta's tools and through the registration calls, not here (a way to add a number from scratch through the API was not found).
3. **Configure each number:** type, owner or team, purposes, hours, routing, visibility, stickiness.
4. **Add agents:** pick users for each number, role and capacity; a bulk action adds everyone under a Sales Person branch. The user must hold the WhatsApp Agent role.
5. **Sync templates;** choose which are allowed on which number and for which purpose.
6. **Go-live check:** send to a test number, receive a reply, see the status ticks. The health panel must be green: token valid, webhook received, billing ok, no restriction.

Screens: Accounts (with health badges), Numbers (with quality and limit cards), an Agents matrix (users by numbers), Routing rules, Health and limits, Cost.

### 12.4 Routing and assignment (per number, first match wins)

1. **Stickiness:** an open conversation with an assigned agent who is still available on this number stays with them.
2. **Personal number:** goes to its owner (if the owner is away longer than the set time, to a supervisor).
3. **Tag:** a QR code or link carries a rep tag in its prefilled text (for example `[R12]`); the conversation goes to that rep if they have access to this number.
4. **Record owner:** the Lead owner, or the Customer's Sales Person, if that user is an agent on this number.
5. **Optional rules:** territory, language, product line.
6. **Round-robin or least-busy** among available agents of the number, respecting capacity.
7. **Unassigned pool:** supervisors are alerted after N minutes.

Out of hours: the auto-reply (a free-form reply inside the window) and the conversation waits. No first reply within N minutes: alert, then reassign.

### 12.5 Permissions

- **WhatsApp Admin:** accounts, secrets, numbers, routing. Secret fields are visible only to this role.
- **WhatsApp Supervisor:** all conversations of the numbers they supervise and, through the Sales Person tree, their hierarchy; can reassign, edit quick replies, run reports.
- **WhatsApp Agent:** their assigned conversations, plus the unassigned pool of their numbers if the visibility mode allows.
- Data-level rules on conversations, messages and notes (so list views, reports and API calls respect them). An audit log of who read, sent, transferred or exported.

### 12.6 Scenario check

| # | Scenario | How the design handles it | Needs or risk |
|---|---|---|---|
| **Structure** | | | |
| 1 | Start: one account, two shared numbers (the cap for an unverified portfolio is 2) | Agents share the numbers; routing by tag, record owner, round-robin | None |
| 2 | One account with a shared pool plus personal numbers for key reps | Number type Shared or Personal; rules 2 and 3 | None |
| 3 | Several accounts in one portfolio (for example brands Splashjet, DuraJet, TexStar) | Separate Account records, or one account with several numbers: admin's choice. The sending limit is shared across the portfolio; templates are separate per account | Brand names in display names; watch the shared limit |
| 4 | Accounts in different portfolios (separate legal entities) | Each portfolio has its own record and token (one system user per portfolio); each account links to its portfolio and to a Meta App; customer identity (BSUID) is scoped per portfolio, so the same person has different IDs in each; opt-in recorded per legal entity; templates recreated by hand per portfolio | More admin; each portfolio needs its own verification (and brings its own number cap); whether one app may serve several portfolios, or each needs its own app or Advanced Access, is UNVERIFIED (12.8) |
| 5 | Accounts share one Meta app, or each has its own app | Both supported: an Account links to a Meta App record. With one app there is one endpoint and one secret and events are told apart by WABA id; with several apps each has its own endpoint key and secret | One app serving several WABAs is documented for partners; for a direct business it is verified only inside one portfolio |
| **Agents** | | | |
| 6 | An agent works several numbers or accounts | One Agent Access row per number; one inbox with a number badge; capacity counted across numbers | None |
| 7 | An agent leaves or the user is disabled | Their open conversations go to a supervisor or the pool; a personal number is unassigned and stays company property; history stays | Needs the user-disable hook |
| 8 | An agent is away | Availability toggle; routing skips them; reassign after N minutes | Thresholds set per number |
| 9 | Two agents open the same chat | Soft lock and an "is replying" marker; a second reply is warned or blocked | Meta's typing indicator is optional |
| 10 | Supervisor takes over or transfers within a number | Reassign with a note; the customer sees nothing | None |
| 11 | Transfer across numbers (customer wrote to North, the right rep works Exports) | WhatsApp cannot move a thread. Either give the rep access to the original number, or start a new thread from the other number with a template (the customer sees another number). The UI warns | A deliberate, visible action, never silent |
| 12 | An agent has access to a number but must not see everyone's chats | Visibility mode: own only or all on this number | Choose per number |
| **Customers and identity** | | | |
| 13 | The same customer writes two numbers | Two conversations, one record timeline with a number badge, and a banner "also chatting with X" | None |
| 14 | A number matches several records (Lead and Contact; shared numbers) | Match by the existing priority; mark ambiguous; triage list | The earlier matching gaps (Customers not in Phone Lookup) |
| 15 | A WhatsApp-username customer arrives without a phone number | Identity store keyed by portfolio and BSUID; matched by BSUID; else triage | Phone often appears only within 30 days of interaction |
| 16 | Unknown number | Triage list; create a Lead only by a person, with the duplicate check | No auto-create |
| 17 | The customer opts out (STOP or the user-preference webhook) | Marketing templates blocked for that party across the accounts of that legal entity | Needs the preference webhook |
| **Messaging** | | | |
| 18 | Reply inside or outside the window | Window state per number and customer; outside means templates only | Meta has no window API: we track it |
| 19 | A template exists only in another account | Blocked with a clear message; template mirror is per account. Within one portfolio an admin button can copy approved templates (`migrate_message_templates`); across portfolios they are recreated by hand | Only approved templates of quality green or unknown can be migrated |
| 20 | A rep sends a quotation PDF | Sender resolution: the rep's personal number if they have one and the purpose is allowed; else the shared number of their region; else the default for documents | Rule is configurable |
| 21 | A campaign | Pick account, number, template and segment; opt-in, quality and the sending limit are checked; throttled; error 131049 handled; personal numbers can be barred from campaigns | Starts small: 250 new contacts a day until verified |
| **Operations and limits** | | | |
| 22 | The number cap is reached | Setup shows used and remaining numbers and refuses more, with guidance (verify the business; no way beyond 20 is documented, so ask Meta support, or use a second verified portfolio) | The cap is per portfolio: 2 until verified, then 20 |
| 23 | Quality drops on one number | Alert; campaigns on that number pause; replies continue; the supervisor sees the portfolio-level risk (limits are shared) | How far one number's violation spreads is UNVERIFIED |
| 24 | The token expires or is revoked on one account | Only that account's numbers fail; others continue; banner and alert; outgoing messages queue, not lost | Rotation procedure documented |
| 25 | The webhook fails or is slow | Backlog shown in health; Meta retries up to 7 days; raw events are stored; processing is queued | Watch the lag |
| 26 | Billing fails on one account (error 131042) | Alert to the owner at once; sending paid categories blocked for that account. Billing, payment method and spend limit are per account (documented), so other accounts should be unaffected | Spillover to other accounts is not documented: UNVERIFIED |
| 27 | An account is restricted or disabled (`account_update`, error 368) | Account marked Restricted; sending hidden on its numbers; admins notified; no automatic failover to another account | Manual decision |
| 28 | UAT and production | Separate Account records per environment; separate Meta apps or test numbers; no secrets in the repository; the environment tag is shown on every screen | Test number availability unverified |
| 29 | A number is re-registered, deleted or goes offline | Number sync marks it offline; conversations and history are kept | None |
| 30 | A rep's QR codes | One code per number and rep with the tag; rotated and deactivated when the rep leaves | Up to 2,000 codes per number |
| 31 | Retention and export | Policy per account; an export for audit | Owner sets the period |
| 32 | An account is "deleted" in the ERP | Soft-disable only; messages are never deleted | None |

### 12.7 Effect on the phases

Phase 1 now also includes the multi-account data model and its admin screens (Account, Number, Agent Access, Routing Rule, the setup workspace), roughly **L** instead of the earlier size. The calls that need a real Meta account (Test connection, Subscribe webhook, Sync numbers) arrive in Phase 2; in Phase 1 they run against a mock. Routing, visibility and the scenario tests run entirely offline.

### 12.8 Research results (Meta documentation, fetched 28 Oct 2026)

**Verified (stated in Meta's pages)**
- **One app, many WABAs, one endpoint.** An app receives webhooks for every WABA it is subscribed to (`POST /{WABA_ID}/subscribed_apps`; list with GET; remove with DELETE). Each app has **one webhook endpoint**. `entry[].id` is the WABA id; the number is in `metadata.phone_number_id` and `display_phone_number` (the second pair is high confidence, not freshly read). Stated for partners; verified for a direct business only inside one portfolio.
- **Callback overrides** exist at WABA and at number level (limit 200 characters, precedence number, then WABA, then app) but cover **message-type fields only**. **Template and account webhooks (`account_update`, `phone_number_quality_update`, ...) always go to the app URL**, so the single app-level endpoint is mandatory and we separate traffic by WABA id.
- **Signature** is `X-Hub-Signature-256`, HMAC-SHA256 of the raw body with the **app secret**: one secret per app. Per-account secrets are therefore impossible inside one app. Payload up to 3 MB; retries up to 7 days; deduplicate.
- **Tokens:** a system-user token is long-lived; an **admin** system user covers all WABAs of its portfolio (an employee system user needs each account granted, both the WhatsApp and the Messaging account). A system user belongs to one portfolio, so plan **one token per portfolio**.
- **Portfolio-level (shared):** messaging limit (a single number can use all of it), business verification, **phone-number cap (2, then 20 after verification or a 2,000 limit)**, and the template-limit tier. A portfolio starts with a limit of 20 WhatsApp account hierarchies.
- **Per WABA (separate):** templates (250 each unverified, up to 6,000 each verified with an approved display name), **billing, payment method and spend limit**, currency and time zone (not editable once a line of credit is attached). **INR:** all Messaging accounts of an eligible Indian business must be migrated by **31 Dec 2026**.
- **Per number:** quality rating (green, yellow, red from the last 7 days of user feedback), display name, throughput (80 messages per second by default; 20 if shared with the Business app).
- **Management APIs:** `GET /{WABA_ID}` (verification and review status, time zone), `GET /{WABA_ID}/phone_numbers` (display number, verified name, quality, `status`, `name_status`, `code_verification_status`, throughput, `whatsapp_business_manager_messaging_limit`, `webhook_configuration`), business profile read and update, `request_code` / `verify_code` / `register` / `deregister` (10 per number per 72 hours), template list with status. **A new number cannot be created through the API**: it is added in WhatsApp Manager or the App Dashboard, then registered by API.
- **Templates:** no share or library API found. `POST /{DESTINATION_WABA_ID}/migrate_message_templates` copies approved templates (quality green or unknown) between WABAs **owned by the same Meta business**; across portfolios they are recreated.
- **BSUID** is unique per (portfolio, user): the same person gets **different IDs in different portfolios**. Parent BSUIDs can link enrolled portfolios, but Meta decides eligibility and publishes no criteria.
- **No agent concept in the API.** Assignment lives in the ERP. **Mark as read** and the **typing indicator** are one call each: `POST /{PHONE_NUMBER_ID}/messages` with `status: read` or `typing_indicator`, using the latest inbound message id; the indicator lasts 25 seconds.
- **Management API rate limit:** 5,000 requests per hour per app per active WABA (200 before a number is registered): polling each WABA every few minutes is far below it.

**Still UNVERIFIED (confirm in a sandbox or with Meta)**
1. Whether "same company, different portfolios" needs **one app per portfolio** or Advanced Access.
2. Whether a payment failure on one account affects the others in the portfolio.
3. Whether the 20-number cap can be raised, and how.
4. Whether the 24-hour window is strictly per number (reading the docs, a second number of the same account should need a template to start; test it).
5. Whether template name plus language is unique per WABA (Meta's page says names are not unique).
6. Whether a restriction or ban is scoped to a number, an account or the portfolio.
7. Parent-BSUID enrolment steps; the exact INR migration endpoints (deadline 31 Dec 2026).
8. Whether Meta's newer account-model wording (a WhatsApp account holds one number, a Messaging account holds templates and billing) changes how many numbers an account may hold in practice.

### 12.9 Decisions needed from the owner

1. How many accounts at the start: one for the company, or one per brand or legal entity?
2. Agent visibility default: own conversations only, or all on the number?
3. Capacity: maximum open conversations per agent (and per number).
4. Which numbers are Shared and which Personal at launch, and who the supervisors are.
5. Out-of-hours behaviour and the "no reply within N minutes" escalation time.
