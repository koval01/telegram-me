import re


def parse_abbreviated_number(s: str) -> int:
    """
    Parse a string with an abbreviated number (e.g., '1.5K', '3M') into an integer.

    Supports the following suffixes:
    - K: thousand (e.g., '1K' = 1000)
    - M: million (e.g., '1M' = 1,000,000)
    - B: billion (e.g., '1B' = 1,000,000,000)
    - T: trillion (e.g., '1T' = 1,000,000,000,000)

    Args:
        s: The string to parse (e.g., '1.5K', '250', '3.2B')

    Returns:
        The parsed number as an integer (rounded to nearest whole number)

    Raises:
        ValueError: If the input string doesn't match the expected format
    """
    multipliers = {
        'K': 1e3,
        'M': 1e6,
        'B': 1e9,
        'T': 1e12
    }

    match = re.match(r'^([\d.]+)([KMBT]?)$', s)

    if not match:
        raise ValueError("Invalid format")

    number = float(match.group(1))
    suffix = match.group(2)

    result = round(number * (multipliers[suffix] if suffix in multipliers else 1))
    return result
