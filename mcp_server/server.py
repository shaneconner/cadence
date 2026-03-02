#!/usr/bin/env python3
"""
Cadence MCP Server

Provides tools for Claude Code to query and modify the chore database
(exercises, household tasks, plant care, etc.) without writing boilerplate
Python code each time.
"""

import asyncio
import json
import os
import sqlite3
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Add parent directory to path to import CadenceManager
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print("MCP SDK not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

from CadenceManager import CadenceManager

# Import ML predictor (optional - gracefully handle if not available)
try:
    sys.path.insert(0, str(Path(__file__).parent.parent / "ml_experiments"))
    from predictor import get_predictor
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False

# Initialize the MCP server
server = Server("cadence")

# Database path - can be overridden by environment variable
DB_PATH = os.environ.get("CHORE_DB_PATH", str(Path(__file__).parent.parent / "data" / "chore_data.db"))

# Initialize CadenceManager instance
manager = CadenceManager(db_path=DB_PATH)


def get_connection():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_ancestors(conn, chore_name):
    """Get all ancestors of a chore using recursive CTE."""
    cursor = conn.cursor()
    cursor.execute('''
        WITH RECURSIVE ancestors AS (
            SELECT parent_chore as name, 1 as level
            FROM parent_chores WHERE chore_name = ?
            UNION ALL
            SELECT pc.parent_chore, a.level + 1
            FROM parent_chores pc
            JOIN ancestors a ON pc.chore_name = a.name
            WHERE a.level < 15
        )
        SELECT DISTINCT name FROM ancestors
    ''', (chore_name,))
    return set(r[0] for r in cursor.fetchall())


# =============================================================================
# TOOL DEFINITIONS
# =============================================================================

@server.list_tools()
async def list_tools():
    """List all available tools."""
    return [
        # ===================== QUERY TOOLS =====================
        Tool(
            name="get_exercise_details",
            description="""Get comprehensive details about an exercise including:
- **description**: IMPORTANT - Contains nuance about how the exercise is performed (rep ranges, rounds, intensity, technique cues). Read this carefully as exercises may not be what they seem (e.g., 'endurance' exercise might be low-rep strength work, 'quick' workout might be for multiple rounds).
- **urls**: Reference links (videos, articles) that can be fetched for deeper understanding of proper form/technique.
- **notes**: User-added notes with context, modifications, or personal observations.
- **parents**: All category tags (muscles, equipment, movement patterns, etc.)
- **children**: Sub-exercises if this is a category node.
- **leaf_descendants**: If this is a category, shows top 10 actionable leaf exercises sorted by due date. Use this to find exercises that would "check off" a suggested category.
- **category_coverage**: Which of the 6 main categories this exercise is tagged with.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact name of the exercise"
                    },
                    "notes_limit": {
                        "type": "integer",
                        "description": "Max number of notes to return (default: 5)",
                        "default": 5
                    }
                },
                "required": ["name"]
            }
        ),
        Tool(
            name="search_exercises",
            description="Search for exercises by name pattern. Returns matching exercises with basic info.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Search pattern (SQL LIKE syntax, use % for wildcards)"
                    },
                    "active_only": {
                        "type": "boolean",
                        "description": "Only return active exercises (default: true)",
                        "default": True
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default: 50)",
                        "default": 50
                    }
                },
                "required": ["pattern"]
            }
        ),
        Tool(
            name="search_semantic",
            description="""Semantic search for chores/exercises using natural language.

Uses sentence-transformers to find semantically similar items. Great for:
- Natural language queries: "stretch my legs", "arm workout", "clean the house"
- Finding exercises by intent rather than exact name
- Discovering related activities

Returns matches ranked by semantic similarity (0-1 scale).""",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query (e.g., 'stretch my legs', 'upper body workout')"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default: 10)",
                        "default": 10
                    },
                    "filter_parent": {
                        "type": "string",
                        "description": "Only return results under this parent category (e.g., 'Exercise', 'Household')"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="list_children",
            description="List all direct children of a category/parent node. Useful for exploring the hierarchy.",
            inputSchema={
                "type": "object",
                "properties": {
                    "parent_name": {
                        "type": "string",
                        "description": "Name of the parent category (e.g., 'Exercise Type', 'Gluteus Maximus')"
                    },
                    "active_only": {
                        "type": "boolean",
                        "description": "Only return active items (default: true)",
                        "default": True
                    },
                    "include_details": {
                        "type": "boolean",
                        "description": "Include description and child count for each item (default: false)",
                        "default": False
                    }
                },
                "required": ["parent_name"]
            }
        ),
        Tool(
            name="list_leaf_exercises",
            description="Get all leaf exercises (actual exercises, not category nodes) under a parent category. Recursively traverses the hierarchy.",
            inputSchema={
                "type": "object",
                "properties": {
                    "parent_name": {
                        "type": "string",
                        "description": "Name of the parent category (e.g., 'Calisthenics', 'Gluteus Maximus')"
                    },
                    "active_only": {
                        "type": "boolean",
                        "description": "Only return active exercises (default: true)",
                        "default": True
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default: 100)",
                        "default": 100
                    }
                },
                "required": ["parent_name"]
            }
        ),
        Tool(
            name="get_category_coverage",
            description="Analyze how well exercises are tagged across the 6 main categories: Objective, Type, Movement, Energy, Equipment, Muscle Group.",
            inputSchema={
                "type": "object",
                "properties": {
                    "show_details": {
                        "type": "boolean",
                        "description": "Show breakdown by which categories are missing (default: true)",
                        "default": True
                    }
                }
            }
        ),
        Tool(
            name="find_exercises_missing_categories",
            description="Find exercises that are missing specific categories. Useful for finding under-tagged exercises.",
            inputSchema={
                "type": "object",
                "properties": {
                    "min_missing": {
                        "type": "integer",
                        "description": "Minimum number of missing categories (default: 1)",
                        "default": 1
                    },
                    "specific_category": {
                        "type": "string",
                        "description": "Filter to exercises missing a specific category (e.g., 'Movement', 'Energy')",
                        "enum": ["Objective", "Type", "Movement", "Energy", "Equipment", "Muscle"]
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default: 50)",
                        "default": 50
                    }
                }
            }
        ),
        Tool(
            name="get_exercise_ancestors",
            description="Get all ancestor categories/parents of an exercise. Useful for understanding how an exercise is categorized.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the exercise"
                    }
                },
                "required": ["name"]
            }
        ),

        # ===================== MODIFICATION TOOLS =====================
        Tool(
            name="add_parent",
            description="Add a parent relationship to an exercise. Both the exercise and parent must already exist.",
            inputSchema={
                "type": "object",
                "properties": {
                    "exercise_name": {
                        "type": "string",
                        "description": "Name of the exercise (child)"
                    },
                    "exercise_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of exercise names to add the parent to (use this OR exercise_name, not both)"
                    },
                    "parent_name": {
                        "type": "string",
                        "description": "Name of the parent category to add"
                    }
                },
                "required": ["parent_name"]
            }
        ),
        Tool(
            name="remove_parent",
            description="Remove a parent relationship from an exercise.",
            inputSchema={
                "type": "object",
                "properties": {
                    "exercise_name": {
                        "type": "string",
                        "description": "Name of the exercise (child)"
                    },
                    "exercise_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of exercise names to remove the parent from (use this OR exercise_name, not both)"
                    },
                    "parent_name": {
                        "type": "string",
                        "description": "Name of the parent category to remove"
                    }
                },
                "required": ["parent_name"]
            }
        ),
        Tool(
            name="update_chore_attributes",
            description="Update one or more attributes of a chore. Can update frequency, description, active status, etc. in a single call.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the chore to update"
                    },
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of chore names to apply the same updates to (use this OR name, not both). Cannot be used with new_name."
                    },
                    "frequency_in_days": {
                        "type": "number",
                        "description": "New frequency in days (e.g., 7 for weekly, 30 for monthly)"
                    },
                    "description": {
                        "type": "string",
                        "description": "New description text"
                    },
                    "active": {
                        "type": "integer",
                        "description": "1 for active, 0 for inactive"
                    },
                    "adjust_frequency": {
                        "type": "integer",
                        "description": "1 to enable auto-adjustment, 0 to disable"
                    },
                    "new_name": {
                        "type": "string",
                        "description": "Rename the chore to this new name"
                    }
                }
            }
        ),
        Tool(
            name="add_note",
            description="Add a note to a chore.",
            inputSchema={
                "type": "object",
                "properties": {
                    "chore_name": {
                        "type": "string",
                        "description": "Name of the chore"
                    },
                    "chore_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of chore names to add the note to (use this OR chore_name, not both)"
                    },
                    "note": {
                        "type": "string",
                        "description": "Note text to add"
                    }
                },
                "required": ["note"]
            }
        ),
        Tool(
            name="delete_note",
            description="Delete a note by its ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "note_id": {
                        "type": "integer",
                        "description": "ID of the note to delete"
                    }
                },
                "required": ["note_id"]
            }
        ),
        Tool(
            name="delete_log",
            description="Delete a log entry by its ID. Use this to undo accidental log entries. The chore's due date will be recalculated based on the previous log.",
            inputSchema={
                "type": "object",
                "properties": {
                    "log_id": {
                        "type": "integer",
                        "description": "ID of the log entry to delete"
                    }
                },
                "required": ["log_id"]
            }
        ),
        Tool(
            name="add_url",
            description="Add a URL reference to a chore.",
            inputSchema={
                "type": "object",
                "properties": {
                    "chore_name": {
                        "type": "string",
                        "description": "Name of the chore"
                    },
                    "url": {
                        "type": "string",
                        "description": "URL to add"
                    }
                },
                "required": ["chore_name", "url"]
            }
        ),
        Tool(
            name="add_chore",
            description="""Create a new chore (exercise, category, or any tracked item).

Works for both leaf exercises and category nodes - they're the same thing structurally.
If frequency_in_days is not provided, it's inferred from siblings with the same parents.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the new chore"
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional description"
                    },
                    "frequency_in_days": {
                        "type": "number",
                        "description": "Days between occurrences (if not provided, inferred from siblings)"
                    },
                    "parent_chores": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of parent categories to assign"
                    },
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Reference URLs (videos, articles)"
                    },
                    "adjust_frequency": {
                        "type": "boolean",
                        "description": "Enable auto frequency adjustment based on completion patterns (default: true)",
                        "default": True
                    }
                },
                "required": ["name"]
            }
        ),

        # ===================== ANALYSIS TOOLS =====================
        Tool(
            name="find_non_granular_tags",
            description="Find exercises tagged with non-granular (intermediate hierarchy) tags instead of leaf-level granular tags.",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Category to check (e.g., 'Muscle Group'). If not specified, checks all.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default: 50)",
                        "default": 50
                    }
                }
            }
        ),
        Tool(
            name="run_sql_query",
            description="Run a read-only SQL query against the database. For advanced exploration. Only SELECT queries allowed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SQL SELECT query to run"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max rows to return (default: 100)",
                        "default": 100
                    }
                },
                "required": ["query"]
            }
        ),

        # ===================== HIERARCHY TOOLS =====================
        Tool(
            name="get_hierarchy_tree",
            description="Generate a visual tree representation of a category hierarchy. Useful for exploring the structure of Exercise Type, Muscle Group, etc.",
            inputSchema={
                "type": "object",
                "properties": {
                    "root_name": {
                        "type": "string",
                        "description": "Root category name (e.g., 'Exercise Type', 'Muscle Group', 'Exercise Objective')"
                    },
                    "include_terminals": {
                        "type": "boolean",
                        "description": "Include leaf nodes/exercises (default: false, shows only category structure)",
                        "default": False
                    }
                },
                "required": ["root_name"]
            }
        ),

        # ===================== OPERATIONAL TOOLS (Generic for all chores) =====================
        Tool(
            name="log_chore",
            description="Log completion of one or more chores. Updates due dates based on frequency. Also logs parent categories recursively. Accepts either a single name or an array of names. Optionally add a note (e.g., weight/reps) in the same call.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of a single chore to log (use this OR names, not both)"
                    },
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of chore names to log in batch (use this OR name, not both)"
                    },
                    "note": {
                        "type": "string",
                        "description": "Optional note to add after logging (e.g., '28kg x 8'). Only works with single 'name', not batch 'names'."
                    }
                }
            }
        ),
        Tool(
            name="delete_chore",
            description="Delete a chore and all its associated data (URLs, notes, logs, parent relationships). Use with caution.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the chore to delete"
                    },
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of chore names to delete (use this OR name, not both)"
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "Must be true to confirm deletion",
                        "default": False
                    }
                },
                "required": ["confirm"]
            }
        ),
        Tool(
            name="reset_overdue_chores",
            description="Reset all chores whose due dates are in the past. Sets them to be due starting from now.",
            inputSchema={
                "type": "object",
                "properties": {
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, only show what would be reset without making changes",
                        "default": True
                    }
                }
            }
        ),
        Tool(
            name="get_upcoming_chores",
            description="Get chores sorted by urgency. Returns two sections by default: top by cycle_progress (relative urgency) and nearest by days_until_due (absolute timing). Works for exercises and all other chore types.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max chores to return per section (default: 10)",
                        "default": 10
                    },
                    "filter_parent": {
                        "type": "string",
                        "description": "Only show chores under this parent category (e.g., 'Exercise', 'Household', etc.)"
                    },
                    "show_by_cycle": {
                        "type": "boolean",
                        "description": "Include section sorted by cycle_progress (days_since_logged/frequency, higher=more urgent)",
                        "default": True
                    },
                    "show_by_due": {
                        "type": "boolean",
                        "description": "Include section sorted by days_until_due (lower=sooner)",
                        "default": True
                    },
                    "include_overdue_only": {
                        "type": "boolean",
                        "description": "Only show overdue chores",
                        "default": False
                    },
                    "exclude_descendants_of": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Exclude chores that are descendants of these categories (default: ['Climbing'] for exercises since climbing requires gym)",
                        "default": ["Climbing"]
                    }
                }
            }
        ),

        # ===================== CONTEXT TOOLS =====================
        Tool(
            name="get_current_datetime",
            description="Get current date and time in Central Time (Wisconsin). Returns datetime, day of week, and time of day category (early_morning, morning, midday, afternoon, evening, night).",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="get_weather",
            description="Get current weather for your configured location. Useful for deciding on outdoor activities like road running. Returns temperature, conditions, wind, and precipitation.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="get_time_since_last_activity",
            description="Get time since last logged activity, optionally filtered by category. Useful for understanding current activity level.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filter_parent": {
                        "type": "string",
                        "description": "Filter to activities under this parent (e.g., 'Exercise', 'Household')"
                    }
                }
            }
        ),

        # ===================== SUGGESTION TOOLS =====================
        Tool(
            name="find_multi_target_exercises",
            description="Find exercises that hit multiple urgent (soonest due) muscle groups at once. Great for efficient workouts that check multiple boxes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "min_targets": {
                        "type": "integer",
                        "description": "Minimum number of urgent muscles an exercise must hit (default: 2)",
                        "default": 2
                    },
                    "top_k_muscles": {
                        "type": "integer",
                        "description": "Number of most urgent muscles to consider (default: 15)",
                        "default": 15
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max exercises to return (default: 20)",
                        "default": 20
                    }
                }
            }
        ),
        Tool(
            name="get_related_chores",
            description="Find chores that share parent categories with a given chore. Useful for batching related tasks (e.g., water plants + fertilize).",
            inputSchema={
                "type": "object",
                "properties": {
                    "chore_name": {
                        "type": "string",
                        "description": "Name of the chore to find related items for"
                    },
                    "include_due_info": {
                        "type": "boolean",
                        "description": "Include due date information (default: true)",
                        "default": True
                    }
                },
                "required": ["chore_name"]
            }
        ),
        Tool(
            name="get_session_context",
            description="""Get comprehensive context for the current session, useful for understanding state and making decisions.

Returns:
- **temporal**: Current datetime, day of week, time of day category (morning/afternoon/evening)
- **weather**: Current conditions (for outdoor activity decisions)
- **recent_activity**: Last 5 leaf logs (not category nodes), time since last, today's count, unique activities in last 7 days
- **session**: What's been logged in the last 30 minutes (current session)
- **model_status**: Training date, logs since training, growth %, should_retrain recommendation
- **feedback_stats**: Suggestions made, feedback collected, observed hit rate

This tool unifies get_current_datetime, get_weather, get_time_since_last_activity, and adds model/training context.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "include_weather": {
                        "type": "boolean",
                        "description": "Include weather data (slightly slower, default: true)",
                        "default": True
                    },
                    "retrain_thresholds": {
                        "type": "object",
                        "description": "Custom thresholds for should_retrain flag",
                        "properties": {
                            "days_since_training": {"type": "integer", "default": 7},
                            "log_growth_percent": {"type": "integer", "default": 20},
                            "min_new_feedback": {"type": "integer", "default": 50}
                        }
                    },
                    "exclude_descendants_of": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Exclude chores that are descendants of these categories from urgency highlights (default: ['Climbing'] since climbing requires gym)",
                        "default": ["Climbing"]
                    }
                }
            }
        ),
        Tool(
            name="suggest_next_chore",
            description="""ML-powered prediction of which chore the user is most likely to select next.

Uses a trained model that considers:
- Session context (what was just logged)
- Time of day and day of week
- Due dates
- Historical patterns and embeddings

Returns ranked suggestions with confidence scores and last_note (most recent weight/rep note) for each exercise. Best used after logging a chore to suggest what might come next.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "prev_chores": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Recently logged chore names (most recent first). If not provided, will look up from logs."
                    },
                    "filter_parent": {
                        "type": "string",
                        "description": "Only suggest chores under this parent category (e.g., 'Exercise', 'Household')"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of suggestions to return (default: 10)",
                        "default": 10
                    },
                    "include_all_due": {
                        "type": "boolean",
                        "description": "Include all due/overdue chores as candidates, not just soonest (default: false)",
                        "default": False
                    },
                    "log_suggestions": {
                        "type": "boolean",
                        "description": "Log suggestions for feedback collection (default: true)",
                        "default": True
                    },
                    "exclude_logged_within_hours": {
                        "type": "number",
                        "description": "Exclude chores logged within this many hours (default: 24)",
                        "default": 24
                    },
                    "exclude_descendants_of": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Exclude chores that are descendants of these categories (default: ['Climbing'] since climbing requires gym)",
                        "default": ["Climbing"]
                    },
                    "only_leaves": {
                        "type": "boolean",
                        "description": "Only return leaf exercises (not category nodes). Categories still influence ranking but only actionable items are returned (default: false)",
                        "default": False
                    },
                    "include_underutilized": {
                        "type": "integer",
                        "description": "Mix in N underutilized exercises (high frequency_in_days, rarely done) to encourage variety. These are added to the suggestions regardless of ML score.",
                        "default": 1
                    },
                    "include_random_underutilized": {
                        "type": "integer",
                        "description": "Include N random exercises from the underutilized pool (frequency > 60 days) as wildcards for exploration.",
                        "default": 1
                    }
                }
            }
        ),
        Tool(
            name="log_suggestion_feedback",
            description="""Log feedback on suggestions - mark which chores were selected and which were rejected.

Call this after suggest_next_chore when the user responds to suggestions.
Accepts single or multiple selections. Non-selected suggestions in the session are marked as rejected.
This data is used to improve future predictions through negative sampling.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from suggest_next_chore response"
                    },
                    "selected_chore": {
                        "type": "string",
                        "description": "Name of a single chore that was selected (use this OR selected_chores)"
                    },
                    "selected_chores": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of chore names that were selected (use this OR selected_chore)"
                    }
                },
                "required": ["session_id"]
            }
        ),

        # ===================== FREQUENCY ADJUSTMENT TOOLS =====================
        Tool(
            name="adjust_all_frequencies",
            description="""Dynamically adjust frequencies for all active chores based on how early/late they are completed.

- If a chore is completed way before its due date, the frequency is DECREASED (more frequent)
- If a chore is overdue when completed, the frequency is INCREASED (less frequent)
- Only adjusts chores that have parent categories and have adjust_frequency enabled
- Uses a dynamic threshold based on the chore's current frequency

Returns a dict of chores that were adjusted and their new frequencies.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "lower_bound_multiplier": {
                        "type": "number",
                        "description": "Multiplier for increasing frequency when overdue (default: 1.382 = golden ratio)",
                        "default": 1.382
                    },
                    "upper_bound_divider": {
                        "type": "number",
                        "description": "Divider for decreasing frequency when early (default: 1.382)",
                        "default": 1.382
                    },
                    "adjust_without_parent": {
                        "type": "boolean",
                        "description": "Also adjust chores without parent categories (default: false)",
                        "default": False
                    },
                    "lower_bound_tightness": {
                        "type": "number",
                        "description": "How tight the lower bound is (0-1, lower = tighter, default: 0.5)",
                        "default": 0.5
                    }
                }
            }
        ),
        Tool(
            name="get_recent_muscle_activity",
            description="""Get muscle groups that were worked in the last N hours.

Helps avoid suggesting exercises that hammer already-fatigued muscles.

Returns muscles grouped by when they were hit, with configurable depth:
- **leaf**: Specific muscles (e.g., "Biceps Brachii — Long Head")
- **mid**: Intermediate groups (e.g., "Quadriceps", "Biceps Brachii")
- **high**: High-level regions (e.g., "Upper Body", "Lower Body", "Core")""",
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": "Lookback window in hours (default: 48)",
                        "default": 48
                    },
                    "depth": {
                        "type": "string",
                        "description": "Granularity level: 'leaf' (specific muscles), 'mid' (muscle groups), 'high' (body regions)",
                        "enum": ["leaf", "mid", "high"],
                        "default": "mid"
                    }
                }
            }
        ),
        Tool(
            name="log_weight",
            description="Log a weight measurement. Records the weight in the weights table and logs the 'Weigh In' chore.",
            inputSchema={
                "type": "object",
                "properties": {
                    "weight": {
                        "type": "number",
                        "description": "Weight in pounds"
                    },
                    "person": {
                        "type": "string",
                        "description": "Name of the person (default: 'Shane')",
                        "default": "Shane"
                    }
                },
                "required": ["weight"]
            }
        ),
    ]


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    """Execute a tool call."""

    try:
        if name == "get_exercise_details":
            return await get_exercise_details(arguments)
        elif name == "search_exercises":
            return await search_exercises(arguments)
        elif name == "search_semantic":
            return await search_semantic(arguments)
        elif name == "list_children":
            return await list_children(arguments)
        elif name == "list_leaf_exercises":
            return await list_leaf_exercises(arguments)
        elif name == "get_category_coverage":
            return await get_category_coverage(arguments)
        elif name == "find_exercises_missing_categories":
            return await find_exercises_missing_categories(arguments)
        elif name == "get_exercise_ancestors":
            return await get_exercise_ancestors(arguments)
        elif name == "add_parent":
            return await add_parent_tool(arguments)
        elif name == "remove_parent":
            return await remove_parent_tool(arguments)
        elif name == "update_chore_attributes":
            return await update_chore_attributes(arguments)
        elif name == "add_note":
            return await add_note(arguments)
        elif name == "delete_note":
            return await delete_note(arguments)
        elif name == "delete_log":
            return await delete_log(arguments)
        elif name == "add_url":
            return await add_url(arguments)
        elif name == "add_chore":
            return await add_chore(arguments)
        elif name == "find_non_granular_tags":
            return await find_non_granular_tags(arguments)
        elif name == "run_sql_query":
            return await run_sql_query(arguments)
        elif name == "get_hierarchy_tree":
            return await get_hierarchy_tree(arguments)
        elif name == "log_chore":
            return await log_chore(arguments)
        elif name == "delete_chore":
            return await delete_chore(arguments)
        elif name == "reset_overdue_chores":
            return await reset_overdue_chores(arguments)
        elif name == "get_upcoming_chores":
            return await get_upcoming_chores(arguments)
        elif name == "get_current_datetime":
            return await get_current_datetime(arguments)
        elif name == "get_weather":
            return await get_weather(arguments)
        elif name == "get_time_since_last_activity":
            return await get_time_since_last_activity(arguments)
        elif name == "find_multi_target_exercises":
            return await find_multi_target_exercises(arguments)
        elif name == "get_related_chores":
            return await get_related_chores(arguments)
        elif name == "get_session_context":
            return await get_session_context(arguments)
        elif name == "suggest_next_chore":
            return await suggest_next_chore(arguments)
        elif name == "log_suggestion_feedback":
            return await log_suggestion_feedback(arguments)
        elif name == "adjust_all_frequencies":
            return await adjust_all_frequencies(arguments)
        elif name == "get_recent_muscle_activity":
            return await get_recent_muscle_activity(arguments)
        elif name == "log_weight":
            return await log_weight(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def get_exercise_details(args: dict):
    """Get comprehensive details about an exercise."""
    name = args["name"]
    notes_limit = args.get("notes_limit", 5)

    conn = get_connection()
    cursor = conn.cursor()

    # Get basic chore info
    cursor.execute("SELECT * FROM chores WHERE name = ?", (name,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return [TextContent(type="text", text=f"Exercise '{name}' not found.")]

    details = dict(row)

    # Get parents
    cursor.execute("SELECT parent_chore FROM parent_chores WHERE chore_name = ?", (name,))
    details["parents"] = [r["parent_chore"] for r in cursor.fetchall()]

    # Get children (direct)
    cursor.execute("SELECT chore_name FROM parent_chores WHERE parent_chore = ?", (name,))
    details["children"] = [r["chore_name"] for r in cursor.fetchall()]

    # If this is a category (has children), get leaf descendants for actionable options
    if details["children"]:
        cursor.execute('''
            WITH RECURSIVE descendants AS (
                SELECT chore_name, 1 as level
                FROM parent_chores WHERE parent_chore = ?
                UNION ALL
                SELECT pc.chore_name, d.level + 1
                FROM parent_chores pc
                JOIN descendants d ON pc.parent_chore = d.chore_name
                WHERE d.level < 15
            )
            SELECT DISTINCT c.name, l.complete_by
            FROM chores c
            JOIN descendants d ON c.name = d.chore_name
            LEFT JOIN (
                SELECT chore_name, complete_by
                FROM logs
                WHERE (chore_name, logged_at) IN (
                    SELECT chore_name, MAX(logged_at) FROM logs GROUP BY chore_name
                )
            ) l ON c.name = l.chore_name
            WHERE NOT EXISTS (SELECT 1 FROM parent_chores pc2 WHERE pc2.parent_chore = c.name)
            AND c.active = 1
            ORDER BY l.complete_by ASC NULLS LAST
            LIMIT 10
        ''', (name,))

        ct = ZoneInfo("America/Chicago")
        now = datetime.now(ct)
        leaf_descendants = []
        for r in cursor.fetchall():
            leaf_name = r["name"]
            complete_by = r["complete_by"]
            if complete_by:
                due_date = datetime.fromisoformat(complete_by)
                if due_date.tzinfo is None:
                    due_date = due_date.replace(tzinfo=ct)
                days_until_due = round((due_date - now).total_seconds() / (3600 * 24), 1)
            else:
                days_until_due = None
            leaf_descendants.append({"name": leaf_name, "days_until_due": days_until_due})
        details["leaf_descendants"] = leaf_descendants

    # Get URLs
    cursor.execute("SELECT url FROM urls WHERE chore_name = ?", (name,))
    details["urls"] = [r["url"] for r in cursor.fetchall()]

    # Get notes (with limit)
    cursor.execute(
        "SELECT note, created_at FROM notes WHERE chore_name = ? ORDER BY created_at DESC LIMIT ?",
        (name, notes_limit)
    )
    details["notes"] = [{"note": r["note"], "created_at": r["created_at"]} for r in cursor.fetchall()]

    # Get last genuine log (actual workout, not adjustment)
    cursor.execute(
        "SELECT logged_at FROM logs WHERE chore_name = ? AND is_genuine = 1 ORDER BY logged_at DESC LIMIT 1",
        (name,)
    )
    genuine_log = cursor.fetchone()
    if genuine_log:
        details["last_logged"] = genuine_log["logged_at"]

    # Get current due date (from most recent log, including adjustments)
    cursor.execute(
        "SELECT complete_by FROM logs WHERE chore_name = ? ORDER BY logged_at DESC LIMIT 1",
        (name,)
    )
    latest_log = cursor.fetchone()
    if latest_log:
        details["next_due"] = latest_log["complete_by"]

    # Get all ancestors for category analysis
    ancestors = get_ancestors(conn, name)

    # Check category coverage
    categories = {
        "Exercise Objective": "Objective" in str(ancestors),
        "Exercise Type": "Exercise Type" in ancestors,
        "Exercise Movement": "Exercise Movement" in ancestors,
        "Energy Systems": "Energy Systems" in ancestors,
        "Exercise Equipment": "Exercise Equipment" in ancestors,
        "Muscle Group": "Muscle Group" in ancestors
    }
    details["category_coverage"] = categories
    details["missing_categories"] = [k for k, v in categories.items() if not v]

    conn.close()
    return [TextContent(type="text", text=json.dumps(details, indent=2, default=str))]


async def search_exercises(args: dict):
    """Search for exercises by name pattern."""
    pattern = args["pattern"]
    active_only = args.get("active_only", True)
    limit = args.get("limit", 50)

    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT name, description, active, frequency_in_days FROM chores WHERE name LIKE ?"
    if active_only:
        query += " AND active = 1"
    query += f" ORDER BY name LIMIT {limit}"

    cursor.execute(query, (pattern,))
    results = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return [TextContent(type="text", text=json.dumps({
        "count": len(results),
        "exercises": results
    }, indent=2))]


async def search_semantic(args: dict):
    """Semantic search for chores using natural language."""
    query = args["query"]
    limit = args.get("limit", 10)
    filter_parent = args.get("filter_parent")

    # Import semantic search (lazy load to avoid slow startup)
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent / "ml_experiments"))
        from semantic_search import find_similar_semantic
    except ImportError:
        return [TextContent(type="text", text=json.dumps({
            "error": "Semantic search not available. Run: python ml_experiments/semantic_search.py to build cache."
        }, indent=2))]

    # Get semantic matches
    matches = find_similar_semantic(query, limit=limit * 2)  # Get extra for filtering

    conn = get_connection()
    cursor = conn.cursor()

    # If filter_parent specified, filter to descendants of that parent
    if filter_parent:
        cursor.execute("""
            WITH RECURSIVE descendants AS (
                SELECT ? as chore_name
                UNION ALL
                SELECT pc.chore_name FROM parent_chores pc
                JOIN descendants d ON pc.parent_chore = d.chore_name
            )
            SELECT DISTINCT chore_name FROM descendants
        """, (filter_parent,))
        valid_chores = {r[0] for r in cursor.fetchall()}
        matches = [(name, score) for name, score in matches if name in valid_chores]

    # Get additional details for top matches
    results = []
    for name, score in matches[:limit]:
        cursor.execute("""
            SELECT c.name, c.description, c.frequency_in_days, l.complete_by
            FROM chores c
            LEFT JOIN (
                SELECT chore_name, complete_by
                FROM logs WHERE (chore_name, logged_at) IN (
                    SELECT chore_name, MAX(logged_at) FROM logs GROUP BY chore_name
                )
            ) l ON c.name = l.chore_name
            WHERE c.name = ?
        """, (name,))
        row = cursor.fetchone()
        if row:
            entry = {
                "name": row[0],
                "similarity": round(score, 3),
                "description": row[1][:100] if row[1] else None,
                "frequency_days": round(row[2], 1) if row[2] else None
            }
            if row[3]:
                from datetime import datetime
                from zoneinfo import ZoneInfo
                ct = ZoneInfo("America/Chicago")
                now = datetime.now(ct)
                due = datetime.fromisoformat(row[3])
                if due.tzinfo is None:
                    due = due.replace(tzinfo=ct)
                entry["days_until_due"] = round((due - now).total_seconds() / 86400, 1)
            results.append(entry)

    conn.close()
    return [TextContent(type="text", text=json.dumps({
        "query": query,
        "count": len(results),
        "results": results
    }, indent=2))]


async def list_children(args: dict):
    """List direct children of a parent category."""
    parent_name = args["parent_name"]
    active_only = args.get("active_only", True)
    include_details = args.get("include_details", False)

    conn = get_connection()
    cursor = conn.cursor()

    if include_details:
        query = """
            SELECT c.name, c.description, c.active,
                   (SELECT COUNT(*) FROM parent_chores pc2 WHERE pc2.parent_chore = c.name) as child_count
            FROM chores c
            JOIN parent_chores pc ON c.name = pc.chore_name
            WHERE pc.parent_chore = ?
        """
    else:
        query = """
            SELECT c.name
            FROM chores c
            JOIN parent_chores pc ON c.name = pc.chore_name
            WHERE pc.parent_chore = ?
        """

    if active_only:
        query += " AND c.active = 1"
    query += " ORDER BY c.name"

    cursor.execute(query, (parent_name,))

    if include_details:
        results = [dict(r) for r in cursor.fetchall()]
    else:
        results = [r["name"] for r in cursor.fetchall()]

    conn.close()
    return [TextContent(type="text", text=json.dumps({
        "parent": parent_name,
        "count": len(results),
        "children": results
    }, indent=2))]


async def list_leaf_exercises(args: dict):
    """Get all leaf exercises under a parent category."""
    parent_name = args["parent_name"]
    active_only = args.get("active_only", True)
    limit = args.get("limit", 100)

    conn = get_connection()
    cursor = conn.cursor()

    # Find all descendants recursively, then filter to leaves
    active_clause = "AND c.active = 1" if active_only else ""

    cursor.execute(f'''
        WITH RECURSIVE descendants AS (
            SELECT chore_name, 1 as level
            FROM parent_chores WHERE parent_chore = ?
            UNION ALL
            SELECT pc.chore_name, d.level + 1
            FROM parent_chores pc
            JOIN descendants d ON pc.parent_chore = d.chore_name
            WHERE d.level < 15
        )
        SELECT DISTINCT c.name, c.description
        FROM chores c
        JOIN descendants d ON c.name = d.chore_name
        WHERE NOT EXISTS (SELECT 1 FROM parent_chores pc2 WHERE pc2.parent_chore = c.name)
        {active_clause}
        ORDER BY c.name
        LIMIT ?
    ''', (parent_name, limit))

    results = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return [TextContent(type="text", text=json.dumps({
        "parent": parent_name,
        "count": len(results),
        "exercises": results
    }, indent=2))]


async def get_category_coverage(args: dict):
    """Analyze category coverage across all exercises."""
    show_details = args.get("show_details", True)

    conn = get_connection()
    cursor = conn.cursor()

    # Get all leaf node exercises (those with no children)
    cursor.execute('''
        WITH RECURSIVE ancestors AS (
            SELECT chore_name, parent_chore as ancestor, 1 as level
            FROM parent_chores
            UNION ALL
            SELECT a.chore_name, pc.parent_chore, a.level + 1
            FROM ancestors a
            JOIN parent_chores pc ON a.ancestor = pc.chore_name
            WHERE a.level < 15
        )
        SELECT DISTINCT c.name
        FROM chores c
        JOIN ancestors a ON c.name = a.chore_name
        WHERE c.active = 1
        AND a.ancestor = 'Muscle Group'
        AND NOT EXISTS (SELECT 1 FROM parent_chores pc2 WHERE pc2.parent_chore = c.name)
    ''')
    exercises = [r[0] for r in cursor.fetchall()]

    categories_to_check = ['Exercise Objective', 'Exercise Type', 'Exercise Movement',
                           'Energy Systems', 'Exercise Equipment', 'Muscle Group']

    counts = {0: 0, 1: 0, 2: 0, "3+": 0}
    missing_by_category = {cat: 0 for cat in categories_to_check}

    for ex_name in exercises:
        ancestors = get_ancestors(conn, ex_name)
        missing = [cat for cat in categories_to_check if cat not in ancestors]

        count = len(missing)
        if count >= 3:
            counts["3+"] += 1
        else:
            counts[count] += 1

        for cat in missing:
            missing_by_category[cat] += 1

    total = len(exercises)
    result = {
        "total_exercises": total,
        "coverage": {
            "complete_6_of_6": {"count": counts[0], "percent": f"{counts[0]*100/total:.1f}%"},
            "missing_1": {"count": counts[1], "percent": f"{counts[1]*100/total:.1f}%"},
            "missing_2": {"count": counts[2], "percent": f"{counts[2]*100/total:.1f}%"},
            "missing_3_plus": {"count": counts["3+"], "percent": f"{counts['3+']*100/total:.1f}%"}
        }
    }

    if show_details:
        result["missing_by_category"] = {
            cat.replace("Exercise ", "").replace(" Systems", ""): {
                "count": missing_by_category[cat],
                "percent": f"{missing_by_category[cat]*100/total:.1f}%"
            }
            for cat in categories_to_check
        }

    conn.close()
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def find_exercises_missing_categories(args: dict):
    """Find exercises missing specific categories."""
    min_missing = args.get("min_missing", 1)
    specific_category = args.get("specific_category")
    limit = args.get("limit", 50)

    # Map short names to full names
    category_map = {
        "Objective": "Exercise Objective",
        "Type": "Exercise Type",
        "Movement": "Exercise Movement",
        "Energy": "Energy Systems",
        "Equipment": "Exercise Equipment",
        "Muscle": "Muscle Group"
    }

    conn = get_connection()
    cursor = conn.cursor()

    # Get all leaf exercises
    cursor.execute('''
        WITH RECURSIVE ancestors AS (
            SELECT chore_name, parent_chore as ancestor, 1 as level
            FROM parent_chores
            UNION ALL
            SELECT a.chore_name, pc.parent_chore, a.level + 1
            FROM ancestors a
            JOIN parent_chores pc ON a.ancestor = pc.chore_name
            WHERE a.level < 15
        )
        SELECT DISTINCT c.name
        FROM chores c
        JOIN ancestors a ON c.name = a.chore_name
        WHERE c.active = 1
        AND a.ancestor = 'Muscle Group'
        AND NOT EXISTS (SELECT 1 FROM parent_chores pc2 WHERE pc2.parent_chore = c.name)
    ''')
    exercises = [r[0] for r in cursor.fetchall()]

    categories_to_check = list(category_map.values())
    results = []

    for ex_name in exercises:
        ancestors = get_ancestors(conn, ex_name)
        missing = [cat for cat in categories_to_check if cat not in ancestors]

        if len(missing) >= min_missing:
            if specific_category:
                full_cat = category_map.get(specific_category, specific_category)
                if full_cat not in missing:
                    continue

            results.append({
                "name": ex_name,
                "missing": [cat.replace("Exercise ", "").replace(" Systems", "") for cat in missing]
            })

            if len(results) >= limit:
                break

    conn.close()
    return [TextContent(type="text", text=json.dumps({
        "count": len(results),
        "exercises": results
    }, indent=2))]


async def get_exercise_ancestors(args: dict):
    """Get all ancestors of an exercise."""
    name = args["name"]

    conn = get_connection()
    ancestors = get_ancestors(conn, name)

    # Organize by category
    categories = {
        "Exercise Objective": [],
        "Exercise Type": [],
        "Exercise Movement": [],
        "Energy Systems": [],
        "Exercise Equipment": [],
        "Muscle Group": []
    }

    other = []

    for ancestor in ancestors:
        # Check which category this ancestor belongs to
        anc_ancestors = get_ancestors(conn, ancestor)
        categorized = False
        for cat in categories:
            if cat in anc_ancestors or ancestor == cat:
                categories[cat].append(ancestor)
                categorized = True
                break
        if not categorized and ancestor not in ["Exercise", "Chore"]:
            other.append(ancestor)

    conn.close()

    result = {
        "exercise": name,
        "ancestors_by_category": {k: v for k, v in categories.items() if v},
        "other_ancestors": other if other else None
    }

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def add_parent_tool(args: dict):
    """Add a parent relationship. Uses CadenceManager. Supports single or batch."""
    names = args.get("exercise_names", [])
    if not names and "exercise_name" in args:
        names = [args["exercise_name"]]
    parent_name = args["parent_name"]

    if not names:
        return [TextContent(type="text", text="Error: Must provide 'exercise_name' or 'exercise_names' parameter.")]

    if len(names) == 1:
        success, message = manager.add_parent(names[0], parent_name)
        if not success:
            return [TextContent(type="text", text=f"Error: {message}")]
        return [TextContent(type="text", text=message)]

    results = []
    errors = []
    for name in names:
        success, message = manager.add_parent(name, parent_name)
        if success:
            results.append(name)
        else:
            errors.append({"name": name, "error": message})

    response = {"added_parent": parent_name, "succeeded": results}
    if errors:
        response["failed"] = errors
    return [TextContent(type="text", text=json.dumps(response, indent=2))]


async def remove_parent_tool(args: dict):
    """Remove a parent relationship. Uses CadenceManager. Supports single or batch."""
    names = args.get("exercise_names", [])
    if not names and "exercise_name" in args:
        names = [args["exercise_name"]]
    parent_name = args["parent_name"]

    if not names:
        return [TextContent(type="text", text="Error: Must provide 'exercise_name' or 'exercise_names' parameter.")]

    if len(names) == 1:
        success, message = manager.remove_parent(names[0], parent_name)
        if not success:
            return [TextContent(type="text", text=message)]
        return [TextContent(type="text", text=f"Removed parent relationship: {names[0]} -> {parent_name}")]

    results = []
    errors = []
    for name in names:
        success, message = manager.remove_parent(name, parent_name)
        if success:
            results.append(name)
        else:
            errors.append({"name": name, "error": message})

    response = {"removed_parent": parent_name, "succeeded": results}
    if errors:
        response["failed"] = errors
    return [TextContent(type="text", text=json.dumps(response, indent=2))]


async def update_chore_attributes(args: dict):
    """Update one or more attributes of a chore. Uses CadenceManager. Supports single or batch."""
    chore_names = args.get("names", [])
    if not chore_names and "name" in args:
        chore_names = [args["name"]]

    if not chore_names:
        return [TextContent(type="text", text="Error: Must provide 'name' or 'names' parameter.")]

    # new_name only works with single chore
    if "new_name" in args and args.get("new_name") and len(chore_names) > 1:
        return [TextContent(type="text", text="Error: 'new_name' cannot be used with batch 'names'. Rename one at a time.")]

    # Build updates dict for CadenceManager
    updates = {}
    if "new_name" in args and args["new_name"]:
        updates["name"] = args["new_name"]
    for field in ["frequency_in_days", "description", "active", "adjust_frequency"]:
        if field in args and args[field] is not None:
            updates[field] = args[field]

    if not updates:
        return [TextContent(type="text", text="No changes specified.")]

    # Build change description
    changed_desc = []
    if "new_name" in args and args.get("new_name"):
        changed_desc.append(f"renamed to '{args['new_name']}'")
    if "frequency_in_days" in args:
        changed_desc.append(f"frequency={args['frequency_in_days']} days")
    if "description" in args:
        changed_desc.append("description updated")
    if "active" in args:
        changed_desc.append(f"active={args['active']}")
    if "adjust_frequency" in args:
        changed_desc.append(f"adjust_frequency={args['adjust_frequency']}")

    if len(chore_names) == 1:
        name = chore_names[0]
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM chores WHERE name = ?", (name,))
        if not cursor.fetchone():
            conn.close()
            return [TextContent(type="text", text=f"Chore '{name}' not found.")]
        conn.close()

        result = manager.update_chore_attributes(name, updates)
        if not result:
            return [TextContent(type="text", text=f"Failed to update '{name}'")]

        final_name = args.get("new_name", name) if "new_name" in args else name
        return [TextContent(type="text", text=f"Updated '{final_name}': {', '.join(changed_desc)}")]

    # Batch mode
    results = []
    errors = []
    for name in chore_names:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM chores WHERE name = ?", (name,))
        if not cursor.fetchone():
            conn.close()
            errors.append({"name": name, "error": "not found"})
            continue
        conn.close()

        result = manager.update_chore_attributes(name, updates.copy())
        if result:
            results.append(name)
        else:
            errors.append({"name": name, "error": "update failed"})

    response = {"updated": results, "changes": ", ".join(changed_desc)}
    if errors:
        response["failed"] = errors
    return [TextContent(type="text", text=json.dumps(response, indent=2))]


async def add_note(args: dict):
    """Add a note to a chore. Uses CadenceManager. Supports single or batch."""
    names = args.get("chore_names", [])
    if not names and "chore_name" in args:
        names = [args["chore_name"]]
    note = args["note"]

    if not names:
        return [TextContent(type="text", text="Error: Must provide 'chore_name' or 'chore_names' parameter.")]

    if len(names) == 1:
        success, message = manager.add_note(names[0], note)
        return [TextContent(type="text", text=message)]

    results = []
    errors = []
    for name in names:
        success, message = manager.add_note(name, note)
        if success:
            results.append(name)
        else:
            errors.append({"name": name, "error": message})

    response = {"note_added_to": results, "note": note}
    if errors:
        response["failed"] = errors
    return [TextContent(type="text", text=json.dumps(response, indent=2))]


async def delete_note(args: dict):
    """Delete a note by ID. Uses CadenceManager."""
    success, message = manager.delete_note(args["note_id"])
    return [TextContent(type="text", text=message)]


async def delete_log(args: dict):
    """Delete a log entry by ID. Uses CadenceManager."""
    success, message, details = manager.delete_log(args["log_id"])
    if success and details:
        result = {
            "message": message,
            "chore_name": details.get("chore_name"),
            "deleted_logged_at": details.get("deleted_logged_at"),
            "old_due": details.get("old_due"),
            "new_due": details.get("new_due")
        }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    return [TextContent(type="text", text=message)]


async def add_url(args: dict):
    """Add a URL to a chore."""
    chore_name = args["chore_name"]
    url = args["url"]

    conn = get_connection()
    cursor = conn.cursor()

    # Check if chore exists
    cursor.execute("SELECT 1 FROM chores WHERE name = ?", (chore_name,))
    if not cursor.fetchone():
        conn.close()
        return [TextContent(type="text", text=f"Chore '{chore_name}' not found.")]

    cursor.execute(
        "INSERT INTO urls (chore_name, url) VALUES (?, ?)",
        (chore_name, url)
    )
    conn.commit()
    conn.close()

    return [TextContent(type="text", text=f"Added URL to '{chore_name}'")]


async def add_chore(args: dict):
    """Create a new chore using CadenceManager."""
    name = args["name"]
    description = args.get("description")
    frequency_in_days = args.get("frequency_in_days")
    parent_chores = args.get("parent_chores")
    urls = args.get("urls")
    adjust_frequency = args.get("adjust_frequency", True)

    # Check if chore already exists
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM chores WHERE name = ?", (name,))
    if cursor.fetchone():
        conn.close()
        return [TextContent(type="text", text=f"'{name}' already exists.")]
    conn.close()

    # Use CadenceManager.add_chore for smart frequency inference and proper creation
    manager.add_chore(
        name=name,
        description=description,
        frequency_in_days=frequency_in_days,
        parent_chores=parent_chores,
        urls=urls,
        adjust_frequency=1 if adjust_frequency else 0
    )

    # Build response
    result_parts = [f"Created '{name}'"]
    if parent_chores:
        result_parts.append(f"under {parent_chores}")
    if frequency_in_days:
        result_parts.append(f"(freq: {frequency_in_days}d)")
    else:
        result_parts.append("(freq: inferred from siblings)")

    return [TextContent(type="text", text=" ".join(result_parts))]


async def find_non_granular_tags(args: dict):
    """Find exercises with non-granular tags."""
    category = args.get("category")
    limit = args.get("limit", 50)

    conn = get_connection()
    cursor = conn.cursor()

    # Find parent tags that have children (non-granular)
    cursor.execute('''
        SELECT DISTINCT pc.parent_chore as non_granular_tag, pc.chore_name as exercise
        FROM parent_chores pc
        JOIN chores c ON pc.chore_name = c.name
        WHERE c.active = 1
        AND EXISTS (SELECT 1 FROM parent_chores pc2 WHERE pc2.parent_chore = pc.parent_chore)
        AND NOT EXISTS (SELECT 1 FROM parent_chores pc3 WHERE pc3.parent_chore = pc.chore_name)
        ORDER BY pc.parent_chore
        LIMIT ?
    ''', (limit,))

    results = {}
    for row in cursor.fetchall():
        tag = row["non_granular_tag"]
        exercise = row["exercise"]
        if tag not in results:
            results[tag] = []
        results[tag].append(exercise)

    conn.close()

    return [TextContent(type="text", text=json.dumps({
        "non_granular_tags": results
    }, indent=2))]


async def run_sql_query(args: dict):
    """Run a read-only SQL query."""
    query = args["query"].strip()
    limit = args.get("limit", 100)

    # Security: only allow SELECT queries
    if not query.upper().startswith("SELECT"):
        return [TextContent(type="text", text="Error: Only SELECT queries are allowed.")]

    # Prevent dangerous operations
    dangerous = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "TRUNCATE"]
    query_upper = query.upper()
    for word in dangerous:
        if word in query_upper:
            return [TextContent(type="text", text=f"Error: {word} operations not allowed.")]

    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Add LIMIT if not present
        if "LIMIT" not in query.upper():
            query = f"{query} LIMIT {limit}"

        cursor.execute(query)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

        conn.close()
        return [TextContent(type="text", text=json.dumps({
            "columns": columns,
            "row_count": len(rows),
            "rows": rows
        }, indent=2))]
    except Exception as e:
        conn.close()
        return [TextContent(type="text", text=f"SQL Error: {str(e)}")]


