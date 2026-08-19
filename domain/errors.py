class UserFacingError(Exception):
    """Error whose message is safe to show in Telegram."""


class AssignmentError(UserFacingError):
    """GitHub accepted the request but did not assign the coding agent."""


class MergeError(UserFacingError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class GitHubUnavailableError(Exception):
    """Transport or API failure talking to GitHub — not an application bug."""


class GitHubForbiddenError(GitHubUnavailableError):
    """GitHub rejected the request with 403 (missing token scope or SSO)."""
