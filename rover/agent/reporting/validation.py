"""Strict deterministic citation validation helpers."""

import re


def validate_citation_schema(report_content: str) -> None:
    """Fail fast if any Evidence line violates schema."""
    evidence_lines = [
        line.strip()
        for line in report_content.splitlines()
        if line.strip().startswith("Evidence:")
    ]
    pattern = re.compile(
        r"^Evidence:\s*metric:[^;]+;\s*pod:[^;]+;\s*endpoint:[^;]+;\s*source:[^;]+$"
    )
    invalid = [line for line in evidence_lines if not pattern.match(line)]
    if invalid:
        raise ValueError(f"Invalid citation schema lines found: {invalid[:3]}")
    if len(evidence_lines) < 4:
        raise ValueError(
            "Insufficient deterministic evidence lines; expected at least 4 major citations."
        )


__all__ = ["validate_citation_schema"]
