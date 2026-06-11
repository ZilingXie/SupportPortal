class TokenUsage:
    def __init__(self, prompt_tokens: int = 0, completion_tokens: int = 0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class TokenUsageTracker:
    def __init__(self):
        self._usage: dict[str, list[TokenUsage]] = {}

    def record(self, prompt_name: str | None, input_tokens: int, output_tokens: int) -> None:
        self._usage.setdefault(prompt_name or 'unknown', []).append(
            TokenUsage(input_tokens, output_tokens)
        )

    def track(self, prompt_type: str, usage: TokenUsage) -> None:
        if prompt_type not in self._usage:
            self._usage[prompt_type] = []
        self._usage[prompt_type].append(usage)

    def get_usage(self) -> dict[str, list[TokenUsage]]:
        return self._usage

    def get_total_usage(self) -> TokenUsage:
        total_prompt = 0
        total_completion = 0
        for usages in self._usage.values():
            for u in usages:
                total_prompt += u.prompt_tokens
                total_completion += u.completion_tokens
        return TokenUsage(total_prompt, total_completion)

    def print_summary(self) -> None:
        total = self.get_total_usage()
        print(f'Total tokens: {total.total_tokens} (prompt: {total.prompt_tokens}, completion: {total.completion_tokens})')

    def reset(self) -> None:
        self._usage.clear()
