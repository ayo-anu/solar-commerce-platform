"""Concrete database resource lifecycle for one application instance."""

from collections.abc import Generator
from contextlib import contextmanager
from enum import Enum, auto
from threading import Lock
from typing import Final, cast

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, DisconnectionError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session, sessionmaker

_READINESS_QUERY: Final = text("SELECT 1")


class DatabaseRuntimeClosedError(RuntimeError):
    """Reject new database work after runtime shutdown has started."""


class DatabaseOperationError(RuntimeError):
    """Report a database operation failure without exposing driver details."""

    def __init__(self, message: str, *, commit_confirmed: bool) -> None:
        super().__init__(message)
        self.commit_confirmed = commit_confirmed


class DatabaseCommitOutcomeUnknownError(DatabaseOperationError):
    """Report that a failed commit must not be treated as rolled back."""


class DatabaseOperationCleanupError(DatabaseOperationError):
    """Report failed rollback or close after the operation outcome is known."""


class _LifecycleState(Enum):
    ACTIVE = auto()
    SHUTTING_DOWN = auto()
    TERMINATED = auto()


class DatabaseOperation:
    """One infrastructure-owned Session with application-authorized commit."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._commit_authorized = False

    def authorize_commit(self) -> None:
        """Record the top-level operation's decision that success may commit."""
        self._commit_authorized = True

    @property
    def commit_authorized(self) -> bool:
        """Expose the commit decision to the infrastructure scope only."""
        return self._commit_authorized


class DatabaseRuntime:
    """Own one Engine, Session factory, and terminal application lifecycle."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session_factory = sessionmaker(
            bind=engine,
            class_=Session,
            autobegin=False,
            autoflush=False,
            expire_on_commit=True,
            close_resets_only=False,
            twophase=False,
        )
        self._state = _LifecycleState.ACTIVE
        self._state_lock = Lock()

    @contextmanager
    def operation(self) -> Generator[DatabaseOperation, None, None]:
        """Provide one explicit transaction and deterministically release it."""
        with self._state_lock:
            if self._state is not _LifecycleState.ACTIVE:
                raise DatabaseRuntimeClosedError("Database runtime is unavailable.")
            session: Session | None = None
            try:
                session = self._session_factory()
                session.begin()
            except Exception:
                if session is not None:
                    try:
                        session.close()
                    except Exception:
                        pass
                raise DatabaseOperationError(
                    "Database operation could not begin.", commit_confirmed=False
                ) from None
        operation = DatabaseOperation(session)
        try:
            yield operation
        except BaseException:
            try:
                session.rollback()
            except Exception:
                pass
            try:
                session.close()
            except Exception:
                pass
            raise

        if operation.commit_authorized:
            try:
                session.commit()
            except Exception:
                try:
                    session.rollback()
                except Exception:
                    pass
                try:
                    session.close()
                except Exception:
                    pass
                raise DatabaseCommitOutcomeUnknownError(
                    "Database commit did not complete with a confirmed outcome.",
                    commit_confirmed=False,
                ) from None
            try:
                session.close()
            except Exception:
                raise DatabaseOperationCleanupError(
                    "Database operation resource release did not complete.",
                    commit_confirmed=True,
                ) from None
            return

        try:
            session.rollback()
        except Exception:
            try:
                session.close()
            except Exception:
                pass
            raise DatabaseOperationCleanupError(
                "Database operation rollback did not complete.",
                commit_confirmed=False,
            ) from None
        try:
            session.close()
        except Exception:
            raise DatabaseOperationCleanupError(
                "Database operation resource release did not complete.",
                commit_confirmed=False,
            ) from None

    def is_ready(self) -> bool:
        """Check checkout, query execution, and connection release as one probe."""
        try:
            with self._state_lock:
                if self._state is not _LifecycleState.ACTIVE:
                    return False
                checked_out_connection = self._engine.connect()
            with checked_out_connection as connection:
                ready = cast(bool, connection.scalar(_READINESS_QUERY) == 1)
        except (PoolTimeoutError, DBAPIError, DisconnectionError):
            return False
        return ready

    def shutdown(self) -> None:
        """Make this runtime terminal and attempt Engine disposal exactly once.

        Application lifespan invokes this after framework serving work has drained.
        This runtime does not coordinate shutdown racing arbitrary active scopes,
        and Engine.dispose() is not treated as closing checked-out resources.
        """
        with self._state_lock:
            if self._state is not _LifecycleState.ACTIVE:
                return
            self._state = _LifecycleState.SHUTTING_DOWN
        try:
            self._engine.dispose()
        finally:
            with self._state_lock:
                self._state = _LifecycleState.TERMINATED
