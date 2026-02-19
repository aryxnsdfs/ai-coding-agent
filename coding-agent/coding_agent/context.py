# Copyright (c) 2026 — See LICENSE file for details.
"""Context assembly engine — builds priority-ordered, token-efficient prompts.

Priority order (highest to lowest):
1. User instruction
2. Active file / target file content
3. Relevant related files (imports, naming, same directory)
4. Workspace rules (.localai/rules.md)
5. Minimal recent history

Automatically trims lowest-priority content when approaching the token budget.
Never sends the entire repository. Only relevant sections.
"""

import os
from typing import Optional

from .workspace import Workspace

# ═══════════════════════════════════════════════════════════════════════════════
#  TOKEN ESTIMATION (lightweight, no external deps)
# ═══════════════════════════════════════════════════════════════════════════════

CHARS_PER_TOKEN = 3.8  # conservative estimate for code


def estimate_tokens(text: str) -> int:
    """Estimate token count from character length."""
    return int(len(text) / CHARS_PER_TOKEN)


def trim_to_tokens(text: str, max_tokens: int) -> str:
    """Trim text to fit within approximate token budget."""
    max_chars = int(max_tokens * CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[... content trimmed to fit token budget]"


# ═══════════════════════════════════════════════════════════════════════════════
#  FILE CONTENT FORMATTING
# ═══════════════════════════════════════════════════════════════════════════════


def format_file_block(rel_path: str, content: str, max_lines: int = 60) -> str:
    """Format a file's content as a labeled block for AI context."""
    lines = content.splitlines()
    if len(lines) > max_lines:
        shown = lines[:max_lines]
        truncated = True
    else:
        shown = lines
        truncated = False

    header = f"── {rel_path} ({len(lines)} lines) ──"
    body = "\n".join(shown)
    if truncated:
        body += f"\n[... {len(lines) - max_lines} more lines — use read_file to see full content]"

    return f"{header}\n{body}\n"


def format_file_summary(rel_path: str, info) -> str:
    """Format a compact summary of a file (symbols only, no full content)."""
    parts = [f"{rel_path} ({info.line_count} lines, {info.language or 'unknown'})"]
    if info.symbols:
        sym_strs = [f"  {s.kind} {s.name} L{s.line}" for s in info.symbols[:20]]
        parts.extend(sym_strs)
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
#  RELEVANCE SCORING
# ═══════════════════════════════════════════════════════════════════════════════


def score_file_relevance(rel_path: str, info, instruction: str,
                         active_file: Optional[str] = None) -> float:
    """Score how relevant a file is to the current instruction.
    Higher = more relevant. Range roughly 0.0 to 10.0."""
    score = 0.0
    instruction_lower = instruction.lower()
    basename = os.path.basename(rel_path).lower()
    basename_no_ext = os.path.splitext(basename)[0]

    # Direct mention in instruction
    if basename in instruction_lower or basename_no_ext in instruction_lower:
        score += 5.0
    if rel_path.lower() in instruction_lower:
        score += 6.0

    # Symbols mentioned in instruction
    for sym in info.symbols:
        if sym.name.lower() in instruction_lower and len(sym.name) > 2:
            score += 3.0
            break

    # Related to active file
    if active_file:
        active_dir = os.path.dirname(active_file)
        file_dir = os.path.dirname(rel_path)
        if active_dir == file_dir:
            score += 1.0
        active_base = os.path.splitext(os.path.basename(active_file))[0]
        if active_base in basename or basename_no_ext in active_base:
            score += 2.0

    # Language-keyword hints from instruction
    lang_hints = {
        "python": [".py", "pip", "pytest", "django", "flask", "fastapi"],
        "javascript": [".js", "npm", "node", "react", "express", "webpack"],
        "typescript": [".ts", ".tsx", "tsc", "angular", "next"],
        "rust": [".rs", "cargo", "crate"],
        "go": [".go", "go mod", "goroutine"],
    }
    for lang, hints in lang_hints.items():
        if info.language == lang:
            for hint in hints:
                if hint in instruction_lower:
                    score += 1.5
                    break

    # Slight boost for smaller files (more likely to be core logic)
    if info.line_count < 200:
        score += 0.3
    elif info.line_count > 1000:
        score -= 0.3

    # Config/entry point boost (exclude generic names like 'app' which are often huge)
    config_names = {"main", "index", "config", "settings", "routes", "urls", "schema"}
    if basename_no_ext in config_names:
        score += 0.5

    return score


# ═══════════════════════════════════════════════════════════════════════════════
#  CONTEXT ASSEMBLER
# ═══════════════════════════════════════════════════════════════════════════════


class ContextAssembler:
    """Builds priority-ordered context for the AI, fitting within a token budget."""

    def __init__(self, workspace: Workspace):
        self.ws = workspace
        self.token_budget = workspace.config.max_context_tokens

    def build(self, instruction: str, active_file: Optional[str] = None) -> str:
        """Assemble context string to inject into the system prompt.

        Strategy: inject ONLY a compact structure + symbol summaries.
        The agent should use read_file for full content — this saves
        massive tokens vs stuffing file contents into the system prompt.
        """
        sections: list[tuple[int, str, str]] = []
        # (priority, label, content)  — lower priority number = higher importance

        # ── Priority 1: Workspace overview (always included, compact) ───────
        structure = self.ws.get_structure_summary(max_lines=60)
        sections.append((1, "WORKSPACE", structure))

        # ── Priority 2: Relevant file SUMMARIES (symbols only, no content) ──
        scored = []
        for rel_path, info in self.ws.index.items():
            if rel_path == active_file:
                continue
            s = score_file_relevance(rel_path, info, instruction, active_file)
            if s >= 2.0:
                scored.append((s, rel_path, info))

        scored.sort(key=lambda x: -x[0])
        top_files = scored[:self.ws.config.max_context_files]

        # Only summaries — never full content in system prompt
        if top_files:
            summary_lines = ["Potentially relevant files (use read_file for content):"]
            for _s, rel_path, info in top_files:
                summary_lines.append(format_file_summary(rel_path, info))
            sections.append((2, "RELEVANT_SUMMARIES", "\n".join(summary_lines)))

        # ── Priority 3: Workspace rules ─────────────────────────────────────
        if self.ws.rules:
            sections.append((3, "RULES", f"## Workspace Rules\n{self.ws.rules}"))

        # ── Assemble within token budget ────────────────────────────────────
        return self._assemble(sections)

    def build_minimal(self) -> str:
        """Build a minimal context with just the workspace structure.
        Used when no specific file context is needed."""
        structure = self.ws.get_structure_summary(max_lines=80)
        result = f"## Workspace Context\n\n{structure}"
        if self.ws.rules:
            rules_trimmed = trim_to_tokens(self.ws.rules, 2000)
            result += f"\n\n## Workspace Rules\n{rules_trimmed}"
        return trim_to_tokens(result, self.token_budget)

    def _assemble(self, sections: list[tuple[int, str, str]]) -> str:
        """Assemble sections in priority order, trimming from lowest priority up."""
        # Sort by priority (ascending = most important first)
        sections.sort(key=lambda x: x[0])

        output_parts: list[str] = []
        used_tokens = 0
        remaining = self.token_budget

        for priority, label, content in sections:
            content_tokens = estimate_tokens(content)

            if content_tokens <= remaining:
                output_parts.append(content)
                used_tokens += content_tokens
                remaining -= content_tokens
            elif remaining > 500:
                # Partial inclusion — trim to fit
                trimmed = trim_to_tokens(content, remaining - 100)
                output_parts.append(trimmed)
                used_tokens += estimate_tokens(trimmed)
                remaining = 0
                break
            else:
                # No more room
                break

        return "\n\n".join(output_parts)


# ═══════════════════════════════════════════════════════════════════════════════
#  FILE CONTENT CACHE  (avoids re-sending unchanged files to the API)
# ═══════════════════════════════════════════════════════════════════════════════


class ContentCache:
    """Tracks which file contents have already been sent to the AI in this session.
    Avoids re-sending identical content, saving tokens."""

    def __init__(self):
        self._sent_hashes: dict[str, str] = {}  # rel_path -> content_hash

    def is_already_sent(self, rel_path: str, content_hash: str) -> bool:
        """Check if this exact file content was already sent."""
        return self._sent_hashes.get(rel_path) == content_hash

    def mark_sent(self, rel_path: str, content_hash: str):
        """Record that this file's content has been sent."""
        self._sent_hashes[rel_path] = content_hash

    def invalidate(self, rel_path: str):
        """Invalidate cache for a file (e.g., after editing it)."""
        self._sent_hashes.pop(rel_path, None)

    def clear(self):
        """Clear all cached state."""
        self._sent_hashes.clear()
