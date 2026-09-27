"""FastAPI dependencies resolved from ``app.state`` (set in the lifespan)."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.event_bus import EventBus
from backend.services.runs import RunService


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


def get_event_bus(request: Request) -> EventBus:
    bus: EventBus = request.app.state.event_bus
    return bus


async def get_session(
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session


def get_samples_root(request: Request) -> Path:
    root: Path = request.app.state.samples_root
    return root


def get_run_service(request: Request) -> RunService:
    return RunService(
        request.app.state.session_factory, request.app.state.event_bus, request.app.state.run_queue
    )


SessionDep = Annotated[AsyncSession, Depends(get_session)]
RunServiceDep = Annotated[RunService, Depends(get_run_service)]
EventBusDep = Annotated[EventBus, Depends(get_event_bus)]
SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
SamplesRootDep = Annotated[Path, Depends(get_samples_root)]
