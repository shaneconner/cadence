---
name: exercise
description: Detailed guidance for categorizing and managing exercises with the 6-category tagging system.
---

# Exercise Categorization

Exercises require comprehensive tagging across **6 categories**. Use granular (leaf-level) parents only.

## Understanding Exercise Nuance

**IMPORTANT**: Always read the description before categorizing:
- **Rep/Set Context**: An "endurance" exercise might be programmed for low-rep strength work
- **Round Structure**: A "quick" exercise might be intended for multiple rounds
- **Intensity Cues**: Description may specify tempo, holds, or intensity modifiers
- **Technique Focus**: Some exercises prioritize form over load

When descriptions are missing or unclear, suggest adding one.

---

## Utility Scripts

Scripts for exercise workflows. Execute directly - they output JSON.

### Suggest Workout

Find exercises that hit multiple urgent muscles:

```bash
python .claude/skills/exercise/scripts/suggest_workout.py
```

Options:
- `--top-muscles 15` - Number of urgent muscles to consider
- `--min-targets 2` - Minimum muscles an exercise must hit
- `--limit 20` - Max exercises to return
- `--include-climbing` - Include climbing wall exercises (excluded by default, except hangboard/no-hang)

### Log with Note

Log an exercise and add weight/rep note in one step:

```bash
python .claude/skills/exercise/scripts/log_with_note.py "Kettlebell Swing" --note "28kg x 20"
```

Options:
- `--note "28kg x 8"` - Weight/rep note to add
- `--show-details` - Show updated due date and recent notes

### Category Audit

Find exercises with tagging issues:

```bash
python .claude/skills/exercise/scripts/category_audit.py
```

Options:
- `--min-missing 1` - Only show exercises missing N+ categories
- `--category Muscle` - Filter to specific missing category (Objective, Type, Movement, Energy, Equipment, Muscle)
- `--check-descriptions` - Also flag missing descriptions
- `--limit 50` - Max results

---

## Exercise Category Hierarchies

### 1. Exercise Objective
Goals or physical attributes targeted. Use granular objectives. Multiple per exercise when meaningful.

```
Exercise Objective
├─ Strength
│  ├─ Functional Strength
│  ├─ Maximal Strength
│  └─ Relative Strength
├─ Power
├─ Flexibility
├─ Mobility
│  ├─ Joint Mobility
│  ├─ Active Mobility
│  └─ Neural Mobility
├─ Balance
│  ├─ Static Balance
│  └─ Dynamic Balance
├─ Coordination
├─ Hypertrophy
├─ Endurance
│  ├─ Cardiovascular Endurance
│  ├─ Muscular Endurance
│  └─ Power Endurance
├─ Stability
│  ├─ Core Stability
│  ├─ Joint Stability
│  ├─ Dynamic Stability
│  ├─ Postural Stability
│  └─ Rotational Stability
├─ Speed
├─ Recovery
│  └─ Soft Tissue Maintenance
├─ Proprioception
├─ Agility
└─ Prehabilitation
```

### 2. Exercise Type
Training modality. Typically one per exercise.

```
Exercise Type
├─ Calisthenics
├─ Climbing
│  ├─ Climbing Board Training
│  ├─ Climbing Endurance
│  ├─ Climbing Power
│  ├─ Climbing Strength
│  │  └─ Finger Strength
│  │     ├─ No-Hang Training
│  │     ├─ Repeaters
│  │     └─ Max Hangs
│  └─ Climbing Technique
├─ Yoga
├─ Tai Chi & Qigong
├─ Functional Movement
├─ Pilates
├─ Weightlifting
├─ Olympic Weightlifting
├─ Plyometrics
├─ Myofascial Release
├─ Stretching
│  ├─ Static Stretching
│  ├─ Dynamic Stretching
│  ├─ PNF Stretching
│  ├─ Active Stretching
│  └─ Passive Stretching
├─ Mobility Training
│  ├─ Controlled Articular Rotations (CARs)
│  ├─ Progressive Angular Isometric Loading (PAILs/RAILs)
│  └─ End-Range Training
├─ Martial Arts
├─ Breathwork
├─ Isometric Training
└─ Cardiovascular
```

### 3. Exercise Movement
Movement pattern. Typically one per exercise.

