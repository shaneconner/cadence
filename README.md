# Cadence: Orchestrating Life

An adaptive life orchestration system where one log event propagates through a hierarchy and task frequencies evolve from real behavior. Built on SQLite with MCP server integration for AI-assisted workflows.

**[Live project page](https://shaneconner.com/projects/cadence)** — Interactive visualizations and full write-up.

---

## The Idea

Most task trackers treat every item as an isolated checkbox. This system flips that model:

- **One action updates a graph.** Log a single leaf task and every parent category it belongs to is recursively updated in a single pass.
- **Frequencies evolve from behavior.** Complete a task early and it surfaces more often (frequency / 1.382). Ignore it and it drifts outward (frequency x 1.382). *Survival of the chosen.*
- **ML predicts what comes next.** A specialist ranker trained on structured completion history suggests the most likely next task, exposed as a callable tool alongside general-purpose AI reasoning.

## Architecture

```
User ─── Table UI / Notebook / CLI / Mobile Chat
              │
         MCP Server (30+ tools)
              │
    ┌─────────┼──────────┐
    │         │          │
CadenceManager  ML Model  Weather API
    │         │
  SQLite    Predictor
  (task     (next-task
   graph)    ranker)
```

**Three layers:**

1. **CadenceManager** — Core database abstraction. Recursive parent logging, frequency adjustment, hierarchy traversal.
2. **MCP Server** — Exposes CadenceManager as callable tools for AI agents. Adds ML predictions, session context, muscle fatigue tracking, and semantic search.
3. **ML Pipeline** — Trains a next-task ranker from structured completion history. ~75% Hit@1 accuracy across 1,399+ active tasks.

## Domains

The same engine powers five task domains:

| Domain | Examples |
|--------|----------|
| **Exercise** | 6-category tagging (objective, type, movement, energy, equipment, muscle group) |
| **Household Cleaning** | Cleaning, sanitation, maintenance |
| **House Plants** | Watering, fertilizing, plant care |
| **Hygiene** | Dental, skincare, personal care |
| **Wellness** | Medications, supplements, health tracking |

## Key Features

- **Hierarchical task graph** with recursive parent propagation
- **Evolutionary frequency scheduling** using golden ratio (1.382) adjustment
- **ML-powered next-task prediction** with equipment continuity and muscle fatigue awareness
- **Batch operations** — single or array input for logging, tagging, updating, deleting
- **Semantic search** across task names using sentence-transformers
- **Multi-target exercise finder** — compound movements that hit multiple urgent muscle groups
- **Weather-aware** outdoor activity suggestions
- **Session context** — time of day, recent activity, model status in one call
- **Claude Code skills** for domain-specific AI guidance (exercise, climbing, plant care, household)

## Tech Stack

`Python` `SQLite` `scikit-learn` `MCP` `sentence-transformers` `D3.js`

## Setup

### Prerequisites

- Python 3.10+
- [MCP SDK](https://github.com/modelcontextprotocol/python-sdk): `pip install mcp`
- Dependencies: `pip install -r mcp_server/requirements.txt`

### Database

The system uses SQLite. Create a fresh database by running CadenceManager:

```python
from CadenceManager import CadenceManager
manager = CadenceManager(db_path="data/chore_data.db")
```

This initializes the schema automatically on first run.

### MCP Server

Configure `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "cadence": {
      "transport": "stdio",
      "command": "python",
      "args": ["mcp_server/server.py"],
      "env": {
        "CHORE_DB_PATH": "data/chore_data.db"
      }
    }
  }
}
```

### ML Model

Train the prediction model once you have completion history:

```bash
cd ml_experiments
python retrain_model.py        # Full training
python retrain_incremental.py  # Incremental update
```

## Project Structure

```
├── CadenceManager.py          # Core database operations
├── CadenceEditor.py           # GUI editor for chore management
├── CadenceTable.py            # Table display and visualization
├── cadence.ipynb              # Interactive analysis notebook
├── CLAUDE.md                 # AI assistant instructions
├── mcp_server/
│   ├── server.py             # MCP tool server (30+ tools)
│   └── requirements.txt
├── ml_experiments/
│   ├── predictor.py          # Inference wrapper
│   ├── retrain_model.py      # Full model training
│   ├── retrain_incremental.py # Incremental training
│   ├── semantic_search.py    # Semantic similarity search
│   └── ...                   # Training pipelines, evaluation
└── .claude/
    ├── hooks/                # Auto-commit hooks
    └── skills/               # Domain-specific AI guidance
        ├── exercise/         # Exercise categorization & logging
        ├── climbing/         # Climbing session structure
        ├── cadence-assistant/ # Frequency adjustment automation
        ├── house-chores/     # Household task management
        └── plant-chores/     # Plant care scheduling
```

## License

[MIT](LICENSE)

---

Built by [Shane Conner](https://shaneconner.com)
