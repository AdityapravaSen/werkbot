# werkbot: Build Plan

Goal: one command that collects English-speaking Werkstudent/HiWi jobs in Germany, removes duplicates, scores each job against my resume, and produces a tailored LaTeX resume and cover letter for the jobs I pick. Everything should be free.

**Pipeline:** scrape → normalize → **deduplicate** → filter → score → tailor → deliver

---

## 1. Core design decision: avoid HTML scraping where possible

Many sites have a free API, a feed, or a hidden request behind their "Load more" button. Use those first. Parse HTML only when there is no other way, and use a real browser (Playwright) only as a last resort.

**Rule:** every source gets a small adapter that returns the same `Job` object. That keeps V1 simple and makes V2 (paste any link) easy.

---

## 2. Job sources (14 sites, grouped)

Dropped: datacareer, absolventa, academics.de.

### Group A: API or feed (easiest, most stable)

| # | Site | URL | How we get the jobs | Notes |
|---|---|---|---|---|
| 1 | Arbeitnow | https://www.arbeitnow.com/?search=working+student&date_posted=7d | Public JSON API: `https://www.arbeitnow.com/api/job-board-api` | Returns `title`, `description`, `location`, `created_at`, `url`, `job_types`, `remote`. No search parameter, so fetch every page, then filter for "working student" and the last 7 days ourselves. |
| 2 | Arbeitsagentur | https://www.arbeitsagentur.de/jobsuche/suche?suchbereich=jobs&was=Werkstudent%2Fin&wo=Kaiserslautern&umkreis=200&veroeffentlichtseit=7&branche=11 | Public Jobsuche REST API (the one the website itself uses) | Parameters: `was`, `wo`, `umkreis=200`, `veroeffentlichtseit=7`, plus paging. |
| 3 | Ramstein AF (softgarden) | https://ramsteinaf.career.softgarden.de/en/ | JSON feed: `https://ramsteinaf.career.softgarden.de/jobs.feed.json` | Filter category (Engineering/technical, IT) and location (Ramstein-Miesenbach, Kaiserslautern) from the feed data. Local national positions. |
| 4 | WeAreDevelopers | https://www.wearedevelopers.com/jobs?q=working+student&country=DE | The "Load more" button calls `https://www.wearedevelopers.com/jobs.turbo_stream?country=DE&q=working+student&page=N` | Request page 2, 3, … until the response is empty. No clicking needed. |

### Group B: Plain HTML with page numbers (simple scraping with httpx, no browser)

| # | Site | URL | How we get the jobs | Notes |
|---|---|---|---|---|
| 5 | connecticum | https://www.connecticum.de/jobs?details=&page=1&beschaeftigungsarten%5B0%5D=6&fieldsgruppen%5B0%5D=11&einsatzort=Kaiserslautern%2C+Germany&umkreis=200&volltext= | Loop `?page=N` until there is no "Weiter" (next) link | 20 jobs per page. Each listing shows a date. Job links follow `/jobs/{slug}-{jobNumber}`. |
| 6 | e-fellows | https://jobs.e-fellows.net/jobs/it/werkstudent-duales-studium?page=1 | Loop `?page=N` | Dates are relative ("vor 1 Monat", i.e. "a month ago"), so convert them to real dates. |
| 7 | Fraunhofer | https://jobs.fraunhofer.de/search/?q=&optionsFacetsDD_customfield5=Rhineland-Palatinate&optionsFacetsDD_lang=en | Parse the server-rendered results table | Posted date is on each job's detail page. Fraunhofer jobs also appear on connecticum (see section 3). |
| 8 | germantechjobs | https://germantechjobs.de/en | Scrape all jobs (about 800, all in the HTML) | The English and part-time filters run in the browser and aren't in the URL, so we apply both ourselves after scraping. |
| 9 | workingstudentjobs.de | https://www.workingstudentjobs.de/?city=mannheim,heidelberg,frankfurt&state=RP,SL&field=tech | Parse HTML, follow Next links | Job links follow `/jobs/{company}-{title}-{8-char id}`. |

### Group C: Loaded by JavaScript (inspect network requests first)