```
Exercise Movement
├─ Push
│  ├─ Vertical Pushing
│  ├─ Horizontal Pushing
│  └─ Squat Pattern
├─ Pull
│  ├─ Vertical Pulling
│  ├─ Horizontal Pulling
│  ├─ Hinge
│  └─ Elbow Flexion
├─ Carry
│  ├─ Bilateral Carries
│  ├─ Asymmetrical Carries
│  └─ Get-ups
├─ Rotational
│  ├─ Dynamic Rotation
│  ├─ Anti-rotation
│  └─ Twisting Movements
├─ Anti-extension
├─ Locomotion
└─ Isometric Hold
```

### 4. Energy Systems
Metabolic pathway. Typically one per exercise.

```
Energy Systems
├─ Aerobic
├─ Anaerobic Lactic
└─ Anaerobic Alactic
```

### 5. Exercise Equipment
Primary equipment. Use 'Bodyweight' for no equipment.

```
Exercise Equipment
├─ Bodyweight
├─ Kettlebells
├─ Dumbbells
├─ Barbells
├─ Resistance Bands
├─ Rings
├─ Pull-up Bar
├─ Hangboard
├─ Exercise Mat
├─ Foam Rollers
├─ Stability Balls
└─ [many more - use get_hierarchy_tree("Exercise Equipment")]
```

### 6. Muscle Group
**CRITICAL: Only use terminal leaf muscles (most granular).** Granular parents auto-inherit higher-level parents.

Key principles:
- Use specific heads: `Biceps Brachii — Long Head` not `Biceps Brachii`
- Use specific portions: `Pectoralis Major - Clavicular Portion` not `Pectoralis Major`
- Use specific fibers: `Gluteus Medius - Anterior Fibers` not `Gluteus Medius`
- Tag all meaningfully involved muscles

Use `get_hierarchy_tree("Muscle Group")` for the full hierarchy.

---

## Exercise Quality Checklist

When reviewing or adding exercises, verify:
- [ ] Has granular Objective tag(s)
- [ ] Has granular Type tag
- [ ] Has Movement pattern (if applicable)
- [ ] Has Energy System tag
- [ ] Has Equipment tag
- [ ] Has granular Muscle tags (leaf nodes only)
- [ ] Description explains how exercise is performed
- [ ] URLs added for reference (if available)

Use `get_category_coverage` to check overall exercise database health.

---

## Adding a New Exercise

```python
add_chore("New Exercise",
    description="How it's performed...",
    parent_chores=["Type", "Objective", "Equipment", "Movement", "Energy", "Muscle1", "Muscle2"])
```

---

## Self-Healing: Fix Issues as You Find Them

When suggesting or reviewing exercises, **proactively correct tagging issues** before proceeding:

- If an exercise appears in results for a muscle it doesn't actually target → `remove_parent()` immediately
- If an exercise is missing an obvious category → `add_parent()` immediately
- If a description is missing or unclear → offer to `update_description()`

**Example**: If "Bicep Curls" appears when searching for calf exercises due to incorrect tagging, fix it first:
```python
# Wrong tag found during suggestion workflow
remove_parent("Bicep Curls", "Gastrocnemius - Lateral Head")
# Then continue with valid suggestions
```

This keeps the database accurate over time without requiring dedicated audit sessions.

---

## Exercise Selection Philosophy

**"Survival of the Selected"** - exercises you choose become more frequent over time. This creates natural curation of your exercise library.

### Muscle Fatigue Awareness

Use `get_recent_muscle_activity` to check what muscles were worked recently:

```python
# Check mid-level muscle groups hit in last 48 hours
get_recent_muscle_activity(hours=48, depth="mid")

# Check specific muscles hit in last 24 hours
get_recent_muscle_activity(hours=24, depth="leaf")

# Check high-level body regions
get_recent_muscle_activity(hours=48, depth="high")
```

**Depth levels:**
- `leaf`: Specific muscles (e.g., "Biceps Brachii — Long Head")
- `mid`: Muscle groups (e.g., "Quadriceps", "Biceps Brachii") — **best for typical recommendations**
- `high`: Body regions (e.g., "Upper Body", "Lower Body", "Core")

Avoid suggesting exercises that heavily target muscles worked in the last 24-48 hours unless the user specifically wants to.

### Equipment Continuity

When recommending exercises, consider equipment context:

