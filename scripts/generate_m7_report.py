"""Generate or verify the canonical M7-02 machine and human reports."""

from __future__ import annotations

import argparse
from pathlib import Path

from lifecase.evaluation import (
    EvaluationService,
    render_report_json,
    render_report_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    corpus = repository / "fixtures" / "synthetic-cases" / "m7-v1"
    output = repository / "docs" / "evaluation"
    report = EvaluationService().evaluate(corpus)
    files = {
        output / "m7-v1-results.json": render_report_json(report),
        output / "m7-v1-results.md": render_report_markdown(report),
    }
    if arguments.check:
        return _check(files)
    output.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        path.write_text(content, encoding="utf-8", newline="\n")
    return 0


def _check(files: dict[Path, str]) -> int:
    for path, expected in files.items():
        try:
            actual = path.read_text(encoding="utf-8")
        except OSError:
            print("M7 evaluation report is missing.")
            return 1
        if actual != expected:
            print("M7 evaluation report is stale.")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
