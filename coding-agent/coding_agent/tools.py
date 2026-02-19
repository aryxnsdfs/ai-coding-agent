# Copyright (c) 2026 — See LICENSE file for details.
"""Tool definitions and execution for the coding agent."""

import os
import re
import ast
import json
import fnmatch
import subprocess
import traceback
from typing import Optional, Callable

# ═══════════════════════════════════════════════════════════════════════════════
#  TOOL SCHEMAS  (sent to the Claude API)
# ═══════════════════════════════════════════════════════════════════════════════

TOOL_DEFINITIONS = [
    {
        "name": "list_directory",
        "description": (
            "List files and directories at the given path. Returns names, types "
            "(file/dir), and sizes. Use this to explore project structure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to list. Use '.' for the working directory.",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "If true, list recursively up to max_depth. Default false.",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Max depth for recursive listing. Default 3.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file. Returns the full text with line numbers. "
            "Use offset/limit to read a portion of large files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read.",
                },
                "offset": {
                    "type": "integer",
                    "description": "1-indexed line number to start reading from.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of lines to read from offset.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Create a new file or overwrite an existing file with the given content. "
            "Parent directories are created automatically. Use edit_file for surgical edits."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to write.",
                },
                "content": {
                    "type": "string",
                    "description": "The full content to write to the file.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Perform a surgical edit on an existing file using line numbers. "
            "Replaces lines from start_line to end_line (inclusive) with new_content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to edit.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "1-indexed starting line number to replace.",
                },
                "end_line": {
                    "type": "integer",
                    "description": "1-indexed ending line number to replace.",
                },
                "new_content": {
                    "type": "string",
                    "description": "The exact new content to insert.",
                },
            },
            "required": ["path", "start_line", "end_line", "new_content"],
        },
    },
    {
        "name": "delete_file",
        "description": "Delete a file or an empty directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file or empty directory to delete.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_files",
        "description": (
            "Search for a text pattern (regex or fixed string) inside files. "
            "Returns matching file paths with line numbers and matched lines. "
            "Like grep/ripgrep."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search pattern (regex by default).",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file to search in. Default is working directory.",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Glob pattern to filter files, e.g. '*.py'. Default: all files.",
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Case-sensitive search. Default false.",
                },
                "fixed_strings": {
                    "type": "boolean",
                    "description": "Treat query as a literal string, not regex. Default false.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "find_files",
        "description": (
            "Find files and directories by name pattern (glob). "
            "Useful for locating files without knowing their exact path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match file/directory names, e.g. '*.tsx' or 'test_*'.",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in. Default is working directory.",
                },
                "type": {
                    "type": "string",
                    "enum": ["file", "directory", "any"],
                    "description": "Filter by type. Default 'any'.",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Execute a shell command and return its stdout and stderr. "
            "Use this to run tests, linters, builds, git commands, etc. "
            "Commands run in the project's working directory by default."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory for the command. Default is project root.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds. Default 120.",
                },
            },
            "required": ["command"],
        },
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
#  SKIP PATTERNS  (reuse from indexer for consistency)
# ═══════════════════════════════════════════════════════════════════════════════

from .indexer import DEFAULT_SKIP_DIRS as SKIP_DIRS, BINARY_EXTENSIONS

# ═══════════════════════════════════════════════════════════════════════════════
#  POST-EDIT HOOK  (notifies workspace of file changes)
# ═══════════════════════════════════════════════════════════════════════════════

_on_file_changed: Optional[Callable[[str], None]] = None


def set_file_change_hook(callback: Optional[Callable[[str], None]]):
    """Register a callback invoked after any file write/edit/delete.
    The callback receives the absolute path of the changed file."""
    global _on_file_changed
    _on_file_changed = callback


def _notify_change(abs_path: str):
    """Call the registered hook if any."""
    if _on_file_changed:
        try:
            _on_file_changed(abs_path)
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════════════════════════
#  TOOL EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════