- **Immediately after logging**: Prefer exercises using the **same equipment** (it's already set up). If the user just did a cable exercise, suggest more cable work.
- **After a gap (hours later, new day)**: Equipment continuity doesn't matter. Suggest based on due dates and muscle recovery.

This is behavioral guidance, not a hard rule. Use judgment based on conversation flow.

### Present Choices, Not Prescriptions

When suggesting exercises, **offer variety** rather than a single "best" option. The user wants to mix it up.

### Climbing Exercise Filter

**Exclude climbing exercises from general suggestions** unless explicitly requested. The user can only do most climbing exercises at the gym.

**Exceptions (can be done at home):**
- No-Hang Training devices
- Hangboard exercises

To filter out climbing exercises in queries, exclude items under "Climbing" parent except those under "No-Hang Training" or "Hangboard".

### Both Ends of the Spectrum

Consider exercises from **two extremes**:

**High Cycle Progress (Urgent)**
- Exercises with `cycle_progress > 1.0` (overdue relative to frequency)
- Use `get_session_context().highlights.most_overdue_by_cycle`

**Underutilized (High Frequency Values)**
- Exercises with very high `frequency_in_days` values (e.g., 100+ days)
- These have drifted out of rotation because they're rarely selected
- The evolutionary frequency system keeps pushing them further out
- **Worth surfacing** to bring variety back or decide to cull them

To find underutilized exercises:
```sql
-- Exercises with high frequency values (rarely done)
SELECT name, frequency_in_days, description
FROM chores
WHERE active = 1
AND frequency_in_days > 60
ORDER BY frequency_in_days DESC
LIMIT 20
```

These deserve attention for two reasons:
1. **Opportunity to return**: Forgotten exercises can be brought back into rotation
2. **Cull candidates**: If something has a 200+ day frequency and you never want to do it, maybe deactivate it

This dual-focus keeps the exercise library dynamic and well-curated.

### Balanced Suggestion Strategy

**IMPORTANT**: Don't over-rely on ML suggestions - this creates a feedback loop where commonly-selected exercises keep getting suggested. Use a **balanced approach**:

#### Primary: Cycle Progress (most important)

The user actively manages frequencies so exercises rarely become "past due" in absolute terms. Use **cycle progress** (days_since_logged / frequency) as the primary urgency metric:

```python
# Get session context - includes most_overdue_by_cycle in highlights
get_session_context()
# → highlights.most_overdue_by_cycle shows top exercises by cycle progress
```

- `cycle_progress = 1.0` means exactly at expected frequency
- `cycle_progress > 1.0` means overdue relative to its frequency
- Higher values = more urgent

#### Secondary: Underutilized Exercises

High `frequency_in_days` (100+ days) indicates exercises that drifted out of rotation. Check `highlights.underutilized_to_review` in session context to surface:
- Exercises worth bringing back
- Exercises to consciously cull

#### Tertiary: ML Signal

Use `suggest_next_chore` as a **supporting signal**, not the primary source:

```python
# Get ML suggestions for session continuity
suggest_next_chore(filter_parent="Exercise", only_leaves=true)
```

ML is useful for:
- Equipment continuity (what's already set up)
- Session flow patterns
- Surfacing exercises that pair well together

But it reinforces existing patterns - exercises you pick often get suggested more.

#### Workflow

1. **Check cycle progress first** - `get_session_context().highlights.most_overdue_by_cycle`
2. **Surface underutilized** - `highlights.underutilized_to_review` for variety/culling
3. **Glance at ML** - for equipment continuity or session flow ideas
4. **Present variety** - mix cycle-progress picks with underutilized options

### ML Tool Details

```python
# Get suggestions (may include categories)
suggest_next_chore(filter_parent="Exercise")

# Get only actionable exercises (excludes category nodes)
suggest_next_chore(filter_parent="Exercise", only_leaves=true)

# Exclude recently done (default: 4 hours)
suggest_next_chore(filter_parent="Exercise", exclude_logged_within_hours=8)
```

**Interpreting results:**

The model may suggest **categories** (e.g., "Power", "Climbing Technique", "Gluteus Maximus") alongside leaf exercises. Check `is_leaf` in each suggestion:

- `is_leaf: true` → Actionable exercise, can be logged directly
- `is_leaf: false` → Category signal, find leaf exercises under it

**When a category is suggested:**
1. Call `get_exercise_details(name="Power")` - it includes `leaf_descendants` with top 10 exercises sorted by due date
2. Present specific options to the user from `leaf_descendants`
3. Logging any leaf exercise automatically logs all ancestor categories

**Always log feedback** when the user selects from suggestions:
```python
# After user picks from ML suggestions
log_suggestion_feedback(session_id="abc123", selected_chore="Box Jump")
```

This improves future predictions through negative sampling.

### Exploration Parameters

To prevent the "rich get richer" problem:

```python
# Mix in underutilized exercises (high frequency = rarely selected)
suggest_next_chore(filter_parent="Exercise", include_underutilized=2)

# Add random wildcards for variety
suggest_next_chore(filter_parent="Exercise", include_random_underutilized=1)
```

Items with `source: "underutilized"` or `source: "random_wildcard"` are exploration picks.

### Example Suggestion Format

When asked for exercise suggestions, present options like:

```
**Urgent (by cycle progress):**
- Exercise A (cycle: 1.4, hits Glutes + Hamstrings)
- Exercise B (cycle: 1.2, hits Core)

**Underutilized (bring back into rotation?):**
- Exercise C (freq: 120 days - forgotten but good?)
- Exercise D (freq: 200 days - keep or cull?)
```

Note: Use cycle progress (days_since / frequency) as the primary metric, not days until due.

---

## Logging an Exercise

### Pre-Log Checklist

Before logging, always:
1. **Review parents** - Use `get_exercise_details` to verify tags are appropriate
2. **Check recent notes** - Look for weight/reps from previous sessions
3. **Consider time since last logged** - Adjust expectations accordingly

### Weight/Rep Progression Guidelines

For **weightlifting-based exercises**:

| Exercise Type | Target Reps | Progression Logic |
|---------------|-------------|-------------------|
| Compound (squat, bench, deadlift) | ~5 reps | Multi-muscle movements |
| Isolation (curls, extensions) | 8-12 reps | Single-muscle focus |
| AMRAP workouts | Rounds | Track total rounds completed |

**Progression decisions:**
- **5+ reps achieved** → Consider going UP next time
- **Under 5 reps** → Stay same or go DOWN
- **Long time since last session** → Don't go up; consider going DOWN to re-establish baseline

### Post-Log Notes

After completing a weight-based exercise, **add a note** with:
- Weight used (e.g., "28kg")
- Reps completed (e.g., "x 8")
- Or rounds for AMRAP (e.g., "6 rounds")

Example notes:
```
"32kg x 5"
"24kg x 8 (felt easy, go up next time)"
"28kg x 3 (too heavy, drop to 24kg)"
"7 rounds in 15 min"
```

### Review Logged Parents

When `log_chore` returns, it includes `parents_logged` - all ancestor categories that were also logged. **Review in both directions with an F1 mindset, leaning slightly toward recall.**

#### Flag Missing Tags (Recall)
Look for what's NOT in the list but should be:

- **Primary movers missing** - e.g., a squat without any quad tags
- **Stabilizers overlooked** - e.g., a single-leg exercise missing balance/stability objectives
- **Isometric involvement** - muscles holding position even if not moving (e.g., biceps holding arms extended, back muscles maintaining posture)
- **Obvious equipment** - e.g., a floor exercise missing "Exercise Mat"
- **Energy system gaps** - e.g., a max-effort lift missing "Anaerobic Alactic"

> "This hip hinge doesn't have any hamstring tags - should I add Biceps Femoris?"

#### Flag Inappropriate Tags (Precision)
Look for tags that shouldn't be there:

- **Anatomically uninvolved** - e.g., "Gastrocnemius" on a seated upper body exercise where feet aren't engaged
- **Wrong category** - e.g., "Push" movement pattern on a pulling exercise
- **Clearly unrelated** - e.g., "Biceps" on a lying hip stretch with arms at rest

Consider whether a muscle could plausibly be:
- Stabilizing the movement
- Working isometrically to hold position
- Engaged for balance/posture

If yes, it's probably a valid tag. If it's truly uninvolved, flag it.

> "I noticed 'Calves' tagged on this supine stretch where legs are relaxed - want me to remove it?"

#### Balance (F1, leaning Recall)
Aim for balanced F1, but when uncertain, **lean toward recall**:

- Missing tags mean exercises don't surface in searches (bad)
- Extra tags mean exercises show up in a few more searches (less bad)

Rules of thumb:
- **Clear cases**: act confidently (add missing, remove wrong)
- **Uncertain add**: lean toward adding
- **Uncertain remove**: ask first before removing
- Consider isometric work, stabilization, and postural demands - these count

This passive review during logging catches tagging errors over time without dedicated audits.

### Suggested Logging Flow

```python
# 1. Get exercise details (check parents + recent notes)
get_exercise_details("Kettlebell Swing")

# 2. If parents look wrong, fix before logging
remove_parent("Kettlebell Swing", "Wrong Tag")
add_parent("Kettlebell Swing", "Correct Tag")

# 3. Log the exercise
log_chore("Kettlebell Swing")

# 4. Review parents_logged for inappropriate tags
# Flag anything that looks wrong to the user

# 5. Add a note with weight/reps
add_note("Kettlebell Swing", "28kg x 20")
```
