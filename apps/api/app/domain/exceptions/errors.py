class DomainError(Exception):
    """Base class for business-rule violations."""


class EntityNotFoundError(DomainError):
    pass


class InvalidStateTransitionError(DomainError):
    pass


class PolicyValidationError(DomainError, ValueError):
    pass


class DomainValidationError(DomainError, ValueError):
    pass


class UserConflictError(DomainError):
    pass


class ReadinessGateError(DomainError):
    pass


class SessionConflictError(DomainError):
    pass


class PolicyInUseError(DomainError):
    pass


class ConcurrencyError(DomainError):
    pass
