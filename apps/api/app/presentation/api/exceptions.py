from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.domain.exceptions.errors import (
    ConcurrencyError,
    DomainError,
    EntityNotFoundError,
    InvalidStateTransitionError,
    PolicyInUseError,
    PolicyValidationError,
    ReadinessGateError,
    SessionConflictError,
)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(EntityNotFoundError)
    async def not_found_handler(_request: Request, exc: EntityNotFoundError) -> JSONResponse:
        return _error(status.HTTP_404_NOT_FOUND, "NOT_FOUND", str(exc))

    @app.exception_handler(ConcurrencyError)
    async def concurrency_handler(_request: Request, exc: ConcurrencyError) -> JSONResponse:
        return _error(status.HTTP_409_CONFLICT, "CONCURRENT_WRITE", str(exc))

    @app.exception_handler(InvalidStateTransitionError)
    async def transition_handler(
        _request: Request, exc: InvalidStateTransitionError
    ) -> JSONResponse:
        return _error(status.HTTP_409_CONFLICT, "INVALID_STATE", str(exc))

    @app.exception_handler(ReadinessGateError)
    async def readiness_handler(_request: Request, exc: ReadinessGateError) -> JSONResponse:
        return _error(status.HTTP_409_CONFLICT, "READINESS_GATE", str(exc))

    @app.exception_handler(SessionConflictError)
    async def session_conflict_handler(
        _request: Request, exc: SessionConflictError
    ) -> JSONResponse:
        return _error(status.HTTP_409_CONFLICT, "SESSION_CONFLICT", str(exc))

    @app.exception_handler(PolicyInUseError)
    async def policy_in_use_handler(
        _request: Request, exc: PolicyInUseError
    ) -> JSONResponse:
        return _error(status.HTTP_409_CONFLICT, "POLICY_IN_USE", str(exc))

    @app.exception_handler(PolicyValidationError)
    async def validation_handler(_request: Request, exc: PolicyValidationError) -> JSONResponse:
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "DOMAIN_VALIDATION", str(exc))

    @app.exception_handler(DomainError)
    async def domain_handler(_request: Request, exc: DomainError) -> JSONResponse:
        return _error(status.HTTP_400_BAD_REQUEST, "DOMAIN_ERROR", str(exc))


def _error(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"code": code, "detail": detail})
