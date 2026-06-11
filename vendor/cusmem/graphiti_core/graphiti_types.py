from pydantic import BaseModel, ConfigDict

from graphiti_core.cross_encoder import CrossEncoderClient
from graphiti_core.driver.driver import GraphDriver
from graphiti_core.embedder import EmbedderClient
from graphiti_core.llm_client import LLMClient
from graphiti_core.tracer import Tracer


class GraphitiClients(BaseModel):
    driver: GraphDriver
    llm_client: LLMClient
    embedder: EmbedderClient
    cross_encoder: CrossEncoderClient | None = None
    tracer: Tracer

    model_config = ConfigDict(arbitrary_types_allowed=True)
