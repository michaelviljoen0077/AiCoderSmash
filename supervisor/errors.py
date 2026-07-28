"""Shared exception types for the AI Switchboard."""


class EngineError(Exception):
    """An engine failed to execute a task."""

    def __init__(self, route: str, message: str):
        self.route = route
        self.message = message
        super().__init__(f"[{route}] {message}")


class EngineUnavailable(EngineError):
    """The engine's CLI/binary/service is not installed or not reachable."""


class RateLimitError(EngineError):
    """The route hit a quota/rate limit and should be flagged in the ledger.

    ``resets_at`` is an ISO-8601 UTC timestamp when the route is expected to
    become usable again (estimated when the tool does not report one).
    """

    def __init__(
        self,
        route: str,
        message: str,
        resets_at: str | None = None,
        status: str = "throttled",
    ):
        self.resets_at = resets_at
        self.status = status
        super().__init__(route, message)
