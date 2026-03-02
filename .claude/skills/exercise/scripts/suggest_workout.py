#!/usr/bin/env python3
"""
Suggest exercises for a workout based on:
- Most urgent (soonest due) muscle groups
- Multi-target exercises that hit multiple urgent muscles
- Current time of day and weather conditions

Outputs a prioritized list of exercise suggestions.
"""
import sys
import os
import json
import argparse
from datetime import datetime

# Add project root to path for imports
# Scripts are in .claude/skills/<skill>/scripts/ - need to go up 4 levels to project root
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, '..', '..', '..', '..'))
sys.path.insert(0, project_root)

from CadenceManager import CadenceManager


def get_time_context():
    """Get current time of day category."""
    hour = datetime.now().hour
    if hour < 6:
        return 'early_morning'
    elif hour < 9:
        return 'morning'
    elif hour < 12:
        return 'midday'
    elif hour < 17:
        return 'afternoon'
    elif hour < 21:
        return 'evening'
    else:
        return 'night'


def get_urgent_muscles(manager, top_k=15):
    """Get the top K most urgent (soonest due) muscle groups."""
    cur = manager.connection.cursor()

    # Find leaf-level muscles (no children) that are due soonest
    # next_due is calculated from most recent log + frequency_in_days
    query = """
        WITH muscle_hierarchy AS (
            SELECT DISTINCT parent_chore as muscle
            FROM parent_chores
            WHERE parent_chore IN (
                SELECT chore_name FROM parent_chores WHERE parent_chore = 'Muscle Group'
                UNION ALL
                SELECT pc2.chore_name
                FROM parent_chores pc1
                JOIN parent_chores pc2 ON pc2.parent_chore = pc1.chore_name
                WHERE pc1.parent_chore = 'Muscle Group'
            )
        ),
        latest_logs AS (
            SELECT chore_name, MAX(logged_at) as last_logged
            FROM logs
            GROUP BY chore_name
        )
        SELECT c.name,
               julianday(datetime(ll.last_logged, '+' || c.frequency_in_days || ' days')) - julianday('now') as days_until_due
        FROM chores c
        JOIN latest_logs ll ON c.name = ll.chore_name
        JOIN parent_chores pc ON c.name = pc.parent_chore
        WHERE c.active = 1
          AND c.name IN (SELECT muscle FROM muscle_hierarchy)
        GROUP BY c.name
        ORDER BY days_until_due ASC
        LIMIT ?
    """
    cur.execute(query, (top_k,))
    return [(row['name'], row['days_until_due']) for row in cur.fetchall()]


def get_climbing_exercises(manager):
    """Get all climbing exercises EXCEPT hangboard and no-hang (which can be done at home)."""
    cur = manager.connection.cursor()

    # Get all exercises under Climbing, excluding Hangboard and No-Hang Training
    query = """
        WITH RECURSIVE climbing_descendants AS (
            SELECT chore_name FROM parent_chores WHERE parent_chore = 'Climbing'
            UNION ALL
            SELECT pc.chore_name FROM parent_chores pc
            JOIN climbing_descendants cd ON pc.parent_chore = cd.chore_name
        ),
        home_climbing AS (
            -- Hangboard and No-Hang exercises can be done at home
            SELECT chore_name FROM parent_chores WHERE parent_chore = 'Hangboard'
            UNION ALL
            SELECT chore_name FROM parent_chores WHERE parent_chore = 'No-Hang Training'
            UNION ALL
            SELECT pc.chore_name FROM parent_chores pc
            JOIN home_climbing hc ON pc.parent_chore = hc.chore_name
        )
        SELECT DISTINCT chore_name FROM climbing_descendants
        WHERE chore_name NOT IN (SELECT chore_name FROM home_climbing)
    """
    cur.execute(query)
    return set(row['chore_name'] for row in cur.fetchall())


def find_multi_target_exercises(manager, urgent_muscles, min_targets=2, limit=20, include_climbing=False):
    """Find exercises that hit multiple urgent muscles."""
    if not urgent_muscles:
        return []

    muscle_names = [m[0] for m in urgent_muscles]
    cur = manager.connection.cursor()

    # Get climbing exercises to exclude (unless include_climbing is True)
    climbing_exclusions = set() if include_climbing else get_climbing_exercises(manager)

    # Find exercises tagged with multiple urgent muscles
    placeholders = ','.join(['?' for _ in muscle_names])
    query = f"""
        WITH latest_logs AS (
            SELECT chore_name, MAX(logged_at) as last_logged
            FROM logs
            GROUP BY chore_name
        )
        SELECT c.name, c.description, c.frequency_in_days,
               GROUP_CONCAT(pc.parent_chore) as muscles_hit,
               COUNT(DISTINCT pc.parent_chore) as target_count,
               julianday(datetime(ll.last_logged, '+' || c.frequency_in_days || ' days')) - julianday('now') as days_until_due
        FROM chores c
        JOIN parent_chores pc ON c.name = pc.chore_name
        LEFT JOIN latest_logs ll ON c.name = ll.chore_name
        WHERE c.active = 1
          AND pc.parent_chore IN ({placeholders})
          AND c.name NOT IN (SELECT parent_chore FROM parent_chores)  -- leaf exercises only
        GROUP BY c.name
        HAVING target_count >= ?
        ORDER BY target_count DESC, days_until_due ASC
        LIMIT ?
    """
    cur.execute(query, (*muscle_names, min_targets, limit * 2))  # fetch extra to account for filtering

    results = []
    for row in cur.fetchall():
        if row['name'] in climbing_exclusions:
            continue
        results.append({
            'name': row['name'],
            'description': row['description'],
            'muscles_hit': row['muscles_hit'].split(',') if row['muscles_hit'] else [],
            'target_count': row['target_count'],
            'days_until_due': row['days_until_due']
        })
        if len(results) >= limit:
            break
    return results


def main():
    parser = argparse.ArgumentParser(description='Suggest exercises for a workout')
    parser.add_argument('--top-muscles', type=int, default=15,
                        help='Number of urgent muscles to consider (default: 15)')
    parser.add_argument('--min-targets', type=int, default=2,
                        help='Minimum muscles an exercise must hit (default: 2)')
    parser.add_argument('--limit', type=int, default=20,
                        help='Max exercises to return (default: 20)')
    parser.add_argument('--include-climbing', action='store_true',
                        help='Include climbing exercises (default: exclude, except hangboard/no-hang)')
    args = parser.parse_args()

    db_path = os.path.join(project_root, 'data', 'chore_data.db')
    manager = CadenceManager(db_path)

    time_context = get_time_context()
    urgent_muscles = get_urgent_muscles(manager, args.top_muscles)
    multi_target = find_multi_target_exercises(
        manager, urgent_muscles, args.min_targets, args.limit, args.include_climbing
    )

    output = {
        'time_of_day': time_context,
        'urgent_muscles': [{'name': m[0], 'days_until_due': round(m[1], 1)} for m in urgent_muscles],
        'suggested_exercises': multi_target
    }

    print(json.dumps(output, indent=2))

if __name__ == '__main__':
    main()
