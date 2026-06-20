from .client import CrossEncoderClient

__all__ = ['CrossEncoderClient', 'BGERerankerClient']


def __getattr__(name: str):
    if name == 'BGERerankerClient':
        from .bge_reranker_client import BGERerankerClient

        return BGERerankerClient
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
