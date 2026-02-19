# Local AI Coding Agent

[![Author](https://img.shields.io/badge/Built%20by-Aryan%20Gupta-blue?style=for-the-badge&logo=github)](https://github.com/aryan-gupta)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-yellow?style=for-the-badge&logo=python&logoColor=white)](https://python.org)

An autonomous AI coding agent that runs **entirely on your PC**. Point it at any project folder — it analyzes the codebase, understands the structure, and completes coding tasks using Claude's API. Works with **any language, any framework, any file type**.

---

## Demo

```
 ╔═══════════════════════════════════════════════════════════╗
 ║            LOCAL AI CODING AGENT  v1.0                   ║
 ║         Autonomous · Precise · Relentless                ║
 ╚═══════════════════════════════════════════════════════════╝

You ▸ Fix the disconnect bug in Platform.tsx

╭──────────────── 🧠 Smart Router ────────────────╮
│  Complexity     MEDIUM                           │
│  Model          claude-sonnet-4-20250514                 │
│  Token budget   8,192                            │
╰──────────────────────────────────────────────────╯

──── Step 1 / 30 ────
◆ Agent: Let me search for the disconnect logic...
⚡ search_files → 7 matches
⚡ read_file → Platform.tsx:260-289
⚡ edit_file → Fixed state update logic
✓ Done — 3 steps, ~$0.08
```

---

## Tech Stack

| Component | Technology |
|---|---|
| **AI Model** | [Anthropic Claude](https://www.anthropic.com/) (Sonnet / Opus) |
| **Task Router** | [Google Gemini Flash](https://aistudio.google.com/) (free tier) |
| **Terminal UI** | [Rich](https://github.com/Textualize/rich) |
| **Language** | Python 3.10+ |
| **Runs on** | Windows, macOS, Linux — fully local, nothing uploaded |

---

## Setup (5 minutes)

### Step 1 — Clone the repo

```bash
git clone https://github.com/aryan-gupta/coding-agent.git
cd coding-agent
```

### Step 2 — Install dependencies

```bash
pip install -r requirements.txt
```

This installs:
- `anthropic` — Claude API client
- `rich` — terminal UI (panels, colors, diffs)
- `python-dotenv` — loads `.env` file
- `google-generativeai` — Gemini Flash for smart routing (optional)

### Step 3 — Get your API keys

You need **one required key** and **one optional key**:

#### Required: Anthropic API Key (Claude)

1. Go to [console.anthropic.com](https://console.anthropic.com/)
2. Sign up / log in
3. Go to **API Keys** → **Create Key**
4. Copy the key (starts with `sk-ant-...`)

#### Optional: Gemini API Key (saves you money)

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Click **Create API Key** (it's free)
3. Copy the key

The Gemini key is used **only** for task classification (picking the right Claude model). Without it, the agent uses a local heuristic instead — still works, just slightly less optimal.

### Step 4 — Create your `.env` file

Create a file called `.env` in the project root (same folder as `run.py`):

```env
ANTHROPIC_API_KEY=sk-ant-paste-your-key-here
GEMINI_API_KEY=paste-your-gemini-key-here
```

> **⚠️ Never share your `.env` file or commit it to git.** The `.gitignore` already protects it.

### Step 5 — Run it

```bash
# Open a project folder in interactive mode
python run.py --dir "C:\path\to\your\project"

# Give it a task directly
python run.py "Fix the login bug" --dir "C:\path\to\your\project"
```

> **Tip:** If your path has spaces, wrap it in quotes: `--dir "C:\Users\me\My Project"`

---

## Usage

### Interactive Mode (recommended)

```bash
python run.py --dir "C:\your\project"
```

Opens the project, analyzes it, then drops you into a prompt where you can give tasks one after another:

```
You ▸ Add error handling to the API routes
You ▸ Write tests for the auth module
You ▸ quit
```

### Single-Shot Mode

```bash
python run.py "Refactor the database layer to use async" --dir "C:\your\project"
```

Runs one task and exits.

### All CLI Options

| Flag | Description |
|---|---|
| `--dir PATH` or `-d PATH` | Project folder to open (default: current directory) |
| `--model MODEL` or `-m MODEL` | Force a specific Claude model (bypasses smart router) |
| `--init` | Create `.localai/` config directory in the project and exit |
| `--no-workspace` | Skip workspace analysis for faster startup |

---

## How It Works

```
You open a folder
    ↓
Workspace auto-analyzes: scans files, detects project type, extracts symbols
    ↓
You give a task
    ↓
Smart Router classifies complexity → picks optimal model + token budget
    ↓
Context Engine builds a compact prompt (structure + relevant file summaries)
    ↓
Agent loop:
    Claude streams response → executes tools locally (read, write, edit, search, run commands)
    → file changes update the workspace index
    → old messages get pruned to save tokens
    → loop until task complete
    ↓
Summary + token usage + estimated cost
```

### Agent Workflow

Every task follows: **PLAN → APPLY → VALIDATE → FIX → COMPLETE**

1. **PLAN** — States what it will do before touching any files
2. **APPLY** — Creates, edits, or deletes files with surgical precision
3. **VALIDATE** — Runs tests, linters, or builds to confirm correctness
4. **FIX** — If validation fails, reads errors and fixes the root cause
5. **COMPLETE** — Summarizes all changes made

### Available Tools

The agent can use these tools autonomously:

| Tool | What it does |
|---|---|
| `list_directory` | Browse folder structure |
| `read_file` | Read file contents (with line range support) |
| `write_file` | Create new files |
| `edit_file` | Surgical find-and-replace edits |
| `delete_file` | Remove files |
| `search_files` | Regex search across the project |
| `find_files` | Find files by name/pattern |
| `run_command` | Execute terminal commands |

---

## Smart Router (Cost Optimization)

The router uses Gemini Flash (free) to classify each task:

| Complexity | Model | Token Budget | Max Steps | Typical Cost |
|---|---|---|---|---|
| **LOW** | Claude Sonnet | 4,096 | 15 | ~$0.01–0.05 |
| **MEDIUM** | Claude Sonnet | 8,192 | 30 | ~$0.05–0.15 |
| **HIGH** | Claude Sonnet | 16,384 | 50 | ~$0.10–0.30 |

Without a Gemini key, a local heuristic classifies tasks based on keywords.

---

## Per-Project Configuration

Run this to create a `.localai/` config folder in any project:

```bash
python run.py --init --dir "C:\your\project"
```

This creates:

```
.localai/
├── config.json    # Workspace settings
├── rules.md       # Custom instructions for the AI
├── ignore         # Extra ignore patterns (like .gitignore)
└── cache/         # Index cache for fast rescans
```

### config.json

```json
{
  "max_file_size": 1000000,
  "max_context_files": 8,
  "max_context_tokens": 6000,
  "auto_validate": true,
  "confirm_large_edits": true,
  "large_edit_threshold": 50
}
```

| Setting | What it controls |
|---|---|
| `max_file_size` | Skip files larger than this (bytes) |
| `max_context_files` | Max files included in context summaries |
| `max_context_tokens` | Token budget for workspace context in system prompt |
| `auto_validate` | Run syntax checks after edits |
| `confirm_large_edits` | Ask before large changes |
| `large_edit_threshold` | Number of lines changed = "large" |

### rules.md

Add project-specific instructions:

```markdown
- Use TypeScript strict mode
- Follow PEP 8 for Python
- Use 2-space indentation
- Prefer functional React components
- Always add error handling to API calls
```

---

## Project Structure

```
coding-agent/
├── run.py               # Launcher script
├── requirements.txt     # Python dependencies
├── .env                 # Your API keys (git-ignored)
├── .env.example         # Template for .env
├── LICENSE              # MIT License
└── coding_agent/        # Main package
    ├── __init__.py      # Version + metadata
    ├── __main__.py      # CLI entry point
    ├── config.py        # API keys, models, system prompt
    ├── agent.py         # Core agent loop + context pruning
    ├── tools.py         # 8 tools + syntax validation
    ├── display.py       # Rich terminal UI
    ├── router.py        # Gemini Flash task classifier
    ├── workspace.py     # Project analysis + config
    ├── indexer.py       # File scanning + symbol extraction
    └── context.py       # Priority-based context assembly
```

---

## Environment Variables Reference

All optional except `ANTHROPIC_API_KEY`. Set these in your `.env` file.

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | **(required)** | Your Claude API key |
| `GEMINI_API_KEY` | *(empty)* | Gemini Flash key for smart routing (free) |
| `MODEL` | `claude-sonnet-4-20250514` | Default model when using `--model` flag |
| `MODEL_LOW` | `claude-sonnet-4-20250514` | Model for simple tasks |
| `MODEL_MEDIUM` | `claude-sonnet-4-20250514` | Model for medium tasks |
| `MODEL_HIGH` | `claude-sonnet-4-20250514` | Model for complex tasks |
| `MAX_TOKENS` | `16384` | Max response tokens (manual mode) |
| `MAX_ITERATIONS` | `75` | Max agent steps (manual mode) |

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `ANTHROPIC_API_KEY is not set` | Create a `.env` file in the project root with your key |
| `Directory not found` | Wrap paths with spaces in quotes: `--dir "C:\My Folder"` |
| `google.generativeai FutureWarning` | Harmless warning, can be ignored — Gemini routing still works |
| High token costs | Reduce `max_context_tokens` in `.localai/config.json` |
| Agent loops too many times | Reduce `MAX_ITERATIONS` in `.env` or use `--model` for a specific model |

---

## License

MIT — See [LICENSE](./LICENSE) for details.
