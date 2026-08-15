# ConsultBae Assignment

## Overview

This repository is a take-home submission covering data integration across three messy source
systems, one no-code/low-code automation on top of that data, a miniature browser-based audio
collection app, a written data-quality report, and (optionally) a scaling stretch exercise. Each
task is documented separately below, based on what is actually implemented in this repository.

---

# Task 1 — Data Integration & Entity Resolution

### Objective

Ingest three CSV exports from different systems (Naukri applicants, gig workers, CBNexus contacts),
resolve the same real-world person appearing across multiple files into a single canonical record,
and load everything into one SQLite database — despite no source sharing a common ID field.

### Source datasets

| File | Rows (raw) | Fields available |
|---|---|---|
| `source1_naukri_applicants.csv` | 42 | Full Name, Email, Phone, City, Experience, Current CTC, Applied Date, Skills |
| `source2_gig_workers.csv` | 32 | email_id, worker_name, rate, location, status, skill_tags (**no phone column**) |
| `source3_cbnexus_contacts.csv` | 31 | Name, Phone Number, City, Verified, Projects Completed (**no email column**) |

### Source-specific cleaning (`ingest/merge_pipeline.py`)

- `clean_naukri()` — drops fully blank rows via `is_empty_row()`, then extracts name/email/phone/
  city/skills.
- `clean_gig_workers()` — drops blank rows, repairs one specific column-shifted row via
  `repair_gig_worker_row()` (a hardcoded match against that row's exact known-bad values — it is
  **not** a generic shift detector; a differently-shifted row would pass through uncorrected), then
  extracts name/email/city/skills. `phone` is always `None` for this source since `source2` has no
  phone column at all.
- `clean_cbnexus()` — drops blank rows, drops a row that is a duplicate of the CSV header embedded
  as data (`is_repeated_header()`), then extracts name/phone/city. `email` is always `None` for this
  source since `source3` has no email column.

### Normalization applied

| Field | Function | Logic |
|---|---|---|
| Phone | `normalize_phone()` | Strips all non-digit characters, removes a leading `91` or leading `0` when the result is longer than 10 digits, keeps the last 10 digits. Collapses `9000000268`, `919000000268`, `+91-9000000131`, and `09000000287`-style variants to one key. |
| Email | `normalize_email()` | Lowercases and trims. |
| City | `normalize_city()` | Lowercases/trims, then maps through a fixed alias table: `gurgaon → gurugram`, `bangalore → bengaluru`, `new delhi → delhi`, `delhi ncr → delhi`. **Note:** `Delhi NCR` (a metro region also covering Noida/Gurugram) is silently folded into plain `delhi` — a judgment call, not a data error. |
| Name | `normalize_name()` | Lowercases, trims, collapses internal whitespace. Used for matching only; not exposed to users. |
| Skill | `normalize_skill()` | Lowercases and trims each comma-split token. Skills are **not** merged across sources for the same person — the `skill` table keeps one row per `(person_id, skill, source_name)`, so the same normalized skill reported by two sources for one person appears twice, tagged by source. |

### Entity resolution logic (`resolve_people()`)

For each cleaned record, checked against every canonical person built so far, in this order:

1. **Exact email match** (`strong_match`, confidence `high`)
2. **Exact phone match** (`strong_match`, confidence `high`)
3. **Exact name + exact city match** (`strong_match`, confidence `medium`)
4. **Fuzzy name (`SequenceMatcher` ratio ≥ 0.90) + exact city match** (`probable_match`, fallback
   only, confidence `medium`)

A matched record fills in any missing canonical email/phone/city on the person but never overwrites
a value that's already set. Unmatched records become a new canonical person.

### Database (`db/schema.sql`)

Four tables: `person` (canonical identity + `match_confidence`), `source_record` (one row per raw
input row, `raw_json` preserves the original unmodified data, linked to a `person_id`), `skill`
(per-person, per-source skill tags), `audio_submission` (used by Task 3, linked to `person_id`).

### Validation — actual output from running `python ingest/merge_pipeline.py`

