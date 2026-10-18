from __future__ import annotations

import asyncio

from app.meeting.providers import GroqProvider, ProviderConfig, _groq_strict_schema, create_provider


def test_groq_provider_is_supported() -> None:
    provider = create_provider(ProviderConfig(
        provider="groq",
        model="qwen/qwen3.8-27b",
        api_key="test-key",
    ))
    assert isinstance(provider, GroqProvider)
    asyncio.run(provider.close())


def test_groq_schema_requires_every_object_property() -> None:
    schema = _groq_strict_schema()

    def check(value: object) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if value.get("type") == "object" and isinstance(properties, dict):
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(properties)
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(schema)
