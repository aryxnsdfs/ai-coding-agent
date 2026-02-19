# Copyright (c) 2026 — See LICENSE file for details.
"""Workspace manager — opens folders, auto-analyzes, tracks changes.

Behaves like opening a project in an IDE: immediate analysis on open,
incremental change detection on subsequent operations.
All processing is local. Nothing is uploaded.
"""

import os
import json
import time
from dataclasses import dataclass
from typing import Optional

from .indexer import (
    Indexer, IgnoreRules, FileInfo,
    save_index_cache, load_index_cache, detect_language,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  PROJECT TYPE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

_PROJECT_MARKERS = {
    "package.json":       "node",
    "tsconfig.json":      "typescript",
    "pyproject.toml":     "python",
    "setup.py":           "python",
    "requirements.txt":   "python",
    "Pipfile":            "python",
    "Cargo.toml":         "rust",
    "go.mod":             "go",
    "pom.xml":            "java-maven",
    "build.gradle":       "java-gradle",
    "build.gradle.kts":   "kotlin-gradle",
    "Gemfile":            "ruby",
    "composer.json":      "php",
    "Package.swift":      "swift",
    "CMakeLists.txt":     "cmake",
    "Makefile":           "make",
    "docker-compose.yml": "docker",
    "Dockerfile":         "docker",
    ".sln":               "dotnet",
    "pubspec.yaml":       "dart-flutter",
    "mix.exs":            "elixir",
    "deno.json":          "deno",
    "bun.lockb":          "bun",
}


def detect_project_types(root: str) -> list[str]:
    """Detect project types from marker files in the root directory."""
    types = []
    try:
        entries = set(os.listdir(root))
    except OSError:
        return types

    for marker, ptype in _PROJECT_MARKERS.items():
        if marker in entries:
            if ptype not in types:
                types.append(ptype)

    # Check for .sln files (glob)
    for entry in entries:
        if entry.endswith(".sln") and "dotnet" not in types:
            types.append("dotnet")
            break

    return types


# ═══════════════════════════════════════════════════════════════════════════════
#  WORKSPACE CONFIG  (reads .localai/config.json)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class WorkspaceConfig:
    """User-customizable workspace settings loaded from .localai/config.json."""
    max_file_size: int = 1_000_000       # skip files larger than this (bytes)
    max_context_files: int = 8           # max files to include in context
    max_context_tokens: int = 6_000      # approximate token budget for context
    auto_validate: bool = True           # run validation after edits
    confirm_large_edits: bool = True     # ask before large edits
    large_edit_threshold: int = 50       # lines changed = "large"


def load_workspace_config(root: str) -> WorkspaceConfig:
    """Load workspace config from .localai/config.json, falling back to defaults."""
    config_path = os.path.join(root, ".localai", "config.json")
    cfg = WorkspaceConfig()

    if not os.path.isfile(config_path):
        return cfg

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return cfg

    for key in ("max_file_size", "max_context_files", "max_context_tokens",
                "large_edit_threshold"):
        if key in data and isinstance(data[key], int):
            setattr(cfg, key, data[key])
    for key in ("auto_validate", "confirm_large_edits"):
        if key in data and isinstance(data[key], bool):
            setattr(cfg, key, data[key])

    return cfg


def load_workspace_rules(root: str) -> str:
    """Load user-defined AI behavior rules from .localai/rules.md."""
    rules_path = os.path.join(root, ".localai", "rules.md")
    if not os.path.isfile(rules_path):
        return ""
    try:
        with open(rules_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(8000)  # cap at 8KB to avoid bloating context
    except OSError:
        return ""


# ═══════════════════════════════════════════════════════════════════════════════
#  WORKSPACE
# ═══════════════════════════════════════════════════════════════════════════════


class Workspace:
    """Represents an opened project folder with indexed file metadata."""

    def __init__(self, root: str):
        self.root = os.path.normpath(os.path.abspath(root))
        self.config = load_workspace_config(self.root)
        self.rules = load_workspace_rules(self.root)
        self.ignore = IgnoreRules(self.root)
        self.project_types: list[str] = []
        self.index: dict[str, FileInfo] = {}
        self.languages: dict[str, int] = {}    # language -> file count
        self.total_files: int = 0
        self.total_lines: int = 0
        self.scan_time: float = 0.0
        self._file_content_cache: dict[str, str] = {}

    # ── Open & Analyze ──────────────────────────────────────────────────────

    def open(self):
        """Open the workspace: detect project type, scan and index all files."""
        self.project_types = detect_project_types(self.root)
        indexer = Indexer(self.root, self.ignore, self.config.max_file_size)

        t0 = time.time()
        self.index = indexer.scan()
        self.scan_time = time.time() - t0

        self._compute_stats()
        save_index_cache(self.root, self.index)

    def refresh(self):
        """Incremental rescan — only re-index changed files."""
        indexer = Indexer(self.root, self.ignore, self.config.max_file_size)
        self.index, changed, removed = indexer.rescan_changed(self.index)
        self._compute_stats()

        # Invalidate cache for changed files
        for rel in changed + removed:
            self._file_content_cache.pop(rel, None)

        if changed or removed:
            save_index_cache(self.root, self.index)

        return changed, removed

    # ── Queries ─────────────────────────────────────────────────────────────

    def get_structure_summary(self, max_lines: int = 200) -> str:
        """Generate a compact project structure string for AI context injection."""
        lines = []
        lines.append(f"Project: {os.path.basename(self.root)}")
        if self.project_types:
            lines.append(f"Type: {', '.join(self.project_types)}")
        lines.append(f"Files: {self.total_files}  |  Lines: {self.total_lines:,}")
        top_langs = sorted(self.languages.items(), key=lambda x: -x[1])[:8]
        if top_langs:
            lines.append("Languages: " + ", ".join(f"{l} ({c})" for l, c in top_langs))
        lines.append("")
        lines.append("Structure:")

        # Build compact tree
        tree_lines = self._build_tree(max_lines - len(lines))
        lines.extend(tree_lines)

        return "\n".join(lines[:max_lines])

    def get_file_content(self, rel_path: str) -> Optional[str]:
        """Read a file's content with caching (avoids re-reads)."""
        if rel_path in self._file_content_cache:
            return self._file_content_cache[rel_path]

        info = self.index.get(rel_path)
        if not info:
            return None

        try:
            with open(info.abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except (OSError, PermissionError):
            return None

        self._file_content_cache[rel_path] = content
        return content

    def invalidate_cache(self, rel_path: str):
        """Remove a file from the content cache after it's been modified."""
        self._file_content_cache.pop(rel_path, None)

    def find_related_files(self, rel_path: str) -> list[str]:
        """Find files related to the given file via imports and naming patterns."""
        info = self.index.get(rel_path)
        if not info:
            return []

        related: list[str] = []
        basename = os.path.splitext(os.path.basename(rel_path))[0]

        # 1. Files referenced in imports
        for imp in info.imports:
            # Convert import path to potential file matches
            imp_clean = imp.replace(".", "/").replace("@", "").strip()
            for candidate_path, candidate_info in self.index.items():
                cand_no_ext = os.path.splitext(candidate_path)[0].replace("\\", "/")
                if cand_no_ext.endswith(imp_clean) or imp_clean in candidate_path:
                    if candidate_path != rel_path and candidate_path not in related:
                        related.append(candidate_path)

        # 2. Files with the same base name (e.g., foo.py ↔ test_foo.py, foo.test.ts)
        for other_path in self.index:
            if other_path == rel_path:
                continue
            other_base = os.path.splitext(os.path.basename(other_path))[0]
            if (basename in other_base or other_base in basename) and len(basename) > 2:
                if other_path not in related:
                    related.append(other_path)

        # 3. Files in the same directory
        my_dir = os.path.dirname(rel_path)
        for other_path in self.index:
            if other_path == rel_path:
                continue
            if os.path.dirname(other_path) == my_dir and other_path not in related:
                related.append(other_path)

        return related[:self.config.max_context_files]

    def search_symbols(self, query: str) -> list[tuple[str, str]]:
        """Search for symbols matching query across all indexed files.
        Returns [(rel_path, "kind:name:line"), ...]."""
        query_lower = query.lower()
        results = []
        for rel_path, info in self.index.items():
            for sym in info.symbols:
                if query_lower in sym.name.lower():
                    results.append((rel_path, f"{sym.kind}:{sym.name}:{sym.line}"))
        return results[:100]

    # ── Internal ────────────────────────────────────────────────────────────

    def _compute_stats(self):
        self.total_files = len(self.index)
        self.total_lines = sum(info.line_count for info in self.index.values())
        self.languages = {}
        for info in self.index.values():
            lang = info.language or "other"
            self.languages[lang] = self.languages.get(lang, 0) + 1

    def _build_tree(self, max_lines: int) -> list[str]:
        """Build a compact directory tree from the index."""
        dirs: dict[str, list[str]] = {}
        for rel_path in sorted(self.index.keys()):
            parent = os.path.dirname(rel_path) or "."
            filename = os.path.basename(rel_path)
            if parent not in dirs:
                dirs[parent] = []
            dirs[parent].append(filename)

        lines = []
        for dir_path in sorted(dirs.keys()):
            if len(lines) >= max_lines:
                lines.append("  ... (truncated)")
                break
            files = dirs[dir_path]
            if dir_path == ".":
                prefix = ""
            else:
                prefix = f"  {dir_path}/"
                lines.append(prefix)
                prefix = "    "

            # Show files, collapse if too many
            if len(files) <= 8:
                for f in files:
                    lines.append(f"{prefix}{f}")
            else:
                for f in files[:4]:
                    lines.append(f"{prefix}{f}")
                lines.append(f"{prefix}... +{len(files) - 4} more files")

            if len(lines) >= max_lines:
                lines.append("  ... (truncated)")
                break

        return lines


# ═══════════════════════════════════════════════════════════════════════════════
#  SCAFFOLD  (creates .localai/ config structure in a workspace)
# ═══════════════════════════════════════════════════════════════════════════════


def init_workspace_config(root: str):
    """Create the .localai/ configuration directory with default files."""
    localai_dir = os.path.join(root, ".localai")
    os.makedirs(os.path.join(localai_dir, "cache"), exist_ok=True)

    # config.json
    config_path = os.path.join(localai_dir, "config.json")
    if not os.path.exists(config_path):
        default_config = {
            "max_file_size": 1000000,
            "max_context_files": 8,
            "max_context_tokens": 6000,
            "auto_validate": True,
            "confirm_large_edits": True,
            "large_edit_threshold": 50,
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=2)

    # rules.md
    rules_path = os.path.join(localai_dir, "rules.md")
    if not os.path.exists(rules_path):
        with open(rules_path, "w", encoding="utf-8") as f:
            f.write("# Workspace Rules\n\n"
                    "Add custom instructions here that the AI should follow when working in this project.\n\n"
                    "Examples:\n"
                    "- Always use TypeScript strict mode\n"
                    "- Follow PEP 8 for Python code\n"
                    "- Use 2-space indentation\n"
                    "- Prefer functional components in React\n")

    # ignore
    ignore_path = os.path.join(localai_dir, "ignore")
    if not os.path.exists(ignore_path):
        with open(ignore_path, "w", encoding="utf-8") as f:
            f.write("# Additional ignore patterns for the AI workspace\n"
                    "# Uses .gitignore syntax\n"
                    "# Directories end with /\n\n"
                    "# Example:\n"
                    "# *.log\n"
                    "# tmp/\n")
