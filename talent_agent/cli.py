from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from .pipeline import run_pipeline
from .search_config import DEFAULT_SEARCH_CONFIG, load_search_config
from .settings import DEFAULT_CV_PATH, GITHUB_PROFILE_URL, LINKEDIN_PROFILE_URL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="talent-agent", description="Run the OpenRouter-powered job application pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Discover/evaluate jobs and generate application documents")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--jobs", type=Path, help="Local JSON array of raw job listings")
    source.add_argument("--search", action="store_true", help="Search using the configured Apify actor")
    source.add_argument("--resume", type=Path, metavar="RUN_DIR", help="Resume an interrupted run from its output directory")
    run.add_argument("--cv", type=Path, default=DEFAULT_CV_PATH, help=f"Master CV (.txt, .md, or .docx; default: {DEFAULT_CV_PATH})")
    run.add_argument("--candidate", type=Path, help="Optional candidate preferences JSON")
    run.add_argument("--config", type=Path, default=DEFAULT_SEARCH_CONFIG, help=f"Search settings JSON (default: {DEFAULT_SEARCH_CONFIG})")
    run.add_argument("--keywords", action="append", help="Search phrase; repeat to override file keywords")
    run.add_argument("--location", action="append", help="Target job location; repeat to search multiple locations (overrides config file)")
    run.add_argument("--employment-type", choices=("contract", "permanent"), action="append", help="Allowed type; repeat for both (overrides config)")
    run.add_argument("--date-posted", choices=("past_24_hours", "past_week"))
    run.add_argument("--max-results", type=int)
    run.add_argument("--minimum-score", type=float, default=70.0)
    run.add_argument("--currency")
    run.add_argument("--min-day-rate", type=float, help="Minimum contract day rate in configured currency")
    run.add_argument("--min-annual-salary", type=float, help="Minimum permanent annual salary in configured currency")
    run.add_argument("--exclude-unknown-compensation", action="store_true", default=None, help="Reject listings without a parseable matching pay amount")
    run.add_argument("--linkedin-url", default=LINKEDIN_PROFILE_URL, help="Candidate profile URL (recorded as context; not fetched)")
    run.add_argument("--github-url", default=None, help="Candidate GitHub profile URL (overrides config)")
    run.add_argument("--output-dir", type=Path, help="Override configured output root directory")
    run.add_argument("--dated-subfolders", action=argparse.BooleanOptionalAction, default=None, help="Enable or disable date-named output folders")
    run.add_argument("--date-format", help="Override strftime date folder pattern")
    run.add_argument("--raw-jobs-filename")
    run.add_argument("--prioritized-jobs-filename")
    run.add_argument("--tracker-html-filename")
    run.add_argument("--tracker-csv-filename")
    run.add_argument("--tracker-format", choices=("html", "csv"), action="append", help="Tracker format; repeat to select both")
    run.add_argument("--application-format", choices=("docx", "md"), help="Generated CV and cover letter format")
    run.add_argument("--run-id", default="", help="Optional suffix to preserve multiple runs on one date")
    run.add_argument("--model", default=None, help="OpenRouter model ID (default: OPENROUTER_MODEL or openrouter/free)")
    return parser


def main() -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    try:
        config = load_search_config(args.config)
        keywords = args.keywords if args.keywords is not None else config["keywords"]
        if not keywords and args.search:
            raise ValueError("Set keywords in the search config or pass --keywords")
        locations = args.location if args.location is not None else config["locations"]
        github_url = (
            args.github_url
            if args.github_url is not None
            else config.get("github_profile_url") or GITHUB_PROFILE_URL
        )
        employment_types = args.employment_type if args.employment_type is not None else config["employment_types"]
        exclude_unknown = args.exclude_unknown_compensation if args.exclude_unknown_compensation is not None else config["exclude_unknown_compensation"]
        output_config = config["output"]
        output_path = run_pipeline(
            jobs_path=args.jobs, search=args.search, resume_from=args.resume,
            cv_path=args.cv, candidate_path=args.candidate,
            keywords=keywords, locations=locations, date_posted=args.date_posted or config["date_posted"],
            max_results=args.max_results if args.max_results is not None else config["max_results"], minimum_score=args.minimum_score,
            employment_types=employment_types, currency=args.currency or config["currency"],
            minimum_day_rate=args.min_day_rate if args.min_day_rate is not None else config["minimum_day_rate"],
            minimum_annual_salary=args.min_annual_salary if args.min_annual_salary is not None else config["minimum_annual_salary"],
            exclude_unknown_compensation=exclude_unknown,
            linkedin_url=args.linkedin_url,
            github_url=github_url,
            output_root=args.output_dir or Path(output_config["root_dir"]), run_id=args.run_id,
            dated_subfolders=args.dated_subfolders if args.dated_subfolders is not None else output_config["dated_subfolders"],
            date_format=args.date_format or output_config["date_format"],
            raw_jobs_filename=args.raw_jobs_filename or output_config["raw_jobs_filename"],
            prioritized_jobs_filename=args.prioritized_jobs_filename or output_config["prioritized_jobs_filename"],
            tracker_html_filename=args.tracker_html_filename or output_config["tracker_html_filename"],
            tracker_csv_filename=args.tracker_csv_filename or output_config["tracker_csv_filename"],
            tracker_formats=args.tracker_format or output_config["tracker_formats"],
            application_format=args.application_format or output_config["application_format"],
            model=args.model,
        )
    except Exception as exc:
        print(f"\n❌ Talent Agent stopped: {exc}", file=sys.stderr)
        return 1
    print(f"Pipeline complete. Artifacts: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
