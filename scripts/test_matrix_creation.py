#!/usr/bin/env python3
"""Test script to validate the matrix creation logic from the GitHub workflow."""

import csv
import json
import sys
from id_range_parser import parse_id_ranges, validate_id_range_constraints


def create_matrix(csv_file: str, package_ids_input: str = "") -> dict:
    """Simulate the matrix creation logic from the GitHub workflow.

    Args:
        csv_file: Path to the CSV dataset file
        package_ids_input: ID range specification (e.g., "1-10,12,19")

    Returns:
        Dictionary representing the GitHub Actions matrix
    """
    matrix_data = []

    # Read all rows indexed by ID
    rows_by_id = {}
    with open(csv_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['package_name']:  # Skip entries without package names
                try:
                    row_id = int(row['random_order'])
                    rows_by_id[row_id] = row
                except (ValueError, KeyError):
                    print(f'Warning: Skipping row without valid random_order ID', file=sys.stderr)

    # Parse package ID ranges
    package_ids_input = package_ids_input.strip()

    if package_ids_input:
        # Parse the ID range specification
        try:
            selected_ids = parse_id_ranges(package_ids_input)
            # Validate constraints (max 120 IDs, valid range 1-532)
            validate_id_range_constraints(selected_ids, max_count=120, valid_range=(1, 532))
        except ValueError as e:
            raise ValueError(f'Invalid package_ids input: {e}')
    else:
        # Default: IDs 1-5
        selected_ids = [1, 2, 3, 4, 5]

    # Build matrix data from selected IDs
    for row_id in selected_ids:
        if row_id in rows_by_id:
            row = rows_by_id[row_id]
            matrix_data.append({
                'package_name': row['package_name'],
                'pname': row['pname'],
                'version': row['version'],
                'id': row_id,
                'dataset_type': 'csv-based',
                'csv_dataset_path': csv_file
            })
        else:
            print(f'Warning: ID {row_id} not found in dataset', file=sys.stderr)

    return {'include': matrix_data}


def main():
    """Run test cases for matrix creation."""
    csv_file = 'research/single_fetcher_2025-08-01_2025-11-20.csv'

    test_cases = [
        ("", "Default (1-5)"),
        ("1-10", "Range 1-10"),
        ("1-10,12,19", "Mixed range and singles"),
        ("532", "Last ID"),
        ("1", "First ID"),
        ("100-105", "Middle range"),
    ]

    print("Testing matrix creation logic:\n")

    for id_spec, description in test_cases:
        print(f"Test: {description}")
        print(f"  Input: '{id_spec}'")

        try:
            matrix = create_matrix(csv_file, id_spec)
            count = len(matrix['include'])
            print(f"  ✓ Generated {count} entries")

            # Show first few entries
            for i, entry in enumerate(matrix['include'][:3]):
                print(f"    [{entry['id']}] {entry['pname']} {entry['version']}")
            if count > 3:
                print(f"    ... and {count - 3} more")
        except Exception as e:
            print(f"  ✗ Error: {e}")

        print()

    # Test error cases
    print("\nTesting error cases:\n")

    error_cases = [
        ("1-600", "ID out of range"),
        ("0", "ID less than 1"),
        ("1-121", "Too many IDs (121)"),
        ("invalid", "Invalid format"),
        ("1-", "Incomplete range"),
    ]

    for id_spec, description in error_cases:
        print(f"Test: {description}")
        print(f"  Input: '{id_spec}'")

        try:
            matrix = create_matrix(csv_file, id_spec)
            print(f"  ✗ Should have failed but got {len(matrix['include'])} entries")
        except ValueError as e:
            print(f"  ✓ Correctly rejected: {e}")
        except Exception as e:
            print(f"  ? Unexpected error: {e}")

        print()

    # Test the 120 ID limit edge case
    print("\nTesting 120 ID limit (edge case):\n")
    print("Test: Exactly 120 IDs")
    print("  Input: '1-120'")

    try:
        matrix = create_matrix(csv_file, "1-120")
        count = len(matrix['include'])
        print(f"  ✓ Generated {count} entries (exactly at limit)")
    except Exception as e:
        print(f"  ✗ Error: {e}")

    print()


if __name__ == "__main__":
    main()
