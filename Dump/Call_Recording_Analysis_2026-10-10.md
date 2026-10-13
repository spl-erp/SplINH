# Call recording analysis — for the app development team

Source: ERPNext production (`erp.splashjetink.com`), Call Log + attached recording files + upload log. Analysed **10 Oct 2026, ~15:30 IST**, read-only (nothing was changed on the server).
Main window: **last 48 hours (8 Oct 15:00 → 10 Oct 15:20)**, with all-time views where noted. Three independent analyses were combined (coverage / upload pipeline / missing-pattern + transcription); the biggest findings were re-verified directly against the database.

> **Privacy:** contains employee names, emails and device IDs. Share only inside the team.

---

## 0. Summary (read this first)

| # | Finding | Strength | Owner |
|---|---|---|---|
| 1 | **Calls of 170 s or longer are never recorded.** 0 of 594 such calls ever (0 of 368 for phones that do record). Longest recorded call: 165 s. Ratio already falls from ~150 s. | Verified, counts | **App** |
| 2 | **~11 phones never upload any recording** although their calls sync fine (no upload attempts in the server log at all). They account for 377 of 650 missing recordings in the window. | Strong (counts + log) | **App** |
| 3 | **2 phones upload only in bursts after a login**, leaving a large backlog (`bd2.sales` 7 of 56 expected, `sales.east1` 7 of 80). Upload latency is bimodal: median 0.8 min, but the slowest 10 % arrive ~20 h late. | Medium-strong | **App** |
| 4 | **Phantom calls cause a retry storm:** 9 call IDs (5 users) have no Call Log and no sync-failure row, yet phones retry their recordings endlessly — **~770 of 1,059 upload attempts (73 %)**. Worst: asm.south call 11434, ~150 attempts, still retrying. | Verified | **App + Server** |
| 5 | **Race:** the phone uploads a recording right after syncing the call, sometimes before the server has created the Call Log → "not found". 52 calls recovered only after retries (median 4 s, worst ~10 min). | Strong | **App + Server** |
| 6 | **Duplicate uploads:** one device (rsm.south1) produced 2–3 identical files on 13 calls; the server has no idempotency check. | Strong | **App + Server** |
| 7 | 3 suspiciously tiny recordings (209 B, 1,440 B, 1,809 B). | Strong (counts), cause inferred | App |
| 8 | **Incoming calls are recorded less than outgoing** (23.7 % vs 36.3 % on 10–169 s calls, recording users). Cause unknown. | Medium (confounded) | App |
| 9 | **AI transcription is throttled by its own schedule** (~20/day vs ~130 new recordings/day); 560 eligible recordings waiting. Also an unexplained anomaly and a Gemini rate-limit hint. | Strong (arithmetic) | **Server** |
| 10 | Server side is healthy: files present on disk, no upload-endpoint errors, disk 30 % used, call sync fine for everyone except one user without a role. | Strong | — |

**Coverage, one line:** of 2,735 SIM calls in the window 416 have a recording (0.16 per call). For calls ≥ 10 s: **26.5 % by sync time, ~35 % by call time**. For phones that record at all and calls under 170 s the ratio is 45–81 % in the last 48 h.

---

## 1. Definitions (how the numbers were made)

- **Call** = `Call Log` with `custom_source = 'SIM'`, type Incoming/Outgoing. WhatsApp rows (`custom_source = 'WhatsApp'`) are mostly notifications with 0 s duration and **never** have recordings — excluded everywhere.
- **Recording** = a private `File` with `attached_to_doctype = 'Call Log'`, `attached_to_name = <Call Log name>`, file name `rec_*.m4a`. (All files are `.m4a`.)
- **Window by sync time** = `Call Log.creation` (when the server inserted the call). **Window by call time** = `start_time`. They differ a lot because many calls are back-filled (~1,900 of ~2,700 calls created in 48 h have a `start_time` more than 1 h earlier).
- **"Users who record"** = users with at least 10 recordings in total (all time).
- **"Expected" recording** (missing-pattern analysis) = Completed or duration ≥ 10 s, started before 12:10 on 10 Oct (drops the newest 3 h, which may still be pending): 966 calls.
- Section numbers differ slightly between analyses because cut-off times differ by minutes (e.g. 2,735 vs 2,896 calls); conclusions are unaffected.

---

## 2. Recording ratio by call duration  ⭐

SIM calls, Incoming + Outgoing. "Users who record" = ≥ 10 recordings.

| Call duration | All users, all time | Users who record, all time | Users who record, last 48 h |
|---|---|---|---|
| 0 s | 0.1 % (3 of 2,821) | 0.2 % (3 of 1,421) | 0.4 % (2 of 453) |
| 1–4 s | 4.5 % (7 of 154) | 11.9 % (7 of 59) | 33 % (3 of 9) |
| 5–9 s | 11.6 % (27 of 232) | 29.3 % (27 of 92) | 45 % (9 of 20) |
| 10–29 s | 11.2 % (136 of 1,217) | 26.7 % (134 of 501) | 54 % (70 of 129) |
| 30–59 s | 14.6 % (196 of 1,345) | 29.2 % (192 of 657) | 64 % (110 of 173) |
| 60–89 s | 21.5 % (143 of 664) | 39.1 % (140 of 358) | 74 % (82 of 111) |
| 90–119 s | 22.9 % (88 of 384) | 39.1 % (88 of 225) | 74 % (46 of 62) |
| 120–149 s | 24.1 % (61 of 253) | 41.7 % (60 of 144) | 81 % (33 of 41) |
| 150–169 s | 11.7 % (12 of 103) | 20.7 % (12 of 58) | 45 % (5 of 11) |
| **170–199 s** | **0 % (0 of 137)** | **0 % (0 of 97)** | **0 % (0 of 27)** |
| 200–299 s | 0 % (0 of 229) | 0 % (0 of 139) | 0 % (0 of 35) |
| 300–599 s | 0 % (0 of 168) | 0 % (0 of 102) | 0 % (0 of 28) |
| 600 s + | 0 % (0 of 60) | 0 % (0 of 30) | 0 % (0 of 2) |
| **Total** | **8.7 % (673 of 7,767)** | **17.1 % (663 of 3,883)** | **32.7 % (360 of 1,101)** |