def execute_tool(name: str, params: dict, working_dir: str) -> dict:
    """Dispatch a tool call and return {"output": str, "is_error": bool}."""
    try:
        handler = _HANDLERS.get(name)
        if handler is None:
            return {"output": f"Unknown tool: {name}", "is_error": True}
        
        result = handler(params, working_dir)
        
        # --- GLOBAL TOKEN FIREWALL ---
        if "output" in result:
            out_str = str(result["output"])
            if len(out_str) > 30000:
                result["output"] = (
                    out_str[:30000] + 
                    "\n\n[SYSTEM WARNING: TOOL OUTPUT TRUNCATED TO 30000 CHARACTERS TO PREVENT TOKEN EXPLOSION. "
                    "USE STRICT LIMIT/OFFSET OR TIGHTER SEARCH QUERIES.]"
                )
                
        return result
    except Exception as exc:
        return {
            "output": f"Tool '{name}' raised an exception:\n{traceback.format_exc()}",
            "is_error": True,
        }
def _resolve_path(raw_path: str, working_dir: str) -> str:
    """Resolve a path relative to the working directory."""
    if os.path.isabs(raw_path):
        return os.path.normpath(raw_path)
    return os.path.normpath(os.path.join(working_dir, raw_path))


# ── list_directory ──────────────────────────────────────────────────────────


def _list_directory(params: dict, working_dir: str) -> dict:
    path = _resolve_path(params.get("path", "."), working_dir)
    recursive = params.get("recursive", False)
    max_depth = params.get("max_depth", 3)

    if not os.path.isdir(path):
        return {"output": f"Not a directory: {path}", "is_error": True}

    lines: list[str] = []

    def _walk(dir_path: str, prefix: str, depth: int):
        try:
            entries = sorted(os.listdir(dir_path))
        except PermissionError:
            lines.append(f"{prefix}[permission denied]")
            return

        dirs = []
        files = []
        for e in entries:
            full = os.path.join(dir_path, e)
            if os.path.isdir(full):
                if e not in SKIP_DIRS:
                    dirs.append(e)
            else:
                files.append(e)

        for d in dirs:
            full = os.path.join(dir_path, d)
            count = _count_items(full)
            lines.append(f"{prefix}{d}/  ({count} items)")
            if recursive and depth < max_depth:
                _walk(full, prefix + "  ", depth + 1)

        for f in files:
            full = os.path.join(dir_path, f)
            try:
                size = os.path.getsize(full)
                lines.append(f"{prefix}{f}  ({_human_size(size)})")
            except OSError:
                lines.append(f"{prefix}{f}")

        if len(lines) > 2000:
            return

    _walk(path, "", 1)

    header = f"Directory: {path}\n"
    if not lines:
        return {"output": header + "(empty)", "is_error": False}
    return {"output": header + "\n".join(lines[:2000]), "is_error": False}


def _count_items(dirpath: str) -> int:
    try:
        return len(os.listdir(dirpath))
    except OSError:
        return 0


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ── read_file ───────────────────────────────────────────────────────────────


def _read_file(params: dict, working_dir: str) -> dict:
    path = _resolve_path(params["path"], working_dir)

    if not os.path.isfile(path):
        return {"output": f"File not found: {path}", "is_error": True}

    ext = os.path.splitext(path)[1].lower()
    if ext in BINARY_EXTENSIONS:
        return {"output": f"Binary file ({ext}): {path}", "is_error": True}

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except Exception as exc:
        return {"output": f"Cannot read {path}: {exc}", "is_error": True}

    offset = params.get("offset")
    limit = params.get("limit")

    if limit is None or limit > 800:
        limit = 800
    if offset is None or offset < 1:
        offset = 1

    start_idx = offset - 1
    end_idx = start_idx + limit
    
    selected = all_lines[start_idx:end_idx]
    
    numbered = []
    for i, line in enumerate(selected, start=offset):
        numbered.append(f"{i:>5}\t{line.rstrip()}")

    header = f"File: {path}  ({len(all_lines)} lines total)\n"
    body = "\n".join(numbered)
    
    if end_idx < len(all_lines):
        body += f"\n\n… ({len(all_lines) - end_idx} more lines in file)"
        
    return {"output": header + body, "is_error": False}
