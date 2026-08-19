from __future__ import annotations

import re

_CLOSES_RE = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)",
    re.IGNORECASE,
)
_HASH_RE = re.compile(r"#(\d+)")
_BRANCH_ISSUE_RE = re.compile(r"issue[-/](\d+)", re.IGNORECASE)


def extract_issue_number(*texts: str | None) -> int | None:
    for pattern in (_CLOSES_RE, _HASH_RE, _BRANCH_ISSUE_RE):
        for text in texts:
            if not text:
                continue
            match = pattern.search(text)
            if match:
                return int(match.group(1))
    return None
