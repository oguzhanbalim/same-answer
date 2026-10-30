"""Adapter from the bench to a real model on the Messages API.

Not run live by this repository. There was no API key where it was written, so its only
test drives it with a scripted fake client. Treat it as a starting point and read the first
replies by hand. A full run makes about 1,300 calls and spends tokens.

    pip install anthropic
    export ANTHROPIC_API_KEY=...
    export SAME_ANSWER_MODEL=...
    same-answer run --agent examples/messages_api_agent.py:agent --limit 8
"""

from __future__ import annotations

import os
from typing import Any

from same_answer.harness import Agent, AgentRequest


def make_agent(client: Any = None, model: str | None = None, max_tokens: int = 1024) -> Agent:
    def run(request: AgentRequest) -> str:
        chosen = model or os.environ.get("SAME_ANSWER_MODEL")
        if not chosen:
            raise RuntimeError("set SAME_ANSWER_MODEL to the model id you want to test")
        sdk = client
        if sdk is None:
            import anthropic

            sdk = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        # No system prompt: the model gets what a person would type, and nothing else.
        response = sdk.messages.create(
            model=chosen,
            max_tokens=max_tokens,
            messages=[{"role": m.role, "content": m.content} for m in request.messages],
        )
        # A cut-off reply would look thinner than it is. It is better scored unscorable.
        if response.stop_reason == "max_tokens":
            raise RuntimeError("reply was cut off at max_tokens")
        return "".join(block.text for block in response.content if block.type == "text")

    return run


agent = make_agent()
