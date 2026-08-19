from __future__ import annotations

# Default coding-agent login. Config COPILOT_USERNAME overrides assignment.
# A separate github-copilot[bot] comment account is an erroneous assumption.
DEFAULT_COPILOT_LOGIN = "copilot-swe-agent[bot]"


def normalize_copilot_login(username: str | None) -> str:
    login = (username or "").strip()
    return login or DEFAULT_COPILOT_LOGIN


def copilot_login_aliases(username: str | None) -> frozenset[str]:
    primary = normalize_copilot_login(username).lower()
    aliases = {primary, primary.removesuffix("[bot]")}
    if not primary.endswith("[bot]"):
        aliases.add(f"{primary}[bot]")
    aliases.update({"copilot-swe-agent[bot]", "copilot-swe-agent"})
    return frozenset(aliases)


def is_copilot_login(login: str | None, username: str | None) -> bool:
    return (login or "").strip().lower() in copilot_login_aliases(username)
