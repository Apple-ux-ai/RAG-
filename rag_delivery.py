"""Persistent delivery state for the IM compatibility API."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Callable, Iterator


_PROCESSING_LEASE_SECONDS = 600
_RECORD_RETENTION_SECONDS = 24 * 60 * 60
RAG_SAFE_LOGGING = ContextVar("rag_safe_logging", default=False)
RAG_CONCISE_STYLE = ContextVar("rag_concise_style", default=False)


@dataclass(frozen=True)
class DeliveryScope:
    tenant_id: str
    channel: str
    customer_id: str
    conversation_id: str


@dataclass(frozen=True)
class DeliveryClaim:
    scope: DeliveryScope
    message_id: str
    lease_token: str


@dataclass(frozen=True)
class DeliveryRecord:
    state: str
    reply: str | None
    action: str | None
    trace_id: str | None


class DeliveryStore:
    """SQLite-backed idempotency and delivery acknowledgement ledger."""

    def __init__(
        self,
        path: str | Path = Path("data/rag_delivery.db"),
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_records (
                    tenant_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    customer_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN ('processing', 'pending', 'acknowledged')
                    ),
                    lease_token TEXT,
                    lease_expires_at REAL,
                    expires_at REAL,
                    reply TEXT,
                    action TEXT,
                    trace_id TEXT,
                    PRIMARY KEY (
                        tenant_id, channel, customer_id, conversation_id, message_id
                    )
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _validate_scope(scope: DeliveryScope, message_id: str) -> None:
        for name in ("tenant_id", "channel", "customer_id", "conversation_id"):
            value = getattr(scope, name, None)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("message_id must be a non-empty string")

    @staticmethod
    def _key(scope: DeliveryScope, message_id: str) -> tuple[str, ...]:
        return (
            scope.tenant_id,
            scope.channel,
            scope.customer_id,
            scope.conversation_id,
            message_id,
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> DeliveryRecord:
        return DeliveryRecord(
            state=row["state"],
            reply=row["reply"],
            action=row["action"],
            trace_id=row["trace_id"],
        )

    def claim(
        self, scope: DeliveryScope, message_id: str
    ) -> DeliveryClaim | DeliveryRecord:
        self._validate_scope(scope, message_id)
        now = self._clock()
        key = self._key(scope, message_id)
        fresh_token = secrets.token_urlsafe(32)

        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM delivery_records
                WHERE tenant_id = ? AND channel = ? AND customer_id = ?
                  AND conversation_id = ? AND message_id = ?
                """,
                key,
            ).fetchone()

            if row is not None:
                if row["state"] == "processing":
                    if row["lease_expires_at"] > now:
                        return self._record(row)
                    connection.execute(
                        """
                        UPDATE delivery_records
                        SET lease_token = ?, lease_expires_at = ?, expires_at = NULL
                        WHERE tenant_id = ? AND channel = ? AND customer_id = ?
                          AND conversation_id = ? AND message_id = ?
                        """,
                        (
                            fresh_token,
                            now + _PROCESSING_LEASE_SECONDS,
                            *key,
                        ),
                    )
                    return DeliveryClaim(scope, message_id, fresh_token)

                if row["expires_at"] is not None and row["expires_at"] > now:
                    return self._record(row)

                connection.execute(
                    """
                    DELETE FROM delivery_records
                    WHERE tenant_id = ? AND channel = ? AND customer_id = ?
                      AND conversation_id = ? AND message_id = ?
                    """,
                    key,
                )

            connection.execute(
                """
                INSERT INTO delivery_records (
                    tenant_id, channel, customer_id, conversation_id, message_id,
                    state, lease_token, lease_expires_at
                ) VALUES (?, ?, ?, ?, ?, 'processing', ?, ?)
                """,
                (*key, fresh_token, now + _PROCESSING_LEASE_SECONDS),
            )
            return DeliveryClaim(scope, message_id, fresh_token)

    def complete(
        self,
        claim: DeliveryClaim,
        *,
        reply: str,
        action: str,
        trace_id: str,
        send_reply: bool,
    ) -> None:
        if not isinstance(reply, str):
            raise ValueError("reply must be a string")
        if not isinstance(action, str) or not action.strip():
            raise ValueError("action must be a non-empty string")
        if not isinstance(trace_id, str) or not trace_id.strip():
            raise ValueError("trace_id must be a non-empty string")
        if not isinstance(send_reply, bool):
            raise ValueError("send_reply must be a boolean")

        key = self._key(claim.scope, claim.message_id)
        now = self._clock()
        state = "pending" if send_reply else "acknowledged"

        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT state, lease_token, lease_expires_at
                FROM delivery_records
                WHERE tenant_id = ? AND channel = ? AND customer_id = ?
                  AND conversation_id = ? AND message_id = ?
                """,
                key,
            ).fetchone()
            if (
                row is None
                or row["state"] != "processing"
                or row["lease_token"] != claim.lease_token
                or row["lease_expires_at"] <= now
            ):
                raise RuntimeError("delivery claim is no longer valid")

            connection.execute(
                """
                UPDATE delivery_records
                SET state = ?, lease_token = NULL, lease_expires_at = NULL,
                    expires_at = ?, reply = ?, action = ?, trace_id = ?
                WHERE tenant_id = ? AND channel = ? AND customer_id = ?
                  AND conversation_id = ? AND message_id = ?
                """,
                (
                    state,
                    now + _RECORD_RETENTION_SECONDS,
                    reply,
                    action,
                    trace_id,
                    *key,
                ),
            )

    def release(self, claim: DeliveryClaim) -> None:
        key = self._key(claim.scope, claim.message_id)
        with self._transaction() as connection:
            connection.execute(
                """
                DELETE FROM delivery_records
                WHERE tenant_id = ? AND channel = ? AND customer_id = ?
                  AND conversation_id = ? AND message_id = ?
                  AND state = 'processing' AND lease_token = ?
                """,
                (*key, claim.lease_token),
            )

    def acknowledge(self, scope: DeliveryScope, message_id: str) -> bool:
        self._validate_scope(scope, message_id)
        key = self._key(scope, message_id)
        now = self._clock()

        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT state, expires_at
                FROM delivery_records
                WHERE tenant_id = ? AND channel = ? AND customer_id = ?
                  AND conversation_id = ? AND message_id = ?
                """,
                key,
            ).fetchone()

            if row is None:
                return False
            if row["state"] == "processing":
                return False
            if row["expires_at"] is None or row["expires_at"] <= now:
                connection.execute(
                    """
                    DELETE FROM delivery_records
                    WHERE tenant_id = ? AND channel = ? AND customer_id = ?
                      AND conversation_id = ? AND message_id = ?
                    """,
                    key,
                )
                return False
            if row["state"] == "acknowledged":
                return True

            connection.execute(
                """
                UPDATE delivery_records
                SET state = 'acknowledged', expires_at = ?
                WHERE tenant_id = ? AND channel = ? AND customer_id = ?
                  AND conversation_id = ? AND message_id = ?
                """,
                (now + _RECORD_RETENTION_SECONDS, *key),
            )
            return True
