# Copyright (c) 2026 — See LICENSE file for details.
"""Configuration and constants for the coding agent."""

import os
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ── Defaults (overridden by the smart router when GEMINI_API_KEY is set) ────
MODEL = os.getenv("MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "16384"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "75"))

# ── Smart Router: Model tiers by task complexity ────────────────────────────
#    The router picks a tier automatically. You can override models here.
MODEL_TIERS = {
    "low": {
        "model": os.getenv("MODEL_LOW", "claude-sonnet-4-20250514"),
        "max_tokens": 4096,
        "max_iterations": 15,
    },
    "medium": {
        "model": os.getenv("MODEL_MEDIUM", "claude-sonnet-4-20250514"),
        "max_tokens": 8192,
        "max_iterations": 30,
    },
    "high": {
        "model": os.getenv("MODEL_HIGH", "claude-sonnet-4-20250514"),
        "max_tokens": 16384,
        "max_iterations": 50,
    },
}

# ── Conversation pruning (saves tokens on long runs) ───────────────────────
#    After this many messages, older tool results get summarized/compressed.
PRUNE_AFTER_MESSAGES = int(os.getenv("PRUNE_AFTER_MESSAGES", "20"))
BASE_SYSTEM_PROMPT = """\
You are an autonomous AI coding agent on the user's local machine.
You have filesystem and terminal access within the working directory.

## Workflow: PLAN → APPLY → VALIDATE → FIX → COMPLETE

1. **PLAN** – Brief numbered list of what you'll do. Use workspace context below to skip exploration.
2. **APPLY** – Surgical edits only. Use `edit_file`, not `write_file` for existing files.
3. **VALIDATE** – Run tests/builds to confirm.
4. **FIX** – If errors, fix root cause.
5. **COMPLETE** – Brief summary of changes.

## CRITICAL: Anti-Looping & Efficiency (SAVE TOKENS)
- If you attempt an action (like searching or editing) and it fails, DO NOT keep retrying the exact same thing.
- Read files in generous chunks (e.g., 300-800 lines) using `offset` and `limit`. DO NOT make dozens of microscopic 20-line read calls.
- Use `search_files` ONLY to find which file contains a term across the workspace, NOT to navigate line-by-line within a file you are already reading.
- If you are stuck or confused, STOP using tools. Output a standard text response explaining what went wrong and ask the user for clarification. DO NOT loop endlessly.
- Combine multiple small edits into one `edit_file` call when possible.

## Rules
- Prefer surgical `edit_file` over `write_file`.
- Read before editing. Never fabricate content.
- Never run destructive commands without user asking.
"""

SYSTEM_PROMPT = BASE_SYSTEM_PROMPT  # fallback for non-workspace mode


def build_system_prompt(workspace_context: str = "", rules: str = "") -> str:
    """Build the full system prompt with optional workspace context and rules injected."""
    parts = [BASE_SYSTEM_PROMPT]

    if workspace_context:
        parts.append(f"## Workspace Context (pre-analyzed — avoid redundant reads)\n\n{workspace_context}")

    if rules:
        parts.append(f"## User-Defined Rules\n\n{rules}")

    return "\n\n".join(parts)
