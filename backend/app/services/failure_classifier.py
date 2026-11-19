from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    OperationalError,
    TimeoutError as SQLAlchemyTimeoutError,
)

FAILURE_MESSAGES = {
    "DATABASE_CONNECTION_ERROR": (
        "We couldn't connect to the database while saving your "
        "reconciliation results. Please try again."
    ),
    "DATABASE_POOL_TIMEOUT": (
        "The database is currently busy. Please try again shortly."
    ),
    "DATABASE_INTEGRITY_ERROR": (
        "Some reconciliation records could not be saved because "
        "of a data conflict. Please contact support."
    ),
    "DATABASE_ERROR": (
        "A database error prevented us from saving your results. "
        "Please try again later."
    ),
    "SERVER_RESTARTED": (
        "Your reconciliation was interrupted because the server restarted. "
        "Please upload your files and run reconciliation again."
    ),
    "PERSISTENCE_ERROR": (
        "We couldn't save your reconciliation results. Please try again."
    ),
    "UNKNOWN_ERROR": (
        "Something unexpected happened while processing your reconciliation. "
        "Please contact support."
    ),
}


def classify_failure(exc: Exception) -> str:
    """Return a machine-readable failure code."""

    if isinstance(exc, SQLAlchemyTimeoutError):
        return "DATABASE_POOL_TIMEOUT"

    if isinstance(exc, IntegrityError):
        return "DATABASE_INTEGRITY_ERROR"

    if isinstance(exc, DBAPIError):
        # SQLAlchemy detected that the connection was invalidated.
        if exc.connection_invalidated:
            return "DATABASE_CONNECTION_ERROR"

        original_error = exc.orig


        sqlstate = getattr(original_error, "sqlstate", None)

        print("DATABASE EXCEPTION:", repr(original_error))
        print("DATABASE SQLSTATE:", sqlstate)
        print("CONNECTION INVALIDATED:", exc.connection_invalidated)

        sqlstate = getattr(original_error, "sqlstate", None)

        # PostgreSQL connection-related SQLSTATE codes.
        if sqlstate and (
            sqlstate.startswith("08") or sqlstate in {"57P01", "57P02", "57P03"}
        ):
            return "DATABASE_CONNECTION_ERROR"

        # Fallback for common connection failures without SQLSTATE.
        error_text = str(original_error).lower()

        connection_errors = (
            "connection refused",
            "connection is closed",
            "server closed the connection",
            "could not connect to server",
            "connection timed out",
            "connection reset",
            "network is unreachable",
        )

        if any(message in error_text for message in connection_errors):
            return "DATABASE_CONNECTION_ERROR"

        if isinstance(exc, OperationalError):
            return "DATABASE_ERROR"

        return "DATABASE_ERROR"

    return "UNKNOWN_ERROR"


def get_failure_message(failure_code: str | None) -> str:
    """Return a safe message for frontend display."""

    return FAILURE_MESSAGES.get(
        failure_code or "",
        FAILURE_MESSAGES["UNKNOWN_ERROR"],
    )
