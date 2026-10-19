from __future__ import annotations

import asyncio

from app.meeting.firestore_mirror import FirestoreMirror


class FakeReference:
    def __init__(self, path: str, writes: list) -> None:
        self.path, self.writes = path, writes

    def collection(self, name: str):
        return FakeCollection(f"{self.path}/{name}", self.writes)

    async def set(self, payload, merge=True):
        self.writes.append((self.path, payload, merge))


class FakeCollection:
    def __init__(self, path: str, writes: list) -> None:
        self.path, self.writes = path, writes

    def document(self, name: str):
        return FakeReference(f"{self.path}/{name}", self.writes)


class FakeClient:
    def __init__(self) -> None:
        self.writes = []

    def collection(self, name: str):
        return FakeCollection(name, self.writes)


def test_firestore_mirror_uses_stable_document_paths():
    async def scenario():
        mirror = FirestoreMirror(enabled=True, collection="meetings")
        mirror._client = FakeClient()

        await mirror.meeting_created("meeting-1", {"status": "active"})
        await mirror.transcript_added("meeting-1", "recording-1", 7, {"text": "hello"})
        await mirror.architecture_updated("meeting-1", {"version": 2})

        assert [item[0] for item in mirror._client.writes] == [
            "meetings/meeting-1",
            "meetings/meeting-1/transcripts/recording-1_00000007",
            "meetings/meeting-1/architecture/current",
        ]
    asyncio.run(scenario())


def test_disabled_firestore_mirror_is_a_noop():
    async def scenario():
        mirror = FirestoreMirror(enabled=False)
        await mirror.open()
        await mirror.meeting_created("meeting-1", {"status": "active"})
        await mirror.close()
    asyncio.run(scenario())
