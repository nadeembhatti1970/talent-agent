# AGENTS.md — Multi-Agent System Architecture & Roles

This document outlines the multi-agent architecture for the automated LinkedIn job scraping, evaluation, tailoring, and tracking pipeline. The system is designed to be **LLM-agnostic**, ensuring that any compatible model can step into any defined role using standardized inputs, structured operational prompts, and consistent file-system outputs.

---

## 📂 Global Directory & Output Conventions

All pipeline executions follow a strict chronological directory schema to ensure clean state management and easy review. 

* **Base Output Directory:** `outputs/`
* **Execution Subfolder:** `outputs/YYYY-MM-DD/` (where `YYYY-MM-DD` represents the runtime execution date)
* **Sub-Artifacts Structure:**
  * `raw_jobs.json` — Unfiltered raw records retrieved by the Researcher.
  * `prioritized_jobs.json` — Scored, filtered, and ranked listings evaluated by the Career Analyst.
  * `applications/` — Directory containing all tailored Word documents (`.docx`).
  * `agent_outputs/` — Per-agent JSON checkpoints and `run_state.json` for recovery after interruption.
  * `tracker.html` — Interactive visual dashboard linking directly to original job listings and local file assets.
  * `tracker.csv` — Flat spreadsheet export for external table importing.

---

## 👥 Autonomous Agent Specifications

### 1. The Researcher Agent
* **Role & Objective:** Acts as the entry point of the pipeline. Responsible for discovering, scraping, and normalizing job openings from LinkedIn using professional automation scrapers (such as Apify, PhantomBuster, or equivalent APIs).
* **Core Responsibilities:**
  * Execute targeted searches based on keywords, geographic locations, and employment parameters.
  * Ingest and normalize raw job posting payloads.
  * Filter out duplicate postings or expired listings.
* **Inputs:** Search query criteria, location parameters, workplace arrangement options (remote, hybrid/partly remote, and onsite), target compensation thresholds.
* **Outputs:** `outputs/YYYY-MM-DD/raw_jobs.json`, `agent_outputs/researcher.json`

### 2. The Career Analyst Agent
* **Role & Objective:** Acts as the qualification and compensation gatekeeper. Evaluates raw job listings against the candidate's actual background.
* **Core Responsibilities:**
  * Parse baseline candidate assets from the local filesystem (Master CV) and optional public LinkedIn profile text.
  * Calculate an alignment score ($0.0\% - 100.0\%$) based on tech stack overlap, seniority alignment, and compensation expectations.
  * Rank and filter listings, discarding roles that fall below the minimum threshold.
* **Inputs:** `outputs/YYYY-MM-DD/raw_jobs.json`, Local Master CV file, Candidate LinkedIn profile URL.
* **Outputs:** `outputs/YYYY-MM-DD/prioritized_jobs.json`, `agent_outputs/career_analyst.json`

### 3. The CV Editor Agent
* **Role & Objective:** Acts as an expert resume consultant. Tailors candidate experience specifically to target job requirements without hallucinating false credentials.
* **Core Responsibilities:**
  * Re-align bullet points, highlight relevant framework experience, and emphasize matching domain expertise.
  * Generate clean, professional text structures ready for word processing conversion.
  * Include the configured GitHub profile URL in the CV contact details; omit template headings, generation labels, and date placeholders.
* **Inputs:** Selected job records from `prioritized_jobs.json`, Local Master CV.
* **Outputs:** `outputs/YYYY-MM-DD/applications/[Company]_[JobTitle]_CV.docx`, `agent_outputs/cv_editor.json`

### 4. The Cover Letter Writer Agent
* **Role & Objective:** Acts as a professional copywriter. Crafts high-converting, concise cover letters tailored to individual company missions and role demands.
* **Core Responsibilities:**
  * Formulate compelling 3 to 4 paragraph introductory letters.
  * Directly bridge candidate achievements with core pain points mentioned in the job description.
  * Include the configured GitHub profile URL in the contact details/sign-off; omit a "Cover Letter" heading, generation labels, and date placeholders.
* **Inputs:** Selected job records from `prioritized_jobs.json`, Candidate profile summary.
* **Outputs:** `outputs/YYYY-MM-DD/applications/[Company]_[JobTitle]_CoverLetter.docx`, `agent_outputs/cover_letter_writer.json`

### 5. The Document Reviewer Agent
* **Role & Objective:** Quality-check each CV and cover letter for alignment with the role's actual responsibilities and hiring company, and for factual support.
* **Core Responsibilities:**
  * Check material claims against the original Master CV and, when accessible, public LinkedIn profile text. Treat the Master CV as authoritative and flag contradictions or unsupported claims; do not infer falsity from missing profile data.
  * Check role and client alignment beyond the job title and identify generic or mismatched content.
  * Return structured findings, request at most one document revision, and re-review the revised drafts.
  * Record whether LinkedIn content was available; never bypass sign-in or other access controls.
* **Inputs:** Master CV, selected job listing, generated CV and cover letter, optional fetched public LinkedIn profile.
* **Outputs:** `agent_outputs/document_reviewer.json`, final review status/findings in the job tracker.

### 6. The Summary Report Writer Agent
* **Role & Objective:** Acts as the reporting and dashboard compiler. Integrates all generated artifacts into an easily navigable control center.
* **Core Responsibilities:**
  * Aggregate metrics and file paths into a structured summary.
  * Generate a responsive HTML dashboard (`tracker.html`) embedding secure relative file links (`file://`) and live external hyperlinks.
* **Inputs:** `prioritized_jobs.json`, Generated application document paths.
* **Outputs:** `outputs/YYYY-MM-DD/tracker.html`, `outputs/YYYY-MM-DD/tracker.csv`

### Durable Agent Outputs
* Save each agent's usable output as JSON under `outputs/YYYY-MM-DD/agent_outputs/`.
* Persist completed Career Analyst batches and generated document text immediately so an interrupted run can resume without repeating completed model calls.
* Persist LinkedIn fetch status and Reviewer progress/results locally so resumed runs retain review state.
* Keep `run_state.json` current with stage status and input identity; resume only when the saved inputs match.
* Treat agent output JSON as local candidate data; do not commit it or include credentials.

---

## 🔄 Sequential Pipeline Execution Flow

```text
               ┌───────────────────────┐
               │    Target Search      │
               └───────────┬───────────┘
                           │
                           ▼
                  [1. Researcher Agent]
                           │
                    raw_jobs.json
                           │
                           ▼
[Master CV] ───► [2. Career Analyst Agent] ◄─── [LinkedIn Profile]
                           │
                 prioritized_jobs.json
                           │
         ┌─────────────────┴─────────────────┐
         ▼                                   ▼
[3. CV Editor Agent]               [4. Cover Letter Writer]
         │                                   │
      CV .docx                         Cover Letter .docx
         │                                   │
         └─────────────────┬─────────────────┘
                           │
                           ▼
              [5. Document Reviewer Agent]
                           │
                           ▼
            [6. Summary Report Writer Agent]
                           │
               tracker.html / tracker.csv
```