from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, allowed_user_ids: set[int]) -> None:
        self._allowed = frozenset(allowed_user_ids)

    def is_authorized(self, user_id: int) -> bool:
        authorized = user_id in self._allowed
        if not authorized:
            logger.warning("Unauthorized access attempt: user_id=%d", user_id)
        return authorized
