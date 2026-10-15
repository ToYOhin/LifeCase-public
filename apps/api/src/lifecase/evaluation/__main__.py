"""Command-line entry point for the offline M7 evaluation."""

from __future__ import annotations

import argparse
from pathlib import Path

from lifecase.evaluation.service import (
    EvaluationCorpusError,
    EvaluationService,
    render_report_json,
    render_report_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the offline LifeCase M7 evaluation")
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    arguments = parser.parse_args()
    try:
        report = EvaluationService().evaluate(arguments.corpus)
    except EvaluationCorpusError as error:
        print(f"evaluation_failed: {error.code}")
        return 2
    if arguments.format == "json":
        print(render_report_json(report), end="")
    else:
        print(render_report_markdown(report), end="")
    return 0 if report.overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