| # | Site | URL | Status |
|---|---|---|---|
| 10 | foorilla | https://foorilla.com/hiring/ (filters: Part-time, English, Germany) | Jobs aren't in the HTML. Find the hidden request the filters send (DevTools → Network → Fetch/XHR). Fall back to Playwright if there isn't one. |
| 11 | DFKI | https://jobs.dfki.de/ausschreibungen.html#filter=p_web_site_id%3D6635%26p_published_to%3DWWW%26p_language%3DDEFAULT%26p_direct%3DY%26p_format%3DMOBILE%26p_include_exclude_from_list%3DN%26p_category_code_arr%3D23427-662551%26p_search%3D | The jobs table is empty in the HTML. The `#filter=…` parameters suggest a JSON call behind it. Find it, with Playwright as the fallback. |

### Group D: Page watchers (no job listings)

| # | Site | URL | What to do |
|---|---|---|---|
| 12 | MPI-INF Computer Vision and ML | https://www.mpi-inf.mpg.de/departments/computer-vision-and-machine-learning/application/hiwi-/-student-assistants | General call only ("limited HiWi positions", apply via d2hiwiapplication[at]mpi-inf.mpg.de). Store a hash of the page text and alert when it changes. **Also a cold-email target.** |
| 13 | MPI-INF Visual Computing and AI | https://www.mpi-inf.mpg.de/departments/visual-computing-and-artificial-intelligence/offers | No HiWi openings listed (only thesis, PhD and postdoc). Watch the page for changes. **Cold-email target.** |

### Group E: LinkedIn

| # | Site | How we get the jobs | Searches |
|---|---|---|---|
| 14 | LinkedIn | `python-jobspy`, which reads LinkedIn's public (logged-out) job pages. There is no free official job-search API. | (1) "working student" near Kaiserslautern, `distance=100` (miles, about 160 km), `hours_old=168`. (2) "working student", location Germany, `is_remote=True`. |

Keep LinkedIn light: about 2 searches a day, logged out, never with a personal account.

### Source config

All sources live in `data/sources.yaml` (name, group, adapter, URL, parameters, enabled flag), so adding or removing a site is a config change, not a code change.

### Expectations

- **German-language boards** (connecticum, e-fellows, Arbeitsagentur) mostly carry German ads. The English filter will remove most of them. They're still worth including, but expect few results from each.
- **"Load more" buttons:** we almost never click them. We find the request the button sends and repeat it with the next page number, which is faster and doesn't break when the design changes.

---

## 3. Deduplication

The same job can appear several times: on two boards (Fraunhofer on its own site and on connecticum), twice on one board, or reposted with a new ID. Deduplication runs **before** filtering and scoring, so no LLM calls are wasted on duplicates.

### 3.1 Normalize first

Normalize these fields before comparing anything:

- **URL:** lowercase the host, remove tracking parameters (`utm_*`, `ref`, `trk`, `refId`) and the trailing slash.
- **Title:** lowercase; remove gender tags (`(m/w/d)`, `(w/m/d)`, `(f/m/d)`, `(all genders)`, `(gn)`, `:in`, `/-in`) and punctuation; collapse spaces.
- **Company:** lowercase; remove legal forms (`GmbH`, `AG`, `SE`, `KG`, `e.V.`, `Inc.`, `Ltd.`); map known aliases (e.g. `Fraunhofer-Gesellschaft` and `Fraunhofer IESE` → `fraunhofer`).
- **City:** lowercase; normalize umlauts (`ü` → `ue`); map `remote`, `homeoffice` and `deutschlandweit` to `remote`.

### 3.2 Four levels of duplicate checks

| Level | What it catches | Rule |
|---|---|---|
| 1. Same URL | The same job seen twice in one run or across runs | `url_hash = sha256(normalized_url)`, unique in the DB |
| 2. Same source ID | The same job with a different URL (e.g. tracking parameters) | `(source, source_job_id)`, unique |
| 3. Same fingerprint | The same job on different boards | `fingerprint = sha256(norm_company + norm_title + norm_city)`, unique |
| 4. Fuzzy match | Slightly different titles, e.g. "Working Student Data Science (m/w/d)" vs "Werkstudent Data Science" | Same normalized company, same city (or one is remote), and `rapidfuzz.token_set_ratio(title_a, title_b) >= 90`. Borderline cases (85–90) are confirmed when the description embeddings have cosine similarity ≥ 0.95. |

Level 4 only compares jobs from the same company, so it stays fast even with thousands of jobs.

