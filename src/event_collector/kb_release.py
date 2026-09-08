"""Atomic Final Release activation, rollback, event audit, and request pinning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid

from event_collector.kb_contracts import KBReleaseContext


class KBActivationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ActivationResult:
    status: str
    namespace: str
    from_release_id: str | None
    to_release_id: str
    previous_lock_version: int
    resulting_lock_version: int
    activation_event_id: str | None
    operation: str
    committed_at: str | None


@dataclass(frozen=True)
class ActivationEvent:
    event_id: str
    namespace: str
    from_release_id: str | None
    to_release_id: str
    operation: str
    expected_lock_version: int
    resulting_lock_version: int
    actor: str
    reason: str
    created_at: str


class SQLiteReleaseController:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)

    def activate(
        self,
        *,
        namespace: str,
        target_release_id: str,
        expected_current_release_id: str | None,
        expected_lock_version: int,
        actor: str,
        reason: str,
    ) -> ActivationResult:
        return self._switch(
            namespace=namespace,
            target_release_id=target_release_id,
            expected_current_release_id=expected_current_release_id,
            expected_lock_version=expected_lock_version,
            actor=actor,
            reason=reason,
            operation="activate",
        )

    def rollback(
        self,
        *,
        namespace: str,
        target_release_id: str,
        expected_current_release_id: str,
        expected_lock_version: int,
        actor: str,
        reason: str,
    ) -> ActivationResult:
        return self._switch(
            namespace=namespace,
            target_release_id=target_release_id,
            expected_current_release_id=expected_current_release_id,
            expected_lock_version=expected_lock_version,
            actor=actor,
            reason=reason,
            operation="rollback",
        )

    def list_events(self, namespace: str) -> tuple[ActivationEvent, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM kb_activation_events WHERE namespace=? ORDER BY created_at,event_id",
                (namespace,),
            ).fetchall()
        finally:
            connection.close()
        return tuple(
            ActivationEvent(
                event_id=row["event_id"],
                namespace=row["namespace"],
                from_release_id=row["from_release_id"],
                to_release_id=row["to_release_id"],
                operation=row["operation"],
                expected_lock_version=row["expected_lock_version"],
                resulting_lock_version=row["resulting_lock_version"],
                actor=row["actor"],
                reason=row["reason"],
                created_at=row["created_at"],
            )
            for row in rows
        )

    def _switch(
        self,
        *,
        namespace: str,
        target_release_id: str,
        expected_current_release_id: str | None,
        expected_lock_version: int,
        actor: str,
        reason: str,
        operation: str,
    ) -> ActivationResult:
        if not actor.strip() or not reason.strip():
            raise KBActivationError("INVALID_ARGUMENT", "actor and reason are required")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            target = connection.execute(
                "SELECT namespace FROM kb_releases WHERE release_id=?",
                (target_release_id,),
            ).fetchone()
            if target is None or target["namespace"] != namespace:
                raise KBActivationError(
                    "KB_RELEASE_INVALID",
                    "target Final Release does not exist in namespace",
                )
            state = connection.execute(
                "SELECT * FROM kb_active_state WHERE namespace=?",
                (namespace,),
            ).fetchone()
            if state is None:
                raise KBActivationError("KB_NAMESPACE_NOT_FOUND", "active state is not seeded")
            if state["active_release_id"] == target_release_id:
                connection.rollback()
                return ActivationResult(
                    status="already_active",
                    namespace=namespace,
                    from_release_id=target_release_id,
                    to_release_id=target_release_id,
                    previous_lock_version=state["lock_version"],
                    resulting_lock_version=state["lock_version"],
                    activation_event_id=None,
                    operation=operation,
                    committed_at=None,
                )
            if (
                state["active_release_id"] != expected_current_release_id
                or state["lock_version"] != expected_lock_version
            ):
                raise KBActivationError(
                    "KB_ACTIVATION_CONFLICT",
                    "active release or lock version changed",
                )
            resulting_lock = state["lock_version"] + 1
            committed_at = _now()
            event_id = "kba_" + uuid.uuid4().hex
            connection.execute(
                """
                UPDATE kb_active_state
                SET active_release_id=?, lock_version=?, updated_at=?, updated_by=?
                WHERE namespace=?
                """,
                (target_release_id, resulting_lock, committed_at, actor, namespace),
            )
            connection.execute(
                """
                INSERT INTO kb_activation_events VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id,
                    namespace,
                    state["active_release_id"],
                    target_release_id,
                    operation,
                    expected_lock_version,
                    resulting_lock,
                    actor,
                    reason,
                    committed_at,
                ),
            )
            connection.commit()
            return ActivationResult(
                status="activated",
                namespace=namespace,
                from_release_id=state["active_release_id"],
                to_release_id=target_release_id,
                previous_lock_version=state["lock_version"],
                resulting_lock_version=resulting_lock,
                activation_event_id=event_id,
                operation=operation,
                committed_at=committed_at,
            )
        except KBActivationError:
            connection.rollback()
            raise
        except sqlite3.Error as error:
            connection.rollback()
            raise KBActivationError("KB_ACTIVATION_STORAGE_FAILURE", str(error)) from error
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


class SQLiteReleaseProvider:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)

    def pin_active_release(self, namespace: str, as_of: str) -> KBReleaseContext:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        try:
            row = connection.execute(
                """
                SELECT r.*
                FROM kb_active_state a
                JOIN kb_releases r ON r.release_id=a.active_release_id
                WHERE a.namespace=?
                """,
                (namespace,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KBActivationError(
                "KB_REQUIRED_UNAVAILABLE",
                f"namespace {namespace!r} has no active Final Release",
            )
        return KBReleaseContext(
            namespace=row["namespace"],
            release_id=row["release_id"],
            snapshot_id=row["snapshot_id"],
            snapshot_checksum=row["snapshot_checksum"],
            schema_version=row["schema_version"],
            resolver_policy_version=row["resolver_policy_version"],
            resolver_policy_checksum=row["resolver_policy_checksum"],
            freshness_policy_version=row["freshness_policy_version"],
            freshness_policy_checksum=row["freshness_policy_checksum"],
            signal_policy_version=row["signal_policy_version"],
            signal_policy_checksum=row["signal_policy_checksum"],
            evaluation_run_id=row["evaluation_run_id"],
            evaluation_manifest_hash=row["evaluation_manifest_hash"],
            id_algorithm_version=row["id_algorithm_version"],
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "ActivationEvent",
    "ActivationResult",
    "KBActivationError",
    "SQLiteReleaseController",
    "SQLiteReleaseProvider",
]
