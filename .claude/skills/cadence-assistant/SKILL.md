---
name: cadence-assistant
description: General guidance for managing the Cadence database including exercises, household tasks, and other recurring activities.
---

# Cadence Database

This skill provides guidance for managing the Cadence database, which includes exercises and other recurring tasks.

## Self-Improvement Directive

**This system should be self-healing and iterative.** As you work with the database:
- Identify gaps in hierarchies that would improve classification
- Suggest new category nodes when items don't fit existing options
- Propose improvements to this documentation when you discover better patterns
- Note when descriptions are missing or unclear and offer to add them
- If a tool is missing that would help, suggest adding it to the MCP server

When you identify improvements, explicitly tell the user: *"I noticed [gap/issue]. Would you like me to [fix/add/update]?"*

---

## Codebase Reference

Before building new functionality, **check existing code first**:

- **[CadenceManager.py](../../../CadenceManager.py)** - Core database operations. Contains methods for logging, updating, and querying chores. Review this before adding new MCP tools to avoid duplication.
- **[CadenceEditor.py](../../../CadenceEditor.py)** - Jupyter widget-based UI for editing chores.
- **[mcp_server/server.py](../../../mcp_server/server.py)** - MCP server exposing database operations as tools.

When adding new MCP tools, check if `CadenceManager` already has the functionality - if so, expose it rather than reimplementing.

---

## Utility Scripts

Scripts for common workflows. Execute these directly - they output JSON.

### Adjust Frequencies

Adjust all chore frequencies based on completion patterns:

```bash
python .claude/skills/cadence-assistant/scripts/adjust_frequencies.py
```

Options:
- `--lower-bound-multiplier 1.382` - Multiplier when overdue (default: 1.382)
- `--upper-bound-divider 1.382` - Divider when early (default: 1.382)
- `--lower-bound-tightness 0.5` - How tight the lower bound is (default: 0.5)
- `--adjust-without-parent` - Also adjust chores without parents
- `--dry-run` - Preview without making changes

---

## Chore Types Overview

The database contains different types of chores with varying levels of categorization:

| Type | Tagging Depth | Parent Structure |
|------|---------------|------------------|
| **Exercises** | Deep - 6 categories, granular muscles | Complex hierarchy (see exercise skill) |
| **Other Chores** | Light - typically 1-2 parents | Simple parent categories |

### Top-Level Categories
Use `get_hierarchy_tree("Chore")` to see all top-level chore types. Common ones include:
- `Exercise` - Physical training activities (see exercise skill for details)
- `Household` - Home maintenance tasks (see house-chores skill)
- `Plant` - Plant care activities (see plant-chores skill)
- `Dog Activity` - Pet-related activities
- `Personal` - Self-care, admin, etc.

---

## Common Operations

### Log any chore
```python
log_chore("Chore Name")  # Works for exercises and all other chores
```

### View upcoming chores
```python
get_upcoming_chores(limit=20)  # All chores (sorted by cycle_progress)
get_upcoming_chores(filter_parent="Exercise")  # Just exercises
get_upcoming_chores(filter_parent="Household")  # Just household
get_upcoming_chores(sort_by="days_until_due")  # Sort by absolute due date instead
```

---

## Context-Aware Assistance

The database includes tools for understanding context and making smart suggestions.

### Context Tools

**Get current time (Central Time / Wisconsin):**
```python
get_current_datetime()
# Returns: datetime, day_of_week, time_of_day (early_morning/morning/midday/afternoon/evening/night)
```

**Get weather for outdoor activities:**
```python
get_weather()
# Returns: temperature, conditions, wind, outdoor_activity_ok, outdoor_notes
# Useful for deciding on road running, outdoor workouts, etc.
```

**Check recent activity level:**
```python
get_time_since_last_activity()  # Any activity
get_time_since_last_activity(filter_parent="Exercise")  # Just exercise
# Returns: last_activity, hours_since, activities_today
```

### Suggestion Tools

**Find exercises that hit multiple urgent muscles:**
```python
find_multi_target_exercises(min_targets=2, top_k_muscles=15, limit=20)
# Returns exercises that hit 2+ of the top K most urgent (soonest due) muscle groups
```

**Find related chores to batch together:**
```python
get_related_chores("Water Plants")
# Returns chores sharing parent categories (e.g., Fertilize Plants)
# Useful for combining related tasks
```

---

## Evolutionary Frequency System

Cadence uses an **evolutionary model** for frequency adjustment:

**Core Principle**: "Survival of the selected"
- Selecting/logging a chore does NOT increase its frequency
- If a chore is selected often enough (due date compounds past a threshold), frequency will **decrease**
- Chores that aren't selected maintain their current frequency

This creates natural selection pressure:
- Chores you actually do regularly become less frequent over time (you've proven competency)
- Chores you avoid stay at their current frequency (still needs attention)
- The system evolves to match your real habits and priorities

**Implication for suggestions**: Focus on **urgency** (nearest due date) rather than "overdue" — the user typically pushes out due dates before they become overdue.

---

## Chore Prioritization

When deciding which chores need attention, consider these metrics **in order of importance**:

### 1. Urgency Ratio (days_until_due / hours_since_last_log)

This is the **primary metric**. It accounts for high-frequency chores (like skincare) that are always near due because they don't push much when logged.

- Low ratio = more urgent (due soon relative to how recently it was done)
- High ratio = less urgent

### 2. Days Until Due

Raw urgency - how soon is this chore due?

### 3. Frequency

**Less frequent chores near due deserve extra attention.** They represent:
- Bigger jobs that may require planning
- Unusual situations becoming urgent
- Tasks that are easy to forget

A monthly chore at 2 days until due is more noteworthy than a daily chore at 2 days.

---

## Workflow Philosophy

Key principles for chore management:
- Consider time of day and energy level
- Check weather before suggesting outdoor activities
- Batch related chores when possible (water + fertilize plants)
- **Present choices** rather than single recommendations

**Important**: The user typically doesn't let chores become overdue - they push them out before due dates. Use **urgency** (nearest due date) rather than "overdue" when suggesting.

---

## Common Routines

### Daily Startup

At the start of each day/session, run frequency adjustments:
```python
adjust_all_frequencies()
```
This tunes chore frequencies based on completion patterns - making frequently-done chores less frequent and neglected ones more frequent.

### Morning Routine

First thing in the morning, the user typically logs these together:
- Daily Meds + Supplements
- Skin Care
- Dental Hygiene
- Floss

**Batching**: When the user mentions morning routine or asks to log these, batch them together rather than prompting individually.

**Filtering**: After these are logged for the day, **hide them from due lists** unless one was skipped. These high-frequency daily chores clutter the view once completed - only surface them if actually overdue.

---

## Chore Aliases

Common phrases the user might say and what they map to:

| User says | Chore name |
|-----------|------------|
| "brushed teeth", "brush teeth" | Dental Hygiene |