*As built (Phase 2):* `token_set_ratio >= 90` alone merged different jobs in testing: it is 100 whenever one title's words are a subset of the other's ("Werkstudent" vs "Werkstudent Data Science"), and above 90 when titles differ in one short word ("Werkstudent HR Backend" vs "Werkstudent Design Backend" = 93). So level 4 looks at the words that differ: same words in any order; or only extra words, with `token_sort_ratio >= 80`; or differing words that are spelling variants (`ratio >= 80`). It never merges two listings from the same board, and groups are only joined if every pair across them matches. Until embeddings exist (Phase 4), borderline titles (85–89) need near-identical description text. Details in `progress.md`.

### 3.3 Merge instead of delete

When duplicates are found, keep **one main record** and store all links to it:

- **Main URL priority:** the company's own careers page (Fraunhofer, softgarden, DFKI) > API boards > aggregators (LinkedIn, connecticum).
- **Description:** keep the longest, most complete one.
- **Date posted:** keep the earliest.
- **All sources:** every board and link is stored in a `job_sources` table, so the output can show "seen on: Fraunhofer, connecticum".

### 3.4 Across runs (daily)

- **Upsert:** if a job already exists, update `last_seen_at`; never create a second row.
- **"New" means new:** only jobs whose `first_seen_at` is today go into the daily digest.
- **Reposts:** a job that matches an old fingerprint with a new ID is marked `reposted`, not new.
- **Expiry:** jobs not seen for 14 days are marked `expired` and hidden.

### 3.5 Database tables

```
jobs         (id, fingerprint UNIQUE, title, company, city, remote, description,
              date_posted, first_seen_at, last_seen_at, status, main_url)
job_sources  (id, job_id → jobs.id, source, source_job_id, url, url_hash UNIQUE,
              UNIQUE(source, source_job_id))
scores       (job_id, fit_score, reason, scored_at)
applications (job_id, status, applied_at, notes)
```

### 3.6 Final check

After each run, a test query confirms there are no duplicate `fingerprint` or `url_hash` values in the DB. If there are any, the run is logged as failed so the bug gets noticed.

---

## 4. V1: step by step

### Phase 0: Setup (about 1 evening)
1. Python repo with a `pydantic` `Job` model: `title`, `company`, `location`, `description`, `url`, `date_posted`, `source`, `source_job_id`, `language`, `remote`.
2. SQLite database with the tables from section 3.5.
3. **Master resume in YAML:** every bullet, project and skill, each tagged. This is the single source of truth for tailoring.
4. `data/sources.yaml` listing the 14 sources from section 2.

### Phase 1: Scrapers (2–3 evenings)
5. Group A adapters first: Arbeitnow, Arbeitsagentur, softgarden feed, WeAreDevelopers.
6. Group B adapters: connecticum, e-fellows, Fraunhofer, germantechjobs, workingstudentjobs.
7. Group E: LinkedIn via `python-jobspy`.
8. Group C: inspect foorilla and DFKI network requests, then build the adapters (Playwright fallback).
9. Group D: page watchers for the two MPI pages.
10. Run everything with one command: `werkbot scrape`.

### Phase 2: Deduplication (1 evening)
11. Normalizer functions (URL, title, company, city).
12. Levels 1–4 from section 3.2, plus the merge rules and upsert logic.
13. Tests with real duplicate examples (Fraunhofer on its own site vs connecticum).

### Phase 3: Filtering (1 evening)
14. **Language:** detect the description's language with `lingua`. Reject jobs that ask for German, e.g. "fließend Deutsch", "Deutschkenntnisse", "Deutsch C1".
15. **Location:** remote, or within about 100 km of Kaiserslautern (hardcoded city list or geocoding).
16. **Role:** keyword filter for Werkstudent, HiWi, working student, part-time / Teilzeit.

*As built:* all rules live in `data/filters.yaml`. Location = 200 km straight line **or** ≤ 3 h by train (a short list of fast DB connections), remote anywhere in Germany, hybrid outside the area → review; places come from an offline GeoNames table. Language = the German *level* the ad asks for (fluent → drop, good / a plus → keep), not the word "Deutsch". Descriptions missing from list pages are fetched from the job pages first (JSON-LD, microdata, main text). Each job ends as keep / review / drop with its reasons. Details in `progress.md`.