### The cliff, in 5-second steps (users who record, all time)

| Duration | Calls | Recorded | Ratio |
|---|---|---|---|
| 140–144 s | 17 | 5 | 29.4 % |
| 145–149 s | 12 | 5 | 41.7 % |
| 150–154 s | 20 | 5 | 25.0 % |
| 155–159 s | 14 | 4 | 28.6 % |
| 160–164 s | 11 | 2 | 18.2 % |
| 165–169 s | 13 | 1 | 7.7 % |
| 170–174 s | 15 | 0 | 0 % |
| 175–179 s | 16 | 0 | 0 % |
| 180–184 s | 15 | 0 | 0 % |
| 185–189 s | 25 | 0 | 0 % |
| 190–194 s | 11 | 0 | 0 % |
| 195–199 s | 15 | 0 | 0 % |

### Other cuts

- **Completed calls only, users who record, all time:** 29.0 % (660 of 2,276); same shape (33–42 % up to 149 s, 20.7 % at 150–169 s, 0 % from 170 s).
- **Direction (users who record, calls 10–169 s):** Incoming 23.7 % (151 of 636) vs Outgoing 36.3 % (475 of 1,307).
- **Recorded calls:** shortest 1 s, longest **165 s**, median 53 s, 90th percentile 123 s (n = 670).
- **Coverage barely depends on a "minimum duration" filter:** all SIM 15.2 % → ≥ 1 s 25.7 % → ≥ 10 s 26.5 % → ≥ 30 s 28.9 % → ≥ 60 s 28.8 % (flat; missing recordings are not mainly a short-call effect).

### Reading

1. **Hard wall at ~165–170 s.** The ratio already declines from ~150 s and is exactly zero from 170 s. The server has no duration/size limit (code searched), nothing is rejected at the server, so the limit is in the phone app / recorder / its upload path. About 600 long calls (often the most valuable) have no recording.
2. **Short calls are rarely recorded.** 0 s calls are never recorded (unanswered); 1–9 s are recorded 12–45 % of the time; the ratio rises steadily with call length up to ~150 s (possible recorder start-up delay or a minimum-length rule — to be checked in the app).
3. The all-time columns are dragged down by 1–5 Oct, when almost nothing uploaded. The last-48-hours column is closer to today.

---

## 3. Coverage in the last 48 hours (by sync time)

### 3.1 By duration threshold (all 2,735 SIM calls)

| Denominator | Calls | With recording | Coverage | Excl. newest 3 h: calls | with rec | % |
|---|---|---|---|---|---|---|
| All SIM | 2,735 | 416 | 15.2 % | 2,322 | 379 | 16.3 % |
| Status Completed | 1,551 | 414 | 26.7 % | 1,307 | 377 | 28.8 % |
| ≥ 1 s | 1,609 | 414 | 25.7 % | 1,346 | 377 | 28.0 % |
| ≥ 5 s | 1,563 | 411 | 26.3 % | 1,304 | 374 | 28.7 % |
| ≥ 10 s | 1,515 | 401 | 26.5 % | 1,266 | 364 | 28.8 % |
| ≥ 30 s | 1,104 | 319 | 28.9 % | 923 | 287 | 31.1 % |
| ≥ 60 s | 640 | 184 | 28.8 % | 545 | 166 | 30.5 % |
| = 0 s | 1,126 | 2 | 0.2 % | 976 | 2 | 0.2 % |

- 432 recording files are attached to calls of this window (0.16 per call; 0.27 per call ≥ 10 s). Last 3 h alone: 413 calls, 37 with a recording (9 %) — mostly pending.
- By *call time* instead of sync time: ≥ 10 s calls 919, covered 323 → **35.1 %**.

### 3.2 By direction and status (window)

| Group | Calls | Coverage | ≥10 s calls | ≥10 s covered | ≥10 s % | ≥10 s excl. 3 h % |
|---|---|---|---|---|---|---|
| Incoming | 628 | 15.0 % | 464 | 91 | 19.6 % | 22.7 % |
| Outgoing | 2,107 | 15.3 % | 1,051 | 310 | 29.5 % | 31.5 % |
| Completed | 1,551 | 26.7 % | 1,425 | 401 | 28.1 % | 30.6 % |
| No Answer | 1,162 | 0.2 % | 87 | 0 | 0 % | 0 % |
| Busy | 22 | 0 % | 3 | 0 | 0 % | 0 % |

(No Answer / Busy calls are not recorded — expected, not a gap.)

### 3.3 Trend by day (calls ≥ 10 s)

| Date | Calls | ≥ 10 s | covered | % | recordings |
|---|---|---|---|---|---|
| 5 Oct | 2,129 | 1,289 | 9 | 1 % | 14 |
| 6 Oct | 552 | 347 | 47 | 14 % | 52 |
| 7 Oct | 926 | 581 | 68 | 12 % | 75 |
| 8 Oct | 2,063 | 1,115 | 214 | 19 % | 221 |
| 9 Oct | 977 | 550 | 220 | **40 %** | 247 |
| 10 Oct (partial) | 620 | 375 | 67 | 18 % | 67 |
| Baseline 5–8 Oct | 5,670 | 3,332 | 338 | 10.1 % | 362 |

### 3.4 Trend by hour (sync time)

