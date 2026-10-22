# KPI module: field logic and points (score) review, production, 14 Oct 2026

Read-only review. Nothing was changed. Numbers are from the live production database on 14 Oct 2026 (~14:30).

## 0. Short answers

1. **Is the KPI module live on production?** Yes, since the migrate at 10:02 today. The doctype, web form, 8 "KPI -" Number Cards, the KPI Dashboard workspace, Server Scripts and the stamp field exist. **Sales KPI Entry has 0 rows**: nobody has filled the web form yet, so the manual fields have no data.
2. **"Points settled":** there is **no incentive, reward or points-payout system** in the KPI module. The only "points" are the **Lead Score points** (data-completeness scores on each Lead). Section 3 shows exactly how they are configured and where they do not work.
3. **Main problems found** (details below):
   - 20 domestic points and 15 export points are tied to **fields that do not exist or are never filled**, so the scores can never reach 100.
   - **Lead Qualification** counts newly created leads on the same day (double counting with Lead Generation) and only started recording today at 10:15.
   - **Incoming calls and WhatsApp "Unknown" calls count as neither Cold nor Followup**; 1,081 Unknown calls in 7 days.
   - Every Lead gets **both** scores, then the KPI averages them, which is hard to explain.

## 1. How the daily record works

- One **Sales KPI Entry** per user per day (name = `user-date`). The 10 system numbers are **virtual fields**: computed live when the record is opened, never stored. Only the manual fields are stored.
- A "day" is the server calendar day (Asia/Kolkata), `00:00` to next `00:00`.
- "User" is matched on the record's **owner** (Call Log, Start-End Meeting) or on **lead_owner** (Lead).

## 2. Logic of each field (and my verdict)

| Field | Logic as built | Verified against SQL today | Weak points to review |
|---|---|---|---|
| Lead Generation | Leads with `lead_owner` = user and `creation` in the day | Yes (tsm.lucknow, 13 Oct: 3 = 3) | Uses lead_owner, not who created it. In the last 7 days only 3 of 863 leads differ, so low risk. Bulk imports owned by one user inflate that person's number. |
| Lead Score | Average over those leads of (export score + domestic score) / 2; missing counts as 0 | Formula matches the code | See section 3. Both scores are applied to every lead and averaged; neither is "the" score of a lead. |
| Lead Qualification | Leads of the user whose `custom_lead_score_updated_on` is in the day | Field exists; 121 stamped so far | See section 4. Counts same-day new leads, ignores who did the work, no history before 14 Oct. |
| Lead Qualification Score | Same average, over the stamped leads | Same | Same |
| Cold Call | Outgoing Call Log of the owner, whose linked Lead status is Lead, Open, Replied or Interested | Yes (asm.north: 26 = 26) | Calls with a Lead but **no link** count as Followup. 31% of outgoing calls in 7 days (1,418 of 4,561) have no lead, so they all fall into Followup. |
| Followup Call | The user's other Outgoing calls | Yes (cold + followup = 71 outgoing) | Mixes "no lead found" with "lead is past Opportunity". Calls of 0 seconds (46% of outgoing) count the same as real conversations; duration buckets are not built. |
| WhatsApp Calling | `custom_source = 'WhatsApp'`, any direction | 1,184 in 7 days | Includes the 1,081 `Unknown` direction rows from the WhatsApp feed (see my WhatsApp analysis). Unknown calls are in no other counter. |
| Visit Existing | Start-End Meeting with id_type Customer or Opportunity | Yes | Opportunity is a provisional choice (only 6 rows). |
| Visit New | id_type Lead | Yes (so.jaipur: 11 = 11) | Any other id_type is dropped. Today all rows are Customer, Lead or Opportunity. |
| Meeting Duration (min) | Sum of end - start of meetings that started that day and have an end | Spot check matches by design | 29 of 4,662 meetings have no end (ignored). 14 meetings in 30 days run longer than 12 hours and are not capped. Two user-days have more than 15 visits. |
| Manual fields (office meeting, training, complaints, reporting, Google Meet, WhatsApp chat, Other Activity rows) | Typed on the web form | No data yet | Other Activity rows are not summed on any dashboard or in Insights. |

**Dashboard cards:** the 8 cards use the same bucket rules as the doctype (shared constants). They filter on `>= Monday` with **no upper bound** and the Monday is set at migrate time, so between migrates the card still shows "this week" from the last migrate's Monday (currently 11 Oct).

## 3. Lead score points, the configuration

Two scores exist on every Lead, both set by Server Scripts managed from `splinh_setup.py`. Server Scripts are enabled on this site (`server_script_enabled = 1`). The scripts live: "Lead Export Profile Score" (Before Save), "Lead Export Profile Score Backfill" (hourly), "Lead Profile Percent Score" (Before Save), "Lead Profile Percent Backfill" (hourly). No duplicate or competing score scripts exist.

