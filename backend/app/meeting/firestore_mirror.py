from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("ai_meeting_draw.meeting.firestore")


class FirestoreMirror:
    """Durable, async mirror for evaluator-visible meeting data.

    SQLite remains the ordered transactional working store used by the live
    pipeline. Firestore receives meeting, transcript, recording and current
    architecture snapshots so Cloud Run instance replacement does not erase
    the submission evidence.
    """

    def __init__(self, *, enabled: bool, project_id: str | None = None,
                 database: str = "(default)", collection: str = "meetings",
                 required: bool = False) -> None:
        self.enabled = enabled
        self.project_id = project_id
        self.database = database
        self.collection = collection
        self.required = required
        self._client: Any = None

    async def open(self) -> "FirestoreMirror":
        if not self.enabled:
            logger.info("Firestore mirror disabled")
            return self
        try:
            from google.cloud.firestore_v1.async_client import AsyncClient
            self._client = AsyncClient(project=self.project_id, database=self.database)
            if self.required:
                # Firestore reserves document IDs matching ``__.*__``.
                await self._client.collection(self.collection).document("connectivity-check").get()
            logger.info("Firestore mirror ready project=%s database=%s collection=%s",
                        self.project_id or "ADC default", self.database, self.collection)
        except Exception:
            logger.exception("Firestore mirror initialization failed")
            if self.required:
                raise
            self.enabled = False
        return self

    async def close(self) -> None:
        if self._client is not None:
            close = getattr(self._client, "close", None)
            if close:
                result = close()
                if hasattr(result, "__await__"):
                    await result
        self._client = None

    async def _set(self, reference: Any, payload: dict[str, Any], *, merge: bool = True) -> None:
        if not self.enabled or self._client is None:
            return
        try:
            await reference.set(payload, merge=merge)
        except Exception:
            logger.exception("Firestore write failed path=%s", getattr(reference, "path", "unknown"))
            if self.required:
                raise

    def _meeting(self, meeting_id: str) -> Any:
        return self._client.collection(self.collection).document(meeting_id)

    async def meeting_created(self, meeting_id: str, payload: dict[str, Any]) -> None:
        if not self.enabled or self._client is None:
            return
        await self._set(self._meeting(meeting_id), payload)

    async def meeting_finished(self, meeting_id: str, payload: dict[str, Any]) -> None:
        if not self.enabled or self._client is None:
            return
        await self._set(self._meeting(meeting_id), payload)

    async def recording_upserted(self, meeting_id: str, recording_id: str,
                                 payload: dict[str, Any]) -> None:
        if not self.enabled or self._client is None:
            return
        await self._set(self._meeting(meeting_id).collection("recordings").document(recording_id), payload)

    async def transcript_added(self, meeting_id: str, recording_id: str, sequence: int,
                               payload: dict[str, Any]) -> None:
        if not self.enabled or self._client is None:
            return
        key = f"{recording_id}_{sequence:08d}"
        await self._set(self._meeting(meeting_id).collection("transcripts").document(key), payload)

    async def architecture_updated(self, meeting_id: str, payload: dict[str, Any]) -> None:
        if not self.enabled or self._client is None:
            return
        await self._set(self._meeting(meeting_id).collection("architecture").document("current"), payload)

    async def correction_added(self, meeting_id: str, correction_id: str,
                               payload: dict[str, Any]) -> None:
        if not self.enabled or self._client is None:
            return
        await self._set(self._meeting(meeting_id).collection("corrections").document(correction_id), payload)
