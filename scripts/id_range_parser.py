"""Utility for parsing ID range specifications.

Supports comma-separated ranges like "1-10,12,19" or "1-5,7,10-15".
"""

def parse_id_ranges(range_spec: str) -> list[int]:
    """Parse a comma-separated list of ID ranges into a list of integers.

    Args:
        range_spec: String like "1-10,12,19" or "1-5,7,10-15"

    Returns:
        Sorted list of unique integers from the ranges

    Raises:
        ValueError: If the format is invalid or contains non-positive integers

    Examples:
        >>> parse_id_ranges("1-3,5")
        [1, 2, 3, 5]
        >>> parse_id_ranges("10,5-7,3")
        [3, 5, 6, 7, 10]
        >>> parse_id_ranges("1")
        [1]
    """
    if not range_spec or not range_spec.strip():
        raise ValueError("Range specification cannot be empty")

    ids = set()

    # Split by commas and process each part
    for part in range_spec.split(','):
        part = part.strip()

        if not part:
            raise ValueError("Empty range component found")

        # Check if it's a range (contains '-')
        if '-' in part:
            # Split into start and end
            range_parts = part.split('-')
            if len(range_parts) != 2:
                raise ValueError(f"Invalid range format: '{part}'. Expected 'start-end'")

            start_str, end_str = range_parts

            try:
                start = int(start_str.strip())
                end = int(end_str.strip())
            except ValueError:
                raise ValueError(f"Invalid range: '{part}'. Both start and end must be integers")

            if start < 1 or end < 1:
                raise ValueError(f"Invalid range: '{part}'. IDs must be positive integers")

            if start > end:
                raise ValueError(f"Invalid range: '{part}'. Start ({start}) must be <= end ({end})")

            # Add all IDs in the range
            ids.update(range(start, end + 1))
        else:
            # Single ID
            try:
                id_val = int(part)
            except ValueError:
                raise ValueError(f"Invalid ID: '{part}'. Must be a positive integer")

            if id_val < 1:
                raise ValueError(f"Invalid ID: {id_val}. IDs must be positive integers")

            ids.add(id_val)

    return sorted(ids)


def validate_id_range_constraints(ids: list[int], max_count: int = 120,
                                   valid_range: tuple[int, int] = (1, 532)) -> None:
    """Validate that a list of IDs meets the specified constraints.

    Args:
        ids: List of integer IDs to validate
        max_count: Maximum number of IDs allowed (default 120)
        valid_range: Tuple of (min_id, max_id) representing the valid range (default 1-532)

    Raises:
        ValueError: If validation fails
    """
    min_valid, max_valid = valid_range

    # Check all IDs are in valid range first (more specific error)
    for id_val in ids:
        if id_val < min_valid or id_val > max_valid:
            raise ValueError(f"ID {id_val} is outside valid range {min_valid}-{max_valid}")

    # Check count
    if len(ids) > max_count:
        raise ValueError(f"Too many IDs selected: {len(ids)}. Maximum is {max_count}")


if __name__ == "__main__":
    import doctest
    doctest.testmod()

    # Additional test cases
    test_cases = [
        ("1-10,12,19", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 19]),
        ("5", [5]),
        ("1-3", [1, 2, 3]),
        ("10-12,1-3", [1, 2, 3, 10, 11, 12]),
    ]

    print("Running tests...")
    for spec, expected in test_cases:
        result = parse_id_ranges(spec)
        assert result == expected, f"Failed for '{spec}': expected {expected}, got {result}"
        print(f"✓ '{spec}' -> {result}")

    # Test validation
    try:
        ids = list(range(1, 122))  # 121 IDs
        validate_id_range_constraints(ids)
        print("✗ Should have failed: too many IDs")
    except ValueError as e:
        print(f"✓ Validation correctly rejects 121 IDs: {e}")

    try:
        ids = [1, 2, 600]
        validate_id_range_constraints(ids)
        print("✗ Should have failed: ID out of range")
    except ValueError as e:
        print(f"✓ Validation correctly rejects ID 600: {e}")

    print("\nAll tests passed!")