```
source1_naukri_applicants.csv: 42 rows
source2_gig_workers.csv: 32 rows
source3_cbnexus_contacts.csv: 31 rows

Cleaning Summary
Naukri: 42 records
Gig Workers: 31 records
CBNexus: 30 records

Issues
{'source': 'gig_workers', 'row': 12, 'issue': 'blank_row', 'action': 'skipped'}
{'source': 'gig_workers', 'row': 20, 'issue': 'repaired_shifted_columns', 'action': 'repaired'}
{'source': 'cbnexus', 'row': 16, 'issue': 'repeated_header', 'action': 'skipped'}

Total usable source records: 103
Canonical people created: 54

Database Validation
Canonical people: 54
Source records: 103
Skills: 328
Orphan source records: 0
Duplicate canonical emails: 0

Match Summary
new_person: 54
exact_email: 17
exact_phone: 26
name_city: 6
```

`fuzzy_name_city` (the conservative 0.90-similarity fallback) never fired on this dataset — every
non-`new_person` match was resolved by exact email, exact phone, or exact name+city.

### Known limitations

- **`person.match_confidence` is hardcoded to `"high"` for every person**, regardless of the actual
  match method that created it:
  ```python
  connection.execute(
      "...",
      (
          person["person_id"], person["canonical_name"], person["canonical_email"],
          person["canonical_phone"], person["city"],
          "high",   # always "high" — match_log's real per-record confidence is never used
          datetime.now().isoformat(),
      ),
  )
  ```
  `resolve_people()` computes real per-match confidence (`high`/`medium`/`new`) in `match_log`, but
  `insert_people()` never reads it. Anyone querying the DB for "matches to manually review" would
  currently get nothing, since all 54 people show `high`.
