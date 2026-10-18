"""Meeting transcription intelligence primitives."""

from .intelligence import IntelligenceCoordinator
from .providers import IntelligenceProvider, ProviderConfig, create_provider
from .schema import AnalysisRequest, AnalysisResult, TranscriptSegment

__all__ = [
    "AnalysisRequest",
    "AnalysisResult",
    "IntelligenceCoordinator",
    "IntelligenceProvider",
    "ProviderConfig",
    "TranscriptSegment",
    "create_provider",
]
