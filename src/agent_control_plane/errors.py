class ControlPlaneError(Exception):
    """Base class for expected domain errors."""


class NotFoundError(ControlPlaneError):
    pass


class ConflictError(ControlPlaneError):
    pass


class AuthorizationError(ControlPlaneError):
    pass


class InvalidTransitionError(ControlPlaneError):
    pass
