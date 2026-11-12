"""Central owner-only guard for developer routers."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware


class OwnerOnlyMiddleware(BaseMiddleware):
    """Reject non-owner events before admin/FSM handlers are reached.

    The only public callback intentionally passed through is mb:<id>,
    which displays an ordinary dynamic message button. Developer-only
    dynamic buttons are still checked again by their handler.
    """

    def __init__(self, owner_ids: Iterable[int]) -> None:
        self.owner_ids = frozenset(int(value) for value in owner_ids)

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is not None and user.id in self.owner_ids:
            return await handler(event, data)

        callback_data = getattr(event, "data", None)
        answer = getattr(event, "answer", None)
        if callback_data is not None and callable(answer):
            if str(callback_data).startswith("mb:"):
                return await handler(event, data)
            await answer("⛔ هذه العملية للمالك فقط.", show_alert=True)
            return None

        if callable(answer):
            await answer("⛔ هذه العملية للمالك فقط.")
        return None


def install_owner_guard(router: Any, owner_ids: Iterable[int]) -> None:
    """Install the owner guard on both message and callback observers."""
    router.message.outer_middleware(OwnerOnlyMiddleware(owner_ids))
    router.callback_query.outer_middleware(OwnerOnlyMiddleware(owner_ids))
