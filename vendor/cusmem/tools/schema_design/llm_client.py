from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')
DEFAULT_API_KEY = os.getenv('DEEPSEEK_API_KEY', '')
DEFAULT_MODEL = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')


class LLMClient:
    """Minimal OpenAI-compatible LLM client for schema generation."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = DEFAULT_API_KEY,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> None:
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """Send a chat completion request and return the response text."""
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError('openai package required for LLM calls') from None

        client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        logger.info('LLM call: model=%s base=%s prompt_len=%d', self.model, self.base_url, len(user_prompt))

        response = client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        )
        content = response.choices[0].message.content or ''
        logger.info('LLM response: %d chars', len(content))
        return content

    def chat_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Chat, expecting JSON response. Returns parsed dict or raises."""
        raw = self.chat(system_prompt, user_prompt)
        return _extract_json(raw)


def _extract_json(text: str) -> dict[str, Any]:
    """Extract JSON object from text that may contain markdown fences."""
    # Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from ```json ... ``` fence
    if '```json' in text:
        start = text.index('```json') + 7
        end = text.index('```', start)
        text = text[start:end].strip()
    elif '```' in text:
        start = text.index('```') + 3
        end = text.index('```', start)
        text = text[start:end].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f'Failed to parse LLM JSON response (first 200 chars): {text[:200]}') from exc
