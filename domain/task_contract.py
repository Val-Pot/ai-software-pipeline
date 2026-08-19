from __future__ import annotations

import re

SECTION_ORDER = (
    "Goal",
    "Scope",
    "Out of Scope",
    "Expected Behavior",
    "Architecture Constraints",
    "Acceptance Criteria",
    "Verification",
    "Visual References",
    "Additional Context",
)

REQUIRED_SECTIONS = (
    "Goal",
    "Scope",
    "Expected Behavior",
    "Architecture Constraints",
    "Acceptance Criteria",
    "Verification",
)

_MD_HEADING_RE = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$")
_BOLD_RE = re.compile(r"^\*\*(.+?)\*\*$")
_TITLE_RE = re.compile(
    r"^(?:#{1,6}[ \t]+)?(?:\*\*)?task[ \t]+contract(?:\*\*)?[ \t]*:?[ \t]*$",
    re.IGNORECASE,
)
_CANONICAL = {name.casefold(): name for name in SECTION_ORDER}


def _canonical_name(raw: str) -> str | None:
    cleaned = (raw or "").strip().rstrip(":").strip()
    bold = _BOLD_RE.match(cleaned)
    if bold:
        cleaned = bold.group(1).strip().rstrip(":").strip()
    return _CANONICAL.get(cleaned.casefold())


def _parse_heading_line(line: str) -> tuple[bool, str | None, str]:
    """Return (is_heading, canonical_section_or_None, inline_body)."""
    stripped = (line or "").strip()
    if not stripped:
        return False, None, ""
    md = _MD_HEADING_RE.match(stripped)
    if md:
        raw = md.group(1).strip()
        if ":" in raw:
            left, right = raw.split(":", 1)
            return True, _canonical_name(left), right.strip()
        return True, _canonical_name(raw), ""
    if ":" in stripped:
        left, right = stripped.split(":", 1)
        name = _canonical_name(left)
        if name:
            return True, name, right.strip()
        return False, None, ""
    return False, None, ""


def looks_like_contract(text: str) -> bool:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if _TITLE_RE.match(stripped):
            return True
        is_heading, name, _inline = _parse_heading_line(line)
        if is_heading and name:
            return True
    return False


def parse(text: str) -> dict[str, str]:
    sections = {name: "" for name in SECTION_ORDER}
    current: str | None = None
    chunks: list[str] = []

    def _flush() -> None:
        nonlocal chunks
        if current is not None:
            sections[current] = "\n".join(chunks).strip()
        chunks = []

    for line in (text or "").splitlines():
        stripped = line.strip()
        if _TITLE_RE.match(stripped):
            continue
        is_heading, name, inline = _parse_heading_line(line)
        if is_heading:
            _flush()
            current = name
            chunks = [inline] if inline else []
            continue
        if current is not None:
            chunks.append(line)
    _flush()
    return sections


def missing_required(text: str) -> list[str]:
    sections = parse(text)
    return [name for name in REQUIRED_SECTIONS if not (sections.get(name) or "").strip()]


def render(user_text: str) -> str:
    raw = (user_text or "").strip()
    if looks_like_contract(raw):
        sections = parse(raw)
    else:
        sections = {name: "" for name in SECTION_ORDER}
        sections["Additional Context"] = raw
    return format_template(sections)


def format_template(sections: dict[str, str] | None = None) -> str:
    values = sections or {}
    parts = ["# Task Contract", ""]
    for name in SECTION_ORDER:
        parts.append(f"## {name}")
        parts.append("")
        value = (values.get(name) or "").strip()
        if value:
            parts.append(value)
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def incomplete_message(missing: list[str]) -> str:
    lines = "\n".join(f"- {name}" for name in missing)
    return (
        "Task Contract is incomplete.\n\n"
        f"Missing:\n{lines}\n\n"
        "Coding Agent was not started."
    )