| Hour | Calls | ≥10 s | covered | % | recs |
|---|---|---|---|---|---|
| 8 Oct 15 | 225 | 104 | 52 | 50 % | 53 |
| 8 Oct 16 | 450 | 231 | 27 | 12 % | 28 |
| 8 Oct 17 | 461 | 253 | 34 | 13 % | 36 |
| 9 Oct 09 | 48 | 37 | 13 | 35 % | 14 |
| 9 Oct 10 | 45 | 16 | 3 | 19 % | 3 |
| 9 Oct 11 | 48 | 31 | 6 | 19 % | 7 |
| 9 Oct 12 | 46 | 32 | 20 | 62 % | 26 |
| 9 Oct 13 | 70 | 36 | 18 | 50 % | 24 |
| 9 Oct 14 | 37 | 22 | 14 | 64 % | 21 |
| 9 Oct 15 | 282 | 159 | 94 | 59 % | 99 |
| 9 Oct 16 | 275 | 158 | 37 | 23 % | 37 |
| 9 Oct 17 | 121 | 57 | 14 | 25 % | 15 |
| 10 Oct 09 | 91 | 61 | 1 | 2 % | 1 |
| 10 Oct 10 | 30 | 19 | 8 | 42 % | 8 |
| 10 Oct 11 | 32 | 14 | 0 | 0 % | 0 |
| 10 Oct 12 | 59 | 37 | 25 | 68 % | 25 |
| 10 Oct 13 | 13 | 9 | 3 | 33 % | 3 |
| 10 Oct 14 | 393 | 234 | 29 | 12 % | 29 |
| 10 Oct 15 | 2 | 1 | 1 | 100 % | 1 |

Coverage drops in hours with large call-sync bursts (8 Oct 16–17, 9 Oct 16–17, 10 Oct 14): these are back-filled calls whose recordings have not been uploaded yet (inference). Recording files arrive in matching bursts (e.g. 120 files in 9 Oct 15 h).

---

## 4. Per user (last 48 h, calls ≥ 10 s, sync time)

Pattern legend: **(a)** never uploads · **(b)** blocked / bursty tail · **(c)** scattered gaps · **(d)** only short calls missing · **good**.

| User | Device ID | SIM calls | ≥10 s | Recordings | Coverage (≥10 s) | Pattern |
|---|---|---|---|---|---|---|
| sales.east1 (Shivani Labhe) | e67398e7e2ae4d11 | 466 | 193 | 7 | 4 % | (b)/(a): first recording only on 9 Oct 14:21 call, uploaded in a 4-second burst on 10 Oct 14:37 right after a login |
| bd2.sales (Shalinee Chauragade) | ba19c9b579bf1f66 | 271 | 125 | 43 | 34 % | (b): uploads in bursts right after logins (8 Oct 15:18, 9 Oct 16:31); 48 calls 9 Oct 11:03–16:43 without file |
| rsm.south1 (Gaurav Noukarkar) | da9bf67df998d008 | 195 | 90 | 58 | 44 % | (d): 93–100 % of 10–169 s calls covered; misses are < 10 s Completed calls; **duplicate uploads** |
| lfp3 (Shubham Sharma) | 00ebec42e2ca4454 | 194 | 134 | 0 | 0 % | **(a) never** (0 files ever) |
| sales.central (Rechal Mohan Bhalerao) | 039fe26bf37f5743 | 185 | 120 | 0 | 0 % | **(a)** |
| bd2.south (Manish Shahu) | a174fc004833bb1d | 173 | 93 | 78 | 80 % | good (2 tiny files) |
| rsm.east (Manish Gujwar) | 4de42514c70c46f1 | 171 | 92 | 0 | 0 % | **(a)** (0 logins since 5 Oct) |
| sales@ (Poornima Dwivedi) | c7c8bf895f6a2a9b | 169 | 124 | 0 | 0 % | **(a)** |
| asm.south (Abhishek Pillaey) | b9755a6a8bc48c60 | 124 | 61 | 44 | 72 % | good; 13 of 16 misses are calls ≥ 170 s |
| bd1.east (Rupa Bhattacharya) | b7afc00cfed8d183 | 115 | 77 | 30 | 39 % | (c)+bursts after logins (9 Oct 15:13, 10 Oct 12:03) |
| sm.sales (Harshal Tambekar) | 488d7bb58730106d | 85 | 57 | 0 | 0 % | **(a)** (phone syncs calls to 10 Oct 14:58) |
| rsm.south (Vishnu Nair) | b0e1def50a9762a3 | 85 | 46 | 37 | 78 % | good; 11 of 12 misses are calls ≥ 170 s |
| nagpursplashjet@gmail.com (Sonal Borkar) | 469b7127fd57ecec | 80 | 50 | 27 | 48 % | (c); recordings began only 9 Oct 15:51 after a login |
| exports.me (Aakif Khan) | 4374f1ac4f420cec | 73 | 17 | 0 | 0 % | **(a)** |
| rsm.central (Abhay Bhardwaj) | 0ded290533b4f663 | 66 | 36 | 26 | 69 % | (d) 8 of 10 misses < 10 s |
| asm2.south (Bhawesh Sanjay Shukla) | c3bddf4965d36c14 | 58 | 52 | 30 | 58 % | (c) |
| em.sales (Priyanshu Singh) | 3051c4ba5cbbaea8 | 51 | 32 | 24 | 72 % | (c): batch uploads (median ~155 min) |
| sales.eu (Vivek Laxmikant Shukla) | e180a5cf8f1f5d5d | 46 | 28 | 0 | 0 % | **(a)** |
| asm.north (Ankit Ingle) | dfcb7960f06d0ceb | 45 | 29 | 20 | 62 % | (c); no calls synced since 9 Oct 13:48 |
| sales.north (Charulata Damodhar Bokade) | fc0acd23eaf89cd5 | 22 | 17 | 0 | 0 % | **(a)** (16 logins since 5 Oct, no upload) |
| sales.east (Minal Thakur) | 9f385dd45a251bd8 | 21 | 17 | 3 | 18 % | 3 files in a 3-second burst on 8 Oct |
| exports.eur (Dipankar Bhojne) | 9ecc652526f21e4e / c9686c78e29fca77 | 20 | 9 | 0 | 0 % | **(a)**; uses two devices |
| erp@ (Danish Khan, test) | 54f82f836657c65b | 10 | 8 | 5 | 62 % | test user; recording bitrate 4× the norm |
| bd2.west (Vaijvi Sote) | 8a862a795f7083f6 | 10 | 8 | 0 | 0 % | **(a)** (9 logins in 3 days) |

