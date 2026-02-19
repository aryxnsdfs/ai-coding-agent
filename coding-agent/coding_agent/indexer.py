# Copyright (c) 2026 — See LICENSE file for details.
"""File indexer — scans workspace, hashes files, extracts symbols, detects changes.

This module never sends data externally. All processing is local.
"""

import os
import re
import hashlib
import json
import fnmatch
from dataclasses import dataclass, field, asdict
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════════
#  DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Symbol:
    """A code symbol (function, class, etc.) found in a file."""
    kind: str          # "function", "class", "method", "interface", "struct", etc.
    name: str
    line: int          # 1-indexed line number
    end_line: int = 0  # approximate end line (0 = unknown)


@dataclass
class FileInfo:
    """Metadata for a single indexed file."""
    abs_path: str
    rel_path: str
    extension: str
    size: int
    content_hash: str
    last_modified: float
    line_count: int = 0
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    language: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
#  DEFAULT IGNORE RULES
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env",
    ".next", ".nuxt", "dist", "build", ".cache", ".tox", ".mypy_cache",
    ".pytest_cache", "coverage", ".turbo", ".svelte-kit", "target",
    ".idea", ".gradle", "vendor", ".localai", ".eggs", ".hg", ".svn",
    "bower_components", ".terraform", ".serverless",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".woff", ".woff2", ".ttf",
    ".eot", ".otf", ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".pyc", ".pyd",
    ".class", ".jar", ".war", ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".pptx", ".db", ".sqlite", ".sqlite3", ".lock", ".bin", ".dat",
    ".iso", ".img", ".dmg", ".msi", ".deb", ".rpm",
}

# ═══════════════════════════════════════════════════════════════════════════════
#  LANGUAGE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

_EXT_TO_LANG = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".go": "go",
    ".rs": "rust",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cxx": "cpp", ".cc": "cpp", ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".scala": "scala",
    ".r": "r", ".R": "r",
    ".lua": "lua",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".ps1": "powershell", ".psm1": "powershell",
    ".sql": "sql",
    ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "scss", ".less": "less",
    ".json": "json", ".jsonc": "json",
    ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".md": "markdown", ".mdx": "markdown",
    ".txt": "text",
    ".env": "dotenv",
    ".dockerfile": "dockerfile",
    ".tf": "terraform", ".hcl": "terraform",
    ".proto": "protobuf",
    ".graphql": "graphql", ".gql": "graphql",
    ".vue": "vue", ".svelte": "svelte",
    ".dart": "dart", ".ex": "elixir", ".exs": "elixir",
    ".zig": "zig", ".nim": "nim", ".v": "vlang",
}


def detect_language(filepath: str) -> str:
    """Detect programming language from file extension."""
    ext = os.path.splitext(filepath)[1].lower()
    lang = _EXT_TO_LANG.get(ext, "")
    if not lang:
        basename = os.path.basename(filepath).lower()
        if basename in ("dockerfile", "containerfile"):
            return "dockerfile"
        if basename in ("makefile", "gnumakefile"):
            return "makefile"
        if basename in (".gitignore", ".dockerignore", ".localaiignore"):
            return "ignore"
    return lang


# ═══════════════════════════════════════════════════════════════════════════════
#  SYMBOL EXTRACTION  (regex-based, multi-language, no external deps)
# ═══════════════════════════════════════════════════════════════════════════════

_SYMBOL_PATTERNS: dict[str, list[tuple[str, re.Pattern]]] = {}


