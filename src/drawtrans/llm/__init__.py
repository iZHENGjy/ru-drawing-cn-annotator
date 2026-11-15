"""LLM layer of the production pipeline: four constrained tasks with JSON Schema, code
validators, retries, replay cache, budget guard and cost ledger.  See session.py."""
from .session import DEFAULT_MODELS, Result, Session, ledger_total
from .tasks import NAMES as TASKS, get as get_task

__all__ = ["Session", "Result", "TASKS", "get_task", "DEFAULT_MODELS", "ledger_total"]
