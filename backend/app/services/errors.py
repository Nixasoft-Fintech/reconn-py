from __future__ import annotations


class ServiceError(Exception):
    """Base class for expected service failures mapped to HTTP responses."""

    status_code = 400


class NotFoundError(ServiceError):
    status_code = 404


class ConflictError(ServiceError):
    status_code = 409


class ValidationError(ServiceError):
    status_code = 422
