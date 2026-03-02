# Cadence: Orchestrating Life

Adaptive life orchestration system for tracking recurring activities with intelligent frequency adjustment. Built on SQLite with MCP server integration for Claude Code.

## Project Overview

This system tracks chores under these top-level categories (use these as `filter_parent` values):
- **Exercise** - Comprehensive 6-category tagging (Objective, Type, Movement, Energy, Equipment, Muscle Group)
- **Household Management** - Contains subcategories:
  - **House Plants** - Watering, fertilizing, plant maintenance
  - **Household Cleaning** - Cleaning and sanitation tasks
  - **Laundry** - Washing, drying, folding
- **Hygiene** - Personal care (dental, skincare, etc.)
- **Wellness** - Medications, supplements, health tracking

### Core Philosophy: Evolutionary Frequency

Chores self-adjust based on completion patterns:
- Complete **early** → frequency decreases (more frequent)
- Complete **late** → frequency increases (less frequent)
- Uses golden ratio (1.382) as default adjustment multiplier
- **Auto-adjusts daily**: Frequencies automatically adjust when logging "Daily Meds + Supplements"

### Urgency Philosophy: Cycle Progress over Due Dates

The user actively manages frequencies so chores rarely become "past due" in absolute terms. Instead, prioritize by:

1. **Cycle progress** (days_since_logged / frequency) - Primary metric
   - `cycle_progress = 1.0` means exactly at expected frequency
   - `cycle_progress > 1.0` means overdue relative to its frequency
   - Higher values = more urgent

2. **Underutilized exercises** - Secondary consideration
   - High `frequency_in_days` (100+ days) indicates exercises that drifted out of rotation
   - Worth surfacing to either reintroduce or consciously cull

3. **Days until due** - Tertiary signal
   - Less useful since the user keeps things from going overdue
   - Still helpful for seeing what's coming up

**When suggesting chores, lead with cycle progress, not raw due dates.**

## Key Files

- `CadenceManager.py` - Core database operations
- `mcp_server/server.py` - MCP tools for Claude integration
- `data/chore_data.db` - SQLite database
- `.claude/skills/` - AI interaction guidelines

## Response Guidelines

### Before Recommending Exercises

**Always invoke the `exercise` skill** before suggesting exercises. It contains critical filtering rules (e.g., exclude climbing exercises unless at the gym) and guidance on muscle fatigue, equipment continuity, and selection philosophy.

### Always Suggest Next Actions

End responses with contextual suggestions for what the user might want to do next:

- **After logging exercise**: Suggest related exercises (same muscle group, same equipment) or complementary movements
- **After logging household chore**: Suggest nearby/related tasks that could be batched
- **After logging plant care**: Suggest other plant tasks (water → fertilize batching)
- **When reviewing due chores**: Highlight multi-target exercises that hit several urgent muscles
- **General**: Consider time of day, weather (for outdoor activities), and what was recently completed

Example endings:
- "Next: You might also want to do X (cycle: 1.2) or Y (same equipment, cycle: 0.9)"
- "Underutilized to consider: Z (freq: 180 days - bring back or cull?)"

### Context Awareness

- Use `get_session_context` to get comprehensive context (time, weather, recent activity, model status, urgency)
- Use `suggest_next_chore` for ML-powered predictions (77% Hit@1 accuracy)
- Use `find_multi_target_exercises` to find efficient compound movements
- Use `get_related_chores` to batch similar tasks

### Exercise Selection: Equal Footing

**Categories and leaf exercises are evaluated on equal footing.** Both have frequencies, due dates, and cycle progress - the hierarchy level doesn't matter for prioritization.

**Don't use `only_leaves=true` by default** - it hides category-level signals. When a category like "Vertical Pushing" or "Hinge" is due, that's actionable information.

**When a category is urgent:**
- Use `get_exercise_details(name="Hinge")` to see `leaf_descendants` sorted by due date
- Any leaf under that category satisfies it when logged

**Key movement categories** (for reference):
- **Push**: Vertical Pushing, Horizontal Pushing, Squat Pattern
- **Pull**: Vertical Pulling, Horizontal Pulling, Hinge
- **Other**: Carry, Rotational, Isometric Hold, Locomotion

## ML Prediction System

The system includes a trained model that predicts which chore the user will select next.

### Key Tools

```bash
# Get full session context (unified tool)
mcp__cadence__get_session_context()

# Get ML-powered suggestions (categories + leaves together)
mcp__cadence__suggest_next_chore(filter_parent="Exercise")

# Get leaf options when a category is urgent
mcp__cadence__get_exercise_details(name="Hinge")  # includes leaf_descendants

# Log feedback when user selects a suggestion
mcp__cadence__log_suggestion_feedback(session_id="abc123", selected_chore="...")
```

