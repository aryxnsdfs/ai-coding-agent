# Copyright (c) 2026 — See LICENSE file for details.
"""Core agent loop — drives the Claude API and executes tools autonomously.

Integrates:
- Workspace:       Auto-analyzes project on open, tracks file changes.
- Context Engine:  Priority-based context assembly injected into system prompt.
- Smart Router:    Gemini Flash classifies task → picks model + token budget.
- Context Pruning: Older tool results get compressed to save input tokens.
- Cost Tracking:   Estimated $ shown at end of every run.
- PLAN→APPLY→VALIDATE→FIX→COMPLETE workflow enforced via system prompt.
"""

import os
import anthropic
from typing import Optional

from .config import (
    ANTHROPIC_API_KEY, MODEL, MAX_TOKENS, MAX_ITERATIONS,
    SYSTEM_PROMPT, build_system_prompt,
)
from .router import classify_task, TaskProfile
from .tools import TOOL_DEFINITIONS, execute_tool, set_file_change_hook
from .workspace import Workspace
from .context import ContextAssembler, ContentCache
from .display import (
    console,
    display_iteration,
    start_thinking,
    stream_text,
    end_thinking,
    display_agent_message,
    display_tool_call,
    display_tool_result,
    display_diff,
    display_error,
    display_status,
    display_token_usage,
    display_routing_decision,
    display_cost_estimate,
    display_prune_notice,
)