def _write_file(params: dict, working_dir: str) -> dict:
    path = _resolve_path(params["path"], working_dir)
    content = params["content"]

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)

    _notify_change(path)

    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    return {
        "output": f"Wrote {line_count} lines to {path}",
        "is_error": False,
        "diff_info": {"path": path, "old": None, "new": content},
    }


# ── edit_file ───────────────────────────────────────────────────────────────


def _edit_file(params: dict, working_dir: str) -> dict:
    path = _resolve_path(params["path"], working_dir)
    start_line = params["start_line"]
    end_line = params["end_line"]
    new_content = params["new_content"]

    if not os.path.isfile(path):
        return {"output": f"File not found: {path}", "is_error": True}

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    if start_line < 1 or end_line > len(lines) or start_line > end_line:
        return {
            "output": f"Invalid line range: {start_line}-{end_line}. File has {len(lines)} lines.", 
            "is_error": True
        }

    original = "".join(lines)
    
    start_idx = start_line - 1
    end_idx = end_line
    
    if new_content and not new_content.endswith('\n'):
        new_content += '\n'
        
    updated_lines = lines[:start_idx] + [new_content] + lines[end_idx:]
    updated = "".join(updated_lines)

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(updated)

    _notify_change(path)

    syntax_warning = _validate_syntax(path, updated)
    edit_msg = f"Edited {path} — Replaced lines {start_line} to {end_line}."
    if syntax_warning:
        edit_msg += f"\n⚠ Syntax check: {syntax_warning}"

    return {
        "output": edit_msg,
        "is_error": False,
        "diff_info": {"path": path, "old": original, "new": updated},
    }

def _delete_file(params: dict, working_dir: str) -> dict:
    path = _resolve_path(params["path"], working_dir)

    if os.path.isfile(path):
        os.remove(path)
        _notify_change(path)
        return {"output": f"Deleted file: {path}", "is_error": False}
    elif os.path.isdir(path):
        try:
            os.rmdir(path)
            return {"output": f"Deleted empty directory: {path}", "is_error": False}
        except OSError:
            return {"output": f"Directory not empty: {path}", "is_error": True}
    else:
        return {"output": f"Path not found: {path}", "is_error": True}


# ── search_files ────────────────────────────────────────────────────────────


