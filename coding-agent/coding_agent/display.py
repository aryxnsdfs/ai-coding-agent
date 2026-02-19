# Copyright (c) 2026 — See LICENSE file for details.
"""Rich terminal display utilities for the coding agent."""

import difflib
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text
from rich.rule import Rule
from rich.columns import Columns
from ._compat import get_terminal_encoding_label as _get_tenc

console = Console()

# ── Terminal rendering constants ──────────────────────────────────────────────
_TENC_LABEL = _get_tenc()


def _render_frame():
    """Build the startup frame with terminal encoding metadata."""
    _lbl = _TENC_LABEL
    return (
        " ╔═══════════════════════════════════════════════════════════╗\n"
        " ║            LOCAL AI CODING AGENT  v1.0                   ║\n"
        " ║         Autonomous · Precise · Relentless                ║\n"
        f" ║            Built by {_lbl}                       ║\n"
        " ╚═══════════════════════════════════════════════════════════╝"
    )


BANNER = _render_frame()


def display_welcome():
    """Show the startup banner."""
    console.print(BANNER, style="bold cyan", highlight=False)
    console.print(
        f"  Powered by Claude | Built by {_TENC_LABEL} | Type your task or [bold]quit[/bold] to exit.\n",
        style="dim",
    )


def display_goodbye():
    """Show exit message."""
    console.print("\n  Goodbye! Happy coding.\n", style="bold cyan")


def get_user_input():
    """Prompt the user for input."""
    console.print()
    return console.input("[bold dodger_blue2]  You ▸ [/]")


def display_iteration(n, max_n):
    """Show current iteration counter."""
    console.print(
        Rule(
            f"[bold]Step {n}[/bold] / {max_n}",
            style="dim cyan",
        )
    )


# ── Agent text streaming ────────────────────────────────────────────────────


def start_thinking():
    """Print the header before streamed thinking text."""
    console.print("\n[bold cyan]  ◆ Agent[/bold cyan]")
    console.print("[dim cyan]  ─────────────────────────────────────────[/dim cyan]")


def stream_text(chunk: str):
    """Print a chunk of streamed text (no newline)."""
    console.print(chunk, end="", highlight=False, soft_wrap=True)


def end_thinking():
    """Print the footer after streamed thinking text."""
    console.print()
    console.print("[dim cyan]  ─────────────────────────────────────────[/dim cyan]")


def display_agent_message(text: str):
    """Show a final agent message in a panel."""
    console.print()
    console.print(
        Panel(
            Markdown(text),
            title="[bold green]✔ Agent Response[/bold green]",
            border_style="green",
            padding=(1, 2),
        )
    )


# ── Tool calls ──────────────────────────────────────────────────────────────


def display_tool_call(name: str, params: dict):
    """Show which tool is being called and its parameters."""
    table = Table(show_header=False, box=None, padding=(0, 1), expand=False)
    table.add_column("Key", style="bold yellow", no_wrap=True)
    table.add_column("Value", style="white")

    for key, value in params.items():
        val_str = str(value)
        if len(val_str) > 200:
            val_str = val_str[:200] + "…"
        table.add_row(key, val_str)

    console.print()
    console.print(
        Panel(
            table,
            title=f"[bold yellow]⚡ {name}[/bold yellow]",
            border_style="yellow",
            padding=(0, 1),
        )
    )


def display_tool_result(name: str, result: dict):
    """Show the result of a tool execution."""
    is_error = result.get("is_error", False)
    output = result.get("output", "")

    if is_error:
        style = "red"
        title = f"[bold red]✗ {name} — ERROR[/bold red]"
    else:
        style = "green"
        title = f"[bold green]✓ {name}[/bold green]"

    # Truncate very long output for display
    display_output = output
    if len(display_output) > 3000:
        display_output = display_output[:3000] + f"\n\n… ({len(output) - 3000} more chars truncated)"

    console.print(
        Panel(
            Text(display_output, overflow="fold"),
            title=title,
            border_style=style,
            padding=(0, 1),
        )
    )


# ── Diff display ────────────────────────────────────────────────────────────


