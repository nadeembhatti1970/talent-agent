# Talent Agent

A six-role job search and application tailoring pipeline. The Researcher normalizes job listings, the Career Analyst scores them against a candidate profile, the CV Editor and Cover Letter Writer create tailored Word documents, the Document Reviewer checks them for role alignment and factual support, and the Summary Report Writer creates HTML and CSV trackers.

Each role uses OpenRouter's OpenAI-compatible Chat Completions API. The default model is `openrouter/free`, which routes requests to currently available free models. Override it with `OPENROUTER_MODEL` if needed.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

On Debian/Ubuntu, install into a virtual environment instead of the system Python (which may be marked as externally managed). With `uv`, the equivalent is:

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e .
cp .env.example .env
```

Then run the installed command from the project directory. To use `talent-agent` directly without typing `.venv/bin/`, activate the environment once in each terminal:

```bash
source .venv/bin/activate
talent-agent run --search
```

Alternatively, install the project as an editable uv tool with `uv tool install --editable .`; ensure `~/.local/bin` is on your `PATH`, then `talent-agent run --search` works without activating `.venv`.

Set `OPENROUTER_API_KEY` in `.env`. A free OpenRouter account and key are required. The key stays local and must never be committed.

## Run from Jupyter

Open [`notebooks/run_talent_agent.ipynb`](notebooks/run_talent_agent.ipynb) with a Jupyter kernel using the project's virtual environment, then run its cells from top to bottom. The notebook runs the equivalent of `talent-agent run --search`, using `config/search.json`, the default candidate CV, and credentials from `.env`; set `APIFY_API_TOKEN` and `APIFY_ACTOR_ID` in `.env` before running.

## Run with local job data

Supply job listings as a JSON array with `title`, `company`, `location`, `url`, and `description` fields (the CLI normalizes common scraper field names too). Include `employment_type` and `salary` or `salaryRange` for best results with the job type and compensation filters. The included candidate CV at `candidate/master_cv.docx` is the default; override it with `--cv` if needed. The LinkedIn profile URL is configured in `talent_agent/settings.py` and can be overridden with `--linkedin-url`.

```bash
talent-agent run --jobs examples/jobs.json
```

Artifacts are written under `outputs/YYYY-MM-DD/` by default. For repeat runs on the same day, pass `--run-id` to avoid replacing the prior run, for example `--run-id evening`.

## Output settings

The `output` object in [`config/search.json`](config/search.json) controls the output root, whether date-named subfolders are used, the date format, artifact filenames, tracker formats, and application document format. Defaults keep artifacts in `outputs/YYYY-MM-DD/`, write the raw and prioritized job data as JSON, create HTML and CSV trackers, and save tailored applications as DOCX. Application documents can also be written as Markdown (`md`).

CLI overrides are available, for example:

```bash
talent-agent run --jobs examples/jobs.json --output-dir ./job-results \
  --no-dated-subfolders --application-format md --tracker-format html