# =============================================================================
# HIERARCHY & OPERATIONAL TOOLS
# =============================================================================

async def get_hierarchy_tree(args: dict):
    """Generate a visual hierarchy tree."""
    root_name = args["root_name"]
    include_terminals = args.get("include_terminals", False)

    conn = get_connection()
    cursor = conn.cursor()

    # Check if root exists
    cursor.execute("SELECT 1 FROM chores WHERE name = ?", (root_name,))
    if not cursor.fetchone():
        conn.close()
        return [TextContent(type="text", text=f"Category '{root_name}' not found.")]

    def build_tree(name, indent=0, result=None):
        if result is None:
            result = []

        if indent == 0:
            result.append(name)

        # Get children
        cursor.execute("""
            SELECT c.name
            FROM chores c
            JOIN parent_chores pc ON c.name = pc.chore_name
            WHERE pc.parent_chore = ? AND c.active = 1
            ORDER BY c.name
        """, (name,))
        children = [r[0] for r in cursor.fetchall()]

        for i, child_name in enumerate(children):
            # Check if child has children
            cursor.execute("SELECT 1 FROM parent_chores WHERE parent_chore = ?", (child_name,))
            has_children = cursor.fetchone() is not None

            should_include = has_children or include_terminals

            if should_include:
                is_last = (i == len(children) - 1)
                prefix = "  " * indent
                branch = "└─ " if is_last else "├─ "
                result.append(f"{prefix}{branch}{child_name}")

                if has_children:
                    build_tree(child_name, indent + 1, result)

        return result

    tree_lines = build_tree(root_name)
    conn.close()

    return [TextContent(type="text", text="\n".join(tree_lines))]


