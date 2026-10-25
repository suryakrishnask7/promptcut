"""
Natural Language Intent Interpreter package for promptcut.

Provides swappable adapters:
- RuleInterpreter: Local deterministic rule engine for commands, ranges, styles.
- MockInterpreter: Deterministic fixture/heuristic interpreter for tests.
- GroqInterpreter: High-performance cloud LLM interpreter using Groq API.
"""

from promptcut.interpreter.base import (
    IntentTarget,
    InterpretationResult,
    InterpreterAdapter,
    InterpreterContext,
)
from promptcut.interpreter.groq import GroqInterpreter
from promptcut.interpreter.mock import MockInterpreter
from promptcut.interpreter.rule import RuleInterpreter

__all__ = [
    "IntentTarget",
    "InterpretationResult",
    "InterpreterAdapter",
    "InterpreterContext",
    "RuleInterpreter",
    "MockInterpreter",
    "GroqInterpreter",
]
