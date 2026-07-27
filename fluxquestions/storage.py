"""Persistent storage for FluxQuestions.

Each question is stored in its own Red Config custom-group document so the cog
can retain a permanent Q&A history without rewriting one ever-growing guild
state dictionary.
"""

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from typing import Any, Dict, List, Optional

from redbot.core import Config

QUESTION_GROUP = "FLUXQUESTIONS_QUESTION"
SOURCE_GROUP = "FLUXQUESTIONS_SOURCE"

GUILD_SCHEMA = 1
QUESTION_SCHEMA = 1
DEFAULT_EDIT_WINDOW_SECONDS = 30 * 60
VALID_STATUSES = {"pending", "answered", "removed"}


class StorageError(RuntimeError):
    """Base exception for FluxQuestions storage errors."""


class QuestionNotFound(StorageError):
    """Raised when a question number is unknown."""


class StorageConflict(StorageError):
    """Raised when a requested state transition is not valid."""


class SourceAlreadySubmitted(StorageConflict):
    """Raised when an original Fluxer message is already a question."""

    def __init__(self, question_number: int):
        self.question_number = question_number
        super().__init__(f"That message is already Question #{question_number}.")


class QuestionStorage:
    """Durable storage and state transitions for FluxQuestions."""

    def __init__(self, config: Config):
        self.config = config
        self._guild_locks: Dict[int, asyncio.Lock] = {}

        self.config.register_guild(
            schema=GUILD_SCHEMA,
            counter=0,
            submitted=0,
            answered=0,
            removed=0,
            questions_channel=None,
            answers_channel=None,
            log_channel=None,
            question_emoji={
                "type": "unicode",
                "value": "❓",
                "id": None,
                "name": None,
            },
            submitter_role_ids=[],
            editor_role_ids=[],
            operator_role_ids=[],
            source_channel_ids=[],
            author_edit_window_seconds=DEFAULT_EDIT_WINDOW_SECONDS,
            answer_embed_version=0,
        )

        # (guild_id, question_number)
        self.config.init_custom(QUESTION_GROUP, 2)
        self.config.register_custom(
            QUESTION_GROUP,
            exists=False,
            schema=QUESTION_SCHEMA,
            number=0,
            author_id=None,
            submitted_by_id=None,
            source_channel_id=None,
            source_message_id=None,
            pending_channel_id=None,
            pending_message_id=None,
            content="",
            created_at=0,
            edited_at=None,
            current_revision=0,
            revisions=[],
            status="pending",
            # These fields are semantically optional, but their registered
            # defaults must be dictionaries because Red's Group.all() merges
            # stored mappings recursively with registered defaults. A None
            # default combined with a later stored dict triggers Red's known
            # "non-dict default with a dict value" Config failure.
            votes={},
            answer={},
            removal={},
            operation={},
        )

        # (guild_id, source_message_id) -> question_number
        # This is persisted so duplicate prevention survives restarts/crashes.
        self.config.init_custom(SOURCE_GROUP, 2)
        self.config.register_custom(
            SOURCE_GROUP,
            question_number=None,
            created_at=0,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def lock_for(self, guild_id: int) -> asyncio.Lock:
        guild_id = self._positive_int(guild_id, "guild_id")
        lock = self._guild_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._guild_locks[guild_id] = lock
        return lock

    @staticmethod
    def unix_now() -> int:
        return int(time.time())

    @staticmethod
    def format_timestamp(timestamp: Any) -> str:
        try:
            value = int(timestamp)
        except (TypeError, ValueError):
            return "Unknown"

        if value <= 0:
            return "Unknown"

        return f"<t:{value}:F> (<t:{value}:R>)"

    @staticmethod
    def _positive_int(value: Any, name: str) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be an integer.") from exc

        if result < 1:
            raise ValueError(f"{name} must be greater than zero.")
        return result

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        try:
            result = int(value)
        except (TypeError, ValueError):
            return None
        return result if result > 0 else None

    @staticmethod
    def _content(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _votes(cls, raw: Any, captured_at: Optional[int] = None) -> Dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {}

        def count(value: Any) -> int:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return 0

        return {
            "up": count(raw.get("up")),
            "down": count(raw.get("down")),
            "conflicts": count(raw.get("conflicts")),
            "exact": bool(raw.get("exact", False)),
            "captured_at": (
                cls._optional_int(captured_at)
                or cls._optional_int(raw.get("captured_at"))
                or cls.unix_now()
            ),
        }

    def _question_scope(self, guild_id: int, number: int):
        return self.config.custom(
            QUESTION_GROUP,
            self._positive_int(guild_id, "guild_id"),
            self._positive_int(number, "number"),
        )

    def _source_scope(self, guild_id: int, source_message_id: int):
        return self.config.custom(
            SOURCE_GROUP,
            self._positive_int(guild_id, "guild_id"),
            self._positive_int(source_message_id, "source_message_id"),
        )

    @classmethod
    def _normalise_record(
        cls,
        raw: Any,
        number_hint: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict) or not raw.get("exists", False):
            return None

        number = cls._optional_int(raw.get("number")) or cls._optional_int(number_hint)
        content = cls._content(raw.get("content"))
        if number is None or not content:
            return None

        status = str(raw.get("status") or "pending").lower()
        if status not in VALID_STATUSES:
            status = "pending"

        record = deepcopy(raw)
        record.update(
            {
                "exists": True,
                "schema": QUESTION_SCHEMA,
                "number": number,
                "content": content,
                "status": status,
                "created_at": cls._optional_int(raw.get("created_at")) or cls.unix_now(),
            }
        )

        if not isinstance(record.get("revisions"), list):
            record["revisions"] = []
        if not isinstance(record.get("answer"), dict):
            record["answer"] = None
        if not isinstance(record.get("removal"), dict):
            record["removal"] = None
        if not isinstance(record.get("operation"), dict):
            record["operation"] = None

        return record

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_question(
        self,
        guild_id: int,
        number: int,
    ) -> Optional[Dict[str, Any]]:
        raw = await self._question_scope(guild_id, number).all()
        return self._normalise_record(raw, number)

    async def require_question(
        self,
        guild_id: int,
        number: int,
    ) -> Dict[str, Any]:
        record = await self.get_question(guild_id, number)
        if record is None:
            raise QuestionNotFound(f"Question #{number} does not exist.")
        return record

    async def list_questions(
        self,
        guild_id: int,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        guild_id = self._positive_int(guild_id, "guild_id")

        if status is not None:
            status = status.lower().strip()
            if status not in VALID_STATUSES:
                raise ValueError(f"Unknown status: {status}")

        raw = await self.config.custom(QUESTION_GROUP, guild_id).all()
        records: List[Dict[str, Any]] = []

        if isinstance(raw, dict):
            for number_text, value in raw.items():
                number = self._optional_int(number_text)
                if number is None:
                    continue

                record = self._normalise_record(value, number)
                if record is None:
                    continue
                if status is not None and record["status"] != status:
                    continue
                records.append(record)

        records.sort(key=lambda item: int(item["number"]))
        return records

    async def find_by_source_message(
        self,
        guild_id: int,
        source_message_id: int,
        *,
        repair_index: bool = True,
    ) -> Optional[Dict[str, Any]]:
        guild_id = self._positive_int(guild_id, "guild_id")
        source_message_id = self._positive_int(source_message_id, "source_message_id")
        source_scope = self._source_scope(guild_id, source_message_id)

        indexed_number = self._optional_int(await source_scope.question_number())
        if indexed_number is not None:
            indexed = await self.get_question(guild_id, indexed_number)
            if indexed and indexed.get("source_message_id") == source_message_id:
                return indexed
            if repair_index:
                await source_scope.clear()

        # Crash safety: if the question record was written but the source index
        # was not, scan once and rebuild the index instead of allowing a duplicate.
        for record in await self.list_questions(guild_id):
            if record.get("source_message_id") != source_message_id:
                continue

            if repair_index:
                await source_scope.set(
                    {
                        "question_number": record["number"],
                        "created_at": record["created_at"],
                    }
                )
            return record

        return None

    # ------------------------------------------------------------------
    # Question creation / pending message
    # ------------------------------------------------------------------

    async def create_question(
        self,
        guild_id: int,
        *,
        author_id: int,
        submitted_by_id: int,
        source_channel_id: int,
        source_message_id: int,
        pending_channel_id: int,
        content: str,
        created_at: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Reserve a permanent question number and create its canonical record.

        The counter is persisted first. A hard crash can therefore leave a gap,
        but a reserved question number is never reused.
        """

        guild_id = self._positive_int(guild_id, "guild_id")
        author_id = self._positive_int(author_id, "author_id")
        submitted_by_id = self._positive_int(submitted_by_id, "submitted_by_id")
        source_channel_id = self._positive_int(source_channel_id, "source_channel_id")
        source_message_id = self._positive_int(source_message_id, "source_message_id")
        pending_channel_id = self._positive_int(pending_channel_id, "pending_channel_id")
        content = self._content(content)
        if not content:
            raise ValueError("Question content cannot be empty.")

        timestamp = self._optional_int(created_at) or self.unix_now()

        async with self.lock_for(guild_id):
            existing = await self.find_by_source_message(
                guild_id,
                source_message_id,
                repair_index=True,
            )
            if existing is not None:
                raise SourceAlreadySubmitted(int(existing["number"]))

            guild_scope = self.config.guild_from_id(guild_id)
            number = max(0, int(await guild_scope.counter())) + 1

            # Reserve before creating the document: gaps are safer than reuse.
            await guild_scope.counter.set(number)

            record = {
                "exists": True,
                "schema": QUESTION_SCHEMA,
                "number": number,
                "author_id": author_id,
                "submitted_by_id": submitted_by_id,
                "source_channel_id": source_channel_id,
                "source_message_id": source_message_id,
                "pending_channel_id": pending_channel_id,
                "pending_message_id": None,
                "content": content,
                "created_at": timestamp,
                "edited_at": None,
                "current_revision": 1,
                "revisions": [
                    {
                        "revision": 1,
                        "content": content,
                        "editor_id": author_id,
                        "created_at": timestamp,
                        "kind": "submitted",
                    }
                ],
                "status": "pending",
                "votes": None,
                "answer": None,
                "removal": None,
                "operation": None,
            }

            await self._question_scope(guild_id, number).set(record)
            await self._source_scope(guild_id, source_message_id).set(
                {
                    "question_number": number,
                    "created_at": timestamp,
                }
            )
            await guild_scope.submitted.set(
                max(0, int(await guild_scope.submitted())) + 1
            )

            return deepcopy(record)

    async def attach_pending_message(
        self,
        guild_id: int,
        number: int,
        *,
        channel_id: int,
        message_id: int,
    ) -> Dict[str, Any]:
        guild_id = self._positive_int(guild_id, "guild_id")
        channel_id = self._positive_int(channel_id, "channel_id")
        message_id = self._positive_int(message_id, "message_id")

        async with self.lock_for(guild_id):
            record = await self.require_question(guild_id, number)
            if record["status"] != "pending":
                raise StorageConflict(f"Question #{number} is not pending.")

            record["pending_channel_id"] = channel_id
            record["pending_message_id"] = message_id
            await self._question_scope(guild_id, number).set(record)
            return deepcopy(record)

    # ------------------------------------------------------------------
    # Question revisions
    # ------------------------------------------------------------------

    async def edit_question(
        self,
        guild_id: int,
        number: int,
        *,
        new_content: str,
        editor_id: int,
        kind: str,
        edited_at: Optional[int] = None,
        reverted_from_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        guild_id = self._positive_int(guild_id, "guild_id")
        editor_id = self._positive_int(editor_id, "editor_id")
        content = self._content(new_content)
        if not content:
            raise ValueError("Question content cannot be empty.")

        timestamp = self._optional_int(edited_at) or self.unix_now()

        async with self.lock_for(guild_id):
            record = await self.require_question(guild_id, number)
            if record["status"] != "pending":
                raise StorageConflict("Only pending questions can be edited normally.")
            if record.get("operation") is not None:
                raise StorageConflict(f"Question #{number} has an unfinished operation.")
            if content == record["content"]:
                return deepcopy(record)

            revision_number = int(record.get("current_revision") or 0) + 1
            revision: Dict[str, Any] = {
                "revision": revision_number,
                "content": content,
                "editor_id": editor_id,
                "created_at": timestamp,
                "kind": str(kind or "edit").lower(),
            }
            if reverted_from_revision is not None:
                revision["reverted_from_revision"] = self._positive_int(
                    reverted_from_revision,
                    "reverted_from_revision",
                )

            record["content"] = content
            record["edited_at"] = timestamp
            record["current_revision"] = revision_number
            record.setdefault("revisions", []).append(revision)
            await self._question_scope(guild_id, number).set(record)
            return deepcopy(record)

    async def revert_question(
        self,
        guild_id: int,
        number: int,
        *,
        revision_number: int,
        editor_id: int,
    ) -> Dict[str, Any]:
        revision_number = self._positive_int(revision_number, "revision_number")
        record = await self.require_question(guild_id, number)

        target = next(
            (
                revision
                for revision in record.get("revisions", [])
                if self._optional_int(revision.get("revision")) == revision_number
            ),
            None,
        )
        if target is None:
            raise StorageConflict(
                f"Question #{number} has no revision {revision_number}."
            )

        return await self.edit_question(
            guild_id,
            number,
            new_content=target["content"],
            editor_id=editor_id,
            kind="staff_revert",
            reverted_from_revision=revision_number,
        )

    # ------------------------------------------------------------------
    # Answer lifecycle
    # ------------------------------------------------------------------

    async def begin_answer(
        self,
        guild_id: int,
        number: int,
        *,
        answer_content: str,
        operator_id: int,
        votes: Dict[str, Any],
        started_at: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Persist an answer draft before posting it to Fluxer.

        ``operation`` remains stored until ``complete_answer`` succeeds. If the
        process dies in between, startup recovery can see exactly what was being
        published instead of silently forgetting the operation.
        """

        guild_id = self._positive_int(guild_id, "guild_id")
        operator_id = self._positive_int(operator_id, "operator_id")
        content = self._content(answer_content)
        if not content:
            raise ValueError("Answer content cannot be empty.")

        timestamp = self._optional_int(started_at) or self.unix_now()

        async with self.lock_for(guild_id):
            record = await self.require_question(guild_id, number)
            if record["status"] != "pending":
                raise StorageConflict(f"Question #{number} is already {record['status']}.")
            if record.get("operation") is not None:
                raise StorageConflict(f"Question #{number} has an unfinished operation.")

            record["votes"] = self._votes(votes, captured_at=timestamp)
            record["answer"] = {
                "content": content,
                "author_id": operator_id,
                "created_at": timestamp,
                "edited_at": None,
                "channel_id": None,
                "message_id": None,
                "current_revision": 1,
                "revisions": [
                    {
                        "revision": 1,
                        "content": content,
                        "editor_id": operator_id,
                        "created_at": timestamp,
                        "kind": "answer",
                    }
                ],
            }
            record["operation"] = {
                "type": "answer",
                "actor_id": operator_id,
                "started_at": timestamp,
            }

            await self._question_scope(guild_id, number).set(record)
            return deepcopy(record)

    async def complete_answer(
        self,
        guild_id: int,
        number: int,
        *,
        channel_id: int,
        message_id: int,
    ) -> Dict[str, Any]:
        guild_id = self._positive_int(guild_id, "guild_id")
        channel_id = self._positive_int(channel_id, "channel_id")
        message_id = self._positive_int(message_id, "message_id")

        async with self.lock_for(guild_id):
            record = await self.require_question(guild_id, number)

            # Idempotent completion helps crash recovery attach a discovered
            # answer message without double-counting it.
            if record["status"] == "answered":
                answer = record.get("answer") or {}
                if answer.get("channel_id") == channel_id and answer.get("message_id") == message_id:
                    return deepcopy(record)
                raise StorageConflict(f"Question #{number} is already answered elsewhere.")

            operation = record.get("operation") or {}
            if record["status"] != "pending" or operation.get("type") != "answer":
                raise StorageConflict(f"Question #{number} has no staged answer.")

            answer = record.get("answer")
            if not isinstance(answer, dict):
                raise StorageConflict(f"Question #{number} has invalid answer state.")

            answer["channel_id"] = channel_id
            answer["message_id"] = message_id
            record["answer"] = answer
            record["status"] = "answered"
            record["operation"] = None

            await self._question_scope(guild_id, number).set(record)

            guild_scope = self.config.guild_from_id(guild_id)
            await guild_scope.answered.set(
                max(0, int(await guild_scope.answered())) + 1
            )
            return deepcopy(record)

    async def abort_answer(self, guild_id: int, number: int) -> Dict[str, Any]:
        """Clear a staged answer after a handled send failure."""

        guild_id = self._positive_int(guild_id, "guild_id")

        async with self.lock_for(guild_id):
            record = await self.require_question(guild_id, number)
            operation = record.get("operation") or {}

            if record["status"] != "pending":
                raise StorageConflict("Only pending questions can abort an answer.")
            if operation.get("type") != "answer":
                return deepcopy(record)

            record["votes"] = None
            record["answer"] = None
            record["operation"] = None
            await self._question_scope(guild_id, number).set(record)
            return deepcopy(record)

    async def edit_answer(
        self,
        guild_id: int,
        number: int,
        *,
        new_content: str,
        editor_id: int,
        kind: str = "answer_edit",
        reverted_from_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        guild_id = self._positive_int(guild_id, "guild_id")
        editor_id = self._positive_int(editor_id, "editor_id")
        content = self._content(new_content)
        if not content:
            raise ValueError("Answer content cannot be empty.")

        async with self.lock_for(guild_id):
            record = await self.require_question(guild_id, number)
            if record["status"] != "answered":
                raise StorageConflict(f"Question #{number} has not been answered.")

            answer = record.get("answer")
            if not isinstance(answer, dict):
                raise StorageConflict(f"Question #{number} has no stored answer.")
            if content == answer.get("content"):
                return deepcopy(record)

            timestamp = self.unix_now()
            revision_number = int(answer.get("current_revision") or 0) + 1
            revision: Dict[str, Any] = {
                "revision": revision_number,
                "content": content,
                "editor_id": editor_id,
                "created_at": timestamp,
                "kind": str(kind or "answer_edit").lower(),
            }
            if reverted_from_revision is not None:
                revision["reverted_from_revision"] = self._positive_int(
                    reverted_from_revision,
                    "reverted_from_revision",
                )

            answer["content"] = content
            answer["edited_at"] = timestamp
            answer["current_revision"] = revision_number
            answer.setdefault("revisions", []).append(revision)
            record["answer"] = answer
            await self._question_scope(guild_id, number).set(record)
            return deepcopy(record)

    async def attach_answer_message(
        self,
        guild_id: int,
        number: int,
        *,
        channel_id: int,
        message_id: int,
    ) -> Dict[str, Any]:
        """Update an answered question after its answer message is recreated."""

        guild_id = self._positive_int(guild_id, "guild_id")
        channel_id = self._positive_int(channel_id, "channel_id")
        message_id = self._positive_int(message_id, "message_id")

        async with self.lock_for(guild_id):
            record = await self.require_question(guild_id, number)
            if record["status"] != "answered" or not isinstance(record.get("answer"), dict):
                raise StorageConflict(f"Question #{number} is not answered.")

            record["answer"]["channel_id"] = channel_id
            record["answer"]["message_id"] = message_id
            await self._question_scope(guild_id, number).set(record)
            return deepcopy(record)

    # ------------------------------------------------------------------
    # Soft removal
    # ------------------------------------------------------------------

    async def remove_question(
        self,
        guild_id: int,
        number: int,
        *,
        actor_id: int,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        guild_id = self._positive_int(guild_id, "guild_id")
        actor_id = self._positive_int(actor_id, "actor_id")

        async with self.lock_for(guild_id):
            record = await self.require_question(guild_id, number)
            if record["status"] == "removed":
                return deepcopy(record)
            if record["status"] != "pending":
                raise StorageConflict("Only pending questions use soft removal.")
            if record.get("operation") is not None:
                raise StorageConflict(f"Question #{number} has an unfinished operation.")

            record["status"] = "removed"
            record["removal"] = {
                "actor_id": actor_id,
                "created_at": self.unix_now(),
                "reason": self._content(reason) or None,
            }

            # Store the removal before deleting the pending Fluxer message.
            await self._question_scope(guild_id, number).set(record)

            guild_scope = self.config.guild_from_id(guild_id)
            await guild_scope.removed.set(
                max(0, int(await guild_scope.removed())) + 1
            )
            return deepcopy(record)

    # ------------------------------------------------------------------
    # Restart / crash reconciliation
    # ------------------------------------------------------------------

    async def reconcile_guild(self, guild_id: int) -> Dict[str, Any]:
        """Repair derived state and report work the main cog should resynchronise.

        This method performs Config I/O only. It deliberately does not fetch,
        post, edit or delete Fluxer messages; ``fluxquestions.py`` will use this
        report during cog startup to reconcile the live messages with the
        canonical records stored here.
        """

        guild_id = self._positive_int(guild_id, "guild_id")

        async with self.lock_for(guild_id):
            records = await self.list_questions(guild_id)
            canonical_sources: Dict[int, int] = {}
            source_conflicts: List[Dict[str, int]] = []

            for record in records:
                source_message_id = self._optional_int(record.get("source_message_id"))
                if source_message_id is None:
                    continue

                if source_message_id in canonical_sources:
                    source_conflicts.append(
                        {
                            "source_message_id": source_message_id,
                            "first_question": canonical_sources[source_message_id],
                            "duplicate_question": int(record["number"]),
                        }
                    )
                    continue

                canonical_sources[source_message_id] = int(record["number"])

            # Remove stale/wrong source indexes.
            raw_indexes = await self.config.custom(SOURCE_GROUP, guild_id).all()
            if isinstance(raw_indexes, dict):
                for source_text, raw in raw_indexes.items():
                    source_message_id = self._optional_int(source_text)
                    if source_message_id is None:
                        continue

                    expected = canonical_sources.get(source_message_id)
                    indexed = (
                        self._optional_int(raw.get("question_number"))
                        if isinstance(raw, dict)
                        else None
                    )
                    if expected is None or indexed != expected:
                        await self._source_scope(guild_id, source_message_id).clear()

            # Rebuild the canonical index from permanent records.
            records_by_number = {int(item["number"]): item for item in records}
            for source_message_id, number in canonical_sources.items():
                record = records_by_number[number]
                await self._source_scope(guild_id, source_message_id).set(
                    {
                        "question_number": number,
                        "created_at": int(record["created_at"]),
                    }
                )

            max_number = max(
                (int(record["number"]) for record in records),
                default=0,
            )
            answered = sum(1 for record in records if record["status"] == "answered")
            removed = sum(1 for record in records if record["status"] == "removed")

            guild_scope = self.config.guild_from_id(guild_id)
            current_counter = max(0, int(await guild_scope.counter()))

            # Never lower the counter: a crash may have reserved an unused number.
            counter = max(current_counter, max_number)
            await guild_scope.counter.set(counter)
            await guild_scope.submitted.set(len(records))
            await guild_scope.answered.set(answered)
            await guild_scope.removed.set(removed)
            await guild_scope.schema.set(GUILD_SCHEMA)

            return {
                "guild_id": guild_id,
                "counter": counter,
                "records": len(records),
                "answered": answered,
                "removed": removed,
                "pending": [
                    deepcopy(record)
                    for record in records
                    if record["status"] == "pending"
                ],
                "incomplete_operations": [
                    deepcopy(record)
                    for record in records
                    if record.get("operation") is not None
                ],
                "source_conflicts": source_conflicts,
            }

    async def guild_statistics(self, guild_id: int) -> Dict[str, int]:
        guild_id = self._positive_int(guild_id, "guild_id")
        guild_scope = self.config.guild_from_id(guild_id)

        return {
            "counter": max(0, int(await guild_scope.counter())),
            "submitted": max(0, int(await guild_scope.submitted())),
            "answered": max(0, int(await guild_scope.answered())),
            "removed": max(0, int(await guild_scope.removed())),
            "pending": len(await self.list_questions(guild_id, status="pending")),
        }
