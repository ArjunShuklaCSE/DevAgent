from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from backend.api.deps import SessionDep
from backend.schemas import Page, RepositoryCreate, RepositoryOut
from backend.services.runs import RepositoryService

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.post("", response_model=RepositoryOut, status_code=status.HTTP_201_CREATED)
async def create_repository(data: RepositoryCreate, session: SessionDep) -> RepositoryOut:
    """Register a GitHub repository (idempotent: returns the existing row if present)."""
    repo = await RepositoryService(session).create(data)
    return RepositoryOut.model_validate(repo)


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
