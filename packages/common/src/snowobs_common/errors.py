"""Application error hierarchy and RFC 7807 problem details (BUILD_PROMPT §15).

Every API error response is a ``application/problem+json`` body produced from
:class:`ProblemDetail`. Application code raises :class:`AppError` subclasses;
the API layer owns the mapping to HTTP.
"""

from __future__ import annotations

from pydantic import BaseModel


class ProblemDetail(BaseModel):
    """RFC 7807 problem document."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None


class AppError(Exception):
    """Base class for expected application failures."""

    status_code: int = 500
    title: str = "Internal error"
    problem_type: str = "about:blank"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail

    def to_problem(self, instance: str | None = None) -> ProblemDetail:
        return ProblemDetail(
            type=self.problem_type,
            title=self.title,
            status=self.status_code,
            detail=self.detail,
            instance=instance,
        )


class ConfigurationError(AppError):
    """Raised at startup when settings are invalid; the process must not serve."""

    status_code = 500
    title = "Configuration error"
    problem_type = "https://snowobs.dev/problems/configuration"


class NotFoundError(AppError):
    status_code = 404
    title = "Not found"
    problem_type = "https://snowobs.dev/problems/not-found"


class DataUnavailableError(AppError):
    """A source the answer needs has not been landed (R3).

    Distinct from a dependency being down: the platform is healthy, the caller's
    request is well-formed, and the data simply is not there yet. It carries a
    422 rather than a 404 because the remedy is to supply the input, not to look
    somewhere else — and it is raised in preference to returning an empty or
    zero-valued answer, which would read as "your account cost nothing".
    """

    status_code = 422
    title = "Required data has not been landed"
    problem_type = "https://snowobs.dev/problems/data-unavailable"


class DependencyUnavailableError(AppError):
    """A required backing service (database, cache, engine) is unreachable."""

    status_code = 503
    title = "Dependency unavailable"
    problem_type = "https://snowobs.dev/problems/dependency-unavailable"
