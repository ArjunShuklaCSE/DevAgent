"""Wake-up signals for live event streams.

Postgres (``run_events``) is the source of truth; the bus only tells listeners that
new rows exist. A lost message delays delivery until the next keepalive poll, it never
loses an event (ADR 0005).
"""

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol
from uuid import UUID

import structlog
from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)


def channel_for(run_id: UUID) -> str:
    return f"devagent:runs:{run_id}:events"


class Subscription(Protocol):
    async def wait(self, max_wait_seconds: float) -> bool:
        """Block until a wake-up arrives (True) or ``max_wait_seconds`` pass (False)."""
        ...


class EventBus(Protocol):
    async def publish(self, run_id: UUID, seq: int) -> None: ...

    def subscribe(self, run_id: UUID) -> AbstractAsyncContextManager[Subscription]: ...


class _RedisSubscription:
    def __init__(self, pubsub: PubSub) -> None:
        self._pubsub = pubsub

    async def wait(self, max_wait_seconds: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_wait_seconds
        while (remaining := deadline - loop.time()) > 0:
            # Returns None both on timeout and for ignored (un)subscribe confirmations.
            message = await self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=remaining
            )
            if message is not None:
                # Coalesce a burst of notifications into one wake-up.
                while await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=0):
                    pass
                return True
        return False


class RedisEventBus:
    def __init__(self, client: Redis) -> None:
        self._client = client

    async def publish(self, run_id: UUID, seq: int) -> None:
        try:
            await self._client.publish(channel_for(run_id), str(seq))
        except (RedisError, OSError) as exc:
            # The event is already committed; listeners pick it up on their next poll.
            logger.warning("event_publish_failed", run_id=str(run_id), seq=seq, error=str(exc))

    @asynccontextmanager
    async def subscribe(self, run_id: UUID) -> AsyncIterator[Subscription]:
        pubsub = self._client.pubsub()
        await pubsub.subscribe(channel_for(run_id))
        try:
            yield _RedisSubscription(pubsub)
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()  # type: ignore[no-untyped-call]  # untyped in redis-py 5


class _MemorySubscription:
    def __init__(self) -> None:
        self.signal = asyncio.Event()

    async def wait(self, max_wait_seconds: float) -> bool:
        try:
            async with asyncio.timeout(max_wait_seconds):
                await self.signal.wait()
        except TimeoutError:
            return False
        self.signal.clear()
        return True


class InMemoryEventBus:
    """Single-process bus for tests and tools that run API and worker in one process."""

    def __init__(self) -> None:
        self._subscribers: defaultdict[UUID, set[_MemorySubscription]] = defaultdict(set)
        self.published: list[tuple[UUID, int]] = []

    async def publish(self, run_id: UUID, seq: int) -> None:
        self.published.append((run_id, seq))
        for subscription in self._subscribers[run_id]:
            subscription.signal.set()

    @asynccontextmanager
    async def subscribe(self, run_id: UUID) -> AsyncIterator[Subscription]:
        subscription = _MemorySubscription()
        self._subscribers[run_id].add(subscription)
        try:
            yield subscription
        finally:
            self._subscribers[run_id].discard(subscription)
