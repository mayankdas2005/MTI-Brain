"""Mock Bedrock LLM responses by node/prompt pattern."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


class MockBedrockFixture:
    """Standalone mock for Bedrock — usable without the conftest patch context.

    Usage in tests:
        mock = MockBedrockFixture()
        mock.set_response("intake", '{"type": "analytics"}')
        result = await mock.get_llm("fast").ainvoke(some_prompt)
    """

    def __init__(self):
        self._responses: dict[str, str] = {}
        self._call_log: list[dict] = []

    def get_llm(self, tier: str = "balanced") -> MagicMock:
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=self._invoke)
        return llm

    async def _invoke(self, prompt, **kwargs):
        self._call_log.append({"prompt": str(prompt)[:200], "kwargs": kwargs})
        prompt_text = str(prompt)
        for key, response in self._responses.items():
            if key.lower() in prompt_text.lower():
                msg = MagicMock()
                msg.content = response
                return msg
        msg = MagicMock()
        msg.content = "{}"
        return msg

    def set_response(self, key: str, response: str):
        self._responses[key] = response

    def set_responses(self, mapping: dict[str, str]):
        self._responses.update(mapping)

    @property
    def call_count(self) -> int:
        return len(self._call_log)

    @property
    def last_call(self) -> dict | None:
        return self._call_log[-1] if self._call_log else None
