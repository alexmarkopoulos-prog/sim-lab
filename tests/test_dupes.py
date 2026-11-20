"""Duplicates are exact lineup matches in the optimizer field, never an ownership product."""

import numpy as np

from simlab.services.dupes import expected_dupes


def test_absent_lineup_has_zero_dupes_even_if_it_would_be_chalk():
    # Lineup 5 never appears. An ownership product of six 50% players would not be zero.
    field = np.array([1, 1, 1, 2, 3, 4])
    dupes = expected_dupes(field, np.array([5, 1]), contest_size=101)
    ownership_product = (0.50 ** 6) * 100
    assert dupes[0] == 0
    assert abs(dupes[1] - (3 / 6) * 100) < 1e-9
    assert dupes[1] != ownership_product


def test_dupe_rate_scales_with_contest_and_ignores_order():
    field = np.array([0, 2, 2, 2, 2, 7])
    dupes = expected_dupes(field, np.array([2, 0, 9]), contest_size=601)
    assert abs(dupes[0] - (4 / 6) * 600) < 1e-9
    assert abs(dupes[1] - (1 / 6) * 600) < 1e-9
    assert dupes[2] == 0


def test_unknown_and_empty_fields_are_zero():
    assert expected_dupes(np.array([]), np.array([1, 2]), 1000).tolist() == [0, 0]
    assert expected_dupes(np.array([-1, -1]), np.array([0]), 100).tolist() == [0]
    assert expected_dupes(np.array([3, 3]), np.array([-1]), 50).tolist() == [0]
