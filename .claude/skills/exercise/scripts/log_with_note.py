#!/usr/bin/env python3
"""
Log an exercise and add a weight/rep note in one operation.

Combines log_chore + add_note for efficient exercise tracking.
Designed for the "one set per exercise" flow-style workout.
"""
import sys
import os
import json
import argparse

# Add project root to path for imports
# Scripts are in .claude/skills/<skill>/scripts/ - need to go up 4 levels to project root
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, '..', '..', '..', '..'))
sys.path.insert(0, project_root)

from CadenceManager import CadenceManager


def main():
    parser = argparse.ArgumentParser(description='Log an exercise with optional weight/rep note')
    parser.add_argument('exercise', help='Name of the exercise to log')
    parser.add_argument('--note', '-n', help='Weight/rep note (e.g., "28kg x 8")')
    parser.add_argument('--show-details', action='store_true',
                        help='Show exercise details after logging')
    args = parser.parse_args()

    db_path = os.path.join(project_root, 'data', 'chore_data.db')
    manager = CadenceManager(db_path)

    # Log the exercise
    result = manager.log_chore(args.exercise)

    output = {
        'exercise': args.exercise,
        'logged': result is not None,
        'note_added': False
    }

    if result:
        # Add note if provided
        if args.note:
            manager.add_note(args.exercise, args.note)
            output['note_added'] = True
            output['note'] = args.note

        # Get updated details if requested
        if args.show_details:
            cur = manager.connection.cursor()
            cur.execute("""
                SELECT datetime(MAX(l.logged_at), '+' || c.frequency_in_days || ' days') as next_due,
                       c.frequency_in_days
                FROM logs l
                JOIN chores c ON l.chore_name = c.name
                WHERE l.chore_name = ?
                GROUP BY c.name
            """, (args.exercise,))
            row = cur.fetchone()
            if row:
                output['next_due'] = row['next_due']
                output['frequency_days'] = row['frequency_in_days']

            # Get recent notes
            cur.execute("""
                SELECT note, created_at
                FROM notes
                WHERE chore_name = ?
                ORDER BY created_at DESC
                LIMIT 3
            """, (args.exercise,))
            output['recent_notes'] = [
                {'note': r['note'], 'date': r['created_at']}
                for r in cur.fetchall()
            ]
    else:
        output['error'] = f"Exercise '{args.exercise}' not found"

    print(json.dumps(output, indent=2))

if __name__ == '__main__':
    main()
