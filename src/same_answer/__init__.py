"""Ask the same factual question many ways and measure whether the answer holds."""

from same_answer.analysis import Analysis, Comparison, PressureRow, analyse, contrast_agents
from same_answer.corpus import Corpus, Question, load_corpus
from same_answer.harness import AgentRequest, BenchResult, Message, run_bench
from same_answer.scoring import Score, score_followup, score_reply

__all__ = [
    "AgentRequest",
    "Analysis",
    "BenchResult",
    "Comparison",
    "Corpus",
    "Message",
    "PressureRow",
    "Question",
    "Score",
    "analyse",
    "contrast_agents",
    "load_corpus",
    "run_bench",
    "score_followup",
    "score_reply",
]