async def log_chore(args: dict):
    """Log completion of one or more chores. Uses CadenceManager which includes frequency adjustment."""
    # Handle both single name and batch names
    names = args.get("names", [])
    if not names and "name" in args:
        names = [args["name"]]

    note = args.get("note")  # Optional note to add after logging

    if not names:
        return [TextContent(type="text", text="Error: Must provide 'name' or 'names' parameter.")]

    results = []
    errors = []

    conn = get_connection()
    cursor = conn.cursor()

    for name in names:
        # Use CadenceManager which handles logging, parent recursion, and frequency adjustment
        result = manager.log_chore(name)

        if result[0] is None:
            errors.append(name)
            continue

        next_due, logged_chores = result

        # Get frequency for response
        cursor.execute("SELECT frequency_in_days FROM chores WHERE name = ?", (name,))
        row = cursor.fetchone()
        freq = float(row[0]) if row else 0

        # Add note if provided (only for single-name logging)
        note_added = False
        if note and len(names) == 1:
            manager.add_note(name, note)
            note_added = True

        result_entry = {
            "logged": name,
            "next_due": next_due.isoformat(),
            "frequency_days": freq,
            "parents_logged": logged_chores[1:] if len(logged_chores) > 1 else []
        }
        if note_added:
            result_entry["note_added"] = note

        results.append(result_entry)

    conn.close()

    # Hook: Auto-adjust all frequencies after logging Daily Meds + Supplements
    if "Daily Meds + Supplements" in [r["logged"] for r in results]:
        # Run frequency adjustment for all chores
        adjustment_result = await adjust_all_frequencies({
            "adjust_without_parent": False,  # Only adjust chores with parents
            "lower_bound_multiplier": 1.382,  # Golden ratio
            "upper_bound_divider": 1.382,
            "lower_bound_tightness": 0.5
        })
        # Add a note about adjustments to the response
        adjustments_data = json.loads(adjustment_result[0].text)
        if adjustments_data.get("adjusted_count", 0) > 0:
            for result in results:
                if result["logged"] == "Daily Meds + Supplements":
                    result["frequency_adjustments_run"] = {
                        "adjusted_count": adjustments_data["adjusted_count"],
                        "message": f"Auto-adjusted {adjustments_data['adjusted_count']} chore frequencies",
                        "adjustments": adjustments_data.get("adjustments", [])
                    }
                    break

    # Return single result format if only one item, batch format otherwise
    if len(names) == 1 and not errors:
        return [TextContent(type="text", text=json.dumps(results[0], indent=2))]

    response = {"results": results}
    if errors:
        # Add fuzzy matching suggestions for not-found chores
        not_found_with_suggestions = []
        for error_name in errors:
            similar = manager.find_similar(error_name, limit=5)
            entry = {"name": error_name}
            if similar:
                entry["did_you_mean"] = [match[0] for match in similar]
            not_found_with_suggestions.append(entry)
        response["not_found"] = not_found_with_suggestions

    return [TextContent(type="text", text=json.dumps(response, indent=2))]


