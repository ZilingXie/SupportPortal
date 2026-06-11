import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

F = TypeVar('F', bound=Callable[..., Awaitable[Any]])


def handle_multiple_group_ids(func: F) -> F:
    """No-op decorator - only needed for FalkorDB multi-group. Neo4j uses single db per group."""
    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        return await func(self, *args, **kwargs)
    return wrapper  # type: ignore[return-value]
