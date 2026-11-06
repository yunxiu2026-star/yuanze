from .base import Citation, Engine, EngineError, EngineResult
from .gemini_engine import GeminiEngine
from .mock_engine import MockEngine
from .openai_engine import OpenAIEngine
from .perplexity_engine import PerplexityEngine

ENGINES = {
    "openai": OpenAIEngine,
    "gemini": GeminiEngine,
    "perplexity": PerplexityEngine,
    "mock": MockEngine,
}

LABELS = {"openai": "ChatGPT", "gemini": "Gemini", "perplexity": "Perplexity", "mock": "Mock"}

__all__ = ["Citation", "Engine", "EngineError", "EngineResult", "ENGINES", "LABELS"]
