"""
Copyright 2024, Zep Software, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import typing

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel

from .config import DEFAULT_MAX_TOKENS, LLMConfig
from .openai_base_client import DEFAULT_REASONING, DEFAULT_VERBOSITY, BaseOpenAIClient


class OpenAIClient(BaseOpenAIClient):
    """
    OpenAIClient is a client class for interacting with OpenAI's language models.

    This class extends the BaseOpenAIClient and provides OpenAI-specific implementation
    for creating completions.

    Attributes:
        client (AsyncOpenAI): The OpenAI client used to interact with the API.
    """

    def __init__(
        self,
        config: LLMConfig | None = None,
        cache: bool = False,
        client: typing.Any = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        reasoning: str = DEFAULT_REASONING,
        verbosity: str = DEFAULT_VERBOSITY,
    ):
        """
        Initialize the OpenAIClient with the provided configuration, cache setting, and client.

        Args:
            config (LLMConfig | None): The configuration for the LLM client, including API key, model, base URL, temperature, and max tokens.
            cache (bool): Whether to use caching for responses. Defaults to False.
            client (Any | None): An optional async client instance to use. If not provided, a new AsyncOpenAI client is created.
        """
        super().__init__(config, cache, max_tokens, reasoning, verbosity)

        if config is None:
            config = LLMConfig()

        if client is None:
            self.client = AsyncOpenAI(api_key=config.api_key, base_url=config.base_url)
        else:
            self.client = client

    async def _create_structured_completion(
        self,
        model: str,
        messages: list[ChatCompletionMessageParam],
        temperature: float | None,
        max_tokens: int,
        response_model: type[BaseModel],
        reasoning: str | None = None,
        verbosity: str | None = None,
    ):
        """Create a structured completion. Uses Responses API for OpenAI, Chat Completions otherwise."""
        # Detect provider: Responses API is only available on OpenAI
        base_url = str(self.client.base_url) if self.client.base_url else ''
        use_responses_api = 'api.openai.com' in base_url

        if not use_responses_api:
            # DeepSeek and other non-OpenAI providers: use Chat Completions directly
            return await self._create_completion(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_model=response_model,
            )

        is_reasoning_model = (
            model.startswith('gpt-5') or model.startswith('o1') or model.startswith('o3')
        )

        request_kwargs = {
            'model': model,
            'input': messages,  # type: ignore
            'max_output_tokens': max_tokens,
            'text_format': response_model,  # type: ignore
        }

        temperature_value = temperature if not is_reasoning_model else None
        if temperature_value is not None:
            request_kwargs['temperature'] = temperature_value

        if is_reasoning_model and reasoning is not None:
            request_kwargs['reasoning'] = {'effort': reasoning}  # type: ignore
        if is_reasoning_model and verbosity is not None:
            request_kwargs['text'] = {'verbosity': verbosity}  # type: ignore

        try:
            response = await self.client.responses.parse(**request_kwargs)
            return response
        except Exception:
            # Fallback: use regular chat completions with JSON mode
            return await self._create_completion(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    async def _create_completion(
        self,
        model: str,
        messages: list[ChatCompletionMessageParam],
        temperature: float | None,
        max_tokens: int,
        response_model: type[BaseModel] | None = None,
        reasoning: str | None = None,
        verbosity: str | None = None,
    ):
        """Create a regular completion, optionally with JSON format."""
        # Reasoning models (gpt-5 family) don't support temperature
        is_reasoning_model = (
            model.startswith('gpt-5') or model.startswith('o1') or model.startswith('o3')
        )

        kwargs: dict[str, typing.Any] = {
            'model': model,
            'messages': messages,
            'max_tokens': max_tokens,
        }
        if temperature is not None and not is_reasoning_model:
            kwargs['temperature'] = temperature

        # When response_model is provided, request JSON output.
        # DeepSeek and most OpenAI-compatible providers support {"type": "json_object"}.
        # We also inject a system hint so the LLM knows to return an object.
        if response_model is not None:
            kwargs['response_format'] = {'type': 'json_object'}
            # Append a lightweight JSON hint to the last user message so
            # providers that don't enforce response_format still see it.
            if messages and messages[-1].get('role') == 'user':
                last = messages[-1]
                content = last.get('content', '')
                if isinstance(content, str) and content:
                    messages[-1] = {
                        **last,
                        'content': content
                        + '\n\n请只输出JSON对象。Do not use markdown code fences.',
                    }

        return await self.client.chat.completions.create(**kwargs)
