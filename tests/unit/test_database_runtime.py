"""Unit tests for terminal database and operation-scope lifecycle."""

import threading
from collections.abc import Callable
from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError, DisconnectionError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session, SessionTransaction

import solar_platform.database_runtime as runtime_module
from solar_platform.database_runtime import (
    DatabaseCommitOutcomeUnknownError,
    DatabaseOperationCleanupError,
    DatabaseOperationError,
    DatabaseRuntime,
    DatabaseRuntimeClosedError,
)

pytestmark = pytest.mark.unit

SENTINEL = "DO-NOT-DISCLOSE-DATABASE-RUNTIME-1f6f91"


class _AdmissionLock:
    """Expose when a second thread attempts to enter the lifecycle lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempts = 0
        self.second_attempt_started = threading.Event()

    def __enter__(self) -> None:
        self._attempts += 1
        if self._attempts == 2:
            self.second_attempt_started.set()
        self._lock.acquire()

    def __exit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        self._lock.release()


def _join(thread: threading.Thread) -> None:
    thread.join(timeout=5)
    assert not thread.is_alive()


def _runtime_with_session(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[DatabaseRuntime, MagicMock, MagicMock, MagicMock, MagicMock]:
    engine = MagicMock(spec=Engine)
    factory = MagicMock()
    session = MagicMock(spec=Session)
    transaction = MagicMock(spec=SessionTransaction)
    session.begin.return_value = transaction
    factory.return_value = session
    sessionmaker_constructor = MagicMock(return_value=factory)
    monkeypatch.setattr(runtime_module, "sessionmaker", sessionmaker_constructor)
    runtime = DatabaseRuntime(cast(Engine, engine))
    return runtime, engine, factory, session, transaction


def test_session_factory_uses_the_reviewed_non_default_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _runtime, engine, _factory, _session, _transaction = _runtime_with_session(
        monkeypatch
    )

    cast(MagicMock, vars(runtime_module)["sessionmaker"]).assert_called_once_with(
        bind=engine,
        class_=Session,
        autobegin=False,
        autoflush=False,
        expire_on_commit=True,
        close_resets_only=False,
        twophase=False,
    )


def test_normal_exit_without_authorization_rolls_back_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _engine, factory, session, _transaction = _runtime_with_session(
        monkeypatch
    )

    with runtime.operation() as operation:
        assert operation.session is session

    factory.assert_called_once_with()
    session.begin.assert_called_once_with()
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()
    session.close.assert_called_once_with()


def test_explicit_authorization_commits_then_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _engine, _factory, session, _transaction = _runtime_with_session(
        monkeypatch
    )

    with runtime.operation() as operation:
        operation.authorize_commit()

    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    session.close.assert_called_once_with()


def test_begin_failure_closes_session_and_exposes_no_driver_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _engine, _factory, session, _transaction = _runtime_with_session(
        monkeypatch
    )
    session.begin.side_effect = RuntimeError(SENTINEL)

    with pytest.raises(DatabaseOperationError) as captured:
        with runtime.operation():
            pass

    assert captured.value.commit_confirmed is False
    assert SENTINEL not in repr(captured.value)
    assert captured.value.__cause__ is None
    session.close.assert_called_once_with()


def test_body_failure_preserves_original_error_despite_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _engine, _factory, session, _transaction = _runtime_with_session(
        monkeypatch
    )
    session.rollback.side_effect = RuntimeError(f"rollback-{SENTINEL}")
    session.close.side_effect = RuntimeError(f"close-{SENTINEL}")
    original = ValueError("application failure")

    with pytest.raises(ValueError) as captured:
        with runtime.operation():
            raise original

    assert captured.value is original
    session.rollback.assert_called_once_with()
    session.close.assert_called_once_with()


def test_commit_failure_is_unknown_and_never_exposes_driver_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _engine, _factory, session, _transaction = _runtime_with_session(
        monkeypatch
    )
    session.commit.side_effect = RuntimeError(f"commit-{SENTINEL}")
    session.rollback.side_effect = RuntimeError(f"rollback-{SENTINEL}")
    session.close.side_effect = RuntimeError(f"close-{SENTINEL}")

    with pytest.raises(DatabaseCommitOutcomeUnknownError) as captured:
        with runtime.operation() as operation:
            operation.authorize_commit()

    assert captured.value.commit_confirmed is False
    assert SENTINEL not in repr(captured.value)
    assert captured.value.__cause__ is None
    session.commit.assert_called_once_with()
    session.rollback.assert_called_once_with()
    session.close.assert_called_once_with()


@pytest.mark.parametrize("failure_point", ("rollback", "close"))
def test_uncommitted_cleanup_failure_has_a_known_uncommitted_outcome(
    monkeypatch: pytest.MonkeyPatch, failure_point: str
) -> None:
    runtime, _engine, _factory, session, _transaction = _runtime_with_session(
        monkeypatch
    )
    target = session.rollback if failure_point == "rollback" else session.close
    target.side_effect = RuntimeError(SENTINEL)

    with pytest.raises(DatabaseOperationCleanupError) as captured:
        with runtime.operation():
            pass

    assert captured.value.commit_confirmed is False
    assert SENTINEL not in repr(captured.value)
    session.close.assert_called_once_with()


def test_close_failure_after_confirmed_commit_preserves_commit_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _engine, _factory, session, _transaction = _runtime_with_session(
        monkeypatch
    )
    session.close.side_effect = RuntimeError(SENTINEL)

    with pytest.raises(DatabaseOperationCleanupError) as captured:
        with runtime.operation() as operation:
            operation.authorize_commit()

    assert captured.value.commit_confirmed is True
    assert SENTINEL not in repr(captured.value)
    session.commit.assert_called_once_with()


def _readiness_runtime(
    connection_behavior: Callable[[MagicMock, MagicMock], None] | None = None,
) -> tuple[DatabaseRuntime, MagicMock, MagicMock, MagicMock]:
    engine = MagicMock(spec=Engine)
    manager = MagicMock()
    connection = MagicMock(spec=Connection)
    manager.__enter__.return_value = connection
    engine.connect.return_value = manager
    connection.scalar.return_value = 1
    if connection_behavior is not None:
        connection_behavior(manager, connection)
    return DatabaseRuntime(cast(Engine, engine)), engine, manager, connection


def test_readiness_requires_checkout_query_and_successful_release() -> None:
    runtime, engine, manager, connection = _readiness_runtime()

    assert runtime.is_ready() is True

    engine.connect.assert_called_once_with()
    connection.scalar.assert_called_once_with(runtime_module._READINESS_QUERY)
    manager.__exit__.assert_called_once()


@pytest.mark.parametrize(
    "expected_failure",
    (
        PoolTimeoutError(SENTINEL),
        DBAPIError(None, None, RuntimeError(SENTINEL)),
        DisconnectionError(SENTINEL),
    ),
)
def test_expected_database_and_resource_failures_return_false(
    expected_failure: Exception,
) -> None:
    def fail_checkout(manager: MagicMock, _connection: MagicMock) -> None:
        manager.__enter__.side_effect = expected_failure

    runtime, _engine, _manager, _connection = _readiness_runtime(fail_checkout)

    assert runtime.is_ready() is False


def test_expected_connection_release_failure_returns_false() -> None:
    def fail_release(manager: MagicMock, _connection: MagicMock) -> None:
        manager.__exit__.side_effect = DBAPIError(None, None, RuntimeError(SENTINEL))

    runtime, _engine, _manager, _connection = _readiness_runtime(fail_release)

    assert runtime.is_ready() is False


def test_unexpected_readiness_defect_propagates() -> None:
    def fail_with_defect(_manager: MagicMock, connection: MagicMock) -> None:
        connection.scalar.side_effect = RuntimeError(SENTINEL)

    runtime, _engine, _manager, _connection = _readiness_runtime(fail_with_defect)

    with pytest.raises(RuntimeError, match=SENTINEL):
        runtime.is_ready()


@pytest.mark.parametrize("dispose_raises", (False, True))
def test_shutdown_is_terminal_and_disposal_is_attempted_once(
    dispose_raises: bool,
) -> None:
    runtime, engine, _manager, _connection = _readiness_runtime()
    if dispose_raises:
        engine.dispose.side_effect = RuntimeError(SENTINEL)

    if dispose_raises:
        with pytest.raises(RuntimeError, match=SENTINEL):
            runtime.shutdown()
    else:
        runtime.shutdown()
    runtime.shutdown()

    engine.dispose.assert_called_once_with()
    engine.connect.assert_not_called()
    assert runtime.is_ready() is False
    with pytest.raises(DatabaseRuntimeClosedError):
        with runtime.operation():
            pass
    engine.connect.assert_not_called()


def test_shutdown_rejects_new_work_before_disposal_runs() -> None:
    runtime, engine, _manager, _connection = _readiness_runtime()

    def inspect_terminal_admission() -> None:
        assert runtime.is_ready() is False
        with pytest.raises(DatabaseRuntimeClosedError):
            with runtime.operation():
                pass

    engine.dispose.side_effect = inspect_terminal_admission

    runtime.shutdown()

    engine.dispose.assert_called_once_with()
    engine.connect.assert_not_called()


def test_operation_admission_holds_lifecycle_lock_through_begin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, engine, factory, session, _transaction = _runtime_with_session(monkeypatch)
    admission_lock = _AdmissionLock()
    runtime.__dict__["_state_lock"] = admission_lock
    factory_entered = threading.Event()
    allow_factory = threading.Event()
    begin_completed = threading.Event()
    scope_entered = threading.Event()
    allow_scope_exit = threading.Event()
    dispose_called = threading.Event()
    failures: list[BaseException] = []

    def blocking_factory() -> MagicMock:
        factory_entered.set()
        assert allow_factory.wait(timeout=5)
        return session

    def tracked_begin() -> MagicMock:
        begin_completed.set()
        return MagicMock(spec=SessionTransaction)

    def tracked_dispose() -> None:
        assert begin_completed.is_set()
        dispose_called.set()

    factory.side_effect = blocking_factory
    session.begin.side_effect = tracked_begin
    engine.dispose.side_effect = tracked_dispose

    def use_operation() -> None:
        try:
            with runtime.operation():
                scope_entered.set()
                assert allow_scope_exit.wait(timeout=5)
        except BaseException as exception:
            failures.append(exception)

    operation_thread = threading.Thread(target=use_operation)
    operation_thread.start()
    assert factory_entered.wait(timeout=5)

    shutdown_thread = threading.Thread(target=runtime.shutdown)
    shutdown_thread.start()
    assert admission_lock.second_attempt_started.wait(timeout=5)
    assert not dispose_called.is_set()

    allow_factory.set()
    assert begin_completed.wait(timeout=5)
    assert scope_entered.wait(timeout=5)
    assert dispose_called.wait(timeout=5)
    allow_scope_exit.set()

    _join(operation_thread)
    _join(shutdown_thread)
    assert failures == []
    factory.assert_called_once_with()
    session.begin.assert_called_once_with()
    engine.dispose.assert_called_once_with()


def test_readiness_admission_holds_lifecycle_lock_through_checkout() -> None:
    runtime, engine, manager, connection = _readiness_runtime()
    admission_lock = _AdmissionLock()
    runtime.__dict__["_state_lock"] = admission_lock
    checkout_entered = threading.Event()
    allow_checkout = threading.Event()
    checkout_completed = threading.Event()
    dispose_called = threading.Event()
    readiness_results: list[bool] = []

    def blocking_connect() -> MagicMock:
        checkout_entered.set()
        assert allow_checkout.wait(timeout=5)
        checkout_completed.set()
        return manager

    def tracked_dispose() -> None:
        assert checkout_completed.is_set()
        dispose_called.set()

    engine.connect.side_effect = blocking_connect
    engine.dispose.side_effect = tracked_dispose

    readiness_thread = threading.Thread(
        target=lambda: readiness_results.append(runtime.is_ready())
    )
    readiness_thread.start()
    assert checkout_entered.wait(timeout=5)

    shutdown_thread = threading.Thread(target=runtime.shutdown)
    shutdown_thread.start()
    assert admission_lock.second_attempt_started.wait(timeout=5)
    assert not dispose_called.is_set()

    allow_checkout.set()
    assert checkout_completed.wait(timeout=5)
    assert dispose_called.wait(timeout=5)

    _join(readiness_thread)
    _join(shutdown_thread)
    assert readiness_results == [True]
    engine.connect.assert_called_once_with()
    connection.scalar.assert_called_once_with(runtime_module._READINESS_QUERY)
    manager.__exit__.assert_called_once()
    engine.dispose.assert_called_once_with()


def test_callers_starting_during_disposal_cannot_acquire_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, engine, factory, _session, _transaction = _runtime_with_session(
        monkeypatch
    )
    disposal_started = threading.Event()
    allow_disposal = threading.Event()
    shutdown_failures: list[BaseException] = []

    def blocking_dispose() -> None:
        disposal_started.set()
        assert allow_disposal.wait(timeout=5)

    def shut_down() -> None:
        try:
            runtime.shutdown()
        except BaseException as exception:
            shutdown_failures.append(exception)

    engine.dispose.side_effect = blocking_dispose
    shutdown_thread = threading.Thread(target=shut_down)
    shutdown_thread.start()
    assert disposal_started.wait(timeout=5)

    with pytest.raises(DatabaseRuntimeClosedError):
        with runtime.operation():
            pass
    assert runtime.is_ready() is False
    factory.assert_not_called()
    engine.connect.assert_not_called()

    allow_disposal.set()
    _join(shutdown_thread)
    assert shutdown_failures == []
    engine.dispose.assert_called_once_with()


def test_each_operation_receives_a_fresh_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _engine, factory, first, _transaction = _runtime_with_session(monkeypatch)
    second = MagicMock(spec=Session)
    second.begin.return_value = MagicMock(spec=SessionTransaction)
    factory.side_effect = [first, second]

    with runtime.operation() as first_operation:
        pass
    with runtime.operation() as second_operation:
        pass

    assert first_operation.session is first
    assert second_operation.session is second
    assert first_operation.session is not second_operation.session
    assert factory.call_count == 2


def test_operation_object_does_not_offer_commit_or_rollback() -> None:
    public_members = set(dir(runtime_module.DatabaseOperation))

    assert "commit" not in public_members
    assert "rollback" not in public_members
    assert "authorize_commit" in public_members
