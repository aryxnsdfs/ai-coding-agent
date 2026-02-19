# Copyright (c) 2026 — See LICENSE file for details.
"""CLI entry point for the local AI coding agent."""

import argparse
import os
import sys

from .config import ANTHROPIC_API_KEY
from .agent import Agent
from .workspace import Workspace, init_workspace_config
from .display import (
    console,
    display_welcome,
    display_goodbye,
    display_error,
    display_status,
    display_workspace_info,
    get_user_input,
)


def main():
    parser = argparse.ArgumentParser(
        prog="coding-agent",
        description="Local AI Coding Agent — autonomous coding powered by Claude",
    )
    parser.add_argument(
        "task",
        nargs="?",
        help="Task to execute (omit for interactive mode)",
    )
    parser.add_argument(
        "-d", "--dir",
        default=".",
        help="Project working directory / workspace folder (default: current dir)",
    )
    parser.add_argument(
        "-m", "--model",
        default=None,
        help="Force a specific Claude model (bypasses smart router). Default: auto-routed",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize .localai/ config directory in the workspace and exit",
    )
    parser.add_argument(
        "--no-workspace",
        action="store_true",
        help="Skip workspace analysis (faster start, but no pre-built context)",
    )

    args = parser.parse_args()

    working_dir = os.path.abspath(args.dir)
    if not os.path.isdir(working_dir):
        # Common Windows issue: unquoted paths with spaces get split.
        # Example: --dir C:\Users\me\New folder  => dir="...\New", task="folder"
        if args.task and not args.task.startswith("-"):
            candidate = os.path.abspath(args.dir + " " + args.task)
            if os.path.isdir(candidate):
                working_dir = candidate
                args.task = None

        if not os.path.isdir(working_dir):
            display_error(
                "Directory not found: "
                f"{working_dir}\n\n"
                "If your path contains spaces, wrap it in quotes. Examples:\n"
                "  python run.py --dir \"C:\\Users\\aryan\\New folder\"\n"
                "  python run.py --dir 'C:\\Users\\aryan\\New folder'\n"
            )
            sys.exit(1)

    # ── --init: scaffold .localai/ and exit ─────────────────────────────────
    if args.init:
        init_workspace_config(working_dir)
        console.print(f"  [green]✔[/green] Created .localai/ config in {working_dir}")
        console.print("  Edit [bold].localai/config.json[/bold] and [bold].localai/rules.md[/bold] to customize.")
        return

    # ── Validate API key ────────────────────────────────────────────────────
    if not ANTHROPIC_API_KEY:
        display_error(
            "ANTHROPIC_API_KEY is not set.\n\n"
            "Set it via environment variable or create a .env file:\n"
            "  ANTHROPIC_API_KEY=sk-ant-..."
        )
        sys.exit(1)

    # ── Open workspace (auto-analyze) ───────────────────────────────────────
    workspace = None
    if not args.no_workspace:
        try:
            display_status("Analyzing workspace...")
            workspace = Workspace(working_dir)
            workspace.open()
            display_workspace_info(
                root=workspace.root,
                total_files=workspace.total_files,
                total_lines=workspace.total_lines,
                languages=workspace.languages,
                project_types=workspace.project_types,
                scan_time=workspace.scan_time,
            )
        except Exception as exc:
            display_error(f"Workspace analysis failed: {exc}\nFalling back to basic mode.")
            workspace = None

    agent = Agent(
        working_dir=working_dir,
        model_override=args.model,
        workspace=workspace,
    )

    # ── Single-shot mode ────────────────────────────────────────────────────
    if args.task:
        agent.run(args.task)
        return

    # ── Interactive REPL mode ───────────────────────────────────────────────
    display_welcome()

    while True:
        try:
            task = get_user_input()
        except (KeyboardInterrupt, EOFError):
            break

        task = task.strip()
        if not task:
            continue
        if task.lower() in ("exit", "quit", "q"):
            break

        agent.run(task)
        agent.reset()

    display_goodbye()


if __name__ == "__main__":
    main()