async def delete_chore(args: dict):
    """Delete a chore and all its associated data. Uses CadenceManager. Supports single or batch."""
    chore_names = args.get("names", [])
    if not chore_names and "name" in args:
        chore_names = [args["name"]]
    confirm = args.get("confirm", False)

    if not chore_names:
        return [TextContent(type="text", text="Error: Must provide 'name' or 'names' parameter.")]

    if not confirm:
        return [TextContent(type="text", text="Error: Must set confirm=true to delete.")]

    if len(chore_names) == 1:
        name = chore_names[0]
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM chores WHERE name = ?", (name,))
        if not cursor.fetchone():
            conn.close()
            return [TextContent(type="text", text=f"Chore '{name}' not found.")]
        conn.close()
        manager.delete_chore(name, cascade=True)
        return [TextContent(type="text", text=f"Deleted '{name}' and all associated data.")]

    # Batch mode
    results = []
    errors = []
    conn = get_connection()
    cursor = conn.cursor()
    for name in chore_names:
        cursor.execute("SELECT 1 FROM chores WHERE name = ?", (name,))
        if not cursor.fetchone():
            errors.append({"name": name, "error": "not found"})
            continue
        manager.delete_chore(name, cascade=True)
        results.append(name)
    conn.close()

    response = {"deleted": results}
    if errors:
        response["failed"] = errors
    return [TextContent(type="text", text=json.dumps(response, indent=2))]


async def reset_overdue_chores(args: dict):
    """Reset overdue chores."""
    dry_run = args.get("dry_run", True)

    conn = get_connection()
    cursor = conn.cursor()

    now = datetime.now()

    # Find overdue exercises
    cursor.execute("""
        SELECT c.name, c.frequency_in_days, l.complete_by
        FROM chores c
        LEFT JOIN (
            SELECT chore_name, complete_by
            FROM logs
            WHERE (chore_name, logged_at) IN (
                SELECT chore_name, MAX(logged_at) FROM logs GROUP BY chore_name
            )
        ) l ON c.name = l.chore_name
        WHERE c.active = 1
    """)

    overdue = []
    for row in cursor.fetchall():
        name, freq, complete_by = row
        if complete_by:
            due_date = datetime.fromisoformat(complete_by)
        else:
            continue  # No log yet, skip

        if due_date < now:
            new_due = now + timedelta(days=float(freq))
            overdue.append({
                "name": name,
                "was_due": complete_by,
                "new_due": new_due.isoformat(),
                "days_overdue": (now - due_date).days
            })

            if not dry_run:
                cursor.execute("""
                    INSERT INTO logs (chore_name, logged_at, complete_by, is_genuine)
                    VALUES (?, ?, ?, 0)
                """, (name, now.isoformat(), new_due.isoformat()))

    if not dry_run:
        conn.commit()

    conn.close()

    return [TextContent(type="text", text=json.dumps({
        "dry_run": dry_run,
        "overdue_count": len(overdue),
        "chores": overdue[:50]  # Limit output
    }, indent=2))]


async def get_upcoming_chores(args: dict):
    """Get chores sorted by urgency. Returns dual sections by default."""
    limit = args.get("limit", 10)
    filter_parent = args.get("filter_parent")
    show_by_cycle = args.get("show_by_cycle", True)
    show_by_due = args.get("show_by_due", True)
    include_overdue_only = args.get("include_overdue_only", False)
    exclude_descendants_of = args.get("exclude_descendants_of", ["Climbing"])

    # Get descendants of excluded categories (full ancestry traversal)
    excluded_chores = set()
    if exclude_descendants_of:
        conn = get_connection()
        cursor = conn.cursor()
        for parent in exclude_descendants_of:
            cursor.execute("""
                WITH RECURSIVE descendants AS (
                    SELECT ? as chore_name
                    UNION ALL
                    SELECT pc.chore_name FROM parent_chores pc
                    JOIN descendants d ON pc.parent_chore = d.chore_name
                )
                SELECT DISTINCT chore_name FROM descendants
            """, (parent,))
            excluded_chores.update(r[0] for r in cursor.fetchall())
        conn.close()

    def format_chore(chore):
        desc = chore.get("description")
        return {
            "name": chore["name"],
            "days_until_due": round(chore["days_until_due"], 1) if chore["days_until_due"] is not None else None,
            "frequency_days": chore["frequency_in_days"],
            "cycle_progress": chore["cycle_progress"],
            "description": desc[:80] if desc else None
        }

    def get_filtered_chores(sort_by):
        fetch_limit = limit + len(excluded_chores) if excluded_chores else limit
        chores = manager.get_sorted_due_chores(
            limit=fetch_limit * 2,  # Fetch extra to account for filtering
            filter_parent=filter_parent,
            include_overdue_only=include_overdue_only,
            leaf_only=True,
            sort_by=sort_by
        )
        results = []
        for chore in chores:
            if chore["name"] in excluded_chores:
                continue
            results.append(format_chore(chore))
            if len(results) >= limit:
                break
        return results

    output = {}

    if show_by_cycle:
        output["by_cycle"] = get_filtered_chores("cycle_progress")

    if show_by_due:
        output["by_due"] = get_filtered_chores("days_until_due")

    return [TextContent(type="text", text=json.dumps(output, indent=2))]


# =============================================================================
# CONTEXT TOOLS
# =============================================================================

async def get_current_datetime(args: dict):
    """Get current datetime in Central Time."""
    ct = ZoneInfo("America/Chicago")
    now = datetime.now(ct)

    hour = now.hour
    if hour < 6:
        time_of_day = "night"
    elif hour < 9:
        time_of_day = "early_morning"
    elif hour < 12:
        time_of_day = "morning"
    elif hour < 14:
        time_of_day = "midday"
    elif hour < 17:
        time_of_day = "afternoon"
    elif hour < 21:
        time_of_day = "evening"
    else:
        time_of_day = "night"

    return [TextContent(type="text", text=json.dumps({
        "datetime": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "day_of_week": now.strftime("%A"),
        "time_of_day": time_of_day,
        "hour": hour
    }, indent=2))]


async def get_weather(args: dict):
    """Get current weather using Open-Meteo API. Configure WEATHER_LAT/WEATHER_LON env vars."""
    # Default: Grafton, WI — override with WEATHER_LAT / WEATHER_LON environment variables
    lat = float(os.environ.get("WEATHER_LAT", "43.3267"))
    lon = float(os.environ.get("WEATHER_LON", "-87.9534"))

    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m,wind_gusts_10m&temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch&timezone=America%2FChicago"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())

        current = data.get("current", {})

        # Weather code descriptions
        weather_codes = {
            0: "Clear sky",
            1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
            45: "Foggy", 48: "Depositing rime fog",
            51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
            61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
            71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
            77: "Snow grains",
            80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
            85: "Slight snow showers", 86: "Heavy snow showers",
            95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail"
        }

        weather_code = current.get("weather_code", 0)
        condition = weather_codes.get(weather_code, f"Unknown ({weather_code})")

        temp = current.get("temperature_2m")
        feels_like = current.get("apparent_temperature")

        # Outdoor activity recommendations
        outdoor_ok = True
        outdoor_notes = []

        if temp is not None:
            if temp < 32:
                outdoor_notes.append("Cold - dress warmly for outdoor activities")
            if temp < 20:
                outdoor_ok = False
                outdoor_notes.append("Very cold - consider indoor alternatives")
            if temp > 90:
                outdoor_notes.append("Hot - stay hydrated, consider early morning")

        if weather_code >= 61:  # Rain or worse
            outdoor_ok = False
            outdoor_notes.append("Precipitation - not ideal for road running")

        wind = current.get("wind_speed_10m", 0)
        if wind > 20:
            outdoor_notes.append(f"Windy ({wind} mph) - may affect outdoor activities")

        return [TextContent(type="text", text=json.dumps({
            "location": os.environ.get("WEATHER_LOCATION", "Grafton, WI"),
            "temperature_f": temp,
            "feels_like_f": feels_like,
            "condition": condition,
            "humidity_percent": current.get("relative_humidity_2m"),
            "wind_mph": wind,
            "wind_gusts_mph": current.get("wind_gusts_10m"),
            "precipitation_inch": current.get("precipitation"),
            "outdoor_activity_ok": outdoor_ok,
            "outdoor_notes": outdoor_notes if outdoor_notes else ["Good conditions for outdoor activities"]
        }, indent=2))]

    except urllib.error.URLError as e:
        return [TextContent(type="text", text=json.dumps({
            "error": f"Could not fetch weather: {str(e)}",
            "outdoor_activity_ok": None
        }, indent=2))]


