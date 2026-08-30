import math

from backend.collision import rectangles_overlap


def test_far_apart_rectangles_do_not_overlap():
    a = (0.0, 0.0, 0.0, 1.0, 1.0)  # x, y, heading, width, length
    b = (10.0, 10.0, 0.0, 1.0, 1.0)

    assert rectangles_overlap(a, b) is False


def test_identical_position_rectangles_overlap():
    a = (0.0, 0.0, 0.0, 1.0, 1.0)
    b = (0.0, 0.0, 0.0, 1.0, 1.0)

    assert rectangles_overlap(a, b) is True


def test_touching_edges_do_not_overlap():
    # Both 1.0 long (0.5 half-length) along x, centers 1.0 apart -> edges
    # exactly meet, no interior overlap.
    a = (0.0, 0.0, 0.0, 1.0, 1.0)
    b = (1.0, 0.0, 0.0, 1.0, 1.0)

    assert rectangles_overlap(a, b) is False


def test_overlapping_rectangles_along_x_axis():
    a = (0.0, 0.0, 0.0, 1.0, 1.0)
    b = (0.5, 0.0, 0.0, 1.0, 1.0)

    assert rectangles_overlap(a, b) is True


def test_rotated_rectangle_overlap_detected():
    # b is rotated 90 degrees and positioned so its long axis crosses a.
    a = (0.0, 0.0, 0.0, 0.5, 2.0)
    b = (0.0, 0.0, math.pi / 2, 0.5, 2.0)

    assert rectangles_overlap(a, b) is True