```

Use `--tracker-format html --tracker-format csv` to generate both tracker formats. Artifact filenames can be changed in the config file or with `--raw-jobs-filename`, `--prioritized-jobs-filename`, `--tracker-html-filename`, and `--tracker-csv-filename`.

## Search with Apify

The Researcher can call an Apify actor if `APIFY_API_TOKEN` is configured:

```bash
talent-agent run --search
```

Set `APIFY_ACTOR_ID` to an actor you have access to. The built-in request supports Curious Coder's [LinkedIn Jobs Scraper](https://apify.com/curious_coder/linkedin-jobs-scraper/input-schema) input (`keywords`, `location`, `datePosted`, and `limitPerSource`); date options are `past_24_hours`, `past_week`, `past_month`, and `any_time`. Store IDs in either `owner/actor` or `owner~actor` form are accepted. The Researcher starts actor runs asynchronously, polls for completion, then retrieves the dataset; this avoids holding a single synchronous request open while an uncapped search is running. Apify's own actor timeout and service limits still apply. For another actor, adjust the input body in `talent_agent/researcher.py`. LinkedIn access and scraping must follow the actor provider's terms and applicable site rules; the project does not automate browser login or bypass access controls.

## Search, workplace, and compensation settings

Edit [`config/search.json`](config/search.json) to set the keywords, `locations` list, employment types, workplace arrangements, pay filters, security-clearance exclusion, and optional `github_profile_url`. The GitHub URL can also be set as `GITHUB_PROFILE_URL` in `talent_agent/settings.py`. With a numeric `max_results`, the Researcher requests up to that many listings per configured location, interleaves and deduplicates results, then caps the combined raw listing set at that number. Set `max_results` to `null` to omit the result-limit fields from Apify requests and skip the local total cap. The actor or platform may still impose its own limits. Keywords can be a string or an array. `employment_types` accepts `contract`, `permanent`, or both. `workplace_arrangements` accepts `remote`, `hybrid` (partly remote), and/or `onsite`; it defaults to all three. For example, set it to `["remote"]` to include known remote jobs only. Listings with an unknown workplace arrangement are retained for manual review; known arrangements outside the selected list are excluded. Workplace type fields supplied by the source are preferred, with listing text used as a fallback. The same setting can be overridden for one run with repeatable `--workplace-arrangement` options. Contract listings use `minimum_day_rate`; permanent listings use `minimum_annual_salary`. Hourly contract rates are converted to a daily rate using an 8-hour workday before comparison. Amounts use the configured `currency`. A listing with no parseable pay amount is kept by default; set `exclude_unknown_compensation` to `true` to reject it when the corresponding minimum is active. `include_unknown_contract_compensation` can override this for contract roles, allowing listings without a stated rate through for manual review while still enforcing the minimum on known daily or converted hourly rates. Listings whose pay period is explicitly annual are not treated as unknown day-rate roles. Set `exclude_security_clearance` to `true` to drop listings that state a clearance is required; it defaults to `false` when omitted. The CLI option `--no-exclude-security-clearance` disables the config setting for one run. Filters also apply when jobs come from a local JSON file.

The Document Reviewer runs by default (`review_documents: true`). It checks the CV and cover letter against the job responsibilities, hiring company, original CV, and—when public profile content is readable—the LinkedIn profile. Public profile fetching is limited to HTTPS `linkedin.com/in/` pages, does not use login credentials or bypass access controls, and may be unavailable if LinkedIn requires sign-in. Unavailable profile content is explicitly marked as unverified; review still proceeds against the original CV. The reviewer flags unsupported or contradictory claims and generic role alignment, then the document writers get one revision attempt followed by a final review. If the documents are still not approved, the run stops without writing them as finished application documents; resume the run to apply the saved reviewer feedback. Model commentary or planning text in a draft is rejected and retried rather than included in the document. Review findings and LinkedIn verification status are saved in `agent_outputs/document_reviewer.json`. Set `review_linkedin_profile: false` or pass `--no-review-linkedin-profile` to skip fetching; set `review_documents: false` or pass `--no-review-documents` to skip document review.

CLI values override the file for that run, for example:

```bash
talent-agent run --search --keywords "Platform Engineer" --location "London" \
  --employment-type contract --min-day-rate 500
```

Repeat `--location` to search more than one place, for example `--location London --location Manchester`. The GitHub profile URL can be set in `config/search.json` as `github_profile_url` or overridden for one run with `--github-url`. The project default is empty; set the URL only if you want it included in generated application documents.

## Pipeline behavior

1. Researcher normalizes and deduplicates source listings into `raw_jobs.json`.
2. Employment type, workplace arrangement, and configured compensation filters are applied, then the Career Analyst returns a 0–100 match score and rationale per listing. Only scores at or above 70 are retained in `prioritized_jobs.json`.
3. CV Editor and Cover Letter Writer create one DOCX each per retained job in `applications/`. Documents contain no template labels, generated-document headings, or date placeholders. A configured GitHub profile URL is included in both documents. Prompts explicitly prohibit inventing candidate credentials.
4. Document Reviewer checks factual support and role/client alignment against the original CV, available public LinkedIn text, and listing. It can request one revision and records final findings.
5. Summary Report Writer creates `tracker.html` and `tracker.csv`, with external job links, relative links to generated files, and document-review status.

Empty or invalid JSON model responses are retried up to three times. If a run still stops on a model response, rerun it or select another OpenRouter model with `--model` or `OPENROUTER_MODEL`.

The pipeline requires a source of jobs. LinkedIn profile URLs are recorded as candidate context; the app does not fetch private profile data. Add candidate details and compensation preferences with CLI options or a candidate JSON file. Candidate CV and profile data are excluded from version control by `.gitignore`.

Runs save `researcher.json`, `career_analyst.json` (after each completed batch), `cv_editor.json`, `cover_letter_writer.json`, and `document_reviewer.json` under `outputs/YYYY-MM-DD/agent_outputs/`. The public LinkedIn profile text, when fetched, is cached in `linkedin_profile.json` for the run. To continue an interrupted run without repeating completed model work, pass its output directory to `--resume`, for example:

```bash
talent-agent run --resume outputs/2026-10-16
```

The saved run directory includes `run_state.json`, which records stage progress and verifies that the model, CV, candidate preferences, and raw listings still match. Keep the saved files local; application-agent JSON includes generated CV and cover-letter text. For legacy runs without checkpoints, `--jobs outputs/2026-10-16/raw_jobs.json` still reruns analysis from the saved listings.

Application drafts are also tagged with a prompt version. When the prompts are improved, resuming a run regenerates drafts made with an older prompt while continuing to reuse other completed agent work.

## Candidate JSON (optional)

```json
{
  "name": "Candidate Name",
  "linkedin_url": "https://www.linkedin.com/in/example/",
  "github_url": "https://github.com/example",
  "target_compensation": 90000,
  "currency": "GBP",
  "minimum_match_score": 70,
  "summary": "Platform engineer focused on AWS and Kubernetes"
}
```

Run `talent-agent --help` or `talent-agent run --help` for all options.