Users with **no calls in the window and 0 recordings ever:** exports.eu (7f10ffb7e4e1c7b7; 414 calls last on 6 Oct), exports.am, exports.asia, exports.af, exports.southasia, exports@, temp@gmail.com, tl.west.

Note on denominators: the table above counts **every** call ≥ 10 s including calls ≥ 170 s, so it understates users such as rsm.south1 (see §2, §6).

### Recordings per call ≥ 10 s (by call time; calls ≥ 170 s are included, so the 170 s limit is not factored out)

| User | SIM calls | Calls ≥ 10 s | Recs | Ratio |
|---|---|---|---|---|
| rsm.south1 | 61 | 28 | 26 | 0.93 |
| rsm.central | 36 | 20 | 18 | 0.90 |
| bd2.south | 147 | 74 | 64 | 0.86 |
| rsm.south | 90 | 49 | 38 | 0.78 |
| em.sales | 53 | 34 | 26 | 0.76 |
| asm.north | 49 | 31 | 23 | 0.74 |
| asm.south | 125 | 61 | 44 | 0.72 |
| erp | 7 | 7 | 5 | 0.71 |
| asm2.south | 60 | 54 | 32 | 0.59 |
| nagpursplashjet | 76 | 46 | 27 | 0.59 |
| bd1.east | 84 | 53 | 30 | 0.57 |
| bd2.sales | 117 | 56 | 7 | 0.12 |
| sales.east1 | 200 | 87 | 7 | 0.08 |
| 11 other users (see pattern (a)) | — | — | 0 | 0.00 |

(This ratio uses the call-time window and a different 3-hour cut-off than §4, hence the different percentages for the same users.)

---

## 5. Missing recordings — patterns and causes

Expected-but-missing (Completed or ≥ 10 s, started before 10 Oct 12:10): **650 of 966 (67 %)**; 110 of the 650 are calls ≥ 170 s, 540 are shorter. (For 7 users the first recording came after the window start, so 650 overstates true failures; for calls of 10–170 s 305 of 762 are covered overall.)

### 5.1 Strong, countable correlations

1. **Duration ≥ 170 s is never recorded** (see §2).
2. **Some phones never upload** (pattern (a): 11 users; 377 of the 650 missing). Their calls sync, so permissions and credentials work; no Call Sync Failure rows; no upload attempts in the server log. Rsm.east, sm.sales and lfp3 have few or no logins, so it is not secret rotation. Probable cause: app build / app setting / recording feature on those phones (inference).
3. **Uploads come in bursts right after a login on some phones** (bd2.sales, bd1.east, nagpursplashjet, sales.east1):
   - bd2.sales: login 9 Oct 16:31 → upload bursts 16:31 and 16:40
   - bd1.east: login 9 Oct 15:13 → burst 15:15; login 10 Oct 12:03 → burst 12:03
   - nagpur: login 9 Oct 15:50 → burst 15:51
   - sales.east1: login 10 Oct 14:21 → burst 14:37
   - Phones that upload one call at a time (asm.north, asm.south, rsm.south) had no logins in the window.
   - Among recording-era calls, 73 % (11 of 15) were covered within 1 h after a login vs 44 % otherwise. **A login does not create a gap; it triggers the flush of the backlog.** Inference: those phones only flush recordings when the app is opened or re-authenticated.

### 5.2 Weak or confounded (treat as unproven)

- Direction (Incoming 49 % vs Outgoing 63 % in the recording era) — confounded by user mix and shorter incoming calls.
- Time of day (11–14 h 43–55 %, 15–17 h 63–68 %) — probably when users open the app.
- No Answer / Busy calls are not recorded (0 of 29) — not meaningful gaps.
- **Not factors:** number of logins, device changes, WhatsApp mix, Call Sync Failures (only `info@` has any).

### 5.3 Per-user missing patterns (expected calls, excluding the newest 3 h)

