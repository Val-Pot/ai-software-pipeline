from pydantic import BaseModel, Field


class GitHubUser(BaseModel):
    login: str
    id: int | None = None


class GitHubIssue(BaseModel):
    number: int
    html_url: str
    title: str = ""
    state: str = "open"
    assignees: list[GitHubUser] = Field(default_factory=list)


class GitHubPullRequest(BaseModel):
    number: int
    html_url: str
    state: str
    draft: bool = False
    requested_reviewers: list[GitHubUser] = Field(default_factory=list)
    merged: bool = False
    mergeable: bool | None = None
    mergeable_state: str | None = None
    head_sha: str = ""
    head_ref: str = ""
