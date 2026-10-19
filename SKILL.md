# SKILL.md — Operational Capabilities & Functional Schemas

This document defines the atomic operational skills, tool integrations, and data schemas required by each agent in the multi-agent pipeline. It is entirely **LLM-agnostic**, focusing on execution logic rather than vendor-specific APIs.

---

## 🛠️ Core Tool Schemas

### 1. Scraping & Data Ingestion (Researcher Skill)
* **Tool Integration:** Apify API / PhantomBuster Webhooks / Custom HTTP Client
* **Execution Parameters:**
  ```json
  {
    "search_keywords": "string",
    "locations": ["string"],
    "date_posted": "past_24_hours | past_week",
    "max_results": 50
  }
  ```
* **Output Standard:** Search each configured location independently, combine and deduplicate results, and write a normalized JSON array containing `job_id`, `title`, `company`, `location`, `url`, `description`, and `scraped_at`.

### 2. Candidate Alignment & Evaluation (Career Analyst Skill)
* **Tool Integration:** Local File Reader & LLM Text Analysis
* **Scoring Formula:**
  $$\text{Match Score} = (w_1 \times \text{Tech Overlap}) + (w_2 \times \text{Seniority Match}) + (w_3 \times \text{Compensation Alignment})$$
* **Threshold Filter:** Reject any job where $\text{Match Score} < 70.0\%$.

### 3. Document Generation & Formatting (CV Editor & Cover Letter Writer Skill)
* **Tool Integration:** Python `python-docx` library
* **Styling Rules:**
  * Use standard professional fonts (Calibri or Arial).
  * Maintain clean heading hierarchies (`Heading 1` for sections, `Normal` for body text).
  * Ensure bullet points use native Word bullet formatting.
  * Include a configured GitHub profile URL in both documents; do not add document-type headings, generation labels, or placeholders such as `[Date]`.

### 4. Tracker & Dashboard Generation (Summary Report Writer Skill)
* **Tool Integration:** Pandas DataFrame to HTML/CSV converter
* **Link Schema:** Embed relative local filesystem paths using URI schemes (`file://abs_path/to/outputs/...`) alongside target-blank web links for original job postings.

### 5. Application Document Quality Review (Document Reviewer Skill)
* **Inputs:** Original Master CV, job listing, generated CV and cover letter, and optional text from an accessible public LinkedIn profile.
* **Checks:** Role responsibility and hiring-company alignment; material claims supported by the Master CV or LinkedIn evidence; contradictions and unsupported claims.
* **Rules:** Treat the Master CV as authoritative. Do not treat missing LinkedIn details as proof that a claim is false. Do not bypass LinkedIn sign-in or other access controls; mark inaccessible profile content as unverified.
* **Output:** JSON findings and approval status under `agent_outputs/document_reviewer.json`. Give document writers one revision attempt, then re-review and preserve unresolved findings for the tracker.

---

## 📋 Execution Checklist

1. **Initialize Directory:** Create `outputs/YYYY-MM-DD/applications/`.
2. **Scrape:** Run Researcher to dump `raw_jobs.json`.
3. **Analyze:** Run Career Analyst to generate `prioritized_jobs.json`.
4. **Tailor (Loop per Job):**
   * Execute CV Editor $\rightarrow$ save `.docx`.
   * Execute Cover Letter Writer $\rightarrow$ save `.docx`.
5. **Review:** Run Document Reviewer; revise flagged documents once and perform a final review.
6. **Report:** Run Summary Report Writer to generate `tracker.html` and `tracker.csv`, including review status.

## 💾 Durable Agent Outputs

- Save each agent's output as JSON under `outputs/YYYY-MM-DD/agent_outputs/`.
- Persist completed Career Analyst batches and CV/cover-letter text immediately; update `run_state.json` with stage status and input identity.
- Save public LinkedIn profile fetch status/text and Reviewer progress/results locally for resume support.
- On resume, load valid saved agent output and continue from the first incomplete unit of work rather than repeating completed model requests.
- These checkpoints may contain candidate CV and generated application content; keep them local and out of version control.