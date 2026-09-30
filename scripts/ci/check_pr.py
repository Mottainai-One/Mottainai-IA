"""Validate the PR body against the repository template as untrusted data."""
import json
import os
import re
from pathlib import Path

HEADINGS = (
    "# Pull Request",
    "## Type of Change",
    "## Description",
    "## Related Issue",
    "## Changes Made",
    "## Validation",
    "## Checklist",
    "## Additional Notes",
)

CHECKBOX_LABELS = (
    "Feature",
    "Bug Fix",
    "Documentation",
    "Refactoring",
    "Test",
    "Chore",
    "CI/CD",
    "Code reviewed",
    "Tests executed",
    "Documentation updated (if applicable)",
    "No breaking changes",
    "Branch follows the naming convention",
    "Commits follow Conventional Commits",
    "No sensitive information included",
    "Ready for review",
)


def visible_lines(body: str) -> list[str]:
    """Remove comments and fenced examples before inspecting visible PR text."""
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    visible = []
    fence = None
    for line in body.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
            continue
        if fence is None:
            visible.append(line)
    return visible


def checked(body: str, criterion: str) -> bool:
    """Retain the legacy attestation helper for existing CI contracts."""
    pattern = rf"^ {{0,3}}[-*] \[[xX]\] {re.escape(criterion)}\s*$"
    return any(re.fullmatch(pattern, line) for line in visible_lines(body))


def template_errors(body: str) -> list[str]:
    """Require the headings and checkbox labels used by the shared PR format."""
    lines = visible_lines(body)
    headings = [line.strip() for line in lines if re.match(r"^#{1,2} ", line)]
    labels = [
        match.group(1)
        for line in lines
        if (match := re.fullmatch(r"- \[[ xX]\] (.+)", line.strip()))
    ]
    errors = []
    if headings != list(HEADINGS):
        errors.append("PR headings differ from .github/pull_request_template.md")
    if labels != list(CHECKBOX_LABELS):
        errors.append("PR checkboxes differ from .github/pull_request_template.md")
    return errors


if __name__ == "__main__":
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    body = event["pull_request"].get("body") or ""
    criterion = os.environ.get("CRITERION")
    if criterion:
        if not checked(body, criterion):
            raise SystemExit(f"Item pendente no checklist da PR: {criterion}")
        print(f"Declaração de revisão confirmada: {criterion}")
    else:
        errors = template_errors(body)
        if errors:
            raise SystemExit("; ".join(errors))
        print("PR body matches the repository template")
