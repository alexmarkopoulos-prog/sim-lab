"""Exact-match duplicate counts for an optimizer-built field.

A duplicate is the same lineup appearing again in the simulated field.
Ownership percentages are never multiplied together. A chalk lineup that the
field builder did not actually produce has zero duplicates, no matter how
high its ownership is.
"""

from __future__ import annotations

import numpy as np


def expected_dupes(
    field_lineup_ids: np.ndarray,
    lineup_ids: np.ndarray,
    contest_size: int,
) -> np.ndarray:
    """Expected opposing copies of each lineup in a contest.

    ``field_lineup_ids`` is the optimizer field pool (one integer id per
    generated opponent lineup). ``lineup_ids`` are the lineups being scored.
    An id of -1 means the lineup was never part of that pool, so it has no
    exact matches.

    Scaling is frequency in the pool times the number of opposing entries::

        count(lineup) / pool_size * (contest_size - 1)

    That is an exact-match rate. It is not a product of player ownerships.
    """
    lineup_ids = np.asarray(lineup_ids, dtype=np.int64)
    out = np.zeros(lineup_ids.shape[0], dtype=np.float64)
    field_lineup_ids = np.asarray(field_lineup_ids, dtype=np.int64)
    if field_lineup_ids.size == 0 or contest_size <= 1 or lineup_ids.size == 0:
        return out
    valid_field = field_lineup_ids[field_lineup_ids >= 0]
    if valid_field.size == 0:
        return out
    counts = np.bincount(valid_field)
    known = (lineup_ids >= 0) & (lineup_ids < counts.shape[0])
    scaled = counts[lineup_ids[known]] / float(valid_field.shape[0]) * float(contest_size - 1)
    out[known] = scaled
    return out


def ownership_product_is_not_used() -> bool:
    """Marker so callers and tests can see the policy in one place."""
    return True
