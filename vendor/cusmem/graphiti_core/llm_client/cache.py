class LLMCache:
    def __init__(self, cache_dir: str = './llm_cache'):
        self.cache_dir = cache_dir

    def get(self, key: str) -> str | None:
        return None

    def set(self, key: str, value: str) -> None:
        pass
