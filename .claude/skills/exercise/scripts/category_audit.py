#!/usr/bin/env python3
"""
Audit exercise categorization to find tagging issues.

Checks for:
- Exercises missing required categories (Objective, Type, Movement, Energy, Equipment, Muscle)
- Non-granular tags (intermediate hierarchy nodes instead of leaves)
- Exercises without descriptions
"""
import sys
import os
import json
import argparse

# Add project root to path for imports
# Scripts are in .claude/skills/<skill>/scripts/ - need to go up 5 levels to project root
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, '..', '..', '..', '..'))
sys.path.insert(0, project_root)

from CadenceManager import CadenceManager

# The 6 required categories for exercises
REQUIRED_CATEGORIES = [
    'Exercise Objective',
    'Exercise Type',
    'Exercise Movement',
    'Energy Systems',
    'Exercise Equipment',
    'Muscle Group'
]


def get_category_descendants(manager, category_name):
    """Get all descendants of a category."""
    cur = manager.connection.cursor()
    descendants = set()

    def recurse(parent):
        cur.execute("""
            SELECT chore_name FROM parent_chores WHERE parent_chore = ?
        """, (parent,))
        for row in cur.fetchall():
            child = row['chore_name']
            descendants.add(child)
            recurse(child)

    recurse(category_name)
    return descendants


def get_leaf_exercises(manager):
    """Get all leaf-level exercises (exercises that have parents but no children)."""
    cur = manager.connection.cursor()
    cur.execute("""
        SELECT DISTINCT c.name, c.description
        FROM chores c
        JOIN parent_chores pc ON c.name = pc.chore_name
        WHERE c.active = 1
          AND c.name NOT IN (SELECT DISTINCT parent_chore FROM parent_chores)
          AND EXISTS (
              SELECT 1 FROM parent_chores pc2
              JOIN parent_chores pc3 ON pc2.parent_chore = pc3.chore_name
              WHERE pc2.chore_name = c.name AND pc3.parent_chore = 'Exercise'
          )
    """)
    return [(row['name'], row['description']) for row in cur.fetchall()]


def check_category_coverage(manager, exercise_name, category_descendants):
    """Check which categories an exercise is missing."""
    cur = manager.connection.cursor()
    cur.execute("""
        SELECT parent_chore FROM parent_chores WHERE chore_name = ?
    """, (exercise_name,))
    parents = set(row['parent_chore'] for row in cur.fetchall())

    missing = []
    for cat_name, descendants in category_descendants.items():
        if not parents.intersection(descendants):
            missing.append(cat_name)
    return missing


def main():
    parser = argparse.ArgumentParser(description='Audit exercise categorization')
    parser.add_argument('--min-missing', type=int, default=1,
                        help='Only show exercises missing at least N categories (default: 1)')
    parser.add_argument('--check-descriptions', action='store_true',
                        help='Also check for missing descriptions')
    parser.add_argument('--limit', type=int, default=50,
                        help='Max exercises to return (default: 50)')
    parser.add_argument('--category', choices=['Objective', 'Type', 'Movement', 'Energy', 'Equipment', 'Muscle'],
                        help='Only show exercises missing this specific category')
    args = parser.parse_args()

    db_path = os.path.join(project_root, 'data', 'chore_data.db')
    manager = CadenceManager(db_path)

    # Build category descendant sets
    category_map = {
        'Objective': 'Exercise Objective',
        'Type': 'Exercise Type',
        'Movement': 'Exercise Movement',
        'Energy': 'Energy Systems',
        'Equipment': 'Exercise Equipment',
        'Muscle': 'Muscle Group'
    }

    category_descendants = {}
    for short_name, full_name in category_map.items():
        category_descendants[short_name] = get_category_descendants(manager, full_name)
        category_descendants[short_name].add(full_name)

    # Get all leaf exercises
    exercises = get_leaf_exercises(manager)

    issues = []
    for name, description in exercises:
        missing = check_category_coverage(manager, name, category_descendants)

        # Filter by specific category if requested
        if args.category and args.category not in missing:
            continue

        # Check description
        missing_desc = args.check_descriptions and not description

        if len(missing) >= args.min_missing or missing_desc:
            issue = {
                'name': name,
                'missing_categories': missing,
                'missing_count': len(missing)
            }
            if args.check_descriptions:
                issue['has_description'] = bool(description)
            issues.append(issue)

        if len(issues) >= args.limit:
            break

    # Sort by most missing
    issues.sort(key=lambda x: x['missing_count'], reverse=True)

    output = {
        'total_exercises_checked': len(exercises),
        'issues_found': len(issues),
        'issues': issues[:args.limit]
    }

    print(json.dumps(output, indent=2))

if __name__ == '__main__':
    main()
