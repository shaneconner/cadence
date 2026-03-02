---
name: plant-chores
description: Guidance for plant care activities including watering, fertilizing, and maintenance.
---

# Plant Care

Plant chores use light categorization - typically just one or two parent tags.

## Adding a Plant Chore

```python
add_chore("Water Monstera",
    description="What this involves",
    parent_chores=["Plant"])
```

## Common Parent Categories

- `Plant` - General plant care
- `Watering` - Watering tasks
- `Fertilizing` - Fertilizing tasks

## Batching Related Tasks

When logging watering, ALWAYS check for upcoming fertilizing or other plant care tasks that could be bundled in. The user typically provides fertilizer at the same time as watering if any fertilizing is due soon.

Use `get_related_chores("Water Plants")` to find tasks that share parent categories, then check their due dates to see if any should be done together.

### Key Metric: Time Since Last Log / Frequency

When deciding whether to bundle a related task, the ratio of (days since last log) / (frequency in days) is most useful:
- **>0.7 (70%+)**: Good candidate for bundling - it's due soon anyway
- **0.5-0.7**: Optional - can bundle if convenient
- **<0.5**: Probably too early to bundle

---

## Grow Room Conditions (Rare Monsteras)

**Environment:**
- 12 hours strong light (~15-20k lux average)
- Temperature: ~75°F
- Humidity: ~70%

These are optimal year-round growing conditions — no winter dormancy, so fertilization schedule remains consistent.

### Current Fertilization Schedule

| Product | Frequency | Purpose |
|---------|-----------|---------|
| AgroThrive 3-3-2 | 17 days | Fast-acting liquid organic; ideal 3-1-2 NPK for foliage |
| Down to Earth 4-4-4 | 35 days | Slow-release granular; balanced background nutrition |
| Maxicrop Liquid Seaweed | 28 days | Cytokinins, micronutrients, hormonal boost |
| Worm Castings | 75 days | Microbial health, gentle slow-release (top-dress) |

### Application Notes

- **AgroThrive & Maxicrop**: Add to water (1 Tbsp/gallon each)
- **Down to Earth**: Scratch 1 Tbsp per pot gallon into top 1-2" of soil
- **Worm Castings**: Top-dress 0.25 cup per pot gallon, scratch in lightly

### Watering Frequency

Currently set to 10 days, but monitor soil moisture — high light conditions may require more frequent watering. Adjust based on:
- Soil dryness 2" down
- Pot weight
- Leaf droop signals

### Signs to Watch

- **Over-fertilization**: Yellow leaf edges, brown tips, white salt crust on soil
- **Under-fertilization**: Pale leaves, slow growth, lack of fenestrations
- **If over-fertilized**: Flush soil thoroughly with plain water