### Model Retraining

Check `get_session_context().model_status.should_retrain` - triggers when:
- **Days since training** >= 7
- **Log growth** >= 20%
- **New feedback** >= 50 samples

To retrain:
```bash
cd ml_experiments && python retrain_model.py
```

### Feedback Collection

When showing suggestions from `suggest_next_chore`, the system logs what was suggested. Call `log_suggestion_feedback` when the user actually selects something to collect negative samples for model improvement.

### Interpreting Category Suggestions

The ML model may suggest **parent categories** (e.g., "Power", "Climbing Technique", "Gluteus Maximus") rather than specific exercises. This is valid signal - it indicates which category needs attention.

**Workflow when a category is suggested:**

1. **Recognize it's a category** - check `is_leaf: false` in the response
2. **Get actionable options** - call `get_exercise_details(name="Power")` which includes `leaf_descendants` with top 10 exercises sorted by due date
3. **Present options** - show the user specific exercises that would satisfy the category
4. **Logging a leaf logs ancestors** - when the user does a leaf exercise, all parent categories are automatically logged too

**Example:**
```
ML suggests: "Power" (is_leaf: false, days_until_due: 2.5)
→ get_exercise_details("Power") returns leaf_descendants: [{name: "Box Jump", days_until_due: 27}, ...]
→ Present: "Power exercises are due - options: Box Jump (27 days), Kettlebell Swing (35 days)"
→ User does Box Jump → "Power" category also gets logged
```

**Tool parameters for filtering:**
- `only_leaves=true` - Only return leaf exercises. **Avoid using by default** - category signals are important for rotation
- `exclude_logged_within_hours=24` - Skip recently completed items (default: 24 hours)
- `exclude_descendants_of=["Climbing"]` - Exclude exercises under these categories (default: Climbing requires gym)

### Exploration & Variety Parameters

To combat the "rich get richer" problem where ML reinforces common exercises:

```bash
# Mix in underutilized exercises (high frequency = rarely done)
suggest_next_chore(filter_parent="Exercise", include_underutilized=2)

# Add random wildcards from underutilized pool
suggest_next_chore(filter_parent="Exercise", include_random_underutilized=1)
```

- `include_underutilized=N` - Add top N exercises with highest frequency_in_days (>60). Default: 1
- `include_random_underutilized=N` - Add N random picks from underutilized pool. Default: 1

These items are marked with `source: "underutilized"` or `source: "random_wildcard"` in results.

### Smart Suggestion Features

The `suggest_next_chore` tool includes automatic enhancements:

- **Equipment info**: Each suggestion includes `equipment: [...]` showing required equipment for planning.
- **Equipment continuity**: Exercises using the same equipment as recent session get a 15% score boost. Look for `equipment_match: true`.
- **Muscle recovery**: Exercises hitting muscles worked in last 24h get penalized (up to 30%). Look for `muscle_fatigue` field showing which muscles.
- **Session phase awareness**: Exploration picks (underutilized/wildcards) only appear at session start. Mid-session (exercises in last 30 min) suppresses exploration to prioritize equipment continuity.

## Session Context Highlights

The `get_session_context` tool includes a `highlights` section with actionable data points:

- **most_overdue_by_cycle** - **PRIMARY METRIC** - Top 5 by cycle progress (days_since_logged / frequency)
  - `cycle_progress > 1.0` means overdue by that many cycles
  - This is the main urgency signal since the user actively manages frequencies
  - **Excludes climbing by default** (requires gym) - pass `exclude_descendants_of: []` to include
- **underutilized_to_review** - Top 5 highest frequency exercises (candidates for reintroduction or culling)
- **culling_candidates** - Exercises with freq > 200 AND not logged in 180+ days (strong candidates for deactivation)
- **nearest_due** - Top 5 exercises closest to due date (secondary reference, also excludes climbing by default)
- **urgency_summary.exercise** - Top 3 urgent exercises (also excludes climbing by default)
- **weekly_review_prompt** - Boolean, true if no underutilized exercise logged in 7+ days
- **days_since_underutilized_logged** - Days since last underutilized exercise was done

**Climbing exclusion**: The `exclude_descendants_of` parameter (default: `["Climbing"]`) filters climbing exercises from urgency sections. Pass an empty array to include them when at the gym.

**Priority order when suggesting:** cycle progress → underutilized review → due dates.

Use `weekly_review_prompt` to periodically surface neglected exercises for review.
Use `culling_candidates` to identify exercises that should probably be deactivated or consciously reintroduced.

