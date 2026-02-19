# Copyright (c) 2026 — See LICENSE file for details.
"""Smart task router — uses Gemini Flash (nearly free) to classify tasks
and pick the optimal Claude model + token budget to minimize cost."""

import json
import re
from dataclasses import dataclass

from .config import GEMINI_API_KEY, MODEL_TIERS

# ═══════════════════════════════════════════════════════════════════════════════
#  TASK PROFILE — output of the router
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class TaskProfile:
    """Routing decision for a single task."""

    complexity: str          # "low", "medium", "high"
    model: str               # Claude model ID
    max_tokens: int          # per-response token cap
    max_iterations: int      # agent loop cap
    reasoning: str           # one-line explanation from the classifier


# ═══════════════════════════════════════════════════════════════════════════════
#  CLASSIFICATION PROMPT (sent to Gemini Flash — costs nearly nothing)
# ═══════════════════════════════════════════════════════════════════════════════

_CLASSIFIER_PROMPT = """\
You are a task-complexity classifier for a coding AI agent.
Given a user's coding task, classify it into exactly one of three levels.

## Complexity Levels

**low** — Simple, well-scoped tasks:
- Quick questions, explanations, small config changes
- Single-file edits, renaming, formatting
- Adding a comment, a print statement, a small function
- Typo fixes, simple one-liner bugs

**medium** — Moderate tasks:
- Bug fixes that require reading 2-5 files
- Adding a feature to an existing module
- Writing tests for existing code
- Refactoring a single module
- Dependency updates with minor code changes

**high** — Complex, multi-step tasks:
- Architecting new systems or modules from scratch
- Debugging complex cross-file issues
- Large refactors across many files
- Performance optimization requiring deep analysis
- Setting up build pipelines, CI/CD, infrastructure

## Output Format
Respond with ONLY this JSON (no markdown, no code fences):
{"complexity": "low|medium|high", "reasoning": "one short sentence why"}
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  ROUTER
# ═══════════════════════════════════════════════════════════════════════════════


def classify_task(task: str) -> TaskProfile:
    """Classify a task using Gemini Flash and return the optimal profile.

    Falls back to a local heuristic if Gemini is unavailable.
    """
    # Try Gemini first (very cheap)
    if GEMINI_API_KEY:
        profile = _classify_with_gemini(task)
        if profile is not None:
            return profile

    # Fallback: local keyword heuristic (free, no API call)
    return _classify_local(task)


def _classify_with_gemini(task: str) -> TaskProfile | None:
    """Call Gemini Flash to classify the task. Returns None on failure."""
    try:
        import google.generativeai as genai

        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.0-flash")

        response = model.generate_content(
            [
                {"role": "user", "parts": [_CLASSIFIER_PROMPT + "\n\nUser task:\n" + task]},
            ],
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=120,
                temperature=0.0,
            ),
        )

        raw = response.text.strip()
        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)
        complexity = data.get("complexity", "medium").lower()
        reasoning = data.get("reasoning", "")

        if complexity not in ("low", "medium", "high"):
            complexity = "medium"

        tier = MODEL_TIERS[complexity]
        return TaskProfile(
            complexity=complexity,
            model=tier["model"],
            max_tokens=tier["max_tokens"],
            max_iterations=tier["max_iterations"],
            reasoning=reasoning,
        )

    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  LOCAL HEURISTIC FALLBACK (no API needed)
# ═══════════════════════════════════════════════════════════════════════════════

_HIGH_KEYWORDS = [
    "architect", "from scratch", "build a", "create a new", "refactor entire",
    "migrate", "redesign", "full stack", "set up ci", "pipeline",
    "performance", "optimize", "security audit", "infrastructure",
    "rewrite", "overhaul",
]

_LOW_KEYWORDS = [
    "explain", "what is", "what does", "how does", "typo", "rename",
    "add a comment", "print statement", "format", "lint", "simple",
    "quick", "small change", "one line", "config", "env",
]


def _classify_local(task: str) -> TaskProfile:
    """Keyword-based complexity classifier — free, instant, no API."""
    task_lower = task.lower()
    task_len = len(task)

    # Check high-complexity keywords
    high_score = sum(1 for kw in _HIGH_KEYWORDS if kw in task_lower)
    low_score = sum(1 for kw in _LOW_KEYWORDS if kw in task_lower)

    if high_score >= 2 or (high_score >= 1 and task_len > 300):
        complexity = "high"
        reasoning = "Task matches high-complexity patterns (local heuristic)."
    elif low_score >= 2 or (low_score >= 1 and task_len < 80):
        complexity = "low"
        reasoning = "Task matches low-complexity patterns (local heuristic)."
    else:
        complexity = "medium"
        reasoning = "Task classified as medium by default (local heuristic)."

    tier = MODEL_TIERS[complexity]
    return TaskProfile(
        complexity=complexity,
        model=tier["model"],
        max_tokens=tier["max_tokens"],
        max_iterations=tier["max_iterations"],
        reasoning=reasoning,
    )
