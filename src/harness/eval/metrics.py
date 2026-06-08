"""Memory evaluation metrics — ragas-powered quality assessment for memory operations.

Three evaluation dimensions:
- RETRIEVAL: context_precision, context_recall — is the right memory retrieved?
- STORAGE: faithfulness — is stored memory accurate to its source?
- IMPACT: answer_correctness, answer_relevancy — does memory improve responses?
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

RAGAS_AVAILABLE = False
try:
    from ragas.metrics.collections import (
        AnswerCorrectness,
        AnswerRelevancy,
        ContextPrecisionWithReference,
        ContextRecall,
        Faithfulness,
    )
    RAGAS_AVAILABLE = True
except ImportError:
    pass


@dataclass
class EvalResult:
    metric_name: str
    value: float
    reason: str = ""
    sample_id: str = ""


class MemoryMetricSuite:
    """Pre-configured metric suite for memory quality evaluation.

    Must be initialized with a ragas-compatible LLM (e.g. LangchainLLMWrapper).
    When ragas is not installed, all methods return empty results gracefully.
    """

    def __init__(self, llm: Any = None):
        self._llm = llm
        if RAGAS_AVAILABLE and llm is not None:
            self._context_precision = ContextPrecisionWithReference(llm=llm)
            self._context_recall = ContextRecall(llm=llm)
            self._faithfulness = Faithfulness(llm=llm)
            self._answer_correctness = AnswerCorrectness(llm=llm)
            self._answer_relevancy = AnswerRelevancy(llm=llm)
        else:
            self._context_precision = None
            self._context_recall = None
            self._faithfulness = None
            self._answer_correctness = None
            self._answer_relevancy = None

    @property
    def is_available(self) -> bool:
        return RAGAS_AVAILABLE and self._llm is not None

    def get_retrieval_metrics(self) -> list:
        if not self.is_available:
            return []
        return [self._context_precision, self._context_recall]

    def get_storage_metrics(self) -> list:
        if not self.is_available:
            return []
        return [self._faithfulness]

    def get_impact_metrics(self) -> list:
        if not self.is_available:
            return []
        return [self._answer_correctness, self._answer_relevancy]

    def get_all(self) -> list:
        if not self.is_available:
            return []
        return [
            self._context_precision,
            self._context_recall,
            self._faithfulness,
            self._answer_correctness,
            self._answer_relevancy,
        ]

    @staticmethod
    def list_metric_names() -> dict[str, list[str]]:
        return {
            "retrieval": ["context_precision", "context_recall"],
            "storage": ["faithfulness"],
            "impact": ["answer_correctness", "answer_relevancy"],
        }