def _compile_patterns():
    """Compile regex patterns for symbol extraction."""
    if _SYMBOL_PATTERNS:
        return

    _SYMBOL_PATTERNS["python"] = [
        ("class",    re.compile(r"^class\s+(\w+)")),
        ("function", re.compile(r"^def\s+(\w+)")),
        ("function", re.compile(r"^async\s+def\s+(\w+)")),
    ]
    _SYMBOL_PATTERNS["javascript"] = _SYMBOL_PATTERNS["typescript"] = [
        ("class",    re.compile(r"^(?:export\s+)?class\s+(\w+)")),
        ("function", re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)")),
        ("function", re.compile(r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=")),
    ]
    _SYMBOL_PATTERNS["java"] = _SYMBOL_PATTERNS["csharp"] = _SYMBOL_PATTERNS["kotlin"] = [
        ("class",     re.compile(r"^\s*(?:public|private|protected|internal|abstract|static|final|sealed|open|data)?\s*(?:class|record)\s+(\w+)")),
        ("interface", re.compile(r"^\s*(?:public|private|protected)?\s*interface\s+(\w+)")),
        ("function",  re.compile(r"^\s*(?:public|private|protected|internal|static|abstract|override|virtual|async)?\s*\w[\w<>\[\]]*\s+(\w+)\s*\(")),
    ]
    _SYMBOL_PATTERNS["go"] = [
        ("function", re.compile(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)")),
        ("struct",   re.compile(r"^type\s+(\w+)\s+struct")),
        ("interface", re.compile(r"^type\s+(\w+)\s+interface")),
    ]
    _SYMBOL_PATTERNS["rust"] = [
        ("function", re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)")),
        ("struct",   re.compile(r"^\s*(?:pub\s+)?struct\s+(\w+)")),
        ("trait",    re.compile(r"^\s*(?:pub\s+)?trait\s+(\w+)")),
        ("impl",     re.compile(r"^\s*impl(?:<[^>]+>)?\s+(\w+)")),
    ]
    _SYMBOL_PATTERNS["ruby"] = [
        ("class",    re.compile(r"^\s*class\s+(\w+)")),
        ("function", re.compile(r"^\s*def\s+(\w+)")),
        ("module",   re.compile(r"^\s*module\s+(\w+)")),
    ]
    _SYMBOL_PATTERNS["php"] = [
        ("class",    re.compile(r"^\s*(?:abstract\s+)?class\s+(\w+)")),
        ("function", re.compile(r"^\s*(?:public|private|protected|static)?\s*function\s+(\w+)")),
    ]
    _SYMBOL_PATTERNS["cpp"] = _SYMBOL_PATTERNS["c"] = [
        ("class",    re.compile(r"^\s*(?:class|struct)\s+(\w+)")),
        ("function", re.compile(r"^\s*(?:\w[\w:*&<> ]*\s+)?(\w+)\s*\([^)]*\)\s*\{")),
    ]
    _SYMBOL_PATTERNS["swift"] = [
        ("class",    re.compile(r"^\s*(?:open|public|internal|fileprivate|private)?\s*class\s+(\w+)")),
        ("struct",   re.compile(r"^\s*(?:public|internal|fileprivate|private)?\s*struct\s+(\w+)")),
        ("function", re.compile(r"^\s*(?:public|internal|fileprivate|private|static|override|mutating)?\s*func\s+(\w+)")),
    ]


_IMPORT_PATTERNS: dict[str, re.Pattern] = {}


def _compile_import_patterns():
    if _IMPORT_PATTERNS:
        return
    _IMPORT_PATTERNS["python"] = re.compile(r"^(?:from\s+(\S+)\s+import|import\s+(\S+))")
    _IMPORT_PATTERNS["javascript"] = re.compile(r"""(?:import\s+.*?from\s+['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"])""")
    _IMPORT_PATTERNS["typescript"] = _IMPORT_PATTERNS["javascript"]
    _IMPORT_PATTERNS["go"] = re.compile(r'^\s*"([^"]+)"')
    _IMPORT_PATTERNS["java"] = re.compile(r"^import\s+(?:static\s+)?([^;]+);")
    _IMPORT_PATTERNS["rust"] = re.compile(r"^use\s+([^;]+);")
    _IMPORT_PATTERNS["csharp"] = re.compile(r"^using\s+(?:static\s+)?([^;]+);")


def extract_symbols(content: str, language: str) -> list[Symbol]:
    """Extract code symbols from file content using regex patterns."""
    _compile_patterns()
    patterns = _SYMBOL_PATTERNS.get(language, [])
    if not patterns:
        return []

    symbols = []
    lines = content.splitlines()
    for i, line in enumerate(lines, 1):
        for kind, pattern in patterns:
            m = pattern.match(line)
            if m:
                name = m.group(1)
                if name and not name.startswith("_") or kind == "class":
                    symbols.append(Symbol(kind=kind, name=name, line=i))
                break
    return symbols


def extract_imports(content: str, language: str) -> list[str]:
    """Extract import/require statements from file content."""
    _compile_import_patterns()
    pattern = _IMPORT_PATTERNS.get(language)
    if not pattern:
        return []

    imports = []
    for line in content.splitlines()[:100]:  # only scan top of file
        m = pattern.search(line)
        if m:
            val = m.group(1) or (m.group(2) if m.lastindex and m.lastindex >= 2 else None)
            if val:
                imports.append(val.strip())
    return imports


# ═══════════════════════════════════════════════════════════════════════════════
#  IGNORE RULES
# ═══════════════════════════════════════════════════════════════════════════════


class IgnoreRules:
    """Loads and evaluates ignore patterns from .localaiignore, .gitignore, and defaults."""

    def __init__(self, workspace_root: str):
        self.root = workspace_root
        self.dir_patterns: set[str] = set(DEFAULT_SKIP_DIRS)
        self.file_patterns: list[str] = []
        self._load()

    def _load(self):
        for fname in (".localaiignore", ".localai/ignore", ".gitignore"):
            path = os.path.join(self.root, fname)
            if os.path.isfile(path):
                self._parse_file(path)

    def _parse_file(self, path: str):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.endswith("/"):
                        self.dir_patterns.add(line.rstrip("/"))
                    else:
                        self.file_patterns.append(line)
        except OSError:
            pass

    def should_skip_dir(self, dirname: str) -> bool:
        return dirname in self.dir_patterns

    def should_skip_file(self, filename: str) -> bool:
        ext = os.path.splitext(filename)[1].lower()
        if ext in BINARY_EXTENSIONS:
            return True
        for pat in self.file_patterns:
            if fnmatch.fnmatch(filename, pat):
                return True
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  INDEXER
# ═══════════════════════════════════════════════════════════════════════════════


class Indexer:
    """Scans a workspace directory and builds a structured index of all files."""

    def __init__(self, workspace_root: str, ignore_rules: Optional[IgnoreRules] = None,
                 max_file_size: int = 1_000_000):
        self.root = workspace_root
        self.ignore = ignore_rules or IgnoreRules(workspace_root)
        self.max_file_size = max_file_size  # skip files larger than this

    def scan(self) -> dict[str, FileInfo]:
        """Full scan of the workspace. Returns {rel_path: FileInfo}."""
        index: dict[str, FileInfo] = {}

        for root, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in sorted(dirs) if not self.ignore.should_skip_dir(d)]

            for fname in sorted(files):
                if self.ignore.should_skip_file(fname):
                    continue

                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, self.root).replace("\\", "/")

                try:
                    stat = os.stat(abs_path)
                except OSError:
                    continue

                if stat.st_size > self.max_file_size:
                    continue

                content_hash = self._hash_file(abs_path)
                language = detect_language(abs_path)

                # Read content for symbol extraction
                content = self._safe_read(abs_path)
                line_count = content.count("\n") + 1 if content else 0
                symbols = extract_symbols(content, language) if content else []
                imports = extract_imports(content, language) if content else []

                info = FileInfo(
                    abs_path=abs_path,
                    rel_path=rel_path,
                    extension=os.path.splitext(fname)[1].lower(),
                    size=stat.st_size,
                    content_hash=content_hash,
                    last_modified=stat.st_mtime,
                    line_count=line_count,
                    symbols=symbols,
                    imports=imports,
                    language=language,
                )
                index[rel_path] = info

        return index

    def rescan_changed(self, old_index: dict[str, FileInfo]) -> tuple[dict[str, FileInfo], list[str], list[str]]:
        """Incremental rescan. Returns (new_index, changed_paths, removed_paths)."""
        new_index: dict[str, FileInfo] = {}
        changed: list[str] = []
        current_paths: set[str] = set()

        for root, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in sorted(dirs) if not self.ignore.should_skip_dir(d)]

            for fname in sorted(files):
                if self.ignore.should_skip_file(fname):
                    continue

                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, self.root).replace("\\", "/")
                current_paths.add(rel_path)

                try:
                    stat = os.stat(abs_path)
                except OSError:
                    continue

                if stat.st_size > self.max_file_size:
                    continue

                old_info = old_index.get(rel_path)
                if old_info and old_info.last_modified == stat.st_mtime and old_info.size == stat.st_size:
                    new_index[rel_path] = old_info
                    continue

                # File is new or changed — re-index it
                content_hash = self._hash_file(abs_path)
                language = detect_language(abs_path)
                content = self._safe_read(abs_path)
                line_count = content.count("\n") + 1 if content else 0
                symbols = extract_symbols(content, language) if content else []
                imports = extract_imports(content, language) if content else []

                info = FileInfo(
                    abs_path=abs_path,
                    rel_path=rel_path,
                    extension=os.path.splitext(fname)[1].lower(),
                    size=stat.st_size,
                    content_hash=content_hash,
                    last_modified=stat.st_mtime,
                    line_count=line_count,
                    symbols=symbols,
                    imports=imports,
                    language=language,
                )
                new_index[rel_path] = info

                if old_info is None or old_info.content_hash != content_hash:
                    changed.append(rel_path)

        removed = [p for p in old_index if p not in current_paths]
        return new_index, changed, removed

    def _hash_file(self, filepath: str) -> str:
        """Compute a fast content hash (MD5) for change detection."""
        h = hashlib.md5()
        try:
            with open(filepath, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
        except OSError:
            return ""
        return h.hexdigest()

    def _safe_read(self, filepath: str) -> str:
        """Read file content safely, returning empty string on failure."""
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except (OSError, PermissionError):
            return ""


# ═══════════════════════════════════════════════════════════════════════════════
#  CACHE PERSISTENCE (saves index to .localai/cache/ to avoid full rescans)
# ═══════════════════════════════════════════════════════════════════════════════


def save_index_cache(workspace_root: str, index: dict[str, FileInfo]):
    """Persist index hashes to disk for fast incremental rescans."""
    cache_dir = os.path.join(workspace_root, ".localai", "cache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, "index.json")

    data = {}
    for rel_path, info in index.items():
        data[rel_path] = {
            "hash": info.content_hash,
            "mtime": info.last_modified,
            "size": info.size,
            "language": info.language,
            "line_count": info.line_count,
            "symbols": [{"kind": s.kind, "name": s.name, "line": s.line} for s in info.symbols],
        }

    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    except OSError:
        pass


def load_index_cache(workspace_root: str) -> dict[str, dict]:
    """Load cached index data from disk. Returns empty dict if no cache."""
    cache_path = os.path.join(workspace_root, ".localai", "cache", "index.json")
    if not os.path.isfile(cache_path):
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