| User | Expected | Missing | Missing ≥170 s | Coverage 10–170 s | Pattern |
|---|---|---|---|---|---|
| rsm.east | 125 | 125 | 14 | 0/77 | (a) 0 files ever (baseline 0/23) |
| sm.sales | 47 | 47 | 5 | 0/40 | (a) (baseline 0/148) |
| lfp3 | 43 | 43 | 2 | 0/40 | (a) (0/104) |
| sales.central | 46 | 46 | 2 | 0/40 | (a) (0/82) |
| sales | 41 | 41 | 7 | 0/34 | (a) (0/78) |
| sales.eu | 23 | 23 | 5 | 0/14 | (a) (0/74) |
| sales.north | 19 | 19 | 0 | 0/17 | (a); 8 logins in window |
| exports.me | 11 | 11 | 1 | 0/8 | (a); 3 logins |
| exports.eur | 6 | 6 | 1 | 0/5 | (a); two devices |
| bd2.west | 4 | 4 | 0 | 0/4 | (a); 9 logins in 3 days |
| bd2.sales | 56 | 49 | 3 | 7/53 | (b) blocked tail + bursts; last recorded call 9 Oct 10:58, then all 48 calls 11:03–16:43 without file |
| sales.east1 | 80 | 73 | 3 | 7/73 | (b)/(a): 50 calls 8 Oct 15:03 → 9 Oct 14:03 without file; 18 more missing |
| bd1.east | 54 | 24 | 3 | 30/50 | (c)+pending; baseline 0/96; first recording 9 Oct 15:15 |
| nagpursplashjet | 48 | 22 | 9 | 24/37 | (c); baseline 0/136; first batch 9 Oct 15:51 |
| asm.north | 34 | 11 | 6 | 20/25 | (c)+long calls |
| asm2.south | 54 | 22 | 9 | 32/45 | (c) |
| asm.south | 54 | 16 | 13 | 38/41 | (c), mostly long calls |
| bd2.south | 64 | 11 | 6 | 51/55 | (c) small gaps |
| em.sales | 28 | 9 | 3 | 18/24 | (c): no recordings 8 Oct 17:38 → 9 Oct 09:54 and 9 Oct 19:19 → 10 Oct 11:53 |
| rsm.south | 43 | 12 | 11 | 30/31 | (c), long calls |
| rsm.central | 28 | 10 | 2 | 17/18 | (d) 8 of 10 misses < 10 s Completed |
| rsm.south1 | 50 | 24 | 3 | 25/25 | (d) all 21 short misses are < 10 s Completed |
| erp | 7 | 2 | 2 | 5/5 | (c) |
| sales.east | 1 | 0 | 0 | 1/1 | fine (2 calls only) |

**Blocked-queue theory ("app stops at first failure"):** weak evidence only. The stuck phantom calls are retried in a loop and neighbouring calls often *do* have files (asm.south 11435 has a file, 11436+ do not; asm2.south 11840 and 11854 have files), so blocking looks partial. The app has no failure log, so this cannot be proven from the server.

### 5.4 Missing calls ≥ 10 s by age (window)

Total 1,114; older than 3 h: 902. Under 18 h old: 308 · 18–24 h: 181 · 24–42 h: 149 · older than 42 h: 476.

Top users by missing calls ≥ 10 s: sales.east1 186 (126 excl. 3 h) · lfp3 134 · sales@ 124 · sales.central 120 · rsm.east 92 · bd2.sales 83 · sm.sales 57 (43) · rsm.south1 50 · bd1.east 47 · sales.eu 28 · nagpur 26 · asm2.south 22 · bd2.south 19 (16) · exports.me 17 · asm.south 17 (16).

Example missing calls (older than 3 h):

| Call Log | User | Call time | Dur | Type | Synced |
|---|---|---|---|---|---|
| M-ba19c9b579bf1f66-61912_1791283987853 | bd2.sales | 6 Oct 16:23 | 21 s | Outgoing | 8 Oct 15:19 |
| M-ba19c9b579bf1f66-61913_1791284070952 | bd2.sales | 6 Oct 16:24 | 128 s | Outgoing | 8 Oct 15:19 |
| M-ba19c9b579bf1f66-61917_1791288817440 | bd2.sales | 6 Oct 17:43 | 433 s | Incoming | 8 Oct 15:19 |
| M-039fe26bf37f5743-2307_1791440547012 | sales.central | 8 Oct 11:52 | 29 s | Outgoing | 8 Oct 16:57 |
| M-0ded290533b4f663-1395_1791450094827 | rsm.central | 8 Oct 14:31 | 176 s | Outgoing | 9 Oct 15:13 |
| M-4de42514c70c46f1-201_1791606738184 | rsm.east | 10 Oct 10:02 | 57 s | Incoming | 10 Oct 10:03 |
| M-e180a5cf8f1f5d5d-2585_1791451631696 | sales.eu | 8 Oct 14:57 | 221 s | Outgoing | 9 Oct 17:29 |
| M-fc0acd23eaf89cd5-39129_1791457482027 | sales.north | 8 Oct 16:34 | 50 s | Outgoing | 8 Oct 16:35 |
| M-e67398e7e2ae4d11-11952_1791450604361 | sales.east1 | 8 Oct 14:40 | 14 s | Outgoing | 8 Oct 17:00 |
| M-4374f1ac4f420cec-12233_1791437589448 | exports.me | 8 Oct 11:03 | 32 s | Outgoing | 9 Oct 10:21 |

---

## 6. Where it works well

Users with ≥ 90 % coverage on calls of 10–170 s (all single-device):

| User | Covered 10–170 s | All ≥10 s calls | Recordings | Median file | Logins in window | Device |
|---|---|---|---|---|---|---|
| rsm.south1 | 25/25 | 28 | 26 | 128 KB | 0 | da9bf67df998d008 |
| rsm.south | 30/31 | 49 | 38 | 181 KB | 0 | b0e1def50a9762a3 |
| rsm.central | 17/18 | 20 | 18 | 150 KB | 0 | 0ded290533b4f663 |
| asm.south | 38/41 | 61 | 44 | 288 KB | 0 | b9755a6a8bc48c60 |
| bd2.south | 51/55 | 74 | 64 | 78 KB (1.6 KB/s, half the usual bitrate) | 3 | a174fc004833bb1d |
| erp | 5/5 | 7 | 5 | 629 KB (15.7 KB/s) | 1 | 54f82f836657c65b |

Next tier (70–90 %): asm.north 80 %, em.sales 75 %, asm2.south 71 %.

**What the good phones have in common:** one call uploaded at a time, almost no logins, upload within a minute. Bitrate is ~3.9 KB/s (~31 kbps) for most users; rsm.central and rsm.south1 ~2.9 KB/s, bd2.south 1.6 KB/s, erp 15.7 KB/s — different recorder settings, app builds or phone models (inference). Comparing the *settings/app version* of these six phones with the 11 "never upload" phones is the fastest way to find the difference.