async def get_time_since_last_activity(args: dict):
    """Get time since last logged activity."""
    filter_parent = args.get("filter_parent")

    conn = get_connection()
    cursor = conn.cursor()

    ct = ZoneInfo("America/Chicago")
    now = datetime.now(ct)

    if filter_parent:
        # Get activities under this parent - only leaf chores (no children)
        cursor.execute("""
            WITH RECURSIVE descendants AS (
                SELECT chore_name FROM parent_chores WHERE parent_chore = ?
                UNION ALL
                SELECT pc.chore_name FROM parent_chores pc
                JOIN descendants d ON pc.parent_chore = d.chore_name
            )
            SELECT l.chore_name, l.logged_at
            FROM logs l
            JOIN descendants d ON l.chore_name = d.chore_name
            WHERE l.is_genuine = 1
            AND NOT EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = l.chore_name)
            ORDER BY l.logged_at DESC
            LIMIT 1
        """, (filter_parent,))
    else:
        cursor.execute("""
            SELECT chore_name, logged_at
            FROM logs
            WHERE is_genuine = 1
            AND NOT EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = chore_name)
            ORDER BY logged_at DESC
            LIMIT 1
        """)

    row = cursor.fetchone()

    # Also get count of activities today
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if filter_parent:
        cursor.execute("""
            WITH RECURSIVE descendants AS (
                SELECT chore_name FROM parent_chores WHERE parent_chore = ?
                UNION ALL
                SELECT pc.chore_name FROM parent_chores pc
                JOIN descendants d ON pc.parent_chore = d.chore_name
            )
            SELECT COUNT(DISTINCT l.chore_name)
            FROM logs l
            JOIN descendants d ON l.chore_name = d.chore_name
            WHERE l.is_genuine = 1 AND l.logged_at >= ?
        """, (filter_parent, today_start.isoformat()))
    else:
        cursor.execute("""
            SELECT COUNT(DISTINCT chore_name)
            FROM logs
            WHERE is_genuine = 1 AND logged_at >= ?
        """, (today_start.isoformat(),))

    today_count = cursor.fetchone()[0]

    conn.close()

    if row:
        last_chore, last_time = row
        last_dt = datetime.fromisoformat(last_time)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=ct)

        delta = now - last_dt
        hours = delta.total_seconds() / 3600

        return [TextContent(type="text", text=json.dumps({
            "last_activity": last_chore,
            "last_activity_time": last_time,
            "hours_since": round(hours, 1),
            "minutes_since": round(delta.total_seconds() / 60),
            "activities_today": today_count,
            "filter": filter_parent
        }, indent=2))]
    else:
        return [TextContent(type="text", text=json.dumps({
            "last_activity": None,
            "message": "No activities logged" + (f" under {filter_parent}" if filter_parent else ""),
            "activities_today": today_count,
            "filter": filter_parent
        }, indent=2))]


# =============================================================================
# SUGGESTION TOOLS
# =============================================================================

async def find_multi_target_exercises(args: dict):
    """Find exercises that hit multiple urgent (soonest due) muscle groups."""
    min_targets = args.get("min_targets", 2)
    limit = args.get("limit", 20)
    top_k_muscles = args.get("top_k_muscles", 15)  # Consider top K most urgent muscles

    conn = get_connection()
    cursor = conn.cursor()

    ct = ZoneInfo("America/Chicago")
    now = datetime.now(ct)

    # Find all leaf muscle groups with their due dates, sorted by urgency
    cursor.execute("""
        WITH RECURSIVE muscle_descendants AS (
            SELECT chore_name FROM parent_chores WHERE parent_chore = 'Muscle Group'
            UNION ALL
            SELECT pc.chore_name FROM parent_chores pc
            JOIN muscle_descendants md ON pc.parent_chore = md.chore_name
        )
        SELECT c.name, l.complete_by
        FROM chores c
        JOIN muscle_descendants md ON c.name = md.chore_name
        LEFT JOIN (
            SELECT chore_name, complete_by
            FROM logs
            WHERE (chore_name, logged_at) IN (
                SELECT chore_name, MAX(logged_at) FROM logs GROUP BY chore_name
            )
        ) l ON c.name = l.chore_name
        WHERE c.active = 1
        AND NOT EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = c.name)
    """)

    all_muscles = []
    for row in cursor.fetchall():
        name, complete_by = row
        if complete_by:
            due_date = datetime.fromisoformat(complete_by)
            if due_date.tzinfo is None:
                due_date = due_date.replace(tzinfo=ct)
            days_until_due = (due_date - now).total_seconds() / (3600 * 24)
            all_muscles.append({"name": name, "days_until_due": round(days_until_due, 1)})

    if not all_muscles:
        conn.close()
        return [TextContent(type="text", text=json.dumps({
            "message": "No muscle groups found with due dates",
            "exercises": []
        }, indent=2))]

    # Sort by urgency (lowest days_until_due first) and take top K
    all_muscles.sort(key=lambda x: x["days_until_due"])
    urgent_muscles = all_muscles[:top_k_muscles]
    urgent_muscle_names = set(m["name"] for m in urgent_muscles)

    # Now find leaf exercises and check how many urgent muscles they hit
    cursor.execute("""
        WITH RECURSIVE exercise_descendants AS (
            SELECT chore_name FROM parent_chores WHERE parent_chore = 'Exercise'
            UNION ALL
            SELECT pc.chore_name FROM parent_chores pc
            JOIN exercise_descendants ed ON pc.parent_chore = ed.chore_name
        )
        SELECT DISTINCT c.name, c.description
        FROM chores c
        JOIN exercise_descendants ed ON c.name = ed.chore_name
        WHERE c.active = 1
        AND NOT EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = c.name)
    """)

    exercises = cursor.fetchall()

    results = []
    for ex_name, ex_desc in exercises:
        # Get all ancestors of this exercise
        ancestors = get_ancestors(conn, ex_name)

        # Find which urgent muscles this exercise hits
        hits = [m for m in urgent_muscle_names if m in ancestors]

        if len(hits) >= min_targets:
            # Get exercise due info
            cursor.execute("""
                SELECT complete_by FROM logs
                WHERE chore_name = ?
                ORDER BY logged_at DESC LIMIT 1
            """, (ex_name,))
            log_row = cursor.fetchone()

            days_until_due = None
            if log_row and log_row[0]:
                due_date = datetime.fromisoformat(log_row[0])
                if due_date.tzinfo is None:
                    due_date = due_date.replace(tzinfo=ct)
                days_until_due = (due_date - now).total_seconds() / (3600 * 24)

            results.append({
                "exercise": ex_name,
                "description": ex_desc[:100] if ex_desc else None,
                "muscles_hit": sorted(hits),
                "num_targets": len(hits),
                "days_until_due": round(days_until_due, 1) if days_until_due else None
            })

    # Sort by number of targets (descending), then by exercise urgency
    results.sort(key=lambda x: (-x["num_targets"], x["days_until_due"] or 999))
    results = results[:limit]

    conn.close()

    return [TextContent(type="text", text=json.dumps({
        "urgent_muscles_considered": len(urgent_muscles),
        "top_urgent_muscles": urgent_muscles[:10],
        "multi_target_exercises": results
    }, indent=2))]


async def get_related_chores(args: dict):
    """Find chores that share parent categories with a given chore."""
    chore_name = args["chore_name"]
    include_due_info = args.get("include_due_info", True)

    conn = get_connection()
    cursor = conn.cursor()

    # Get direct parents of the chore
    cursor.execute("SELECT parent_chore FROM parent_chores WHERE chore_name = ?", (chore_name,))
    parents = [r[0] for r in cursor.fetchall()]

    if not parents:
        conn.close()
        return [TextContent(type="text", text=json.dumps({
            "chore": chore_name,
            "error": "Chore not found or has no parents"
        }, indent=2))]

    # Find other chores that share these parents (siblings)
    related = {}
    for parent in parents:
        cursor.execute("""
            SELECT c.name, c.description, c.frequency_in_days
            FROM chores c
            JOIN parent_chores pc ON c.name = pc.chore_name
            WHERE pc.parent_chore = ?
            AND c.name != ?
            AND c.active = 1
            AND NOT EXISTS (SELECT 1 FROM parent_chores pc2 WHERE pc2.parent_chore = c.name)
        """, (parent, chore_name))

        for row in cursor.fetchall():
            name, desc, freq = row
            if name not in related:
                related[name] = {"name": name, "description": desc, "frequency_days": freq, "shared_parents": []}
            related[name]["shared_parents"].append(parent)

    if include_due_info:
        ct = ZoneInfo("America/Chicago")
        now = datetime.now(ct)

        for name in related:
            cursor.execute("""
                SELECT complete_by, logged_at FROM logs
                WHERE chore_name = ?
                ORDER BY logged_at DESC LIMIT 1
            """, (name,))
            row = cursor.fetchone()

            if row and row[0]:
                due_date = datetime.fromisoformat(row[0])
                if due_date.tzinfo is None:
                    due_date = due_date.replace(tzinfo=ct)
                days_until = (due_date - now).total_seconds() / (3600 * 24)
                related[name]["days_until_due"] = round(days_until, 1)

                # Calculate cycle progress: days_since_last_log / frequency
                if row[1] and related[name].get("frequency_days"):
                    logged_dt = datetime.fromisoformat(row[1])
                    if logged_dt.tzinfo is None:
                        logged_dt = logged_dt.replace(tzinfo=ct)
                    days_since_log = (now - logged_dt).total_seconds() / (3600 * 24)
                    freq = float(related[name]["frequency_days"])
                    related[name]["cycle_progress"] = round(days_since_log / freq, 2)
                else:
                    related[name]["cycle_progress"] = None
            else:
                related[name]["days_until_due"] = None
                related[name]["cycle_progress"] = None

    # Sort by due date (most overdue first)
    results = sorted(related.values(), key=lambda x: x.get("days_until_due") or 999)

    conn.close()

    return [TextContent(type="text", text=json.dumps({
        "chore": chore_name,
        "parents": parents,
        "related_chores": results
    }, indent=2))]


