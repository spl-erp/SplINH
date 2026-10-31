# WhatsApp in the ERP: where we are, what the platform offers now, and the plan (27 Oct 2026)

> **DECISION UPDATE, 27 Oct 2026 (later the same day): the owner decided NOT to use DoubleTick.** The ERP will integrate **directly with Meta's WhatsApp Cloud API** and build its own inbox and workflows. This report was written assuming DoubleTick as the transport, so read it like this:
> - **Still valid:** section 1 (where we are), section 2 (Meta's rules: cost, the 24-hour window, templates, media, webhooks, usernames, policy), section 5 (what our product should be), the risks in section 8 that come from Meta, and the data model and flows in section 6.
> - **Superseded:** section 3 (DoubleTick's API), section 4's "embedded panel" discussion, the DoubleTick questions in section 9 (no longer needed), the DoubleTick provider in section 6 (replaced by a Meta Cloud API client), and the phase table's references to DoubleTick keys. **Open items that now apply instead:** a verified Meta business account, a WABA and a phone number (the current number is registered through DoubleTick), a system-user access token and the app secret, and a built-in inbox (assignment, unread, quick replies, templates screen) that DoubleTick used to provide.
> - **The revised plan for the direct route is `WhatsApp_Direct_Meta_Plan_2026-10-27.md`** (setup checklist, build scope, phases, risks). Build from that, not from the superseded parts here.

Written for the project owner and for whoever builds this next (an engineer or an AI model with no earlier context). It replaces the planning that was lost with an earlier chat. **Nothing here has been built or changed.** Facts carry a source; anything I could not confirm is marked **UNVERIFIED**. Three independent web-research passes (Meta's platform, DoubleTick, the Frappe ecosystem) plus an audit of our own files and code feed this report. The DoubleTick pricing page refused every fetch, so prices below are not confirmed.

---

## 0. The short version

1. **Where we are:** only an identity handshake works. DoubleTick can identify and list our users. The ERP cannot send, receive, template, attach or show any WhatsApp conversation yet. DoubleTick's outbound API key, the business number details and current docs were never obtained.
2. **Direction (from the owner, 27 Oct):** we are **not** copying DoubleTick. We build our own product inside the ERP, the best we can offer for the company's requirements. DoubleTick is only the **pipe** to WhatsApp. This replaces the earlier "embed DoubleTick's chat panel first" decision: the research shows embedding gives the ERP no copy of the conversation, depends on several unverified conditions, and cannot serve the reporting we want (section 4).
3. **Recommendation:** a native layer in `splinh` (message log, webhook receiver, send actions, 24-hour-window tracking, template picker, attachments, record timeline, KPI links) built behind a **provider adapter**, so DoubleTick can be replaced later. Most of it can be built and tested **before the API key arrives**, against a mock provider and DoubleTick's documented payload shapes.
4. **The things that will bite if ignored:** WhatsApp usernames can hide the customer's phone number from webhooks (our whole matching depends on it); documented opt-in is mandatory and our 300k imported Leads probably have none; Meta started charging for more message types on 1 Oct 2026; DoubleTick documents no webhook signature.
5. **What unblocks everything:** a written answer from DoubleTick to the list in section 9, plus the API key.

---

## 1. Where we are today (from our own files and code)

| Item | State | Source |
|---|---|---|
| DoubleTick identity handshake | **Working.** Two read-only Server Scripts on production, `doubletick_list_users` and `doubletick_validate_user`. DoubleTick's docs explain them: they are the "List Users" and "Validate User" endpoints of its **Custom CRM integration**, used to map ERP users to agent seats and to identify the logged-in user. | `whatsapp_integration_analysis.md` (23 Sep); DoubleTick docs |
| Send, receive, template, attachment, conversation history | **Not built.** No outbound call to DoubleTick exists anywhere in our code; no webhook receiver; no message log. | same |
| DoubleTick access | Outbound API key **not obtained**; business number details **not on file**; current API docs **were never read** (now partly read, section 3). A service account `doubletick_api@...` exists but is disabled. | same |
| Users' mobile numbers | 51 of 71 enabled users have none (DoubleTick needs them for agent-seat mapping). Measured 23 Sep; may have changed. | same |
| WhatsApp **call** tracking | **Built** (25 Sep): the Android app detects WhatsApp calls from notifications and sends them to the same Call Log, tagged `custom_source = WhatsApp`, status `Unknown`. Shown as "Missed" in the UI. | CHANGELOG 25 Sep |
| What that tracking really captures | **92.5% of 1,315 WhatsApp rows (4-10 Oct, production) are chat notifications, not calls**; only 21 were real completed calls. Chat and group titles and phone numbers from employees' phones are being uploaded. The analysis itself flags it as a privacy issue. | `WhatsApp_CallLog_Analysis_2026-10-10.md` |
| Earlier decisions on record | One number, many agents (shared inbox). A message-log doctype and webhook that record **which number** a message belongs to from day one. A separate message log (never Communication, which has no WhatsApp medium). | `PROJECT_CONTEXT.md` item 9 |
| Security items flagged earlier, still open | An open user-list endpoint, a dormant Guest branch, and external-vendor Server Script read access (independent of the build). | `PROJECT_CONTEXT.md` |
| The detailed end-to-end plan from the lost chat | **Not recoverable.** It lived under an account this session cannot read. Nothing is in git or the repo. | search, 27 Oct |

---

## 2. What the WhatsApp platform looks like now, and what it does to our four features

Source for all of this section: Meta's developer documentation read on 27 Oct 2026 (pages listed in section 11), unless marked. Whether **DoubleTick passes these Meta details through** (BSUIDs, signatures, media ids) is unverified.

### 2.1 Cost (changed on 1 Oct 2026)
- Per-message pricing (since 1 Jul 2025). Users are never charged for messaging a business.
- **From 1 Oct 2026** Meta charges again for **service messages** (free-form replies inside the 24-hour window) after the first **1,000 per month per number**, and for **utility templates sent inside an open window**.
- India, Meta's own INR list rates effective 1 Oct 2026: **marketing 0.8631, utility 0.115, authentication 0.115, service after the free 1,000: 0.115** (INR per message). One third-party provider publishes exactly these x 1.15, so a BSP can add a margin: **ask DoubleTick what it adds.**
- Marketing costs about **7.5x** utility. A quotation or follow-up template is easily classed as marketing.
- **INR billing deadline:** eligible Indian customers must be on INR accounts by **31 Dec 2026**; from 1 Jan 2027 messages from non-INR accounts stop. Confirm our DoubleTick account is INR.
- DoubleTick's own plan price: **UNVERIFIED** (conflicting USD figures from search snippets; Starter about 5 agents, Pro about 10; extra seats unknown).

### 2.2 The 24-hour window and templates
- A window opens when the customer messages or calls us and refreshes on each new message. It is tracked **per business number and customer**, so every agent on the number shares the state. Inside it: any free-form message including documents. Outside it: **approved templates only.**
- Templates: categories marketing, utility, authentication. Anything promotional or unclear defaults to **marketing**. Approval "can take up to 24 hours". Limit 100 template creations per hour. A template can carry an image or **document header**.
- WhatsApp can refuse a marketing template to an individual user (error **131049**); wait at least 24 hours before retrying. A template with poor feedback is paused automatically (3 h, then 6 h, then disabled).
- Outbound template sends to **new** unique numbers are capped by a messaging tier set per business portfolio: new portfolios start at **250**, and scale to 2,000 and beyond with verification and quality. In-window and reply traffic does not count.

### 2.3 Media
- Documents (PDF, DOCX, XLSX and others) up to **100 MB**. Images only **JPEG or PNG, 5 MB**. Video 16 MB. Audio 16 MB.
- **Inbound media must be downloaded at once:** Meta's download link lasts 5 minutes and the media id 7 days. (DoubleTick delivers a `url`; its lifetime is unverified.)

### 2.4 Webhooks
- Any non-200 answer is retried for up to **7 days**, so duplicates are normal. Updates can arrive out of order (a "read" before "delivered"). **There is no way to fetch missed history.** Key on message id plus status; make status updates monotonic.
- Meta signs payloads (HMAC-SHA256 of the raw body, keyed by the app secret). **DoubleTick documents no signature**, only a static Basic or Bearer token it sends to us.

### 2.5 Identity: usernames (the biggest design risk)
- Since **April 2026** every webhook carries a **business-scoped user id** (BSUID, looks like `IN.xxxxx`). WhatsApp users can now have **usernames**; Meta says the rollout is gradual in 2026 with no fixed date, and whether it has reached India is **UNVERIFIED**.
- For a user **without** a username the phone number is still always delivered. For a user **with** one, the phone number is delivered **only if within the previous 30 days** we messaged them, they messaged us, or they are in Meta's contact book. Otherwise `from` can be absent.
- Our ERP matches senders by phone number (Phone Lookup). We therefore need an **identity store** of seen phone-to-BSUID pairs, matched first by BSUID, then by phone, then an "unmatched" queue. Whether DoubleTick forwards the BSUID at all is **UNVERIFIED**. DoubleTick offers a `RAW_CLOUD_API_WEBHOOK` event that passes Meta's raw payload through, which is the fallback.

### 2.6 Coexistence (the WhatsApp Business app and the API on one number)
- Live, but not a shared inbox: one phone plus up to 4 linked devices; no groups or broadcast lists on the API side; 20 messages per second fixed; messages sent from the phone neither open nor extend the API window; up to 6 months of history can sync.
- Our reps already chat from their own WhatsApp Business apps. The Cloud API must be the main channel; coexistence is only a bridge. **Whether our DoubleTick number is coexistence or pure Cloud API is UNVERIFIED.**

### 2.7 Calling API
- Live in India for business-initiated calls, but only for a number on the Cloud API (not the Business app). **Not needed for version 1.** Our Android WhatsApp-call tracking stays as it is, with the notification fix in section 6.

### 2.8 Policy and compliance
- **Opt-in is required** before messaging, must name the business, and the business carries the legal burden (India: TRAI and DPDP specifics **UNVERIFIED**). Our imported 300k Leads almost certainly do not all have documented WhatsApp opt-in. **This is the main compliance risk.**
- Repeated violations escalate to template blocks, send blocks and permanent disabling.
- Meta barred general-purpose AI assistants from the platform (15 Jan 2026). An ERP replying to customers is not that; AI-drafted replies need a read of the Business Solution Terms.
- Whether **ink and solvent products** could count as "hazardous materials" under the commerce policy: **UNVERIFIED, check with DoubleTick or Meta.**

### 2.9 Effect on the four version-1 features

| Feature | Helps / Hurts / Neutral | Why | Design consequence |
|---|---|---|---|
| 1. Send from a Lead / Contact / Customer | **Neutral** | Simple, but in-window sends now cost INR 0.115 after 1,000 a month. | Normalise every number to E.164. Show window state before sending. Count monthly service messages and warn. |
| 2. Receive and log on the right record | **Hurts** | BSUIDs can hide the phone number. 7-day retries cause duplicates. No history API. | Idempotent webhook. Identity store (BSUID first, phone second). "Unmatched" queue. Store every raw payload. |
| 3. Templates outside the window | **Mixed** | Up to 24 h approval; likely marketing price for quotation text; per-user refusals; pausing. | Mirror template list and status. Send only approved ones. Check opt-in first. Handle 131049 with a 24 h guard. |
| 4. Attachments both ways | **Helps outbound, hurts slightly inbound** | Documents up to 100 MB. Images 5 MB JPEG or PNG only. Media links expire fast. | Convert images before sending. Download inbound media immediately into private Files. Document-header template for a PDF outside the window. |

---

## 3. What DoubleTick gives us (documentation read on 27 Oct; sources in section 11)

**Verified from DoubleTick's public docs**
- **REST API:** base `https://public.doubletick.io`, raw API key in the `Authorization` header (no "Bearer"). The key is created in Settings, Developer Documentation, shown once, with optional IP whitelisting. There is **one key type**; the same key sends messages and requests embed URLs.
- **Endpoints:** send text (24 h window only), send template (document, image and video headers supported), send document / image / video / audio / location / interactive; upload media (returns a URL that expires); list templates (`/v2/templates`, filter by status) and create template; **get chat messages** by customer number and date range (no pagination documented, so poll by date); **chat window status** (`/chat/status` returns `isOpen`); assign or unassign a chat to an agent; customer details, tags, custom fields; wallet balance; broadcast groups; chat export.
- **Webhooks:** registered in the dashboard or via `POST /v2/webhook/register`. Events include message received, message status update (SENT, DELIVERED, READ, FAILED), template update, chat assigned and unassigned, conversation opened and closed, and `RAW_CLOUD_API_WEBHOOK`. Authentication is only a static Basic or Bearer token DoubleTick sends to us. A `retryOnTimeout` flag exists; retry count and backoff are not documented.
- **Custom CRM integration with an embeddable inbox:** our two existing endpoints are its List Users and Validate User calls. The flow: the ERP backend asks DoubleTick for a single-use **embed token** (valid 5 minutes), then for a **per-phone conversation URL**, and the page loads it in an iframe with flags to show or hide parts of the UI. Deep links per record exist (the URL call takes a phone number).

**Unverified or missing (each needs a written answer from DoubleTick)**
- The inbound message webhook's full schema (only "media arrives as `payload.message.url`" is documented); the retry policy; rate limits; maximum file size; media URL lifetime and whether DoubleTick keeps the file; any webhook signature; whether the BSUID is forwarded.
- What exactly `sessionToken` is in the embed call, and whether our Validate endpoint can accept Frappe's session; how DoubleTick authenticates its calls to our two endpoints.
- Whether `webextend.doubletick.io` allows framing from `erp.splashjetink.com` (framing headers, third-party cookies).
- Plan tier, seats, INR price per extra seat, whether API, webhooks and embed are included, any markup over Meta, wallet and onboarding terms, data residency and retention.
- The **plain facts about DoubleTick's gaps**: no signed webhook, no pagination, no per-message lookup, so we cannot treat DoubleTick as the system of record.

---

## 4. Why native first, and what happened to "embed first"

The embedded inbox is real and useful, but the research makes it the wrong foundation for **our** goal:
- **No ERP copy.** An embedded conversation lives entirely in DoubleTick. Without our own webhook and log the ERP knows nothing, so no timeline on the record, no KPI, no manager view, no quotation linkage.
- **Several unverified dependencies:** framing permission, third-party cookies, the session-token handshake. Any one can make it impossible.
- It would make DoubleTick's product the user experience, which is the opposite of the owner's direction.

So the plan is native-first. **The embedded panel stays available as an optional extra** (for agents who want DoubleTick's full inbox) once a short spike confirms it works. It does not replace the log.

**Reuse versus build (Frappe ecosystem, 27 Oct):**
- Core Frappe/ERPNext v16 has no WhatsApp channel; its Communication medium has no WhatsApp option, and Communication is unsuited to chat volume.
- `frappe_whatsapp` (community, active, claims v16 support, **unverified on our 16.12/16.10 build**, no version-16 branch) is built for Meta's Cloud API directly, has no DoubleTick support, and registers a handler on **every save of every doctype** (reported; verify before any use), which would sit on our 300k-row Lead and Contact tables. Not recommended as a base; borrow ideas only.
- Frappe CRM's WhatsApp tab runs on its own CRM Lead/Deal doctypes, which we do not use.
- No Frappe app exists for DoubleTick.

---

## 5. What our product should be (not a DoubleTick copy)

A generic chat inbox is DoubleTick's job. What the ERP can do that they cannot is know **who the customer is and what happened with them**. Proposed capabilities, to be confirmed by the owner:

1. **Conversations belong to the record.** A WhatsApp timeline on Lead, Customer and Opportunity next to calls, quotations and visits, resolved through our Phone Lookup, including Contact-to-Customer resolution.
2. **Quotations and invoices go out in one click.** A button on the Quotation sends its PDF with an approved document-header template (or free-form inside the window) and logs it against that Quotation.
3. **Window-aware compose.** Free text while the window is open; only approved templates once closed; the choice is computed by us and re-checked at send time.
4. **The KPI becomes automatic.** Sales KPI Entry still has **WhatsApp Chat count and duration as manual fields**; with our own message log they become system-generated like calls, and DILO can show WhatsApp touches.
5. **Visibility follows our sales hierarchy.** Reps see their own conversations, managers their team's (the Sales Person tree DILO already uses). Messages cannot be deleted; history is exportable.
6. **Opt-in is enforced.** Opt-in status, source, date and wording on the Lead/Contact; template sends are blocked without it.
7. **Cost visibility.** A monthly count of billable messages per category against the free allowance, so the 1 Oct change never surprises us.
8. **Unmatched and ambiguous senders are handled.** A triage list for numbers with no record, or shared by several (3,054 numbers sit on more than one Contact, 1,284 on more than one Lead).
9. **Optional later:** assignment tied to Sales Person ownership, review of conversations like Call Review, and a DoubleTick panel for heavy-chat agents.

---

## 6. Architecture (all in `splinh`, so UAT and production migrate identically)

**Provider adapter** (`splinh/whatsapp/providers/`): one interface (`send_text`, `send_template`, `send_document`, `upload_media`, `window_open`, `list_templates`, `parse_webhook`) with a DoubleTick implementation and a **mock** used by tests and by UAT until the key arrives. Swapping the provider touches one file.

**Doctypes** (names are working titles; created by the config engine, not by hand in a UI, the lesson from the Sales Scorecard):
- `WhatsApp Number`: the business number(s) and their provider reference, so a second number can be added later. The API key lives in a Password field or site config, never in a client script.
- `WhatsApp Message`: direction, status (queued, sent, delivered, read, failed, received), provider and provider message id with a **unique index** for idempotency, business number, customer number as an indexed E.164 key plus the raw value, type, body, caption, template name and parameters, media as a private File, sent by, party (Dynamic Link to Lead / Contact / Customer), the raw payload, failure reason. Indexed by (party, time) and (number, time). Plan for archiving: the table will grow.
- `WhatsApp Conversation` (small): per business number and customer, last inbound time (window state), party, unread count. Makes the record timeline and any inbox list fast.
- `WhatsApp Template` mirror (name, language, category, status, quality, header type) synced from the provider and its status webhook.
- `WhatsApp Identity`: phone-to-BSUID pairs, per portfolio.
- Custom fields on Lead and Contact: opt-in status, source, date, wording.
- Failure log in the style of `Call Sync Failure`, so a failed event is never silently lost.

**Endpoints:** a guest webhook receiver that checks a long secret (header or URL token, compared in constant time), stores the raw event, returns 200 at once and processes in a **queued job** (the pattern `ingest_whatsapp_call` and the call-recording upload already use); a whitelisted send method that checks the user's permission on the record, writes a queued row and enqueues the provider call; rate limiting sized to DoubleTick's burst, not a guess. Duplicate events return 200.

**Matching** reuses `splinh.custom.phone_lookup` (`find_all_by_key`, `normalize_phone_key`). Known gaps to close: **Customers are not indexed** in Phone Lookup (resolve through their linked Contact); DoubleTick numbers arrive without `+` and need one added; several holders need a rule (prefer the party with our most recent outbound message); an unknown number goes to the triage list, **no automatic Lead creation** without a decision (duplicates are costly at 300k).

**UI:** a button group and a timeline on Lead, Contact and Customer via `doctype_js` in `splinh`, **additive only**. The vendor `sigzensfa` Lead and Customer scripts rebind address buttons and re-add Opportunity and Quotation buttons; ours must use a unique button label, never remove other buttons, and namespace its events.

**Call tracker change:** stop sending WhatsApp **message** notifications from the Android app, keep only call events (the Oct 10 analysis, section 3, already asks for this). That ends the privacy problem and the 92% noise.

---

## 7. Phased plan (effort is relative: S small, M medium, L large)

| Phase | Content | Needs the DoubleTick key? | Effort |
|---|---|---|---|
| **0. Facts** | Send the section 9 questions to DoubleTick; get the key, number, plan and a **real sample webhook payload**; decide opt-in policy and the INR billing check; confirm the Business number type. | produces it | S |
| **1. Core log and receive** | Doctypes, provider adapter + mock, webhook receiver with idempotency and raw capture, matching with the identity store and triage list, opt-in fields, offline tests with fixtures from DoubleTick's documented shapes. | **No** (mock) | L |
| **2. Send and window** | Send method, queued sending, window state, "send to this record" button and timeline UI, cost counters. | key for the live test only | M |
| **3. Templates and attachments** | Template mirror and picker with variable mapping from the record, document-header quotation send, inbound media download into Files, image conversion. | yes | M |
| **4. KPI, DILO and manager views** | System-generated WhatsApp counts in Sales KPI Entry, hierarchy visibility, export, the call-tracker notification change. | no | M |
| **5. Optional** | DoubleTick embedded panel (after a spike), assignment sync, conversation review, AI-drafted replies (after reading the Business Solution Terms). | yes | S to M each |

Phase 1 can start now. Each phase ends with a UAT verification run in the same style used for the Duplicate Check and DILO (offline tests, a rolled-back test against real data, a changelog entry).

---

## 8. Risks

- **Identity:** username users without a recent interaction have no phone number in the webhook. Mitigation: identity store, BSUID-first matching, triage list, and confirming what DoubleTick forwards.
- **Opt-in and compliance:** no documented opt-in on most Leads. Mitigation: opt-in fields, blocked template sends, a plan to collect opt-in.
- **Cost:** marketing is about 7.5x utility; the free service allowance ended on 1 Oct 2026; unknown DoubleTick margin. Mitigation: category-aware template planning, counters, a written INR quote.
- **No webhook signature from DoubleTick:** a secret token and optional IP allowlist, constant-time comparison, rate limit, idempotency, nothing trusted before the secret passes.
- **Dependence on a vendor's undocumented behaviour:** the provider adapter and the stored raw payloads isolate us; every unverified item has a test against a captured real payload before go-live.
- **Template approval, per-user refusals and pausing:** mirror status, never send an unapproved template, back off on 131049.
- **Table growth and privacy:** index and archive the message log; keep access by role and hierarchy; retention policy decided by the owner.
- **Vendor JS on Lead/Customer forms is brittle:** our UI is additive only.
- **Product policy:** whether inks or solvents are treated as restricted goods is unverified.

---

## 9. Questions to send to DoubleTick (copy-paste)

1. Please enable the Developer API for our account and issue a key, with IP whitelisting to our ERP server. Is the Custom CRM integration and embedding included in our plan?
2. Our ERP has List Users and Validate User endpoints. Where are their URLs registered, how does DoubleTick authenticate to them, and what exactly is the `sessionToken` you pass to Validate?
3. Please send a **real sample of the MESSAGE_RECEIVED webhook payload**, including one with an attachment and one from a user with a WhatsApp **username**, and the contents of `docs.doubletick.io/llms.txt`.
4. Does DoubleTick forward WhatsApp's business-scoped user id (BSUID) and the original phone number? Is `RAW_CLOUD_API_WEBHOOK` available on our plan?
5. Webhook signature or a shared-secret header option; retry count and backoff; delivery order guarantees; rate limits on the send and read APIs.
6. Media: maximum sizes, how long inbound media URLs stay valid, whether you keep the files, and whether `webextend.doubletick.io` allows framing from `erp.splashjetink.com`.
7. Is our number on the pure Cloud API or coexistence with the WhatsApp Business app? Our business number in international format and the `integrationId`.
8. Plan tier, number of agent seats, a **written INR price per extra seat**, any markup over Meta's message rates, wallet and onboarding terms, and confirmation that our account is billed in **INR** before 31 Dec 2026.
9. Data: where messages are stored (region), retention, export, your security document (ISO 27001) and a DPA.
10. Could ink and solvent products be treated as restricted goods under WhatsApp's commerce policy?

---

## 10. Decisions needed from the owner

1. Confirm the **native-first** direction and the capability list in section 5 (add, remove, reorder).
2. **Opt-in policy:** what we may send without documented opt-in (likely only replies inside a window until opt-in is collected), and how opt-in gets collected.
3. **Number model:** one shared company number (current decision), and whether reps may keep chatting from their own WhatsApp Business apps meanwhile.
4. **Unknown numbers:** triage only (recommended) or auto-create a Lead.
5. **Who sees what:** reps own conversations only, managers their hierarchy; who may see everything.
6. **Retention and deletion** of messages and media.
7. Approve starting **Phase 1 on UAT now** against the mock provider, while Phase 0 runs.

---

## 11. Sources and confidence

- **Our files:** `whatsapp_integration_analysis.md`, `PROJECT_CONTEXT.md` item 9, CHANGELOG entries of 25 Sep, `WhatsApp_CallLog_Analysis_2026-10-10.md`, `Sales KPI Entry` fields, `splinh/custom/phone_lookup.py`, `phone_normalize.py`, `api/call_tracking.py` (`ingest_whatsapp_call`), `scorecard_schema.py`.
- **Meta** (read 27 Oct 2026): `developers.facebook.com/documentation/business-messaging/whatsapp/` pages on pricing, send-messages, templates (overview, review, pausing, quality, media, categorization, per-user limits), messaging-limits, media, webhooks (overview, status, document), business-scoped-user-ids (changelog through 15 Sep 2026), embedded-signup onboarding for business app users (coexistence), calling, getting-opt-in, policy-enforcement, and `whatsappbusiness.com/policy/` (updated 23 Sep 2026). India INR rates are from Meta's published rate file, parsed by the research pass. Messaging tier values for India, the INR calling rate, username availability in India, and Indian-law opt-in specifics are **UNVERIFIED**.
- **DoubleTick** (docs read 27 Oct 2026): `docs.doubletick.io/docs`, including custom-crm-integration, embed-chat-conversation, register-new-webhook, template-messages, upload-media, get-chat-messages, and `learn.doubletick.io/settings/get-developer-api`. Pricing page unreachable; plan and price figures are **UNVERIFIED** and conflict between sources.
- **Frappe ecosystem:** the `frappe_whatsapp` and `frappe/crm` GitHub repositories and Frappe docs, plus a read of the installed Frappe 16.12 code and the vendor apps' form scripts. The wildcard-save-handler claim about `frappe_whatsapp` is the research pass's reading and must be re-checked before anyone installs it.
- Two of the research passes' claims were **discarded as unreliable**: one table-size figure that does not match our data (we have about 300k Leads and 300k Contacts), and unsupported price statements from competitor blogs.
