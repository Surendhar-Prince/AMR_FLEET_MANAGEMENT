import math

Rectangle = tuple[float, float, float, float, float]  # x, y, heading, width, length


def _corners(rect: Rectangle) -> list[tuple[float, float]]:
    x, y, heading, width, length = rect
    half_w, half_l = width / 2, length / 2
    local = [(-half_l, -half_w), (half_l, -half_w), (half_l, half_w), (-half_l, half_w)]
    cos_h, sin_h = math.cos(heading), math.sin(heading)
    return [(x + lx * cos_h - ly * sin_h, y + lx * sin_h + ly * cos_h) for lx, ly in local]


def _axes(corners: list[tuple[float, float]]) -> list[tuple[float, float]]:
    axes = []
    for i in range(len(corners)):
        x1, y1 = corners[i]
        x2, y2 = corners[(i + 1) % len(corners)]
        edge = (x2 - x1, y2 - y1)
        normal = (-edge[1], edge[0])
        length = math.hypot(*normal)
        axes.append((normal[0] / length, normal[1] / length))
    return axes


def _project(corners: list[tuple[float, float]], axis: tuple[float, float]) -> tuple[float, float]:
    dots = [c[0] * axis[0] + c[1] * axis[1] for c in corners]
    return min(dots), max(dots)


def rectangles_overlap(a: Rectangle, b: Rectangle) -> bool:
    """Check whether two oriented rectangles overlap (separating axis theorem).

    Args:
        a: (x, y, heading_radians, width, length) of the first rectangle.
        b: (x, y, heading_radians, width, length) of the second rectangle.

    Returns:
        True if the rectangles' interiors overlap; touching edges (zero
        overlap) count as not overlapping.
    """
    corners_a, corners_b = _corners(a), _corners(b)
    for axis in _axes(corners_a) + _axes(corners_b):
        min_a, max_a = _project(corners_a, axis)
        min_b, max_b = _project(corners_b, axis)
        if max_a <= min_b or max_b <= min_a:
            return False
    return True
