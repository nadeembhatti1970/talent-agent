from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from .pipeline import run_pipeline
from .search_config import ALLOWED_DATE_POSTED, DEFAULT_SEARCH_CONFIG, load_search_config
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
    run.add_argument(
        "--workplace-arrangement",
        choices=("remote", "hybrid", "partly-remote", "onsite"),
        action="append",
        help="Allowed workplace arrangement; repeat for multiple (hybrid and partly-remote are equivalent; overrides config)",
    )
    run.add_argument("--date-posted", choices=tuple(sorted(ALLOWED_DATE_POSTED)))
    run.add_argument("--max-results", type=int)
    run.add_argument("--minimum-score", type=float, default=70.0)
    run.add_argument("--currency")
    run.add_argument("--min-day-rate", type=float, help="Minimum contract day rate in configured currency")
    run.add_argument("--min-annual-salary", type=float, help="Minimum permanent annual salary in configured currency")
    run.add_argument("--exclude-unknown-compensation", action="store_true", default=None, help="Reject listings without a parseable matching pay amount")
    run.add_argument(
        "--exclude-security-clearance",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Exclude jobs that require security clearance (overrides config)",
    )
    run.add_argument(
        "--review-documents",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Quality-review generated CVs and cover letters (overrides config)",
    )
    run.add_argument(
        "--review-linkedin-profile",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Fetch public LinkedIn profile content for document verification (overrides config)",
    )
    run.add_argument("--linkedin-url", default=LINKEDIN_PROFILE_URL, help="Candidate profile URL")
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
    run.add_argument(
        "--allow-model-change-on-resume",
        action="store_true",
        help="On resume, discard saved agent outputs and rerun model-backed stages using the selected model",
    )
    return parser


def main() -> int:
    load_dotenv(override=True)
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
        workplace_arrangements = (
            ["hybrid" if value == "partly-remote" else value for value in args.workplace_arrangement]
            if args.workplace_arrangement is not None
            else config["workplace_arrangements"]
        )
        exclude_unknown = args.exclude_unknown_compensation if args.exclude_unknown_compensation is not None else config["exclude_unknown_compensation"]
        exclude_security_clearance = (
            args.exclude_security_clearance
            if args.exclude_security_clearance is not None
            else config["exclude_security_clearance"]
        )
        review_documents = (
            args.review_documents if args.review_documents is not None else config["review_documents"]
        )
        review_linkedin_profile = (
            args.review_linkedin_profile
            if args.review_linkedin_profile is not None
            else config["review_linkedin_profile"]
        )
        output_config = config["output"]
        output_path = run_pipeline(
            jobs_path=args.jobs, search=args.search, resume_from=args.resume,
            cv_path=args.cv, candidate_path=args.candidate,
            keywords=keywords, locations=locations, date_posted=args.date_posted or config["date_posted"],
            max_results=args.max_results if args.max_results is not None else config["max_results"], minimum_score=args.minimum_score,
            employment_types=employment_types, currency=args.currency or config["currency"],
            workplace_arrangements=workplace_arrangements,
            minimum_day_rate=args.min_day_rate if args.min_day_rate is not None else config["minimum_day_rate"],
            minimum_annual_salary=args.min_annual_salary if args.min_annual_salary is not None else config["minimum_annual_salary"],
            exclude_unknown_compensation=exclude_unknown,
            include_unknown_contract_compensation=config.get(
                "include_unknown_contract_compensation", not exclude_unknown
            ),
            exclude_security_clearance=exclude_security_clearance,
            allowed_location_terms=config.get("allowed_location_terms"),
            review_documents=review_documents,
            review_linkedin_profile=review_linkedin_profile,
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
            allow_model_change_on_resume=args.allow_model_change_on_resume,
        )
    except Exception as exc:
        print(f"\n❌ Talent Agent stopped: {exc}", file=sys.stderr)
        return 1
    print(f"Pipeline complete. Artifacts: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