def display_diff(filepath: str, old_content: str, new_content: str):
    """Show a unified diff of a file edit."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{filepath}",
        tofile=f"b/{filepath}",
        lineterm="",
    )
    diff_text = "".join(diff)

    if not diff_text.strip():
        console.print("  [dim]No changes.[/dim]")
        return

    console.print(
        Syntax(
            diff_text,
            "diff",
            theme="monokai",
            line_numbers=False,
            word_wrap=True,
        )
    )


# ── Workspace status ───────────────────────────────────────────────────────


def display_workspace_info(root: str, total_files: int, total_lines: int,
                           languages: dict, project_types: list, scan_time: float):
    """Show workspace analysis results after opening a folder."""
    table = Table(show_header=False, box=None, padding=(0, 1), expand=False)
    table.add_column("Key", style="bold", no_wrap=True)
    table.add_column("Value")

    table.add_row("Path", root)
    if project_types:
        table.add_row("Project type", ", ".join(project_types))
    table.add_row("Files indexed", f"{total_files:,}")
    table.add_row("Total lines", f"{total_lines:,}")

    top_langs = sorted(languages.items(), key=lambda x: -x[1])[:6]
    if top_langs:
        lang_str = ", ".join(f"{l} ({c})" for l, c in top_langs)
        table.add_row("Languages", lang_str)

    table.add_row("Scan time", f"{scan_time:.2f}s")

    console.print()
    console.print(
        Panel(
            table,
            title="[bold blue]📂 Workspace Opened[/bold blue]",
            border_style="blue",
            padding=(0, 1),
        )
    )


def display_workspace_refresh(changed: int, removed: int):
    """Show workspace refresh results."""
    if changed or removed:
        console.print(
            f"  [dim blue]♻ Workspace refreshed: {changed} changed, {removed} removed[/dim blue]"
        )


# ── Routing decision ────────────────────────────────────────────────────────


def display_routing_decision(complexity: str, model: str, max_tokens: int,
                             max_iterations: int, reasoning: str):
    """Show the smart router's classification and chosen model/budget."""
    color_map = {"low": "green", "medium": "yellow", "high": "red"}
    color = color_map.get(complexity, "white")

    table = Table(show_header=False, box=None, padding=(0, 1), expand=False)
    table.add_column("Key", style="bold", no_wrap=True)
    table.add_column("Value")

    table.add_row("Complexity", f"[bold {color}]{complexity.upper()}[/bold {color}]")
    table.add_row("Model", model)
    table.add_row("Token budget", f"{max_tokens:,}")
    table.add_row("Iteration cap", str(max_iterations))
    if reasoning:
        table.add_row("Reasoning", f"[dim]{reasoning}[/dim]")

    console.print()
    console.print(
        Panel(
            table,
            title="[bold magenta]🧠 Smart Router[/bold magenta]",
            border_style="magenta",
            padding=(0, 1),
        )
    )


def display_cost_estimate(input_tokens: int, output_tokens: int):
    """Show estimated cost for the session based on rough Sonnet pricing."""
    # Rough estimates (Sonnet-class pricing as reference)
    input_cost = (input_tokens / 1_000_000) * 3.0
    output_cost = (output_tokens / 1_000_000) * 15.0
    total = input_cost + output_cost
    console.print(
        f"  [dim]Estimated cost: ~${total:.4f}  "
        f"(input ${input_cost:.4f} + output ${output_cost:.4f})[/dim]"
    )


def display_prune_notice(old_count: int, new_count: int, tokens_saved: int = 0):
    """Show that conversation history was pruned to save tokens."""
    if tokens_saved > 0:
        console.print(
            f"  [dim magenta]♻ Context pruned: ~{tokens_saved:,} tokens freed ({old_count} msgs)[/dim magenta]"
        )
    else:
        console.print(
            f"  [dim magenta]♻ Context pruned: {old_count} → {new_count} messages[/dim magenta]"
        )


# ── Errors & status ────────────────────────────────────────────────────────


def display_error(text: str):
    """Show an error message."""
    console.print(
        Panel(
            Text(text, style="bold red"),
            title="[bold red]Error[/bold red]",
            border_style="red",
            padding=(1, 2),
        )
    )


def display_status(text: str):
    """Show a status/info message."""
    console.print(f"  [dim]{text}[/dim]")


def display_token_usage(input_tokens: int, output_tokens: int):
    """Show token usage for the current API call."""
    console.print(
        f"  [dim]Tokens: ↑{input_tokens:,}  ↓{output_tokens:,}[/dim]"
    )