def _search_files(params: dict, working_dir: str) -> dict:
    query = params["query"]
    search_path = _resolve_path(params.get("path", "."), working_dir)
    file_pattern = params.get("file_pattern", "*")
    case_sensitive = params.get("case_sensitive", False)
    fixed_strings = params.get("fixed_strings", False)

    if fixed_strings:
        if case_sensitive:
            def match(line):
                return query in line
        else:
            q_lower = query.lower()
            def match(line):
                return q_lower in line.lower()
    else:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(query, flags)
        except re.error as exc:
            return {"output": f"Invalid regex: {exc}", "is_error": True}
        def match(line):
            return pattern.search(line) is not None

    results: list[str] = []
    files_searched = 0
    max_results = 200

    def _should_include(filename):
        return fnmatch.fnmatch(filename, file_pattern)

    if os.path.isfile(search_path):
        _search_single_file(search_path, match, results, max_results)
        files_searched = 1
    elif os.path.isdir(search_path):
        for root, dirs, files in os.walk(search_path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fname in files:
                if not _should_include(fname):
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext in BINARY_EXTENSIONS:
                    continue
                full = os.path.join(root, fname)
                _search_single_file(full, match, results, max_results)
                files_searched += 1
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
    else:
        return {"output": f"Path not found: {search_path}", "is_error": True}

    header = f"Searched {files_searched} file(s) | {len(results)} match(es)\n\n"
    if not results:
        return {"output": header + "No matches found.", "is_error": False}
    body = "\n".join(results[:max_results])
    if len(results) >= max_results:
        body += "\n\n… (results capped at 200 matches)"
    return {"output": header + body, "is_error": False}


def _search_single_file(filepath, match_fn, results, max_results):
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                if match_fn(line):
                    results.append(f"{filepath}:{i}: {line.rstrip()}")
                    if len(results) >= max_results:
                        return
    except (OSError, PermissionError):
        pass


# ── find_files ──────────────────────────────────────────────────────────────


def _find_files(params: dict, working_dir: str) -> dict:
    pattern = params["pattern"]
    search_path = _resolve_path(params.get("path", "."), working_dir)
    type_filter = params.get("type", "any")

    if not os.path.isdir(search_path):
        return {"output": f"Not a directory: {search_path}", "is_error": True}

    matches: list[str] = []
    max_matches = 100

    for root, dirs, files in os.walk(search_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        if type_filter in ("directory", "any"):
            for d in dirs:
                if fnmatch.fnmatch(d, pattern):
                    matches.append(os.path.join(root, d) + "/")
                    if len(matches) >= max_matches:
                        break

        if type_filter in ("file", "any"):
            for f in files:
                if fnmatch.fnmatch(f, pattern):
                    matches.append(os.path.join(root, f))
                    if len(matches) >= max_matches:
                        break

        if len(matches) >= max_matches:
            break

    if not matches:
        return {"output": f"No matches for pattern '{pattern}' in {search_path}", "is_error": False}

    body = "\n".join(matches[:max_matches])
    if len(matches) >= max_matches:
        body += f"\n\n… (capped at {max_matches} results)"
    return {"output": f"Found {len(matches)} match(es):\n\n{body}", "is_error": False}


# ── run_command ─────────────────────────────────────────────────────────────


def _run_command(params: dict, working_dir: str) -> dict:
    command = params["command"]
    cwd = _resolve_path(params.get("cwd", "."), working_dir)
    timeout = params.get("timeout", 120)

    if not os.path.isdir(cwd):
        return {"output": f"Working directory not found: {cwd}", "is_error": True}

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        parts = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"STDERR:\n{result.stderr}")
        parts.append(f"\nExit code: {result.returncode}")
        output = "\n".join(parts)

        # Truncate massive output
        if len(output) > 20000:
            output = output[:20000] + f"\n\n… (output truncated, total {len(output)} chars)"

        return {
            "output": output,
            "is_error": result.returncode != 0,
        }
    except subprocess.TimeoutExpired:
        return {
            "output": f"Command timed out after {timeout}s: {command}",
            "is_error": True,
        }
    except Exception as exc:
        return {"output": f"Command execution failed: {exc}", "is_error": True}


# ═══════════════════════════════════════════════════════════════════════════════
#  HANDLER DISPATCH MAP
# ═══════════════════════════════════════════════════════════════════════════════

_HANDLERS = {
    "list_directory": _list_directory,
    "read_file": _read_file,
    "write_file": _write_file,
    "edit_file": _edit_file,
    "delete_file": _delete_file,
    "search_files": _search_files,
    "find_files": _find_files,
    "run_command": _run_command,
}


# ═══════════════════════════════════════════════════════════════════════════════
#  SYNTAX VALIDATION  (runs locally after edits, catches obvious errors)
# ═══════════════════════════════════════════════════════════════════════════════


def _validate_syntax(filepath: str, content: str) -> str:
    """Run lightweight syntax validation. Returns warning string or empty."""
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".py":
        return _validate_python(content)
    if ext == ".json":
        return _validate_json(content)
    return ""


def _validate_python(content: str) -> str:
    try:
        ast.parse(content)
        return ""
    except SyntaxError as e:
        return f"Python syntax error at line {e.lineno}: {e.msg}"


def _validate_json(content: str) -> str:
    try:
        json.loads(content)
        return ""
    except json.JSONDecodeError as e:
        return f"JSON parse error at line {e.lineno}: {e.msg}"