### Phase 4: Scoring (2 evenings)
17. Call it a **fit score**, not a "chance of getting hired". No tool can predict hiring. Hybrid score:
    - **Semantic similarity (40%):** embed the resume and the description locally with `sentence-transformers` (e.g. `bge-small`).
    - **Requirement coverage (45%):** an LLM extracts must-have and nice-to-have requirements as JSON, then checks each against the master resume.
    - **Hard penalties (15%):** German required, onsite too far away, wrong level.
18. Store the score with a one-line reason, e.g. "missing: Kubernetes, AWS".

### Phase 5: Tailoring (2–3 evenings)
19. Jinja2-templated LaTeX resume and cover letter.
20. The LLM only **selects, reorders and rewords** bullets from the master YAML. It never adds skills that aren't there.
21. Compile with `tectonic` (single free binary, no full TeX Live install).
22. Output both the `.pdf` and the `.tex` file for hand-editing.

### Phase 6: Delivery and scheduling (1–2 evenings)
23. **Telegram bot:** daily digest of new jobs with buttons ("Tailor CV", "Skip", "Applied"); "Tailor CV" makes the bot reply with the PDFs.
24. **Scheduling:** daily run via GitHub Actions cron (free) or local cron in WSL.

**Total V1 estimate:** roughly 10–13 evenings.

---

## 5. Free tool stack

| Need | Tool |
|---|---|
| Language | Python |
| HTTP and parsing | `httpx`, `selectolax` / BeautifulSoup |
| JavaScript-heavy sites | `playwright` |
| LinkedIn | `python-jobspy` |
| Page text cleanup before LLM | `trafilatura` |
| Data models and validation | `pydantic` |
| Deduplication | `hashlib` (fingerprints), `rapidfuzz` (fuzzy titles), embeddings (borderline cases) |
| LLM | Gemini Flash (free tier), Groq (free, Llama models), or Ollama locally |
| Embeddings | `sentence-transformers` (local) |
| Language detection | `lingua` |
| Storage | SQLite |
| LaTeX | Jinja2 + `tectonic` |
| Output | `python-telegram-bot` |
| Scheduling | GitHub Actions cron or local cron |
| Resume PDF parsing (V2) | `pymupdf` |

**LLM note:** free tiers change. Reports say that since April 2026 the Gemini free tier covers Flash models only, with Pro paid-only. Check current limits before relying on any provider, and put the LLM behind one small interface so providers can be swapped.

---

## 6. Output: Telegram vs website

**V1: Telegram.**
- Free, and pushes to the phone.
- Inline buttons act as the interface; no frontend to build.
- Also covers most of V2: paste a link into the chat, or send a resume PDF to the bot.

**V2.5 (optional): website.** Streamlit, hosted free on Hugging Face Spaces. Only worth it for a sortable, filterable table of all jobs, or to share the tool with friends.

---

## 7. V2: making it generic

1. **Paste any link:** check the domain for a known job platform and use its adapter. Otherwise fall back to Playwright, extract the text, and have the LLM turn it into a `Job`.
2. **Upload a resume:** parse the PDF with `pymupdf`, and have the LLM convert it into the master YAML format. Review it once manually.
3. **Per-user config:** move location, language rules, target roles and sources into a config file, with one database per user.
4. **Application tracking (optional):** record status (applied, interview, rejected) to see which sources and score ranges actually lead to interviews.

---

## 8. Things to watch

- Rate-limit politely and respect robots.txt. Keep LinkedIn light and logged out.
- Keep API keys and resume data in GitHub Secrets or a private repo.
- Cold outreach to RPTU chairs, DFKI groups and the MPI-INF departments is the strongest channel and doesn't come from job boards.

---

## 9. Next steps

1. Inspect network requests for foorilla and DFKI (Group C).
2. Share the current resume (PDF or LaTeX) to build the master YAML.
3. Set up the Phase 0–1 repo, starting with Group A.

---

## Sources

- [Gemini API pricing changes – agentdeals.dev](https://agentdeals.dev/gemini-api-pricing-changes)
- [Groq free tier – Groq community](https://community.groq.com/t/is-there-a-free-tier-and-what-are-its-limits/790)
- [Best Free AI APIs in 2026 – toolhalla](https://toolhalla.ai/blog/best-free-ai-apis-2026)