Server and data health (all good):
- Call sync works for everyone except `info@` (no role; 235 failed syncs in the window, all with empty reason).
- 500 of 501 recording files exist on disk with matching size; 0 zero-byte files; 0 files with missing hash; 0 orphan files.
- Disk 30 % used (171 GB free), `private/files` 2.2 GB.
- No exception on the upload endpoint in the window; gunicorn restarted once (8 Oct 12:51), no SIGKILL/OOM/timeouts; RQ queues currently empty.

---

## 7. Upload pipeline (server log analysis)

The upload log (`logs/recording_upload.log`, temporary diagnostic) covers only **8 Oct 14:24 → 10 Oct 15:14** (log rotation), so no earlier baseline is available from it. It records `received`, three `REJECTED` reasons and `STORED`; it has no failure/exception line.

### 7.1 Outcomes (2,121 unique lines)

| Outcome | Count |
|---|---|
| received | 1,059 |
| **REJECTED "no Call Log with this name"** | **680** |
| STORED | 382 |
| REJECTED "no read permission" | 0 |
| REJECTED "no audio_file" | 0 |

Only 36 % of attempts end in STORED; 64 % end in "not found". Per call (386 distinct): 315 stored on first attempt · 52 rejected first then stored later · 8 still missing with a Call Sync Failure row · 8 missing without one · 3 now exist but have no file yet.

### 7.2 Attempts per day

| Day | Received | STORED | Rejected |
|---|---|---|---|
| 8 Oct (from 14:24) | 71 | 35 | 39 |
| 9 Oct | 770 | 276 | 494 |
| 10 Oct (to 15:14) | 218 | 71 | 147 |

The 9 Oct 15:00–17:59 peak is mostly the retry storm.

### 7.3 Attempts per user

| User | Received | STORED | Rejected | Reject share |
|---|---|---|---|---|
| rsm.central | 262 | 26 | 236 | 90 % |
| asm2.south | 190 | 26 | 166 | 87 % |
| asm.south | 186 | 36 | 151 | 81 % |
| rsm.south | 67 | 31 | 36 | |
| nagpursplashjet | 59 | 27 | 32 | |
| bd2.south | 107 | 80 | 27 | |
| rsm.south1 | 77 | 57 | 20 | |
| info@ | 4 | 0 | 4 | 100 % |
| sales.east1 | 11 | 7 | 4 | |
| bd1.east | 32 | 30 | 2 | |
| asm.north | 16 | 15 | 1 | |
| erp | 6 | 5 | 1 | |
| bd2.sales | 24 | 24 | 0 | |
| em.sales | 18 | 18 | 0 | |

Every rejection has the same reason: the Call Log does not exist.

### 7.4 Phantom calls — the retry storm  ⚠

Call IDs the phone keeps uploading although **no Call Log, no Call Sync Failure row and no Deleted Document exists** for them:

| User | Phone-local call id | Attempts |
|---|---|---|
| asm.south | 11434 (`M-b9755a6a8bc48c60-11434_1791376491822`) | 149 |
| asm2.south | 11839 | 60 |
| asm2.south | 11857 | 63 |
| asm2.south | 11914 | 39 |
| rsm.central | 1299 | 57 |
| rsm.central | 1300 | 56 |
| rsm.central | 1301 | 55 |
| rsm.central | 1302 | 55 |
| rsm.south | 17013 | 34 |

≈ 770 attempts = ~73 % of all traffic, all rejected. asm.south 11434 and rsm.south 17013 were still being retried at 15:14 / 15:11 on 10 Oct. The phone believes these calls were synced; the server never created them (inference: dropped at call-sync time — to be investigated on the server side). The local ids have gaps (e.g. 11434 sits between 11433 and 11435).

### 7.5 The race (upload before the Call Log exists)

For all 52 calls that were rejected and later stored, the **first rejection came before the Call Log's creation time** (gap up to ~650 s, median ~4 s). Time from first reject to eventual store: median 130 s, p90 928 s, max 73,318 s (~20 h). Inference: the phone calls `receive_call_recording` right after `push_calls`, but the Call Log is only inserted by a background job, so the upload can arrive first. Retries fix it, but the first "not found" can stall the app's upload queue meanwhile.

### 7.6 Latency (File creation − Call Log creation, 501 files, no negatives)

| Bucket | Files |
|---|---|
| < 1 min | 224 |
| 1–10 min | 66 |
| 10–60 min | 59 |
| 1–6 h | 82 |
| > 6 h | 70 |

Percentiles over the 432 files of §3: p10 0.1 min · median 0.8 min · p90 ~1,209 min (~20 h) · p99 ~1,521 min · max ~1,524 min. **Bimodal: ~1 minute or ~a day late.**

Per user (median / p90 / max minutes):

| User | n | Median | p90 | Max | Pattern |
|---|---|---|---|---|---|
| asm.south | 44 | 0.4 | 0.6 | 0.8 | fast |
| rsm.south | 39 | 0.1 | 0.2 | 2.4 | fast |
| rsm.central | 26 | 0.4 | 0.8 | 11 | fast |
| asm2.south | 32 | 0.2 | 4.1 | 8.8 | fast |
| rsm.south1 | 58 | 0.2 | 1,210 | 1,210 | 31 under 1 min, 22 over 6 h (~20 h backlog) |
| bd2.south | 80 | 6.6 | 11.8 | 1,765 | mostly 1–10 min (queue-lag race), 5 over 6 h |
| bd1.east | 30 | 10.9 | 31.8 | | |
| sales.east1 | 7 | 15.1 | 15.2 | | |
| em.sales | 53 | 155 | 1,390 | | batch uploads |
| asm.north | 54 | 184 | 363 | | batch uploads |
| nagpursplashjet | 27 | 132 | 388 | | batch uploads |
| bd2.sales | 43 | 30 | 1,521 | | 25 in 10–60 min, 18 over 6 h |

