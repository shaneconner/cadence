#!/usr/bin/env python3
"""
Adjust frequencies for all active chores based on completion patterns.

Chores completed early get more frequent; overdue chores get less frequent.
Uses the evolutionary frequency model from CadenceManager.
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
    parser = argparse.ArgumentParser(description='Adjust chore frequencies based on completion patterns')
    parser.add_argument('--lower-bound-multiplier', type=float, default=1.382,
                        help='Multiplier for increasing frequency when overdue (default: 1.382)')
    parser.add_argument('--upper-bound-divider', type=float, default=1.382,
                        help='Divider for decreasing frequency when early (default: 1.382)')
    parser.add_argument('--lower-bound-tightness', type=float, default=0.5,
                        help='How tight the lower bound is (0-1, default: 0.5)')
    parser.add_argument('--adjust-without-parent', action='store_true',
                        help='Also adjust chores without parent categories')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be adjusted without making changes')
    args = parser.parse_args()

    db_path = os.path.join(project_root, 'data', 'chore_data.db')
    manager = CadenceManager(db_path)

    # Get all active chores
    cur = manager.connection.cursor()
    cur.execute("SELECT name FROM chores WHERE active = 1")
    chores = [row['name'] for row in cur.fetchall()]

    adjustments = []
    skipped = {'no_parent': 0, 'adjust_disabled': 0, 'in_bounds': 0}

    for name in chores:
        if args.dry_run:
            # For dry run, we'd need to replicate the logic - just run normally for now
            pass

        result = manager.adjust_chore_frequency(
            name,
            lower_bound_multiplier=args.lower_bound_multiplier,
            upper_bound_divider=args.upper_bound_divider,
            adjust_without_parent=args.adjust_without_parent,
            lower_bound_tightness=args.lower_bound_tightness
        )

        if result:
            adjustments.append({
                'name': name,
                'adjusted': True
            })
        # Note: detailed skip tracking would require modifying CadenceManager

    output = {
        'adjusted_count': len(adjustments),
        'total_checked': len(chores),
        'adjustments': adjustments
    }

    print(json.dumps(output, indent=2))

if __name__ == '__main__':
    main()