async def get_session_context(args: dict):
    """Get comprehensive context for the current session."""
    include_weather = args.get("include_weather", True)
    thresholds = args.get("retrain_thresholds", {})
    exclude_descendants_of = args.get("exclude_descendants_of", ["Climbing"])

    # Default thresholds
    days_threshold = thresholds.get("days_since_training", 7)
    growth_threshold = thresholds.get("log_growth_percent", 20)
    feedback_threshold = thresholds.get("min_new_feedback", 50)

    conn = get_connection()
    cursor = conn.cursor()

    ct = ZoneInfo("America/Chicago")
    now = datetime.now(ct)

    result = {}

    # === TEMPORAL CONTEXT ===
    hour = now.hour
    if 5 <= hour < 12:
        time_of_day = "morning"
    elif 12 <= hour < 17:
        time_of_day = "afternoon"
    elif 17 <= hour < 21:
        time_of_day = "evening"
    else:
        time_of_day = "night"

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    result["temporal"] = {
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "day_of_week": day_names[now.weekday()],
        "hour": hour,
        "time_of_day": time_of_day,
        "is_weekend": now.weekday() >= 5
    }

    # === WEATHER (optional) ===
    if include_weather:
        try:
            weather_result = await get_weather({})
            weather_data = json.loads(weather_result[0].text)
            result["weather"] = weather_data
        except:
            result["weather"] = {"error": "Could not fetch weather"}

    # === RECENT ACTIVITY ===
    # Last 5 leaf logs (not parent categories)
    cursor.execute("""
        SELECT l.chore_name, l.logged_at
        FROM logs l
        WHERE l.is_genuine = 1
        AND l.chore_name NOT IN (
            SELECT DISTINCT parent_chore FROM parent_chores WHERE parent_chore IS NOT NULL
        )
        ORDER BY l.logged_at DESC
        LIMIT 5
    """)
    recent_leaves = []
    for row in cursor.fetchall():
        logged_dt = datetime.fromisoformat(row[1])
        if logged_dt.tzinfo is None:
            logged_dt = logged_dt.replace(tzinfo=ct)
        mins_ago = (now - logged_dt).total_seconds() / 60
        recent_leaves.append({
            "chore": row[0],
            "logged_at": row[1],
            "minutes_ago": round(mins_ago)
        })

    # Time since last activity
    time_since_last = recent_leaves[0]["minutes_ago"] if recent_leaves else None

    # Today's activity count
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cursor.execute("""
        SELECT COUNT(DISTINCT l.chore_name)
        FROM logs l
        WHERE l.is_genuine = 1
        AND l.logged_at >= ?
        AND l.chore_name NOT IN (
            SELECT DISTINCT parent_chore FROM parent_chores WHERE parent_chore IS NOT NULL
        )
    """, (today_start.isoformat(),))
    today_count = cursor.fetchone()[0]

    # Last 7 days activity count (unique leaf exercises)
    week_start = now - timedelta(days=7)
    cursor.execute("""
        SELECT COUNT(DISTINCT l.chore_name)
        FROM logs l
        WHERE l.is_genuine = 1
        AND l.logged_at >= ?
        AND l.chore_name NOT IN (
            SELECT DISTINCT parent_chore FROM parent_chores WHERE parent_chore IS NOT NULL
        )
    """, (week_start.isoformat(),))
    week_count = cursor.fetchone()[0]

    result["recent_activity"] = {
        "last_5_leaves": recent_leaves,
        "minutes_since_last": time_since_last,
        "activities_today": today_count,
        "unique_activities_last_7_days": week_count
    }

    # === CURRENT SESSION (last 30 min) ===
    cursor.execute("""
        SELECT l.chore_name
        FROM logs l
        WHERE l.is_genuine = 1
        AND l.logged_at > datetime('now', '-30 minutes')
        AND l.chore_name NOT IN (
            SELECT DISTINCT parent_chore FROM parent_chores WHERE parent_chore IS NOT NULL
        )
        ORDER BY l.logged_at DESC
    """)
    session_chores = [row[0] for row in cursor.fetchall()]
    result["session"] = {
        "chores_in_session": session_chores,
        "session_size": len(session_chores)
    }

    # === MODEL/TRAINING STATUS ===
    model_metadata_path = Path(__file__).parent.parent / "ml_experiments" / "model_metadata.json"
    model_status = {"available": ML_AVAILABLE}

    if model_metadata_path.exists():
        with open(model_metadata_path) as f:
            metadata = json.load(f)

        last_trained = datetime.fromisoformat(metadata.get("last_trained", "2025-01-01"))
        if last_trained.tzinfo is None:
            last_trained = last_trained.replace(tzinfo=ct)

        days_since_training = (now - last_trained).days
        logs_at_training = metadata.get("leaf_logs_at_training", 0)

        # Current leaf log count
        cursor.execute("""
            SELECT COUNT(*)
            FROM logs l
            WHERE l.is_genuine = 1
            AND l.chore_name NOT IN (
                SELECT DISTINCT parent_chore FROM parent_chores WHERE parent_chore IS NOT NULL
            )
        """)
        current_leaf_logs = cursor.fetchone()[0]

        logs_since_training = current_leaf_logs - logs_at_training
        growth_percent = (logs_since_training / logs_at_training * 100) if logs_at_training > 0 else 0

        # Feedback stats
        cursor.execute("SELECT COUNT(*) FROM suggestions")
        total_suggestions = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM suggestions WHERE was_selected = 1")
        feedback_collected = cursor.fetchone()[0]

        # Calculate observed hit rate from feedback
        cursor.execute("""
            SELECT suggestion_rank, COUNT(*) as cnt
            FROM suggestions
            WHERE was_selected = 1
            GROUP BY suggestion_rank
        """)
        hit_by_rank = {row[0]: row[1] for row in cursor.fetchall()}
        hit_at_1 = hit_by_rank.get(1, 0)
        observed_hit_rate = hit_at_1 / feedback_collected if feedback_collected > 0 else None

        # Should retrain?
        retrain_reasons = []
        if days_since_training >= days_threshold:
            retrain_reasons.append(f"days_since_training ({days_since_training}) >= {days_threshold}")
        if growth_percent >= growth_threshold:
            retrain_reasons.append(f"log_growth ({growth_percent:.1f}%) >= {growth_threshold}%")
        if feedback_collected >= feedback_threshold:
            retrain_reasons.append(f"new_feedback ({feedback_collected}) >= {feedback_threshold}")

        should_retrain = len(retrain_reasons) > 0

        model_status.update({
            "last_trained": metadata.get("last_trained"),
            "days_since_training": days_since_training,
            "model_version": metadata.get("model_version"),
            "training_hit_at_1": metadata.get("hit_at_1"),
            "logs_at_training": logs_at_training,
            "current_leaf_logs": current_leaf_logs,
            "logs_since_training": logs_since_training,
            "log_growth_percent": round(growth_percent, 1),
            "should_retrain": should_retrain,
            "retrain_reasons": retrain_reasons
        })

        result["feedback_stats"] = {
            "total_suggestions_made": total_suggestions,
            "feedback_collected": feedback_collected,
            "observed_hit_at_1": observed_hit_rate,
            "feedback_rate": feedback_collected / total_suggestions if total_suggestions > 0 else None
        }

    result["model_status"] = model_status

    # === BUILD EXCLUSION CTE FOR EXERCISE QUERIES ===
    # This will be reused across nearest_due and most_overdue_by_cycle
    exclusion_cte = ""
    exclusion_filter = ""
    if exclude_descendants_of:
        category_unions = " UNION ".join([
            f"SELECT '{cat}' as root_cat, chore_name FROM parent_chores WHERE parent_chore = '{cat}'"
            for cat in exclude_descendants_of
        ])
        exclusion_cte = f""",
            excluded_chores AS (
                {category_unions}
                UNION ALL
                SELECT e.root_cat, pc.chore_name FROM parent_chores pc
                JOIN excluded_chores e ON pc.parent_chore = e.chore_name
            )"""
        exclusion_filter = " AND c.name NOT IN (SELECT chore_name FROM excluded_chores)"

    # === HIGHLIGHTABLE DATA POINTS ===
    highlights = {}

    # 1. Nearest due exercises (top 5 closest to due date across all exercises)
    cursor.execute(f"""
        WITH RECURSIVE exercise_descendants AS (
            SELECT chore_name FROM parent_chores WHERE parent_chore = 'Exercise'
            UNION ALL
            SELECT pc.chore_name FROM parent_chores pc
            JOIN exercise_descendants ed ON pc.parent_chore = ed.chore_name
        ),
        distinct_exercises AS (SELECT DISTINCT chore_name FROM exercise_descendants)
        {exclusion_cte}
        SELECT c.name, c.frequency_in_days, l.complete_by, l.logged_at
        FROM chores c
        JOIN distinct_exercises ed ON c.name = ed.chore_name
        LEFT JOIN (
            SELECT chore_name, complete_by, logged_at
            FROM logs
            WHERE (chore_name, logged_at) IN (
                SELECT chore_name, MAX(logged_at) FROM logs GROUP BY chore_name
            )
        ) l ON c.name = l.chore_name
        WHERE c.active = 1
        AND NOT EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = c.name)
        AND l.complete_by IS NOT NULL
        {exclusion_filter}
        ORDER BY l.complete_by
        LIMIT 5
    """)

    nearest_due = []
    for row in cursor.fetchall():
        name, freq, complete_by, logged_at = row
        due_date = datetime.fromisoformat(complete_by)
        if due_date.tzinfo is None:
            due_date = due_date.replace(tzinfo=ct)
        days_until = (due_date - now).total_seconds() / 86400
        nearest_due.append({
            "name": name,
            "days_until_due": round(days_until, 1),
            "frequency_days": round(freq, 1) if freq else None
        })
    highlights["nearest_due"] = nearest_due

    # 1b. Nearest due PARENT categories (categories with children)
    cursor.execute(f"""
        WITH RECURSIVE exercise_descendants AS (
            SELECT chore_name FROM parent_chores WHERE parent_chore = 'Exercise'
            UNION ALL
            SELECT pc.chore_name FROM parent_chores pc
            JOIN exercise_descendants ed ON pc.parent_chore = ed.chore_name
        ),
        distinct_exercises AS (SELECT DISTINCT chore_name FROM exercise_descendants)
        {exclusion_cte}
        SELECT c.name, c.frequency_in_days, l.complete_by, l.logged_at
        FROM chores c
        JOIN distinct_exercises ed ON c.name = ed.chore_name
        LEFT JOIN (
            SELECT chore_name, complete_by, logged_at
            FROM logs
            WHERE (chore_name, logged_at) IN (
                SELECT chore_name, MAX(logged_at) FROM logs GROUP BY chore_name
            )
        ) l ON c.name = l.chore_name
        WHERE c.active = 1
        AND EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = c.name)
        AND l.complete_by IS NOT NULL
        {exclusion_filter}
        ORDER BY l.complete_by
        LIMIT 5
    """)

    nearest_due_parents = []
    for row in cursor.fetchall():
        name, freq, complete_by, logged_at = row
        due_date = datetime.fromisoformat(complete_by)
        if due_date.tzinfo is None:
            due_date = due_date.replace(tzinfo=ct)
        days_until = (due_date - now).total_seconds() / 86400
        nearest_due_parents.append({
            "name": name,
            "days_until_due": round(days_until, 1),
            "frequency_days": round(freq, 1) if freq else None
        })
    highlights["nearest_due_parents"] = nearest_due_parents

    # 2. Most overdue by cycle ratio (days_since_logged / frequency)
    # These are exercises that have gone the most "cycles" past their due date
    # Uses exclusion_cte and exclusion_filter built earlier
    cursor.execute(f"""
        WITH RECURSIVE exercise_descendants AS (
            SELECT chore_name FROM parent_chores WHERE parent_chore = 'Exercise'
            UNION ALL
            SELECT pc.chore_name FROM parent_chores pc
            JOIN exercise_descendants ed ON pc.parent_chore = ed.chore_name
        ),
        distinct_exercises AS (SELECT DISTINCT chore_name FROM exercise_descendants)
        {exclusion_cte}
        SELECT c.name, c.frequency_in_days, l.logged_at
        FROM chores c
        JOIN distinct_exercises ed ON c.name = ed.chore_name
        LEFT JOIN (
            SELECT chore_name, MAX(logged_at) as logged_at
            FROM logs WHERE is_genuine = 1
            GROUP BY chore_name
        ) l ON c.name = l.chore_name
        WHERE c.active = 1
        AND NOT EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = c.name)
        AND l.logged_at IS NOT NULL
        AND c.frequency_in_days > 0
        {exclusion_filter}
    """)

    overdue_ratios = []
    for row in cursor.fetchall():
        name, freq, logged_at = row
        if logged_at and freq > 0:
            logged_dt = datetime.fromisoformat(logged_at)
            if logged_dt.tzinfo is None:
                logged_dt = logged_dt.replace(tzinfo=ct)
            days_since = (now - logged_dt).total_seconds() / 86400
            cycle_progress = days_since / freq  # 1.0 = exactly due, >1.0 = overdue
            overdue_ratios.append({
                "name": name,
                "cycle_progress": round(cycle_progress, 2),
                "days_since_logged": round(days_since, 1),
                "frequency_days": round(freq, 1)
            })

    # Sort by cycle progress descending, take top 5
    overdue_ratios.sort(key=lambda x: x["cycle_progress"], reverse=True)
    highlights["most_overdue_by_cycle"] = overdue_ratios[:5]

    # 2b. Most overdue PARENT categories by cycle ratio
    cursor.execute(f"""
        WITH RECURSIVE exercise_descendants AS (
            SELECT chore_name FROM parent_chores WHERE parent_chore = 'Exercise'
            UNION ALL
            SELECT pc.chore_name FROM parent_chores pc
            JOIN exercise_descendants ed ON pc.parent_chore = ed.chore_name
        ),
        distinct_exercises AS (SELECT DISTINCT chore_name FROM exercise_descendants)
        {exclusion_cte}
        SELECT c.name, c.frequency_in_days, l.logged_at
        FROM chores c
        JOIN distinct_exercises ed ON c.name = ed.chore_name
        LEFT JOIN (
            SELECT chore_name, MAX(logged_at) as logged_at
            FROM logs WHERE is_genuine = 1
            GROUP BY chore_name
        ) l ON c.name = l.chore_name
        WHERE c.active = 1
        AND EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = c.name)
        AND l.logged_at IS NOT NULL
        AND c.frequency_in_days > 0
        {exclusion_filter}
    """)

    overdue_parent_ratios = []
    for row in cursor.fetchall():
        name, freq, logged_at = row
        if logged_at:
            last_date = datetime.fromisoformat(logged_at)
            if last_date.tzinfo is None:
                last_date = last_date.replace(tzinfo=ct)
            days_since = (now - last_date).total_seconds() / 86400

            if freq and freq > 0:
                cycle_progress = days_since / freq
                overdue_parent_ratios.append({
                    "name": name,
                    "cycle_progress": round(cycle_progress, 2),
                    "days_since_logged": round(days_since, 1),
                    "frequency_days": round(freq, 1)
                })

    # Sort by cycle progress descending, take top 5
    overdue_parent_ratios.sort(key=lambda x: x["cycle_progress"], reverse=True)
    highlights["most_overdue_by_cycle_parents"] = overdue_parent_ratios[:5]

    # 3. Underutilized to review (top 5 highest frequency)
    cursor.execute("""
        WITH RECURSIVE exercise_descendants AS (
            SELECT chore_name FROM parent_chores WHERE parent_chore = 'Exercise'
            UNION ALL
            SELECT pc.chore_name FROM parent_chores pc
            JOIN exercise_descendants ed ON pc.parent_chore = ed.chore_name
        ),
        distinct_exercises AS (SELECT DISTINCT chore_name FROM exercise_descendants)
        SELECT c.name, c.frequency_in_days, l.logged_at
        FROM chores c
        JOIN distinct_exercises ed ON c.name = ed.chore_name
        LEFT JOIN (
            SELECT chore_name, MAX(logged_at) as logged_at
            FROM logs WHERE is_genuine = 1
            GROUP BY chore_name
        ) l ON c.name = l.chore_name
        WHERE c.active = 1
        AND NOT EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = c.name)
        AND c.frequency_in_days > 60
        ORDER BY c.frequency_in_days DESC
        LIMIT 5
    """)

    underutilized_review = []
    for row in cursor.fetchall():
        name, freq, logged_at = row
        days_since = None
        if logged_at:
            logged_dt = datetime.fromisoformat(logged_at)
            if logged_dt.tzinfo is None:
                logged_dt = logged_dt.replace(tzinfo=ct)
            days_since = round((now - logged_dt).total_seconds() / 86400, 1)
        underutilized_review.append({
            "name": name,
            "frequency_days": round(freq, 1),
            "days_since_logged": days_since
        })
    highlights["underutilized_to_review"] = underutilized_review

    # 4. Weekly review prompt - suggest review if no underutilized exercise logged in 7+ days
    cursor.execute("""
        SELECT MAX(l.logged_at)
        FROM logs l
        JOIN chores c ON l.chore_name = c.name
        WHERE l.is_genuine = 1
        AND c.frequency_in_days > 60
        AND c.active = 1
    """)
    last_underutilized_log = cursor.fetchone()[0]

    if last_underutilized_log:
        last_dt = datetime.fromisoformat(last_underutilized_log)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=ct)
        days_since_underutilized = (now - last_dt).total_seconds() / 86400
        highlights["weekly_review_prompt"] = days_since_underutilized >= 7
        highlights["days_since_underutilized_logged"] = round(days_since_underutilized, 1)
    else:
        highlights["weekly_review_prompt"] = True
        highlights["days_since_underutilized_logged"] = None

    # 5. Culling candidates - freq > 200 AND not logged in 180+ days
    cursor.execute("""
        WITH RECURSIVE exercise_descendants AS (
            SELECT chore_name FROM parent_chores WHERE parent_chore = 'Exercise'
            UNION ALL
            SELECT pc.chore_name FROM parent_chores pc
            JOIN exercise_descendants ed ON pc.parent_chore = ed.chore_name
        ),
        distinct_exercises AS (SELECT DISTINCT chore_name FROM exercise_descendants)
        SELECT c.name, c.frequency_in_days, c.description, l.logged_at
        FROM chores c
        JOIN distinct_exercises ed ON c.name = ed.chore_name
        LEFT JOIN (
            SELECT chore_name, MAX(logged_at) as logged_at
            FROM logs WHERE is_genuine = 1
            GROUP BY chore_name
        ) l ON c.name = l.chore_name
        WHERE c.active = 1
        AND NOT EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = c.name)
        AND c.frequency_in_days > 200
        AND (l.logged_at IS NULL OR l.logged_at < datetime('now', '-180 days'))
        ORDER BY c.frequency_in_days DESC
        LIMIT 5
    """)

    culling_candidates = []
    for row in cursor.fetchall():
        name, freq, desc, logged_at = row
        days_since = None
        if logged_at:
            logged_dt = datetime.fromisoformat(logged_at)
            if logged_dt.tzinfo is None:
                logged_dt = logged_dt.replace(tzinfo=ct)
            days_since = round((now - logged_dt).total_seconds() / 86400, 1)
        culling_candidates.append({
            "name": name,
            "frequency_days": round(freq, 1),
            "days_since_logged": days_since,
            "description": desc[:80] if desc else None,
            "recommendation": "Consider deactivating or reintroducing"
        })
    highlights["culling_candidates"] = culling_candidates

    result["highlights"] = highlights

    # === UNDERUTILIZED CHORES ===
    # High frequency (rarely done) active leaf chores - candidates for reintroduction or deactivation
    cursor.execute("""
        SELECT c.name, c.frequency_in_days, c.description, l.logged_at as last_logged
        FROM chores c
        LEFT JOIN (
            SELECT chore_name, MAX(logged_at) as logged_at
            FROM logs WHERE is_genuine = 1
            GROUP BY chore_name
        ) l ON c.name = l.chore_name
        WHERE c.active = 1
        AND c.frequency_in_days > 30
        AND NOT EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = c.name)
        ORDER BY c.frequency_in_days DESC
        LIMIT 10
    """)

    underutilized = []
    for row in cursor.fetchall():
        name, freq, desc, last_logged = row
        days_since_log = None
        if last_logged:
            logged_dt = datetime.fromisoformat(last_logged)
            if logged_dt.tzinfo is None:
                logged_dt = logged_dt.replace(tzinfo=ct)
            days_since_log = round((now - logged_dt).total_seconds() / 86400, 1)

        # Get parent categories for context
        cursor.execute("SELECT parent_chore FROM parent_chores WHERE chore_name = ?", (name,))
        parents = [r[0] for r in cursor.fetchall()]

        underutilized.append({
            "name": name,
            "frequency_days": round(freq, 1),
            "description": desc[:100] if desc else None,
            "days_since_last_log": days_since_log,
            "parents": parents[:5]  # Top 5 parents for context
        })

    # Also find chores that haven't been logged in a very long time (>60 days)
    cursor.execute("""
        SELECT c.name, c.frequency_in_days, l.logged_at as last_logged
        FROM chores c
        LEFT JOIN (
            SELECT chore_name, MAX(logged_at) as logged_at
            FROM logs WHERE is_genuine = 1
            GROUP BY chore_name
        ) l ON c.name = l.chore_name
        WHERE c.active = 1
        AND NOT EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = c.name)
        AND l.logged_at < datetime('now', '-60 days')
        ORDER BY l.logged_at ASC
        LIMIT 10
    """)

    stale = []
    for row in cursor.fetchall():
        name, freq, last_logged = row
        if last_logged:
            logged_dt = datetime.fromisoformat(last_logged)
            if logged_dt.tzinfo is None:
                logged_dt = logged_dt.replace(tzinfo=ct)
            days_since = round((now - logged_dt).total_seconds() / 86400, 1)
            # cycle_progress = days_since / freq (how many cycles overdue)
            cycle_progress = round(days_since / freq, 2) if freq and freq > 0 else None
            stale.append({
                "name": name,
                "frequency_days": round(freq, 1) if freq else None,
                "days_since_last_log": days_since,
                "cycle_progress": cycle_progress
            })
    # Sort by cycle_progress descending (most cycles overdue first)
    stale.sort(key=lambda x: x.get("cycle_progress") or 0, reverse=True)

    result["data_hygiene"] = {
        "underutilized": underutilized,
        "stale_chores": stale,
        "recommendation": "Review underutilized chores - consider reintroducing, finding alternatives, or deactivating"
    }

    conn.close()

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def suggest_next_chore(args: dict):
    """ML-powered prediction of which chore the user is most likely to select next."""
    if not ML_AVAILABLE:
        return [TextContent(type="text", text=json.dumps({
            "error": "ML model not available. Run training scripts in ml_experiments/ first."
        }, indent=2))]

    prev_chores = args.get("prev_chores", [])
    filter_parent = args.get("filter_parent")
    top_k = args.get("top_k", 10)
    include_all_due = args.get("include_all_due", False)
    log_suggestions = args.get("log_suggestions", True)
    exclude_logged_within_hours = args.get("exclude_logged_within_hours", 24)
    only_leaves = args.get("only_leaves", False)
    include_underutilized = args.get("include_underutilized", 1)
    include_random_underutilized = args.get("include_random_underutilized", 1)
    # Exclude exercises that are descendants of these categories (default: Climbing requires gym)
    exclude_descendants_of = args.get("exclude_descendants_of", ["Climbing"])

    conn = get_connection()
    cursor = conn.cursor()

    ct = ZoneInfo("America/Chicago")
    now = datetime.now(ct)

    # Get last log time (for hours_since_last_log feature)
    # Only get LEAF logs (chores that are not parents of other chores)
    cursor.execute("""
        SELECT l.chore_name, l.logged_at FROM logs l
        JOIN chores c ON l.chore_name = c.name
        WHERE l.is_genuine = 1
        AND NOT EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = l.chore_name)
        ORDER BY l.logged_at DESC
        LIMIT 5
    """)
    recent_logs = cursor.fetchall()

    last_log_time = None
    session_equipment = set()  # Equipment from exercises in current session (last 30 min)
    session_chore_names = []  # Exercises logged in last 30 min
    if recent_logs:
        last_log_str = recent_logs[0][1]  # Most recent log's timestamp
        last_log_time = datetime.fromisoformat(last_log_str)
        if last_log_time.tzinfo is None:
            last_log_time = last_log_time.replace(tzinfo=ct)

        # Get equipment from exercises logged in last 30 minutes (current session)
        session_cutoff = now - timedelta(minutes=30)
        session_chore_names = []
        for log_name, log_time_str in recent_logs:
            log_time = datetime.fromisoformat(log_time_str)
            if log_time.tzinfo is None:
                log_time = log_time.replace(tzinfo=ct)
            if log_time >= session_cutoff:
                session_chore_names.append(log_name)

        # Get equipment parents for session exercises
        if session_chore_names:
            placeholders = ','.join('?' * len(session_chore_names))
            cursor.execute(f"""
                WITH RECURSIVE equipment_tree AS (
                    SELECT chore_name FROM parent_chores WHERE parent_chore = 'Exercise Equipment'
                    UNION ALL
                    SELECT pc.chore_name FROM parent_chores pc
                    JOIN equipment_tree et ON pc.parent_chore = et.chore_name
                )
                SELECT DISTINCT pc.parent_chore
                FROM parent_chores pc
                JOIN equipment_tree et ON pc.parent_chore = et.chore_name
                WHERE pc.chore_name IN ({placeholders})
            """, tuple(session_chore_names))
            session_equipment = {r[0] for r in cursor.fetchall()}

    # Session phase awareness: suppress exploration mid-session unless explicitly requested
    # Mid-session = have exercises in last 30 min
    is_mid_session = len(session_chore_names) > 0
    if is_mid_session:
        # Only use explicit values, don't apply defaults during mid-session
        if args.get("include_underutilized") is None:
            include_underutilized = 0
        if args.get("include_random_underutilized") is None:
            include_random_underutilized = 0

    # If no prev_chores provided, use recent leaf logs only
    if not prev_chores:
        prev_chores = [r[0] for r in recent_logs]

    # Get chores logged within the exclusion window
    recently_logged = set()
    if exclude_logged_within_hours and exclude_logged_within_hours > 0:
        cutoff = now - timedelta(hours=exclude_logged_within_hours)
        cursor.execute("""
            SELECT DISTINCT chore_name FROM logs
            WHERE logged_at >= ? AND is_genuine = 1
        """, (cutoff.isoformat(),))
        recently_logged = {r[0] for r in cursor.fetchall()}

    # Get descendants of excluded categories (e.g., Climbing exercises require gym)
    excluded_chores = set()
    if exclude_descendants_of:
        for parent in exclude_descendants_of:
            cursor.execute("""
                WITH RECURSIVE descendants AS (
                    SELECT ? as chore_name
                    UNION ALL
                    SELECT pc.chore_name FROM parent_chores pc
                    JOIN descendants d ON pc.parent_chore = d.chore_name
                )
                SELECT DISTINCT chore_name FROM descendants
            """, (parent,))
            excluded_chores.update(r[0] for r in cursor.fetchall())

    # Get candidate chores (all active chores with due dates, including parents)
    # Parents are logged when children are logged, so they have valid training signal
    if filter_parent:
        # Get descendants of the filter parent (including the parent itself)
        cursor.execute("""
            WITH RECURSIVE descendants AS (
                SELECT ? as chore_name
                UNION ALL
                SELECT pc.chore_name FROM parent_chores pc
                JOIN descendants d ON pc.parent_chore = d.chore_name
            )
            SELECT c.name, l.complete_by
            FROM chores c
            JOIN descendants d ON c.name = d.chore_name
            LEFT JOIN (
                SELECT chore_name, complete_by
                FROM logs
                WHERE (chore_name, logged_at) IN (
                    SELECT chore_name, MAX(logged_at) FROM logs GROUP BY chore_name
                )
            ) l ON c.name = l.chore_name
            WHERE c.active = 1
        """, (filter_parent,))
    else:
        # Get all active chores (leaves and parents)
        cursor.execute("""
            SELECT c.name, l.complete_by
            FROM chores c
            LEFT JOIN (
                SELECT chore_name, complete_by
                FROM logs
                WHERE (chore_name, logged_at) IN (
                    SELECT chore_name, MAX(logged_at) FROM logs GROUP BY chore_name
                )
            ) l ON c.name = l.chore_name
            WHERE c.active = 1
        """)

    # Fetch candidate rows first
    candidate_rows = cursor.fetchall()

    # Get set of leaf chores (not parents of anything) for filtering
    cursor.execute("""
        SELECT name FROM chores c
        WHERE NOT EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = c.name)
    """)
    leaf_chores = {r[0] for r in cursor.fetchall()}

    candidates = []
    seen_names = set()  # For deduplication
    for row in candidate_rows:
        name, complete_by = row

        # Skip duplicates
        if name in seen_names:
            continue
        seen_names.add(name)

        # Skip recently logged chores
        if name in recently_logged:
            continue

        # Skip chores in excluded lineages (e.g., Climbing)
        if name in excluded_chores:
            continue

        if complete_by:
            due_date = datetime.fromisoformat(complete_by)
            if due_date.tzinfo is None:
                due_date = due_date.replace(tzinfo=ct)
            days_until_due = (due_date - now).total_seconds() / (3600 * 24)
        else:
            days_until_due = 0

        candidates.append({
            'name': name,
            'days_until_due': round(days_until_due, 2),
            'is_leaf': name in leaf_chores
        })

    conn.close()

    if not candidates:
        return [TextContent(type="text", text=json.dumps({
            "suggestions": [],
            "message": "No candidate chores found" + (f" under {filter_parent}" if filter_parent else "")
        }, indent=2))]

    # Filter to leaves first if only_leaves requested (before limiting to top N)
    if only_leaves:
        candidates = [c for c in candidates if c['is_leaf']]

    # Filter to soonest due unless include_all_due
    if not include_all_due:
        # Sort by due date and take top 50 most urgent
        candidates.sort(key=lambda x: x['days_until_due'])
        candidates = candidates[:50]

    # Get predictions
    try:
        predictor = get_predictor(DB_PATH)

        suggestions = predictor.predict(
            candidate_chores=candidates,
            prev_chores=prev_chores,
            last_log_time=last_log_time,
            top_k=top_k,
            now=now
        )

        # Get equipment for each suggestion (always, for display purposes)
        conn2 = get_connection()
        cursor2 = conn2.cursor()

        suggestion_names = [s['name'] for s in suggestions]
        exercise_equipment = {}
        if suggestion_names:
            placeholders = ','.join('?' * len(suggestion_names))
            cursor2.execute(f"""
                WITH RECURSIVE equipment_tree AS (
                    SELECT chore_name FROM parent_chores WHERE parent_chore = 'Exercise Equipment'
                    UNION ALL
                    SELECT pc.chore_name FROM parent_chores pc
                    JOIN equipment_tree et ON pc.parent_chore = et.chore_name
                )
                SELECT pc.chore_name, pc.parent_chore
                FROM parent_chores pc
                JOIN equipment_tree et ON pc.parent_chore = et.chore_name
                WHERE pc.chore_name IN ({placeholders})
            """, tuple(suggestion_names))

            for chore_name, equip in cursor2.fetchall():
                if chore_name not in exercise_equipment:
                    exercise_equipment[chore_name] = set()
                exercise_equipment[chore_name].add(equip)

        # Add equipment to each suggestion and apply boost if session equipment matches
        EQUIPMENT_BOOST = 0.15  # 15% boost for matching equipment
        for sugg in suggestions:
            sugg_equipment = exercise_equipment.get(sugg['name'], set())
            sugg['equipment'] = list(sugg_equipment) if sugg_equipment else []

            # Apply boost only if we have session equipment context
            if session_equipment and sugg_equipment & session_equipment:
                sugg['score'] = sugg.get('score', 0) + EQUIPMENT_BOOST
                sugg['equipment_match'] = True

        # Re-sort by boosted score if we applied any boosts
        if session_equipment:
            suggestions.sort(key=lambda x: x.get('score', 0), reverse=True)

        conn2.close()

        # Apply muscle recovery penalty (deprioritize exercises hitting recently worked muscles)
        conn_muscle = get_connection()
        cursor_muscle = conn_muscle.cursor()

        # Get muscles worked in last 24 hours (mid-level granularity)
        cursor_muscle.execute("""
            WITH RECURSIVE
            muscle_tree AS (
                SELECT chore_name FROM parent_chores WHERE parent_chore = 'Muscle Group'
                UNION ALL
                SELECT pc.chore_name FROM parent_chores pc
                JOIN muscle_tree mt ON pc.parent_chore = mt.chore_name
            ),
            exercise_muscles AS (
                SELECT pc.chore_name as exercise, pc.parent_chore as muscle
                FROM parent_chores pc
                WHERE pc.parent_chore IN (SELECT chore_name FROM muscle_tree)
            )
            SELECT DISTINCT em.muscle
            FROM logs l
            JOIN exercise_muscles em ON l.chore_name = em.exercise
            WHERE l.is_genuine = 1
            AND l.logged_at > datetime('now', '-24 hours')
        """)
        recent_muscles = {r[0] for r in cursor_muscle.fetchall()}

        if recent_muscles:
            # Get muscles for each suggestion
            suggestion_names = [s['name'] for s in suggestions]
            if suggestion_names:
                placeholders = ','.join('?' * len(suggestion_names))
                cursor_muscle.execute(f"""
                    WITH RECURSIVE muscle_tree AS (
                        SELECT chore_name FROM parent_chores WHERE parent_chore = 'Muscle Group'
                        UNION ALL
                        SELECT pc.chore_name FROM parent_chores pc
                        JOIN muscle_tree mt ON pc.parent_chore = mt.chore_name
                    )
                    SELECT pc.chore_name, pc.parent_chore
                    FROM parent_chores pc
                    WHERE pc.parent_chore IN (SELECT chore_name FROM muscle_tree)
                    AND pc.chore_name IN ({placeholders})
                """, tuple(suggestion_names))

                exercise_muscles = {}
                for chore_name, muscle in cursor_muscle.fetchall():
                    if chore_name not in exercise_muscles:
                        exercise_muscles[chore_name] = set()
                    exercise_muscles[chore_name].add(muscle)

                # Penalize exercises that hit recently worked muscles
                MUSCLE_FATIGUE_PENALTY = 0.10  # 10% penalty per overlap
                for sugg in suggestions:
                    sugg_muscles = exercise_muscles.get(sugg['name'], set())
                    overlap = sugg_muscles & recent_muscles
                    if overlap:
                        penalty = min(len(overlap) * MUSCLE_FATIGUE_PENALTY, 0.30)  # Cap at 30%
                        sugg['score'] = sugg.get('score', 0) - penalty
                        sugg['muscle_fatigue'] = list(overlap)[:3]  # Show up to 3 fatigued muscles

                # Re-sort by adjusted score
                suggestions.sort(key=lambda x: x.get('score', 0), reverse=True)

        conn_muscle.close()

        # Deduplicate suggestions and add is_leaf flag
        seen_suggestion_names = set()
        deduped_suggestions = []
        for sugg in suggestions:
            if sugg['name'] not in seen_suggestion_names:
                seen_suggestion_names.add(sugg['name'])
                sugg['is_leaf'] = sugg['name'] in leaf_chores
                sugg['source'] = 'ml'
                deduped_suggestions.append(sugg)
        suggestions = deduped_suggestions[:top_k]

        # Mix in underutilized exercises if requested
        if include_underutilized > 0 or include_random_underutilized > 0:
            conn3 = get_connection()
            cursor3 = conn3.cursor()

            # Build exclusion list (already suggested + recently logged)
            exclude_names = seen_suggestion_names | recently_logged
            placeholders = ','.join('?' * len(exclude_names)) if exclude_names else "''"

            # Get underutilized exercises (high frequency = rarely done)
            # Must respect filter_parent if specified
            if filter_parent:
                query = f"""
                    WITH RECURSIVE descendants AS (
                        SELECT ? as chore_name
                        UNION ALL
                        SELECT pc.chore_name FROM parent_chores pc
                        JOIN descendants d ON pc.parent_chore = d.chore_name
                    )
                    SELECT c.name, c.frequency_in_days, l.complete_by
                    FROM chores c
                    JOIN descendants d ON c.name = d.chore_name
                    LEFT JOIN (
                        SELECT chore_name, complete_by
                        FROM logs
                        WHERE (chore_name, logged_at) IN (
                            SELECT chore_name, MAX(logged_at) FROM logs GROUP BY chore_name
                        )
                    ) l ON c.name = l.chore_name
                    WHERE c.active = 1
                    AND c.frequency_in_days > 60
                    AND NOT EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = c.name)
                    {"AND c.name NOT IN (" + placeholders + ")" if exclude_names else ""}
                    ORDER BY c.frequency_in_days DESC
                    LIMIT 50
                """
                query_params = (filter_parent,) + (tuple(exclude_names) if exclude_names else ())
            else:
                query = f"""
                    SELECT c.name, c.frequency_in_days, l.complete_by
                    FROM chores c
                    LEFT JOIN (
                        SELECT chore_name, complete_by
                        FROM logs
                        WHERE (chore_name, logged_at) IN (
                            SELECT chore_name, MAX(logged_at) FROM logs GROUP BY chore_name
                        )
                    ) l ON c.name = l.chore_name
                    WHERE c.active = 1
                    AND c.frequency_in_days > 60
                    AND NOT EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = c.name)
                    {"AND c.name NOT IN (" + placeholders + ")" if exclude_names else ""}
                    ORDER BY c.frequency_in_days DESC
                    LIMIT 50
                """
                query_params = tuple(exclude_names) if exclude_names else ()
            cursor3.execute(query, query_params)
            underutilized_pool = cursor3.fetchall()

            # Add top N underutilized (sorted by highest frequency)
            added_underutilized = 0
            for row in underutilized_pool:
                if added_underutilized >= include_underutilized:
                    break
                name, freq, complete_by = row
                if complete_by:
                    due_date = datetime.fromisoformat(complete_by)
                    if due_date.tzinfo is None:
                        due_date = due_date.replace(tzinfo=ct)
                    days_until_due = round((due_date - now).total_seconds() / (3600 * 24), 2)
                else:
                    days_until_due = 0
                suggestions.append({
                    'name': name,
                    'score': 0.0,
                    'days_until_due': days_until_due,
                    'is_leaf': name in leaf_chores,
                    'source': 'underutilized',
                    'frequency_days': round(freq, 1)
                })
                seen_suggestion_names.add(name)
                added_underutilized += 1

            # Add N random from remaining underutilized pool
            if include_random_underutilized > 0:
                import random
                remaining_pool = [r for r in underutilized_pool if r[0] not in seen_suggestion_names]
                random_picks = random.sample(remaining_pool, min(include_random_underutilized, len(remaining_pool)))
                for row in random_picks:
                    name, freq, complete_by = row
                    if complete_by:
                        due_date = datetime.fromisoformat(complete_by)
                        if due_date.tzinfo is None:
                            due_date = due_date.replace(tzinfo=ct)
                        days_until_due = round((due_date - now).total_seconds() / (3600 * 24), 2)
                    else:
                        days_until_due = 0
                    suggestions.append({
                        'name': name,
                        'score': 0.0,
                        'days_until_due': days_until_due,
                        'is_leaf': name in leaf_chores,
                        'source': 'random_wildcard',
                        'frequency_days': round(freq, 1)
                    })

            conn3.close()

        # Add latest note for each suggestion (for weight/rep history)
        if suggestions:
            conn_notes = get_connection()
            cursor_notes = conn_notes.cursor()

            suggestion_names = [s['name'] for s in suggestions]
            placeholders = ','.join('?' * len(suggestion_names))

            # Get most recent note for each suggestion
            cursor_notes.execute(f"""
                SELECT chore_name, note, created_at
                FROM notes
                WHERE chore_name IN ({placeholders})
                AND (chore_name, created_at) IN (
                    SELECT chore_name, MAX(created_at) FROM notes GROUP BY chore_name
                )
            """, tuple(suggestion_names))

            latest_notes = {row[0]: row[1] for row in cursor_notes.fetchall()}
            conn_notes.close()

            for sugg in suggestions:
                if sugg['name'] in latest_notes:
                    sugg['last_note'] = latest_notes[sugg['name']]

        # Generate session ID and log suggestions for feedback collection
        session_id = None
        if log_suggestions and suggestions:
            import uuid
            session_id = str(uuid.uuid4())[:8]

            conn2 = get_connection()
            cursor2 = conn2.cursor()

            for rank, sugg in enumerate(suggestions, 1):
                cursor2.execute("""
                    INSERT INTO suggestions (
                        suggested_at, session_id, suggested_chore, suggestion_rank,
                        suggestion_score, prev_chores_json, days_until_due, hour, day_of_week
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    now.isoformat(),
                    session_id,
                    sugg['name'],
                    rank,
                    sugg['score'],
                    json.dumps(prev_chores[:5]),
                    sugg['days_until_due'],
                    now.hour,
                    now.weekday()
                ))

            conn2.commit()
            conn2.close()

        return [TextContent(type="text", text=json.dumps({
            "suggestions": suggestions,
            "session_id": session_id,
            "prev_chores": prev_chores[:5],
            "filter": filter_parent,
            "only_leaves": only_leaves,
            "excluded_recently_logged": len(recently_logged),
            "candidates_considered": len(candidates)
        }, indent=2))]

    except Exception as e:
        return [TextContent(type="text", text=json.dumps({
            "error": f"Prediction failed: {str(e)}"
        }, indent=2))]


async def log_suggestion_feedback(args: dict):
    """Log which suggestions were selected (and implicitly, which were rejected)."""
    session_id = args["session_id"]

    # Handle both single and batch selections
    selected_chores = args.get("selected_chores", [])
    if not selected_chores and "selected_chore" in args:
        selected_chores = [args["selected_chore"]]

    conn = get_connection()
    cursor = conn.cursor()

    ct = ZoneInfo("America/Chicago")
    now = datetime.now(ct)

    # Mark selected chores
    rows_updated = 0
    for chore in selected_chores:
        cursor.execute("""
            UPDATE suggestions
            SET was_selected = 1, selected_at = ?
            WHERE session_id = ? AND suggested_chore = ?
        """, (now.isoformat(), session_id, chore))
        rows_updated += cursor.rowcount

    # Mark non-selected suggestions as explicitly rejected (was_selected = 0)
    # This distinguishes "rejected" from "no feedback yet" (NULL)
    if selected_chores:
        placeholders = ",".join("?" * len(selected_chores))
        cursor.execute(f"""
            UPDATE suggestions
            SET was_selected = 0, selected_at = ?
            WHERE session_id = ? AND suggested_chore NOT IN ({placeholders})
            AND was_selected IS NULL
        """, (now.isoformat(), session_id, *selected_chores))

    # Get final state for this session
    cursor.execute("""
        SELECT suggested_chore, suggestion_rank, was_selected
        FROM suggestions
        WHERE session_id = ?
        ORDER BY suggestion_rank
    """, (session_id,))

    session_suggestions = [
        {"chore": r[0], "rank": r[1], "selected": bool(r[2]) if r[2] is not None else None}
        for r in cursor.fetchall()
    ]

    conn.commit()
    conn.close()

    if rows_updated == 0 and selected_chores:
        return [TextContent(type="text", text=json.dumps({
            "error": f"No suggestions found for session '{session_id}' with chores: {selected_chores}",
            "session_suggestions": session_suggestions
        }, indent=2))]

    # Count selected vs rejected
    selected_count = sum(1 for s in session_suggestions if s["selected"] is True)
    rejected_count = sum(1 for s in session_suggestions if s["selected"] is False)

    return [TextContent(type="text", text=json.dumps({
        "logged": True,
        "session_id": session_id,
        "selected_chores": selected_chores,
        "selected_count": selected_count,
        "rejected_count": rejected_count,
        "total_suggestions": len(session_suggestions),
        "details": session_suggestions
    }, indent=2))]


# =============================================================================
# FREQUENCY ADJUSTMENT TOOLS
# =============================================================================

async def adjust_all_frequencies(args: dict):
    """Dynamically adjust frequencies for all active chores based on completion patterns."""
    lower_bound_multiplier = args.get("lower_bound_multiplier", 1.382)
    upper_bound_divider = args.get("upper_bound_divider", 1.382)
    adjust_without_parent = args.get("adjust_without_parent", False)
    lower_bound_tightness = args.get("lower_bound_tightness", 0.5)

    # Parameters for threshold calculation
    base = 1
    power = 1.1
    scaling = 66

    conn = get_connection()
    cursor = conn.cursor()

    now = datetime.now()
    adjustments = []
    skipped = {"no_parent": 0, "adjust_disabled": 0, "in_bounds": 0}

    # Get all active chores
    cursor.execute("SELECT name, frequency_in_days, adjust_frequency FROM chores WHERE active = 1")
    chores = cursor.fetchall()

    for chore in chores:
        name = chore["name"]
        original_frequency = float(chore["frequency_in_days"])
        adjust_freq_enabled = chore["adjust_frequency"] if chore["adjust_frequency"] is not None else 1

        # Check if frequency adjustment is enabled
        if not adjust_freq_enabled:
            skipped["adjust_disabled"] += 1
            continue

        # Check if chore has parents
        cursor.execute("SELECT COUNT(*) as cnt FROM parent_chores WHERE chore_name = ?", (name,))
        has_parents = cursor.fetchone()["cnt"] > 0

        if not has_parents and not adjust_without_parent:
            skipped["no_parent"] += 1
            continue

        # Get last log to determine next due date
        cursor.execute(
            "SELECT complete_by FROM logs WHERE chore_name = ? ORDER BY logged_at DESC LIMIT 1",
            (name,)
        )
        last_log = cursor.fetchone()

        if last_log and last_log["complete_by"]:
            next_due = datetime.fromisoformat(last_log["complete_by"])
        else:
            # No log yet, skip
            continue

        days_until_due = (next_due - now).total_seconds() / (3600 * 24)

        # Calculate bounds using dynamic threshold
        tau = base + scaling / (original_frequency ** power)
        lower_bound = (original_frequency / tau) * lower_bound_tightness
        upper_bound = original_frequency * tau

        adjusted = False
        new_frequency = original_frequency
        reason = ""

        if days_until_due < lower_bound:
            # Overdue - increase frequency (make it less frequent)
            new_frequency = original_frequency * lower_bound_multiplier
            adjusted = True
            reason = "overdue"
        elif days_until_due > upper_bound:
            # Way ahead - decrease frequency (make it more frequent)
            new_frequency = original_frequency / upper_bound_divider
            adjusted = True
            reason = "early"
        else:
            skipped["in_bounds"] += 1

        if adjusted:
            # Update frequency
            cursor.execute("UPDATE chores SET frequency_in_days = ? WHERE name = ?", (new_frequency, name))

            # Calculate new due date positioned at golden ratio within new bounds
            new_tau = base + scaling / (new_frequency ** power)
            new_lower_bound = (new_frequency / new_tau) * lower_bound_tightness
            new_upper_bound = new_frequency * new_tau
            safe_range = new_upper_bound - new_lower_bound
            due_offset_days = new_lower_bound + (safe_range * 0.618)
            new_next_due = now + timedelta(days=due_offset_days)

            # Log the adjustment (is_genuine=0 indicates system adjustment)
            cursor.execute("""
                INSERT INTO logs (chore_name, logged_at, complete_by, is_genuine)
                VALUES (?, ?, ?, 0)
            """, (name, now.isoformat(), new_next_due.isoformat()))

            adjustments.append({
                "name": name,
                "reason": reason,
                "old_frequency": round(original_frequency, 2),
                "new_frequency": round(new_frequency, 2),
                "days_until_due_was": round(days_until_due, 1),
                "new_due": new_next_due.strftime("%Y-%m-%d %H:%M")
            })

    conn.commit()
    conn.close()

    return [TextContent(type="text", text=json.dumps({
        "adjusted_count": len(adjustments),
        "skipped": skipped,
        "adjustments": adjustments
    }, indent=2))]


async def get_recent_muscle_activity(args: dict):
    """Get muscle groups that were worked in the last N hours."""
    hours = args.get("hours", 48)
    depth = args.get("depth", "mid")

    conn = get_connection()
    cursor = conn.cursor()

    ct = ZoneInfo("America/Chicago")
    now = datetime.now(ct)
    cutoff = now - timedelta(hours=hours)

    # Define depth levels based on hierarchy position
    # High-level: direct children of "Muscle Group" (Upper Body, Lower Body, Core)
    # Mid-level: have children but aren't high-level (e.g., Quadriceps, Biceps Brachii)
    # Leaf-level: no children (specific muscle heads/portions)

    # Get high-level categories (direct children of "Muscle Group")
    cursor.execute("""
        SELECT chore_name FROM parent_chores WHERE parent_chore = 'Muscle Group'
    """)
    high_level = set(r[0] for r in cursor.fetchall())

    # Get all exercises logged in the time window
    cursor.execute("""
        SELECT DISTINCT l.chore_name, l.logged_at
        FROM logs l
        WHERE l.is_genuine = 1
        AND l.logged_at >= ?
        AND l.chore_name NOT IN (
            SELECT DISTINCT parent_chore FROM parent_chores WHERE parent_chore IS NOT NULL
        )
        ORDER BY l.logged_at DESC
    """, (cutoff.isoformat(),))

    exercises_logged = []
    for row in cursor.fetchall():
        logged_dt = datetime.fromisoformat(row[1])
        if logged_dt.tzinfo is None:
            logged_dt = logged_dt.replace(tzinfo=ct)
        hours_ago = (now - logged_dt).total_seconds() / 3600
        exercises_logged.append({
            "name": row[0],
            "hours_ago": round(hours_ago, 1)
        })

    # For each exercise, get its muscle ancestors
    muscle_hits = {}  # muscle_name -> {"hours_ago": min_hours, "exercises": [...]}

    for ex in exercises_logged:
        # Get all ancestors of this exercise
        ancestors = get_ancestors(conn, ex["name"])

        # Filter to only muscle-related ancestors (those under Muscle Group)
        cursor.execute("""
            WITH RECURSIVE muscle_tree AS (
                SELECT chore_name, 0 as level FROM parent_chores WHERE parent_chore = 'Muscle Group'
                UNION ALL
                SELECT pc.chore_name, mt.level + 1
                FROM parent_chores pc
                JOIN muscle_tree mt ON pc.parent_chore = mt.chore_name
                WHERE mt.level < 10
            )
            SELECT chore_name FROM muscle_tree
        """)
        all_muscles = set(r[0] for r in cursor.fetchall())

        # Intersect with this exercise's ancestors
        exercise_muscles = ancestors & all_muscles

        # Filter by depth
        for muscle in exercise_muscles:
            # Determine if this muscle is at the requested depth
            is_high = muscle in high_level

            # Check if it has children (not a leaf)
            cursor.execute("SELECT COUNT(*) FROM parent_chores WHERE parent_chore = ?", (muscle,))
            has_children = cursor.fetchone()[0] > 0

            include = False
            if depth == "high" and is_high:
                include = True
            elif depth == "mid" and has_children and not is_high:
                include = True
            elif depth == "leaf" and not has_children:
                include = True

            if include:
                if muscle not in muscle_hits:
                    muscle_hits[muscle] = {
                        "hours_ago": ex["hours_ago"],
                        "exercises": [ex["name"]]
                    }
                else:
                    # Update with more recent time if applicable
                    if ex["hours_ago"] < muscle_hits[muscle]["hours_ago"]:
                        muscle_hits[muscle]["hours_ago"] = ex["hours_ago"]
                    if ex["name"] not in muscle_hits[muscle]["exercises"]:
                        muscle_hits[muscle]["exercises"].append(ex["name"])

    conn.close()

    # Sort by most recently hit
    sorted_muscles = sorted(
        [{"muscle": k, **v} for k, v in muscle_hits.items()],
        key=lambda x: x["hours_ago"]
    )

    # Group by time buckets for easier reading
    buckets = {
        "last_12h": [],
        "12_to_24h": [],
        "24_to_48h": [],
        "older": []
    }

    for m in sorted_muscles:
        h = m["hours_ago"]
        entry = {"muscle": m["muscle"], "hours_ago": h, "exercises": m["exercises"][:3]}  # Limit exercises shown
        if h <= 12:
            buckets["last_12h"].append(entry)
        elif h <= 24:
            buckets["12_to_24h"].append(entry)
        elif h <= 48:
            buckets["24_to_48h"].append(entry)
        else:
            buckets["older"].append(entry)

    # Remove empty buckets
    buckets = {k: v for k, v in buckets.items() if v}

    return [TextContent(type="text", text=json.dumps({
        "lookback_hours": hours,
        "depth": depth,
        "exercises_in_window": len(exercises_logged),
        "muscles_hit": len(sorted_muscles),
        "by_recency": buckets,
        "all_muscles": [m["muscle"] for m in sorted_muscles]
    }, indent=2))]


async def log_weight(args: dict):
    """Log a weight measurement using CadenceManager.log_weight_chore."""
    weight = args["weight"]
    person = args.get("person", "Shane")

    # Use the existing log_weight_chore function from CadenceManager
    weights_dict = {person: weight}
    next_due, logged_chores = manager.log_weight_chore(
        chore_name="Weigh In",
        weights_dict=weights_dict
    )

    result = {
        "person": person,
        "weight": weight,
        "logged_chores": logged_chores,
        "next_due": next_due.isoformat() if next_due else None
    }

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


# =============================================================================
# MAIN
# =============================================================================

async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
