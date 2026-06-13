"""LLM adapter package."""

from codeoracle.adapters.llm.embeddings import EmbeddingsProvider
from codeoracle.adapters.llm.interfaces import LLMProvider

__all__ = ["EmbeddingsProvider", "LLMProvider"]
