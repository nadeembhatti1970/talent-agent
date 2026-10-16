# Talent Agent

A five-role job search and application tailoring pipeline. The Researcher normalizes job listings, the Career Analyst scores them against a candidate profile, the CV Editor and Cover Letter Writer create tailored Word documents, and the Summary Report Writer creates HTML and CSV trackers.

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

Set `APIFY_ACTOR_ID` to an actor you have access to. The built-in request supports Curious Coder's [LinkedIn Jobs Scraper](https://apify.com/curious_coder/linkedin-jobs-scraper/input-schema) input (`keywords`, `location`, `datePosted`, and `limitPerSource`); Store IDs in either `owner/actor` or `owner~actor` form are accepted. For another actor, adjust the input body in `talent_agent/researcher.py`. LinkedIn access and scraping must follow the actor provider's terms and applicable site rules; the project does not automate browser login or bypass access controls.

## Search and compensation settings

Edit [`config/search.json`](config/search.json) to set the keywords, `locations` list, employment types, pay filters, and optional `github_profile_url`. The GitHub URL can also be set as `GITHUB_PROFILE_URL` in `talent_agent/settings.py`. The Researcher searches each location separately and divides `max_results` across those searches; the combined results are deduplicated and capped at `max_results` total. Keywords can be a string or an array. `employment_types` accepts `contract`, `permanent`, or both. Contract listings use `minimum_day_rate`; permanent listings use `minimum_annual_salary`. Amounts use the configured `currency`. A listing with no parseable pay amount is kept by default; set `exclude_unknown_compensation` to `true` to reject it when the corresponding minimum is active. `include_unknown_contract_compensation` can override this for contract roles, allowing listings without a stated rate through for manual review while still enforcing the minimum on known day rates. Listings whose pay period is explicitly annual are not treated as unknown day-rate roles. Filters also apply when jobs come from a local JSON file.

CLI values override the file for that run, for example:

```bash
talent-agent run --search --keywords "Platform Engineer" --location "London" \
  --employment-type contract --min-day-rate 500
```

Repeat `--location` to search more than one place, for example `--location London --location Manchester`. The GitHub profile URL can be set in `config/search.json` as `github_profile_url` or overridden for one run with `--github-url`. The project default is empty; set the URL only if you want it included in generated application documents.

## Pipeline behavior

1. Researcher normalizes and deduplicates source listings into `raw_jobs.json`.
2. Employment type and configured compensation filters are applied, then the Career Analyst returns a 0–100 match score and rationale per listing. Only scores at or above 70 are retained in `prioritized_jobs.json`.
3. CV Editor and Cover Letter Writer create one DOCX each per retained job in `applications/`. Documents contain no template labels, generated-document headings, or date placeholders. A configured GitHub profile URL is included in both documents. Prompts explicitly prohibit inventing candidate credentials.
4. Summary Report Writer creates `tracker.html` and `tracker.csv`, with external job links and relative links to generated files.

Empty or invalid JSON model responses are retried up to three times. If a run still stops on a model response, rerun it or select another OpenRouter model with `--model` or `OPENROUTER_MODEL`.

The pipeline requires a source of jobs. LinkedIn profile URLs are recorded as candidate context; the app does not fetch private profile data. Add candidate details and compensation preferences with CLI options or a candidate JSON file. Candidate CV and profile data are excluded from version control by `.gitignore`.

Runs save `researcher.json`, `career_analyst.json` (after each completed batch), `cv_editor.json`, and `cover_letter_writer.json` under `outputs/YYYY-MM-DD/agent_outputs/`. To continue an interrupted run without repeating completed model work, pass its output directory to `--resume`, for example:

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
