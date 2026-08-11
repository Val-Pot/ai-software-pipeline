"""
GitHub REST API Pydantic response models and DTOs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class GitHubUser(BaseModel):
    login: str
    id: int
    type: str = "User"


class GitHubIssue(BaseModel):
    id: int
    number: int
    title: str
    body: Optional[str] = None
    state: str
    html_url: str
    user: GitHubUser
    labels: List[Dict[str, Any]] = Field(default_factory=list)
    assignees: List[GitHubUser] = Field(default_factory=list)


class GitHubPullRequest(BaseModel):
    id: int
    number: int
    title: str
    body: Optional[str] = None
    state: str
    html_url: str
    merged: bool = False
    mergeable: Optional[bool] = None
    head_sha: Optional[str] = None
    user: GitHubUser


class GitHubComment(BaseModel):
    id: int
    body: str
    user: GitHubUser
    created_at: datetime
    html_url: str


class GitHubWorkflowRun(BaseModel):
    id: int
    name: str
    status: str      # e.g., queued, in_progress, completed
    conclusion: Optional[str] = None  # e.g., success, failure, cancelled
    html_url: str
    head_sha: str
