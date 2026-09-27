from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from backend.api.deps import SamplesRootDep, SessionDep
from backend.schemas import Page, RepositoryCreate, RepositoryOut
from backend.services.runs import RepositoryService
from backend.services.sources import list_samples

router = APIRouter(prefix="/repositories", tags=["repositories"])


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
