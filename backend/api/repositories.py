from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from backend.api.deps import (
    AuthServiceDep,
    RequiredUserDep,
    SamplesRootDep,
    SessionDep,
    require_user,
)
from backend.errors import ConflictError, ExternalServiceError
from backend.github.client import (
    GitHubAuthError,
    GitHubError,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubRateLimitError,
)
from backend.schemas import GitHubIssueOut, Page, RepositoryCreate, RepositoryOut
from backend.services.runs import RepositoryService
from backend.services.sources import list_samples
from database.models import RepositorySource

router = APIRouter(
    prefix="/repositories", tags=["repositories"], dependencies=[Depends(require_user)]
)


def github_http_error(exc: GitHubError) -> ExternalServiceError:
    """Map a GitHub failure to an API error that keeps GitHub's meaning."""
    status_code = HTTPStatus.BAD_GATEWAY
    details: dict[str, str] = {}
    if isinstance(exc, GitHubRateLimitError):
        status_code = HTTPStatus.TOO_MANY_REQUESTS
        if exc.reset_at is not None:
            details["reset_at"] = exc.reset_at.isoformat()
    elif isinstance(exc, GitHubNotFoundError):
        status_code = HTTPStatus.NOT_FOUND
    elif isinstance(exc, GitHubPermissionError | GitHubAuthError):
        status_code = HTTPStatus.FORBIDDEN
    return ExternalServiceError(status_code, exc.code, exc.message, details)


@router.post("", response_model=RepositoryOut, status_code=status.HTTP_201_CREATED)
async def create_repository(
    data: RepositoryCreate, session: SessionDep, samples_root: SamplesRootDep
) -> RepositoryOut:
    """Register a GitHub repository or a bundled sample (idempotent: returns the existing
    row if present)."""
    repo = await RepositoryService(session).create(data, samples_root)
    return RepositoryOut.model_validate(repo)


@router.get("/samples", response_model=list[str])
async def list_sample_repositories(samples_root: SamplesRootDep) -> list[str]:
    """Names of the bundled sample repositories that can be registered with ``sample``."""
    return list_samples(samples_root)


@router.get("", response_model=Page[RepositoryOut])
async def list_repositories(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[RepositoryOut]:
    result = await RepositoryService(session).list_page(limit, offset)
    return Page(
        items=[RepositoryOut.model_validate(r) for r in result.items],
        total=result.total,
        limit=limit,
        offset=offset,
    )


@router.get("/{repository_id}", response_model=RepositoryOut)
async def get_repository(repository_id: UUID, session: SessionDep) -> RepositoryOut:
    return RepositoryOut.model_validate(await RepositoryService(session).get(repository_id))


@router.get("/{repository_id}/issues", response_model=list[GitHubIssueOut])
async def list_open_issues(
    repository_id: UUID,
    session: SessionDep,
    auth: AuthServiceDep,
    user: RequiredUserDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[GitHubIssueOut]:
    """Open issues of a GitHub repository (pull requests excluded), newest activity first.

    Uses the signed-in user's token, else DEVAGENT_GITHUB_TOKEN, else anonymous access
    (public repositories only, with GitHub's lower rate limit).
    """
    repo = await RepositoryService(session).get(repository_id)
    if repo.source is not RepositorySource.GITHUB:
        raise ConflictError("Only GitHub repositories have issues to import", {})
    token = await auth.token_for(user.id if user is not None else None)
    try:
        async with auth.client(token) as client:
            issues = await client.list_open_issues(repo.owner, repo.name, limit)
    except GitHubError as exc:
        raise github_http_error(exc) from exc
    return [GitHubIssueOut.model_validate(issue, from_attributes=True) for issue in issues]