class Agent:
    """Autonomous coding agent that uses Claude to complete tasks.

    When a Workspace is provided, the agent:
    - Injects pre-analyzed project context into the system prompt (saves tokens)
    - Tracks file changes to keep the workspace index current
    - Uses priority-based context assembly for each task
    """

    def __init__(self, working_dir: str, model_override: str | None = None,
                 workspace: Optional[Workspace] = None):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model_override = model_override
        self.working_dir = working_dir
        self.workspace = workspace
        self.context_cache = ContentCache()
        self.messages: list[dict] = []
        self.iteration = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

        # Register file-change hook so the workspace stays up to date
        if self.workspace:
            set_file_change_hook(self._on_file_changed)

    # ── public API ──────────────────────────────────────────────────────────

    def run(self, task: str):
        """Execute a task through the autonomous agent loop."""

        # ── 1. Smart routing ────────────────────────────────────────────────
        if self.model_override:
            model = self.model_override
            max_tokens = MAX_TOKENS
            max_iterations = MAX_ITERATIONS
            display_status(f"Model: {model} (manual)  |  Working dir: {self.working_dir}")
        else:
            profile: TaskProfile = classify_task(task)
            model = profile.model
            max_tokens = profile.max_tokens
            max_iterations = profile.max_iterations
            display_routing_decision(
                profile.complexity, profile.model,
                profile.max_tokens, profile.max_iterations,
                profile.reasoning,
            )
            display_status(f"Working dir: {self.working_dir}")

        # ── 2. Build system prompt with workspace context ───────────────────
        system_prompt = self._build_prompt(task)
        prompt_tokens = len(system_prompt) // 4  # rough estimate
        display_status(f"System prompt: ~{prompt_tokens:,} tokens")

        # ── 3. Start the agent loop ─────────────────────────────────────────
        self.messages.append({"role": "user", "content": task})
        self.iteration = 0

        while self.iteration < max_iterations:
            self.iteration += 1
            display_iteration(self.iteration, max_iterations)

            self._maybe_prune_context()

            try:
                response = self._call_api_streaming(model, max_tokens, system_prompt)
            except anthropic.APIError as exc:
                display_error(f"API error: {exc}")
                break
            except Exception as exc:
                display_error(f"Unexpected error: {exc}")
                break

            if response.usage:
                self.total_input_tokens += response.usage.input_tokens
                self.total_output_tokens += response.usage.output_tokens
                display_token_usage(response.usage.input_tokens, response.usage.output_tokens)

            assistant_content = response.content
            self.messages.append({"role": "assistant", "content": assistant_content})

            tool_uses = [b for b in assistant_content if b.type == "tool_use"]

            if not tool_uses:
                self._display_final_text(assistant_content)
                break

            tool_results = self._execute_tools(tool_uses)
            self.messages.append({"role": "user", "content": tool_results})

        else:
            display_error(
                f"Agent reached the iteration limit ({max_iterations}). "
                "The task may be incomplete."
            )

        self._display_session_summary()

    def reset(self):
        """Clear conversation history for a new task (keeps token totals and workspace)."""
        self.messages.clear()
        self.iteration = 0
        self.context_cache.clear()

        # Refresh workspace index to pick up any file changes from last run
        if self.workspace:
            self.workspace.refresh()

    # ── prompt construction ─────────────────────────────────────────────────

    def _build_prompt(self, task: str) -> str:
        """Build the system prompt, injecting workspace context if available."""
        if not self.workspace:
            return SYSTEM_PROMPT

        assembler = ContextAssembler(self.workspace)
        ws_context = assembler.build(task)
        rules = self.workspace.rules
        return build_system_prompt(workspace_context=ws_context, rules=rules)

    # ── API call with streaming ─────────────────────────────────────────────

    def _call_api_streaming(self, model: str, max_tokens: int,
                            system_prompt: str) -> anthropic.types.Message:
        """Call the Claude API with streaming for real-time text output."""
        text_started = False

        with self.client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=[{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"}
            }],
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
            tools=TOOL_DEFINITIONS,
            messages=self.messages,
        ) as stream:
            for text in stream.text_stream:
                if not text_started:
                    start_thinking()
                    text_started = True
                stream_text(text)

            if text_started:
                end_thinking()

            return stream.get_final_message()

    # ── tool execution ──────────────────────────────────────────────────────

    def _execute_tools(self, tool_uses: list) -> list[dict]:
        """Execute tool calls and return results for the API."""
        tool_results = []

        for tool_use in tool_uses:
            name = tool_use.name
            params = tool_use.input

            display_tool_call(name, params)

            result = execute_tool(name, params, self.working_dir)

            display_tool_result(name, result)

            diff_info = result.get("diff_info")
            if diff_info and diff_info.get("old") is not None:
                display_diff(
                    diff_info["path"],
                    diff_info["old"],
                    diff_info["new"],
                )

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": result["output"],
                    **({"is_error": True} if result.get("is_error") else {}),
                }
            )

        return tool_results

    # ── workspace integration ───────────────────────────────────────────────

    def _on_file_changed(self, abs_path: str):
        """Called by tools after any file write/edit/delete.
        Invalidates caches so the workspace stays current."""
        if not self.workspace:
            return
        rel = os.path.relpath(abs_path, self.workspace.root).replace("\\", "/")
        self.workspace.invalidate_cache(rel)
        self.context_cache.invalidate(rel)

    # ── context pruning (token saver) ───────────────────────────────────────

    def _maybe_prune_context(self):
        """Aggressively prune old messages to prevent token explosion.

        The messages list contains a mix of:
        - User messages with string content (the original task)
        - User messages with list content (tool_result dicts)
        - Assistant messages with list content (Anthropic SDK objects: TextBlock, ToolUseBlock)

        SDK objects must be converted to dicts before we can modify them.
        """
        keep_recent = 4  # Keep last N messages fully intact
        if len(self.messages) <= keep_recent + 2:
            return

        chars_before = self._estimate_message_chars()

        # First pass: convert any SDK objects to mutable dicts
        self._ensure_messages_are_dicts()

        # Second pass: prune old messages (keep msg[0] = original task, and last N)
        for msg in self.messages[1:-keep_recent]:
            content = msg.get("content")

            # Skip string-only messages (original user prompt)
            if isinstance(content, str):
                continue

            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue

                btype = block.get("type", "")

                # Prune assistant text blocks (reasoning)
                if btype == "text" and isinstance(block.get("text"), str):
                    if len(block["text"]) > 200:
                        block["text"] = block["text"][:80] + "\n[...pruned]"

                # Prune tool_use input blocks (the params sent to tools)
                elif btype == "tool_use" and isinstance(block.get("input"), dict):
                    inp = block["input"]
                    for key, val in inp.items():
                        if isinstance(val, str) and len(val) > 150:
                            inp[key] = val[:60] + "...[pruned]"

                # Prune tool_result blocks (THE main token burner)
                elif btype == "tool_result":
                    c = block.get("content")
                    if isinstance(c, str) and len(c) > 150:
                        block["content"] = c[:60] + "...[pruned]"
                    elif isinstance(c, list):
                        for sub in c:
                            if isinstance(sub, dict) and sub.get("type") == "text":
                                if isinstance(sub.get("text"), str) and len(sub["text"]) > 150:
                                    sub["text"] = sub["text"][:60] + "...[pruned]"

        chars_after = self._estimate_message_chars()
        tokens_saved = (chars_before - chars_after) // 4
        if tokens_saved > 0:
            display_prune_notice(
                len(self.messages), len(self.messages), tokens_saved
            )

    def _ensure_messages_are_dicts(self):
        """Convert Anthropic SDK content objects to plain dicts so we can mutate them."""
        for msg in self.messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue

            new_content = []
            for block in content:
                if isinstance(block, dict):
                    new_content.append(block)
                elif hasattr(block, "type"):
                    # Anthropic SDK object (TextBlock, ToolUseBlock, etc.)
                    if block.type == "text":
                        new_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        new_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": dict(block.input) if block.input else {},
                        })
                    else:
                        # Unknown type — keep as-is
                        new_content.append(block)
                else:
                    new_content.append(block)

            msg["content"] = new_content

    def _estimate_message_chars(self) -> int:
        """Rough char count of all messages for pruning metrics."""
        total = 0
        for msg in self.messages:
            content = msg.get("content")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                total += len(str(content))
        return total

    # ── display helpers ─────────────────────────────────────────────────────

    def _display_final_text(self, content_blocks):
        """Display the final agent response (when no tools are called)."""
        text_parts = [b.text for b in content_blocks if b.type == "text"]
        if text_parts:
            display_agent_message("\n".join(text_parts))

    def _display_session_summary(self):
        """Show cumulative token usage and estimated cost."""
        console.print()
        console.print(
            f"  [dim]Session totals — "
            f"Steps: {self.iteration}  |  "
            f"Tokens: ↑{self.total_input_tokens:,}  ↓{self.total_output_tokens:,}[/dim]"
        )
        if self.total_input_tokens > 0 or self.total_output_tokens > 0:
            display_cost_estimate(self.total_input_tokens, self.total_output_tokens)
        console.print()