- **Ambiguous same-name case, verified against the code:** two `source3` rows are both "Arjun Mehta"
  in Noida with different phone numbers. One phone exact-matches `source1`'s Arjun Mehta (correct
  merge). The second, with an unrelated phone number, still merges into the same person via the
  `name_city` rule (same normalized name + same normalized city) — its differing phone number is
  discarded from the canonical record (kept only in that row's `raw_json`). This is a real,
  defensible-but-arguable behavior of the current matching logic, not a deliberately designed
  dedup rule for this specific pair.
- The shifted-row repair in `source2` is a hardcoded, single-case pattern match, not a general
  column-shift detector.
- CTC (`source1`), rate (`source2`), Applied Date (`source1`), status (`source2`), and Verified
  (`source3`) are preserved only in `raw_json` — not parsed, unit-normalized, or typed, since they
  aren't used for matching. See Task 4 for the full detail on each.

---

# Task 2 Documentation: AI Skill Categorization Automation using n8n + SQLite + Groq
### 1. Purpose
This document explains how to set up, run, and reuse the Task 2 automation for the ConsultBae AI Automation Assignment.
The automation reads candidate/person records from the SQLite database, sends each person's skills to an LLM using n8n, categorizes the person into one skill category, and writes the result back into the database.
Task 2 requirement:
> Build one working no-code/low-code automation using n8n, Make, or Zapier, connected to the project data. The chosen implementation uses n8n with an LLM step to auto-tag each person's skill category and write results back to the database.
---
### 2. Final Automation Summary
Tool used: n8n
Database used: SQLite
LLM provider used: Groq
Model used during testing:
```text
llama-3.1-8b-instant
```
Workflow file:
```text
automation/skill\_tagger.json
```
Database file:
```text
db/consultbae.db
```
Main output table:
```text
skill\_category
```
---
### 3. What the Workflow Does
The workflow performs the following steps:
Starts manually from n8n using the Execute workflow button.
Reads untagged people from the SQLite database.
Combines each person's available skills using `GROUP\_CONCAT`.
Sends the person's name, city, and skills to the LLM.
The LLM returns one category and a short reason.
The result is inserted into the `skill\_category` table.
The workflow can be safely rerun because it only selects people who have not yet been tagged.
---
### 4. Database Setup
Before running the automation, make sure the ingestion pipeline has been executed.
From the repository root:
```powershell
python .\\ingest\\merge\_pipeline.py
```
This creates the SQLite database at:
```text
db/consultbae.db
```
Expected output includes something like:
```text
Canonical people created: 54
Database created: db\\consultbae.db
```
---
### 5. Additional Table Required for Task 2
The original schema contains `person`, `source\_record`, `skill`, and `audio\_submission` tables. For Task 2, an additional table is used to store the AI-generated category.
Run this once from the repository root:
```powershell
python -c "import sqlite3; con=sqlite3.connect('db/consultbae.db'); con.execute('CREATE TABLE IF NOT EXISTS skill\_category (person\_id INTEGER PRIMARY KEY, category TEXT, reasoning TEXT, tagged\_at TEXT, FOREIGN KEY(person\_id) REFERENCES person(person\_id))'); con.commit(); con.close(); print('skill\_category table ready')"
```
Expected output:
```text
skill\_category table ready
```
---
### 6. Running n8n Locally with Docker
Start n8n from the repository root:
```powershell
docker run -it --rm --name n8n `
  -p 5678:5678 `
  -e NODE\_TLS\_REJECT\_UNAUTHORIZED=0 `
  -v "${PWD}:/repo" `
  -v n8n\_data:/home/node/.n8n `
  n8nio/n8n
```
Then open:
```text
http://localhost:5678
```
Why `/repo` is used:
The local repository is mounted inside the n8n Docker container as `/repo`, so n8n can access the SQLite database using this path:
```text
/repo/db/consultbae.db
```
---
### 7. Required n8n Setup
7.1 Install SQLite Community Node
The standard n8n setup may not include a built-in SQLite node. Install the SQLite community node from n8n:
```text
Settings/Gear icon → Community nodes → Install
```
Install package:
```text
n8n-nodes-sqlite3
```
After installation, restart n8n.
---
7.2 SQLite Credential
Create a SQLite credential in n8n with the database path:
```text
/repo/db/consultbae.db
```
This credential is used by both SQLite nodes in the workflow.
---
7.3 Groq Credential
Create a Groq API key from:
```text
https://console.groq.com/keys
```
Then create a Groq credential in n8n and select the model:
```text
llama-3.1-8b-instant
```
Any working Groq chat model can be used, but this model was used successfully during testing.
---
### 8. Workflow Structure
The workflow has these nodes:
```text
Manual Trigger
    ↓
SQLite: Read untagged people
    ↓
Basic LLM Chain
    ↓
SQLite: Insert/update skill category

Groq Chat Model
    ↓
Connected to Basic LLM Chain as model input
```
---
### 9. First SQLite Node: Read Untagged People
Node type:
```text
SQLite → Execute SQL Query
```
Query used:
```sql
SELECT 
  p.person\_id,
  p.canonical\_name,
  p.city,
  GROUP\_CONCAT(s.skill, ', ') AS skills
FROM person p
LEFT JOIN skill s ON p.person\_id = s.person\_id
LEFT JOIN skill\_category sc ON p.person\_id = sc.person\_id
WHERE sc.person\_id IS NULL
GROUP BY p.person\_id
LIMIT 10;
```
Why this query is safe:
It only selects people who are not already present in `skill\_category`.
The workflow can be rerun multiple times without tagging the same person again.
`LIMIT 10` keeps each run small and avoids LLM rate-limit problems.
If the API rate limit is hit, reduce this to:
```sql
LIMIT 5;
```
---
### 10. LLM Prompt
Node type:
```text
Basic LLM Chain
```
Prompt source:
```text
Define below
```
Prompt:
```text
Categorize this person into exactly one category:
Web Dev, Data, Automation-heavy, Backend, Mobile, Other.

Name: {{ $json.canonical\_name }}
City: {{ $json.city }}
Skills: {{ $json.skills }}

Return only JSON:
{
  "category": "...",
  "reasoning": "short reason"
}
```
Expected LLM output example:
```json
{
  "category": "Automation-heavy",
  "reasoning": "Core skills include n8n, workflow automation, and API orchestration."
}
```
---
### 11. Second SQLite Node: Write Tags Back
Node type:
```text
SQLite → Execute SQL Query
```
Important setting:
```text
Allow Expressions in Query (Unsafe): ON
```
Query expression:
```sql
INSERT OR REPLACE INTO skill\_category (person\_id, category, reasoning, tagged\_at)
VALUES (
  {{ $('Execute a SQL query').item.json.person\_id }},
  '{{ JSON.parse($json.text).category.replace(/'/g, "''") }}',
  '{{ JSON.parse($json.text).reasoning.replace(/'/g, "''") }}',
  datetime('now')
);
```
Why `INSERT OR REPLACE` is used:
If a person already has a tag, it can be updated.
If a person does not have a tag, a new row is inserted.
Why quotes are escaped:
Some LLM reasons may contain apostrophes.
`.replace(/'/g, "''")` prevents SQL errors caused by single quotes.
---
### 12. How to Run the Workflow
Start n8n using Docker.
Open `http://localhost:5678`.
Open the workflow.
Click Execute workflow.
The workflow processes up to 10 untagged people per run.
Repeat until the first SQLite node returns `0 items`.
In this project, the workflow was run until all 54 people were tagged.
---
### 13. How to Verify Results
Run this from the repository root:
```powershell
python -c "import sqlite3; con=sqlite3.connect('db/consultbae.db'); print(con.execute('SELECT COUNT(\*) FROM skill\_category').fetchone()\[0]); con.close()"
```
Expected output:
```text
54
```
To preview saved tags:
```powershell
python -c "import sqlite3; con=sqlite3.connect('db/consultbae.db'); rows=con.execute('SELECT person\_id, category, reasoning FROM skill\_category LIMIT 10').fetchall(); \[print(r) for r in rows]; con.close()"
```
---
### 14. Exporting the Workflow
In n8n:
```text
Top-right menu / three dots → Download → Workflow JSON
```
Save the exported file as:
```text
automation/skill\_tagger.json
```
This file should be committed to the repository.
---
15. Notes and Issues Encountered
Issue 1: SQLite Node Was Not Available by Default
The SQLite node did not appear initially in n8n.
Fix:
Installed the community node:
```text
n8n-nodes-sqlite3
```
---
Issue 2: OpenRouter Rate Limit
The first LLM setup used OpenRouter, but the free model hit a rate limit after around 40 records.
Fix:
Switched the LLM provider to Groq and continued processing the remaining untagged records.
---
Issue 3: Processing Too Many Rows at Once
Running all 54 people in one go can hit LLM provider rate limits.
Fix:
The SQLite query uses:
```sql
WHERE sc.person\_id IS NULL
LIMIT 10;
```
This allows the workflow to be safely rerun in small batches.
---
Issue 4: SQL Insert Initially Failed Due to n8n Expressions
The second SQLite node initially treated `{{ ... }}` as literal SQL text.
Fix:
Enabled:
```text
Allow Expressions in Query (Unsafe)
```
Then used a query expression to dynamically insert the LLM output.
---
### 16. Final Status
Task 2 is complete.
Final result:
```text
54/54 people tagged successfully
```
The workflow is reusable because it only processes untagged people and stores results in the `skill\_category` table.

---

# Task 3 — Mini Audio Collection App

### Objective

A web page where a person enters name + phone, submits an audio file, and the app extracts and
stores duration, sample rate, bitrate, and loudness, plus a rough quality note — matched against
the Task 1 canonical database.

### Implementation (`audio_app/app.py`, `audio_app/templates/submissions.html` — verified)

- **Flask app**, single file, SQLite connection via `sqlite3.Row` for dict-style row access.
- **Person lookup (`find_person`)** — reuses the same matching philosophy as Task 1: normalizes the
  submitted phone (strip non-digits, keep last 10) and tries an exact match against
  `person.canonical_phone` first; if no phone match, falls back to an exact match on the normalized,
  lowercased name against `person.canonical_name`. If neither matches, the submission is rejected
  with a flash message telling the user to check their name/phone against the Task 1 database — the
  app does **not** create a new person on the fly.
- **Upload handling** — accepts `.wav`, `.mp3`, `.ogg`, `.webm`, `.m4a`, `.flac`. Saves the file as
  `{person_id}_{timestamp}{extension}` under `audio_app/uploads/`, storing only the filename (not a
  full path) in the database.
- **FFmpeg/pydub integration** — `AudioSegment.converter` and `AudioSegment.ffprobe` are pointed at
  an explicit, hardcoded Windows path (a WinGet-installed FFmpeg build under
  `AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg.Shared_...`). **This makes the app
  Windows-machine-specific as written** — it will not run as-is on Linux, macOS, or another
  Windows machine without that exact FFmpeg install path, or without editing `FFMPEG_DIR` /
  putting `ffmpeg` on `PATH` first.
- **Metadata extraction (`get_audio_metadata`)**:
  - Duration and sample rate — read via `pydub.AudioSegment.from_file`.
  - Bitrate — a separate `ffprobe` subprocess call (`-show_entries stream=bit_rate`), independent of
    pydub.
  - Loudness — `AudioSegment.dBFS`, only computed if `audio.rms` is truthy.
- **Quality note (bonus, `get_quality_note`)** — a simple rule-based heuristic, not a trained model:
  flags `"Very short recording"` (< 1s), `"Long recording"` (> 120s), `"Low sample rate"`
  (< 16kHz), `"Very quiet"` (loudness < -35 dBFS), `"Very loud"` (loudness > -3 dBFS); returns
  `"OK"` if none apply.
- **Error handling** — if metadata extraction throws, the partially-saved file is deleted (with a
  `PermissionError` guard for Windows file locks) and the user sees a flash message with the
  exception text; nothing is written to the database on failure.
- **Storage** — one row per submission in `audio_submission`, linked to the matched `person_id`.
- **Submissions view (`/submissions`)** — lists every submission (newest first, via a `LEFT JOIN`
  on `person` for the canonical name), each with an HTML5 `<audio controls>` player served from
  `/uploads/<filename>`, and the five extracted properties (duration, sample rate, bitrate,
  loudness, quality note) in a simple grid layout.
- **Routes:** `GET /` (submission form — see caveat below), `POST /submit`, `GET /submissions`,
  `GET /uploads/<filename>` (via `send_from_directory`).

### Not verified

I have not seen `audio_app/templates/index.html` or `audio_app/static/`, so I can't confirm from
code review whether the submission page supports live in-browser recording (e.g. via the
`MediaRecorder` API) in addition to file upload, or file upload only. The backend (`/submit`) only
requires a `name`, `phone`, and a file under the `audio` form field — it doesn't care whether that
file came from a recorder or a file picker, so either is compatible with the backend as written.
**Please confirm and fill in which was implemented.**

### Running it

The app runs locally via Flask's development server (`app.run(host="127.0.0.1", port=5000,
debug=True)`) — there is no evidence in the reviewed files of a production deployment
(Render/Railway/Streamlit Cloud/ngrok config). Demo it running locally, or add deployment
config/instructions here if you did deploy it before recording your video.

---

# Task 4 — Data Issues Report

Reviewed all three source files row by row, cross-checked against `merge_pipeline.py` and
`schema.sql`, and confirmed exact row numbers by actually running the pipeline (output above).

### 1. Structural / file-level issues

**1.1 Blank row — `source2_gig_workers.csv`, row 12.** Fully empty across all 6 fields. Caught by
`is_empty_row()` and skipped, logged as `blank_row`.

**1.2 Duplicated header embedded as data — `source3_cbnexus_contacts.csv`, row 16.** A second copy
of the header row. Caught by `is_repeated_header()` and skipped, logged as `repeated_header`.

**1.3 Column-shifted row — `source2_gig_workers.csv`, row 20 (the "Isha Chopra" record).** Every
field is rotated one column over: `email_id` holds a skills string, `worker_name` holds an email,
`rate` holds a name, `location` holds an hourly rate, `status` holds a city, `skill_tags` holds
`"active"`. `repair_gig_worker_row()` detects this **exact** row by matching its known bad values
and un-rotates the columns, logged as `repaired_shifted_columns`.
**Limitation:** hardcoded pattern match against this one row's specific values — not a generic
shift detector. A differently-shifted row would silently pass through uncorrected.

### 2. Duplicate raw records within a source (not deduped before insertion)

**2.1 `source1` — "R. Verma" / "Rohit Verma".** Identical email, phone, city, experience, CTC,
applied date, and skills; only the name differs. `clean_naukri()` only filters blank rows, so both
rows are cleaned and inserted into `source_record`. They collapse to one `person` via `exact_phone`
matching, but the database keeps two raw `source_record` rows for the same person from the same
source.

**2.2 `source1` — Nikhil Chopra, two email variants.** `alt.nikhil.chopra70@example.com` vs.
`nikhil.chopra70@example.com`, otherwise identical. Same outcome: one canonical person, two
redundant `source_record` rows.

### 3. No shared identifier across sources

None of the three files share a primary key, and `source2` has no phone column while `source3` has
no email column at all — by design, `clean_gig_worker_row` hardcodes `phone: None` and
`clean_cbnexus_row` hardcodes `email: None`. This is why entity resolution needs the tiered
email → phone → name+city → fuzzy-name+city approach described in Task 1, rather than a single join
key.

### 4. Phone number format inconsistency — handled

Same number appears as `9000000268`, `919000000268`, `+91-9000000131`, `09000000287`.
`normalize_phone()` correctly collapses all four to the same 10-digit key.

### 5. Email casing inconsistency — handled

Several `source2` emails are stored uppercase while the same person's email elsewhere is lowercase.
`normalize_email()` lowercases and trims before comparison.

### 6. City name inconsistency — handled, including the ambiguous case

Casing (`GURGAON`/`Gurgaon`/`gurgaon`) and true aliasing (`Gurgaon`↔`Gurugram`,
`Bangalore`↔`Bengaluru`) are both handled by `normalize_city()`. **`Delhi NCR` is deliberately
collapsed into plain `delhi`**, even though NCR is a metro region also covering Noida and Gurugram —
a defensible but silent judgment call worth being able to explain.

### 7. Fields identified as messy but intentionally left unnormalized

Not used for matching, so preserved only inside `source_record.raw_json`, never parsed:

- **`source1` Current CTC mixes units** — most rows are full annual rupees, roughly a third are
  clearly in lakhs (e.g. `4.2`, `8.3`, `11.2` instead of six-digit values), with no unit column to
  disambiguate.
- **`source2` `rate` mixes hourly and monthly pay** (`1415/hr` vs. `15k/month`), no unit column.
- **`source1` Applied Date has 5+ formats** in one column (`24-07-2026`, `2026-08-08`,
  `7 Jul 2026`, `07/13/2026`, etc.).
- **`source2` `status`** (`Active`/`active`/`ACTIVE`/`Inactive`/`paused`) and **`source3`
  `Verified`** (`Y`/`N` vs. `Yes`/`No`, mixed casing) are not normalized into a consistent
  boolean/enum.

### 8. Skill vocabulary casing — handled, kept per-source by design

`source1` skills are Title Case, `source2` skill_tags are lowercase. `normalize_skill()` lowercases
and trims every token. Per the `skill` table schema, skills are **not** merged across sources for
one person — the same skill from two sources is kept as two rows (deduped only within
`person_id + skill + source`).

### 9. Ambiguous entity-matching cases

**9.1 Deepak Nair (`source2`) — correctly kept as two people.** Two gig-worker rows share the name
but have different emails and different cities (Bengaluru vs. New Delhi); neither exact nor fuzzy
city-matching succeeds between them, so they correctly end up as two separate `person` records.

**9.2 Arjun Mehta (`source3`) — silently merged despite a phone mismatch.** See Task 1's "Known
limitations" for the full trace — two rows with different phone numbers merge into one person via
the `name_city` rule because their normalized name and city both match. Genuinely ambiguous (typo
vs. two different people with the same common name), and the pipeline resolves it silently in one
direction.

### 10. Known pipeline limitation: `match_confidence` always `"high"`

Documented in full under Task 1. `insert_people()` hardcodes `"high"` instead of using the real
per-match confidence already computed in `match_log`.

### Summary

| Category | Status |
|---|---|
| Blank row, embedded header, shifted columns | Handled (shift-repair is case-specific) |
| Duplicate raw rows within a source | Collapse to 1 person via matching; raw duplication persists in `source_record` |
| Phone format variants | Handled |
| Email casing | Handled |
| City casing + aliasing + Delhi NCR collapse | Handled (Delhi NCR silently folded into "delhi") |
| CTC unit mismatch (lakhs vs. rupees) | Identified, not normalized (unused field) |
| Rate unit mismatch (hourly vs. monthly) | Identified, not normalized (unused field) |
| Date format variants | Identified, not normalized (unused field) |
| Status / Verified representation | Identified, not normalized (unused field) |
| Skill vocabulary casing | Handled, kept per-source (not merged across sources) |
| Deepak Nair (ambiguous same-name pair) | Correctly kept separate |
| Arjun Mehta (ambiguous same-name pair) | Silently merged despite phone mismatch |
| `match_confidence` hardcoded to `"high"` | Not yet fixed |

---

# Task 5 — Stretch (5,000 gig workers over a weekend)

**Not implemented.** `stretch.md` exists in the repository but is currently empty (0 bytes). If you
complete this before submitting, replace this section with your actual write-up — objective,
what breaks first, what you'd change before launch, and why — following the same
implemented-vs-not honesty standard as the rest of this README.

---

# Project Structure

```
consultbae-assignment/
├── data/
│   ├── source1_naukri_applicants.csv
│   ├── source2_gig_workers.csv
│   └── source3_cbnexus_contacts.csv
├── db/
│   ├── schema.sql
│   └── consultbae.db
├── ingest/
│   └── merge_pipeline.py
├── automation/
│   └── skill_tagger.json
├── audio_app/
│   ├── app.py
│   ├── static/
│   ├── templates/
│   │   ├── index.html
│   │   └── submissions.html
│   └── uploads/
├── .gitignore
├── README.md
├── requirements.txt
└── stretch.md
```

---

# Setup & Installation

Python version is not pinned in the repository (no `runtime.txt` / `.python-version` found) — any
Python 3.9+ should work with the package versions below.

**1. Clone and enter the repo**
```powershell
git clone https://github.com/twerkborne123/consultbae-assignment.git
cd consultbae-assignment
```

**2. Create and activate a virtual environment**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**3. Install dependencies** (from `requirements.txt`):
```
blinker==1.9.0
click==8.4.2
colorama==0.4.6
Flask==3.1.3
itsdangerous==2.2.0
Jinja2==3.1.6
MarkupSafe==3.0.3
pydub==0.25.1
Werkzeug==3.1.8
```
```powershell
pip install -r requirements.txt
```

**4. Install FFmpeg** — required by `audio_app/app.py` for `pydub`, but not installable via pip. As
written, `app.py` points at a hardcoded WinGet install path
(`...\WinGet\Packages\Gyan.FFmpeg.Shared_...\bin`). Either install FFmpeg via that exact WinGet
package on Windows, or edit `FFMPEG_DIR` in `app.py` to point at wherever `ffmpeg.exe` /
`ffprobe.exe` are on your machine.

**5. Database** — `ingest/merge_pipeline.py` deletes and recreates `db/consultbae.db` from
`db/schema.sql` on every run, so no manual database setup is needed.

---

# Running the Project

**Task 1 — build the merged database:**
```powershell
python ingest\merge_pipeline.py
```
Produces `db/consultbae.db` and prints the cleaning/validation/match summary shown in Task 1 above.

**Task 3 — run the audio app** (requires Task 1 to have been run first, since it matches submissions
against the `person` table):
```powershell
python audio_app\app.py
```
Serves at `http://127.0.0.1:5000`. Submit at `/`, view all submissions at `/submissions`.

**Task 2 — automation:** import `automation/skill_tagger.json` into n8n/Make/Zapier (whichever tool
it targets — see the caveat in Task 2 above) and follow that tool's run instructions. Add exact
steps here once confirmed.

---

# Technologies Used

- **Python** — pipeline and web app
- **Flask 3.1.3** — web framework for the audio app
- **SQLite** (via Python's built-in `sqlite3`) — single database for both Task 1 and Task 3
- **pydub 0.25.1** + **FFmpeg** (external binary, not pip-installable) — audio metadata extraction
- Python standard library: `csv`, `json`, `re`, `difflib.SequenceMatcher`, `pathlib`, `datetime` —
  used throughout `merge_pipeline.py` for parsing, normalization, and entity resolution
- **n8n — workflow automation / low-code orchestration
SQLite — database for candidate/person records and skill categories
Groq API — LLM inference provider
Llama 3.1 8B Instant — LLM used for skill categorization
n8n LangChain / Basic LLM Chain — connects the workflow to the LLM
SQL — querying untagged candidates and writing categories back
Docker — used to run n8n locally in a consistent environment
- **Git / GitHub** — version control and submissions
