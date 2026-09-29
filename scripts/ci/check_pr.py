"""Validate a review attestation, without executing PR body contents."""
import json
import os
import re
from pathlib import Path


def checked(body: str, criterion: str) -> bool:
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
    pattern = rf"^ {{0,3}}[-*] \[[xX]\] {re.escape(criterion)}\s*$"
    return any(re.fullmatch(pattern, line) for line in visible)


if __name__ == "__main__":
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    criterion = os.environ["CRITERION"]
    if not checked(event["pull_request"].get("body") or "", criterion):
        raise SystemExit(f"Item pendente no checklist da PR: {criterion}")
    print(f"Declaração de revisão confirmada: {criterion}")