Note: the Call Log creation time is the *sync* time, so latency measures sync → upload, not call → upload.

### 7.7 Gaps in the call sequence (queue-blocking evidence)

| User | Calls dur>0 | With file | Middle gaps | Tail | First gap | Last file | Tail starts |
|---|---|---|---|---|---|---|---|
| sales.east1 | 204 | 7 | 166 | 31 | 8 Oct 16:58 | 10 Oct 14:22 | 10 Oct 14:22 |
| bd2.sales | 193 | 43 | 102 | 48 | 8 Oct 15:18 | 9 Oct 16:31 | 9 Oct 16:31 |
| rsm.south1 | 97 | 42 | 55 | 0 | 8 Oct 17:29 | 9 Oct 14:52 | |
| bd1.east | 83 | 30 | 48 | 5 | 9 Oct 15:13 | 10 Oct 12:03 | 10 Oct 12:03 |
| nagpursplashjet | 54 | 26 | 28 | 0 | 9 Oct 09:27 | 9 Oct 16:01 | |
| asm2.south | 54 | 32 | 22 | 0 | 9 Oct 09:30 | 10 Oct 10:53 | |
| bd2.south | 97 | 77 | 20 | 0 | 9 Oct 15:21 | 10 Oct 14:57 | |
| asm.south | 62 | 44 | 18 | 0 | 8 Oct 15:05 | 10 Oct 14:49 | |
| rsm.south | 52 | 39 | 13 | 0 | 8 Oct 15:03 | 10 Oct 15:10 | |
| rsm.central | 38 | 26 | 12 | 0 | 9 Oct 15:12 | 9 Oct 17:42 | |
| asm.north | 35 | 23 | 10 | 2 | 8 Oct 15:57 | 9 Oct 13:30 | 9 Oct 13:48 |
| em.sales | 35 | 26 | 9 | 0 | 8 Oct 16:32 | 10 Oct 14:50 | |
| sales.east | 18 | 3 | 15 | 0 | 8 Oct 17:28 | 8 Oct 17:28 | |
| erp | 8 | 5 | 2 | 1 | 9 Oct 16:04 | 9 Oct 16:04 | 9 Oct 16:04 |