## Data Hygiene & Curation

The `get_session_context` tool includes a `data_hygiene` section showing:
- **underutilized**: High-frequency (rarely done) active chores - evolved to be infrequent
- **stale_chores**: Haven't been logged in 60+ days despite being active, sorted by `cycle_progress` (days_since / frequency)

### How to Use This Context

When reviewing underutilized/stale exercises, I should:

1. **Analyze the exercise**: What muscles/movements does it target?
2. **Find alternatives**: Are there better exercises already in the database that cover the same targets?
3. **Make a recommendation**:
   - **Reintroduce**: If it fills a gap (unique muscles, equipment, movement pattern)
   - **Deactivate**: If better alternatives exist and it's redundant
   - **Keep but lower priority**: If it's a valid backup option

4. **Ask the user** with context like:
   > "X hasn't been logged in 90 days. You have Y and Z which hit similar muscles. Should we deactivate X, or is there a reason to keep it (e.g., variety, specific use case)?"

### Culling Opinion Guidelines

When reviewing culling candidates, provide an informed opinion on whether the exercise is worth keeping:

1. **Exercise quality assessment**: Is this a "bang for your buck" exercise? Does it efficiently target the intended muscles?
2. **Safety considerations**: Are there known issues with this movement pattern? (e.g., behind-the-neck lat pulldowns are controversial for shoulder health)
3. **Redundancy check**: Do better alternatives exist in the database that cover the same targets more effectively?
4. **Recommendation format**:
   > "Standing Behind-the-back wrist curl targets forearm flexors but has limited ROM. You already have Wrist Roller and Farmer's Carries which hit similar muscles with better functional carryover. **Recommendation: Deactivate** unless you specifically want isolation work."

### Systematic Underutilized Review

**Proactively surface exercises above a frequency threshold for review.**

Current threshold: **285 days** (adjustable based on progress)

**Workflow:**
1. Query exercises with `frequency_in_days >= THRESHOLD` that are **descendants of "Exercise"** (exclude category nodes and non-exercise items)
2. For each, provide opinion: **Cull** or **Keep** with reasoning
3. If **Keep**: Look for opportunities to work it into current session if appropriate
4. When no exercises remain at threshold, lower it (e.g., 275 → 250 → 225)

**SQL pattern:**
```sql
SELECT c.name, c.frequency_in_days, c.description
FROM chores c
WHERE c.active = 1
AND c.frequency_in_days >= 285  -- adjustable threshold
AND NOT EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.parent_chore = c.name)  -- leaf only
AND EXISTS (SELECT 1 FROM parent_chores pc WHERE pc.chore_name = c.name AND pc.parent_chore = 'Exercise')  -- exercise descendants only
ORDER BY c.frequency_in_days DESC
```

**Review criteria:**
- Is it a valuable movement pattern or redundant?
- Does it require equipment the user doesn't regularly access?
- Is it an aspirational/progression exercise worth keeping for goals?
- Are there better alternatives already in rotation?

**Categories of underutilized exercises:**
- **Cable/gym-dependent** - cull if user doesn't go to gym regularly
- **Advanced progressions** (Dragon Squat, Human Flag) - keep if still a goal
- **Redundant variations** - cull in favor of primary version

### Goal

Help reduce noise in the exercise library while ensuring good coverage. Quality over quantity - fewer exercises that get done regularly is better than many that get ignored.

## Exercise Tagging Standards

- Use **leaf-level (granular) tags only** - never intermediate categories
- Specific muscle heads: "Biceps Brachii — Long Head" not just "Biceps"
- Descriptions should explain how it's performed (rep ranges, tempo, technique)
- All 6 categories should be covered where applicable

## Common Workflows

```bash
# Log a chore
mcp__cadence__log_chore(name="...")

# Find upcoming chores by category (returns both by_cycle and by_due sections)
mcp__cadence__get_upcoming_chores(filter_parent="Exercise")
mcp__cadence__get_upcoming_chores(filter_parent="House Plants")  # Plant care
mcp__cadence__get_upcoming_chores(filter_parent="Household Cleaning")
mcp__cadence__get_upcoming_chores(filter_parent="Hygiene")
mcp__cadence__get_upcoming_chores(filter_parent="Wellness")
# Use show_by_cycle=false or show_by_due=false to get only one section

# Find efficient exercises
mcp__cadence__find_multi_target_exercises(min_targets=2)

# Search exercises
mcp__cadence__search_exercises(pattern="%keyword%")
```

## User Location

User location configurable - weather checks available for outdoor activity planning.