### 3a. Export score (`custom_export_profile_percent`, max intended 100)
| Field | Pts | Status on production |
|---|---|---|
| first_name | 15 | filled for all |
| mobile_no | 15 | ~99% |
| **custom_work_email__owner** | **15** | **field does not exist: these 15 points can never be earned** |
| whatsapp_no | 10 | ~48% |
| company_name, country, customer_type | 5 each | filled for all |
| custom_marketing_keywords (multi-select), website, printer_model_info, business_vertical (multi-select) | 5 each | website 0.3%, printer model 14% |
| Contact designation (via linked Contact) | 10 | only ~3,900 of ~299,000 leads have a designation |

- Real maximum is **85**, not 100.
- The script that sets the score on save runs **only when a Lead is created**; later edits are picked up by the **hourly backfill** (looks at leads or contacts changed in the last 2 hours, max 500 each).
- Distribution today: 50-75% band holds 270,486 of 299,360 leads (90%); 90%+ only 120 leads (old snapshot values).

### 3b. Domestic score (`custom_profile_percent`, stored as text, max intended 100, mirrors a vtiger query)
Live on every save (also edits) with Contact full-name points (5) added by the hourly backfill.

Points that **cannot be earned** on production:
| Field | Pts | Why |
|---|---|---|
| custom_city | 2 | field does not exist |
| custom_state | 5 | field does not exist |
| custom_work_email__owner (email2) | 5 | field does not exist |
| custom_profile_photo_owner | 3 | exists, 0 filled |
| custom_latitude + custom_longitude | 5 | exist, 0 filled |
| custom_geotag | 5 | 36 of 264,968 filled |
| addresslevel8a (vtiger row, 2 pts) | 2 | never mapped |

Practically **~25 of 100 points are unreachable**. `lead_name` (2 pts) just mirrors `first_name`. Several mappings are still labelled "guess, unconfirmed" in the code (city, state, address, country, industry, business type, email2) and the whole formula is marked "awaiting sign-off".

### 3c. Behaviour you should know
- The KPI "Lead Score" is **(export + domestic) / 2** of the same lead. Typical today: ~46 to 50.
- The domestic backfill compares a text value with a number (`"38" != 38` is always true), so it **rewrites every candidate lead every hour**. Harmless (no `modified` change, no stamp) but wasteful.
- Preview client scripts on the Lead form calculate only for new leads and are lower bounds (designation and contact-name points land within the hour).
- Score history was rewritten once on 22 Sep 2026; the pre-rescore snapshot CSV is at the bench root.

## 4. Lead Qualification stamp: what actually happens

- Stamped when the **domestic** score changes on an existing Lead (Before Save, any save) or when the **export** score changes in the hourly backfill. The domestic backfill does not stamp.
- Today: 121 leads stamped between 10:15 and 14:21; **75 of them were created today** (so a lead created this morning is counted in Lead Generation and again in Lead Qualification, usually when its Contact lands and the hourly job adds points), and 41 sit under lead_owner **Administrator**.
- A stamp is a score **change**, not proof that a person qualified the lead. Any edit that adds a filled field (including bulk or system edits) counts, credited to whoever is the current lead_owner.
- No history exists before 14 Oct 10:15.

## 5. Data-quality facts the numbers depend on (7 days)
- Call Log owner always equals the mapped employee's user (0 mismatches). 1,207 of 7,281 call logs have no employee (users without an Employee link).
- Outgoing calls: 4,561; with a Lead 3,159 (69%); duration 0 for 2,116 (46%).
- Meetings: ~2,100 in 30 days, none with end before start.
- Sales KPI Entry: 0 records, the web form is published (login required) and unused.

## 6. Suggested decisions (for you to approve)
1. **Fix the scores' dead points:** either create the missing fields (work email, city, state) and start filling photo/lat-long/geotag, or **re-weight** the formulas to the fields that really exist so 100 is reachable. Decide which formula is "the" Lead Score instead of averaging two.
2. **Qualification rule:** exclude leads created the same day, and decide whether Administrator/system edits count; or define qualification by a real event (status change, first call connected).
3. **Calls:** decide where Incoming and WhatsApp Unknown calls belong, treat "no Lead found" separately from "past Opportunity", and add the duration buckets (0, up to 90 s, 90 to 300 s, 300+).
4. **Meetings:** cap or flag meetings over 12 hours; decide Opportunity visit = Existing.
5. **Cards:** add an upper bound and make "this week" live instead of fixed at migrate.
6. **Pilot** the web form with two users so manual fields get data, then sum Other Activity.