("Middle gap" = a no-file call created before the user's last call that has a file. Because creation time is sync time, a batch-synced backlog looks like a gap.)

Of 1,689 Call Logs with duration > 0 created in the window, 1,266 have no file; **only 3 of those 1,266 (all sales.east1) have any upload attempt in the log** — i.e. the phone never tries to upload them (not a server rejection). Users with calls but zero upload attempts in the whole log: lfp3 (143 calls with duration > 0), sales.central (132), sales@ (132), rsm.east (99), sm.sales (63), sales.eu (33), exports.me (21), sales.north (19), exports.eur (9), bd2.west (8), sales.east (15).

---

## 8. File quality and integrity

- 432 files on window-A calls (501 attached in the upload window): **all `.m4a`**, all private, 0 zero-byte, 0 missing on disk, 0 null content hash, 0 orphans (a File whose Call Log is missing).
- File size (432 files): p5 27 KB · p25 95.5 KB · median 164 KB · p75 282 KB · p95 509 KB · max 2.3 MB.
- Bytes per call-second (single-recording calls ≥ 10 s, n = 389): p1 1,573 · p5 1,612 · p25 2,993 · median 3,991 · p75 4,023 · p95 4,205 · p99 16,056 → nothing obviously truncated or oversized (≈ 4 KB/s, ~32 kbps).
- **Tiny files (probably empty/truncated — inference):**

| Size | Attached to |
|---|---|
| 209 B | M-a174fc004833bb1d-2268_1791529033164 (No Answer, 0 s; also 1 size mismatch: 219 B on disk vs 209 B in DB) |
| 1,440 B | M-469b7127fd57ecec-44547_1791458420232 (No Answer, 0 s) |
| 1,809 B | M-a174fc004833bb1d-2297_1791540403370 (2 s) |

- **Duplicates:** 13 calls have 2–3 files (10 with two, 3 with three), all from one device (rsm.south1, `da9bf67df998d008`, calls 2310…2324). 12 groups have identical size and hash. In the log 12 calls were STORED 2–3 times. Filenames carry repeated hex suffixes, e.g. `rec_1791535128245566cd5566cd5566cd5566cd5.m4a`. The server has **no idempotency check**, so every retry creates another File row.
- Recordings on very short calls: 5 on calls of 0–2 s (2 are 0-second No Answer, 3 tiny); 13 recordings on calls under 10 s in total (wasted upload slots). 0 recordings on WhatsApp rows; 0 on calls ≥ 170 s.

---

## 9. Sync failures and infrastructure

- **Call Sync Failure:** 892 rows overall (1 Oct → 9 Oct 16:01). In the window 235, **all from `info@splashjetink.com`** (device 5507a47bec6110f3; roles Central Co-ordinator + Employee only — missing the Call Tracker User role). 154 on 8 Oct from 17:02 (one row every ~10 s) and 81 on 9 Oct; every row has attempts = 3 and an empty reason; payloads are missed calls. Earlier failures: 416 on 5 Oct, 15 on 6 Oct. No other real user has a sync failure in the window.
- **Error Log in window:** 384 rows, 235 of them the same `info@` "Call Log insert failed after retries"; nothing about `receive_call_recording`, `save_file`, disk or permissions.
- **Workers:** gunicorn master restarted once (8 Oct 12:51); worker recycling ("autorestarting worker after current request") 6 / 13 / 13 times on 8 / 9 / 10 Oct; no TIMEOUT / SIGKILL / OOM.
- **Overload:** "Too many queued background jobs (550)" appears ~6,846 times in `web.error.log` (tracebacks without timestamps — cannot be windowed). The RQ failed registry holds 859 jobs in `default`, 850 of them QueueOverloaded, nearly all dated 5, 7 and 8 Oct (not the last 24 h). Queues now: `default` 0 queued, `long` 0, `short` 0.
- **Not readable:** nginx logs (permission denied), supervisor logs. HTTP status codes of uploads can therefore not be seen.

---

## 10. AI transcription (server side)

Settings: enabled, `auto_transcribe` on, min 10 s, daily limit 100, auto daily limit 200, summary model `gemini-3.5-flash-lite`, transcribe model `gemini-3.5-transcribe`.

| Status | Recordings in window | Eligible (≥ 10 s) |
|---|---|---|
| blank (never attempted) | 346 | 333 |
| Completed | 2 (manual button) | 2 |
| Failed | 0 | 0 |

- **Backlog:** 333 eligible recordings in the window untouched; **560** eligible recordings waiting since the auto marker (5 Oct 15:02).
- **Throughput limit:** the Scheduler job runs `0 0,15 * * *` (twice a day) with a batch of 10 → at most ~20/day vs ~130 new recordings/day, so it can never catch up.
- **Anomaly:** the job runs ~108 s each time (15:02:08 → 15:03:56 on 10 Oct) and the daily counter shows 16 claims for 10 Oct, yet no Call Log status changed and the first candidates (7 Oct) are still blank. Cause not found (needs a look at the job).
- **Gemini:** historically 44 Completed, 38 Failed (all dates): 16 connection errors to the old local endpoint (3–4 Oct), 8 × "429 Rate limit … 3 requests per minute on Free Tier", 7 empty transcripts, 3 × "Thinking is not enabled for this model", 1 incomplete. The 429 message suggests the key may be on a free tier — to be checked in the Google account.
- **Cost so far:** ≈ USD 0.255 for 44 completed calls (≈ 0.003–0.011 each).

---

## 11. Recommendations (for discussion)

### App team
1. **Investigate the ~165–170 s ceiling.** Check recorder max duration (e.g. `setMaxDuration`), any time/size limit in the upload, memory limits, chunking. About 600 long calls have no recording.
2. **Find why 11 phones never upload** (lfp3, sales.central, sales@, rsm.east, sm.sales, sales.eu, sales.north, exports.me, exports.eur, bd2.west, exports.eu): compare app version, recording setting/permission, battery optimisation, storage permission with the six good phones (§6). Device IDs are in §4/§6.
3. **Upload continuously, not only at app open / login** (bd2.sales, sales.east1, bd1.east, nagpur): background upload worker with retry; today a login triggers a burst after hours of backlog.
4. **Make retries smarter:** exponential back-off; stop (or flag) after N "not found" answers for the same call (phantom ids: ~770 wasted attempts); wait for the call to be confirmed synced before uploading its recording, or send the recording together with the call.
5. **Idempotent uploads:** send an idempotency key (call id + content hash); do not re-upload a recording the server already confirmed (rsm.south1 duplicates).
6. **Do not upload empty / tiny recordings** (< ~5 KB) and consider why 0–2 s No Answer calls produce files.
7. **Report in the sync payload:** app version, recorder settings, recording length and file size, upload attempt count, last upload error — the server currently cannot see why a phone is silent.
8. **Check short-call recording start** (1–9 s calls are recorded only 12–45 % of the time).
9. Clarify why Incoming calls are recorded less often than Outgoing.

### Server team
1. **Accept early uploads:** if the Call Log does not exist yet, keep the file and attach it when the call arrives (or answer with a retryable status instead of a "not found" error).
2. **Idempotent upload endpoint:** reuse the existing File when the same call already has a file with the same content hash.
3. **Find out why the 9 phantom calls were never created** (call-sync job, dedup, queue) — they have no Call Log and no failure row.
4. **Add explicit failure logging** to the upload path (reason, HTTP-level outcome) and keep the diagnostic log longer (it rotates after ~2 days).
5. **Transcription:** raise run frequency or loop until the backlog is cleared; look into the unexplained 16 claims; verify the Gemini billing tier.
6. Give `info@splashjetink.com` the Call Tracker User role (or stop its sync) — 235 failed syncs in 48 h.

---

## 12. Caveats

- The server cannot tell **why** a phone does not upload (permissions, battery optimisation, app version, recorder failure). The data only shows absence.
- "Missing" may include recordings still waiting in an upload queue: some phones upload ~24 h late, so a 3-hour cut-off is only a rough guide.
- Coverage by *sync time* mixes new calls with back-filled ones; coverage by *call time* is higher (~35 % vs 26.5 % for calls ≥ 10 s).
- Several numbers differ slightly between sections because the analyses used cut-offs a few minutes apart and different windows (sync vs call time) — conclusions are unaffected.
- Hour-of-day, direction and login correlations are observational and confounded by user mix.
- The window spans Thursday–Saturday morning; Saturday has little volume so far. Several users have no calls after 9 Oct evening (could be a day off or a sync issue).
- Recorded minutes were derived from `Call Log.duration`, not from the audio length; audio files were not opened.
- The 170 s ceiling is verified on counts (0 of 594 calls ≥ 170 s ever recorded; longest recorded call 165 s); its **cause is unknown**. It is not in the server code (searched for max-duration / max-size logic). Not checked: the site's `max_file_size` setting and nginx body-size limits (although a 170 s file would be only ~0.7 MB).
- Items marked "inference" are interpretations, not facts established from the data.
