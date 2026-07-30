"""
JavaScript/Python implementation of
Algorithm for Automatically Fitting Digitized Curves
by Philip J. Schneider
"Graphics Gems", Academic Press, 1990

The MIT License (MIT)

https://github.com/soswow/fit-curves
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any


Point = list[float]
BezierCurve = list[Point]
ProgressCallback = Callable[[dict[str, Any]], None] | None


def fit_curve(
    points: Sequence[Sequence[float]],
    max_error: float,
    progress_callback: ProgressCallback = None,
) -> list[BezierCurve]:
    """Fit one or more Bezier curves to a set of points.

    Args:
        points: Digitized points, e.g. ``[[5,5],[5,50],[110,140],...]``
        max_error: Tolerance, squared error between points and fitted curve
        progress_callback: Optional progress reporter

    Returns:
        Array of Bezier curves; each element is
        ``[first-point, control-point-1, control-point-2, second-point]``.
    """
    if not isinstance(points, (list, tuple)):
        raise TypeError("First argument should be an array")

    pts: list[Point] = [list(p) for p in points]

    for point in pts:
        if (
            not isinstance(point, list)
            or any(not isinstance(item, (int, float)) for item in point)
            or len(point) != len(pts[0])
        ):
            raise ValueError(
                "Each point should be an array of numbers. "
                "Each point should have the same amount of numbers."
            )

    # Remove duplicate points
    filtered: list[Point] = []
    for i, point in enumerate(pts):
        if i == 0 or not all(val == pts[i - 1][j] for j, val in enumerate(point)):
            filtered.append(point)
    pts = filtered

    if len(pts) < 2:
        return []

    length = len(pts)
    left_tangent = create_tangent(pts[1], pts[0])
    right_tangent = create_tangent(pts[length - 2], pts[length - 1])

    return fit_cubic(pts, left_tangent, right_tangent, max_error, progress_callback)


def fit_cubic_tang(
    points: Sequence[Sequence[float]],
    tangents: Sequence[Sequence[float]],
    error: float,
    progress_callback: ProgressCallback = None,
) -> list[BezierCurve]:
    """Fit a Bezier curve to digitized points using per-point tangents."""
    max_iterations = 20  # Max times to try iterating (to find an acceptable curve)

    pts: list[Point] = [list(p) for p in points]
    tans: list[Point] = [list(t) for t in tangents]

    left_tangent = tans[0]
    right_tangent = Maths.mul_items(tans[len(pts) - 1], -1)

    # Use heuristic if region only has two points in it
    if len(pts) == 2:
        dist = Maths.vector_len(Maths.subtract(pts[0], pts[1])) / 3.0
        bez_curve = [
            pts[0],
            Maths.add_arrays(pts[0], Maths.mul_items(left_tangent, dist)),
            Maths.add_arrays(pts[1], Maths.mul_items(right_tangent, dist)),
            pts[1],
        ]
        return [bez_curve]

    # Parameterize points, and attempt to fit curve
    u = chord_length_parameterize(pts)
    bez_curve, max_err, split_point = generate_and_report(
        pts, u, u, left_tangent, right_tangent, progress_callback
    )

    if max_err < error:
        return [bez_curve]

    # If error not too large, try some reparameterization and iteration
    if max_err < error * error:
        u_prime = u
        prev_err = max_err
        prev_split = split_point

        for _i in range(max_iterations):
            u_prime = reparameterize(bez_curve, pts, u_prime)
            bez_curve, max_err, split_point = generate_and_report(
                pts, u, u_prime, left_tangent, right_tangent, progress_callback
            )

            if max_err < error:
                return [bez_curve]
            # If the development of the fitted curve grinds to a halt,
            # we abort this attempt (and try a shorter curve):
            if split_point == prev_split:
                err_change = max_err / prev_err
                if 0.9999 < err_change < 1.0001:
                    break

            prev_err = max_err
            prev_split = split_point

    # Fitting failed -- split at max error point and fit recursively
    beziers: list[BezierCurve] = []

    # To and from need to point in opposite directions:
    #
    # Note: An alternative to this "divide and conquer" recursion could be to
    # always let new curve segments start by trying to go all the way to the end,
    # instead of only to the end of the current subdivided polyline.
    # That might let many segments fit a few points more, reducing the number
    # of total segments.
    #
    # However, a few tests have shown that the segment reduction is insignificant
    # (240 pts, 100 err: 25 curves vs 27 curves. 140 pts, 100 err: 17 curves on both),
    # and the results take twice as many steps and milliseconds to finish,
    # without looking any better than what we already have.
    beziers = beziers + fit_cubic_tang(
        pts[: split_point + 1],
        tans[: split_point + 1],
        error,
        progress_callback,
    )
    beziers = beziers + fit_cubic_tang(
        pts[split_point:],
        tans[split_point:],
        error,
        progress_callback,
    )
    return beziers


def fit_cubic(
    points: list[Point],
    left_tangent: Point,
    right_tangent: Point,
    error: float,
    progress_callback: ProgressCallback,
) -> list[BezierCurve]:
    max_iterations = 20  # Max times to try iterating (to find an acceptable curve)

    # Use heuristic if region only has two points in it
    if len(points) == 2:
        dist = Maths.vector_len(Maths.subtract(points[0], points[1])) / 3.0
        bez_curve = [
            points[0],
            Maths.add_arrays(points[0], Maths.mul_items(left_tangent, dist)),
            Maths.add_arrays(points[1], Maths.mul_items(right_tangent, dist)),
            points[1],
        ]
        return [bez_curve]

    # Parameterize points, and attempt to fit curve
    u = chord_length_parameterize(points)
    bez_curve, max_err, split_point = generate_and_report(
        points, u, u, left_tangent, right_tangent, progress_callback
    )

    if max_err < error:
        return [bez_curve]

    # If error not too large, try some reparameterization and iteration
    if max_err < error * error:
        u_prime = u
        prev_err = max_err
        prev_split = split_point

        for _i in range(max_iterations):
            u_prime = reparameterize(bez_curve, points, u_prime)
            bez_curve, max_err, split_point = generate_and_report(
                points, u, u_prime, left_tangent, right_tangent, progress_callback
            )

            if max_err < error:
                return [bez_curve]
            # If the development of the fitted curve grinds to a halt,
            # we abort this attempt (and try a shorter curve):
            if split_point == prev_split:
                err_change = max_err / prev_err
                if 0.9999 < err_change < 1.0001:
                    break

            prev_err = max_err
            prev_split = split_point

    # Fitting failed -- split at max error point and fit recursively
    beziers: list[BezierCurve] = []

    # To create a smooth transition from one curve segment to the next, we
    # calculate the line between the points directly before and after the
    # center, and use that as the tangent both to and from the center point.
    center_vector = Maths.subtract(points[split_point - 1], points[split_point + 1])
    # However, this won't work if they're the same point, because the line we
    # want to use as a tangent would be 0. Instead, we calculate the line from
    # that "double-point" to the center point, and use its tangent.
    if all(val == 0 for val in center_vector):
        # [x,y] -> [-y,x]: http://stackoverflow.com/a/4780141/1869660
        center_vector = Maths.subtract(points[split_point - 1], points[split_point])
        center_vector[0], center_vector[1] = -center_vector[1], center_vector[0]

    to_center_tangent = Maths.normalize(center_vector)
    # To and from need to point in opposite directions:
    from_center_tangent = Maths.mul_items(to_center_tangent, -1)

    #
    # Note: An alternative to this "divide and conquer" recursion could be to
    # always let new curve segments start by trying to go all the way to the end,
    # instead of only to the end of the current subdivided polyline.
    # That might let many segments fit a few points more, reducing the number
    # of total segments.
    #
    # However, a few tests have shown that the segment reduction is insignificant
    # (240 pts, 100 err: 25 curves vs 27 curves. 140 pts, 100 err: 17 curves on both),
    # and the results take twice as many steps and milliseconds to finish,
    # without looking any better than what we already have.
    beziers = beziers + fit_cubic(
        points[: split_point + 1],
        left_tangent,
        to_center_tangent,
        error,
        progress_callback,
    )
    beziers = beziers + fit_cubic(
        points[split_point:],
        from_center_tangent,
        right_tangent,
        error,
        progress_callback,
    )
    return beziers


def generate_and_report(
    points: list[Point],
    params_orig: list[float],
    params_prime: list[float],
    left_tangent: Point,
    right_tangent: Point,
    progress_callback: ProgressCallback,
) -> tuple[BezierCurve, float, int]:
    bez_curve = generate_bezier(points, params_prime, left_tangent, right_tangent)
    # Find max deviation of points to fitted curve.
    # Here we always use the original parameters (from chord_length_parameterize()),
    # because we need to compare the current curve to the actual source polyline,
    # and not the currently iterated parameters which reparameterize() &
    # generate_bezier() use, as those have probably drifted far away and may no
    # longer be in ascending order.
    max_err, split_point = compute_max_error(points, bez_curve, params_orig)

    if progress_callback:
        progress_callback(
            {
                "bez": bez_curve,
                "points": points,
                "params": params_orig,
                "maxErr": max_err,
                "maxPoint": split_point,
            }
        )

    return bez_curve, max_err, split_point


def generate_bezier(
    points: list[Point],
    parameters: list[float],
    left_tangent: Point,
    right_tangent: Point,
) -> BezierCurve:
    """Use least-squares method to find Bezier control points for region."""
    first_point = points[0]
    last_point = points[len(points) - 1]

    bez_curve: BezierCurve = [first_point, None, None, last_point]  # type: ignore[list-item]

    # Compute the A's
    a_matrix = Maths.zeros_xx2x2(len(parameters))
    for i, u in enumerate(parameters):
        ux = 1 - u
        a = a_matrix[i]
        a[0] = Maths.mul_items(left_tangent, 3 * u * (ux * ux))
        a[1] = Maths.mul_items(right_tangent, 3 * ux * (u * u))

    # Create the C and X matrices
    c = [[0.0, 0.0], [0.0, 0.0]]
    x = [0.0, 0.0]
    for i in range(len(points)):
        u = parameters[i]
        a = a_matrix[i]

        c[0][0] += Maths.dot(a[0], a[0])
        c[0][1] += Maths.dot(a[0], a[1])
        c[1][0] += Maths.dot(a[0], a[1])
        c[1][1] += Maths.dot(a[1], a[1])

        tmp = Maths.subtract(
            points[i],
            BezierEval.q([first_point, first_point, last_point, last_point], u),
        )

        x[0] += Maths.dot(a[0], tmp)
        x[1] += Maths.dot(a[1], tmp)

    # Compute the determinants of C and X
    det_c0_c1 = c[0][0] * c[1][1] - c[1][0] * c[0][1]
    det_c0_x = c[0][0] * x[1] - c[1][0] * x[0]
    det_x_c1 = x[0] * c[1][1] - x[1] * c[0][1]

    # Finally, derive alpha values
    alpha_l = 0 if det_c0_c1 == 0 else det_x_c1 / det_c0_c1
    alpha_r = 0 if det_c0_c1 == 0 else det_c0_x / det_c0_c1

    # If alpha negative, use the Wu/Barsky heuristic (see text).
    # If alpha is 0, you get coincident control points that lead to
    # divide by zero in any subsequent newton_raphson_root_find() call.
    seg_length = Maths.vector_len(Maths.subtract(first_point, last_point))
    epsilon = 1.0e-6 * seg_length
    if alpha_l < epsilon or alpha_r < epsilon:
        # Fall back on standard (probably inaccurate) formula, and subdivide
        # further if needed.
        bez_curve[1] = Maths.add_arrays(
            first_point, Maths.mul_items(left_tangent, seg_length / 3.0)
        )
        bez_curve[2] = Maths.add_arrays(
            last_point, Maths.mul_items(right_tangent, seg_length / 3.0)
        )
    else:
        # First and last control points of the Bezier curve are
        # positioned exactly at the first and last data points
        # Control points 1 and 2 are positioned an alpha distance out
        # on the tangent vectors, left and right, respectively
        bez_curve[1] = Maths.add_arrays(
            first_point, Maths.mul_items(left_tangent, alpha_l)
        )
        bez_curve[2] = Maths.add_arrays(
            last_point, Maths.mul_items(right_tangent, alpha_r)
        )

    return bez_curve


def reparameterize(
    bezier: BezierCurve, points: list[Point], parameters: list[float]
) -> list[float]:
    """Given set of points and their parameterization, find a better one."""
    return [
        newton_raphson_root_find(bezier, points[i], p)
        for i, p in enumerate(parameters)
    ]


def newton_raphson_root_find(bez: BezierCurve, point: Point, u: float) -> float:
    """Use Newton-Raphson iteration to find better root.

    Newton's root finding algorithm calculates f(x)=0 by reiterating
    x_n+1 = x_n - f(x_n)/f'(x_n)
    We are trying to find curve parameter u for some point p that minimizes
    the distance from that point to the curve. Distance point to curve is d=q(u)-p.
    At minimum distance the point is perpendicular to the curve.
    We are solving
    f = q(u)-p * q'(u) = 0
    with
    f' = q'(u) * q'(u) + q(u)-p * q''(u)
    gives
    u_n+1 = u_n - |q(u_n)-p * q'(u_n)| / |q'(u_n)**2 + q(u_n)-p * q''(u_n)|
    """
    d = Maths.subtract(BezierEval.q(bez, u), point)
    qprime = BezierEval.qprime(bez, u)
    numerator = Maths.mul_matrix(d, qprime)
    denominator = Maths.sum(Maths.square_items(qprime)) + 2 * Maths.mul_matrix(
        d, BezierEval.qprimeprime(bez, u)
    )

    if denominator == 0:
        return u
    return u - numerator / denominator


def chord_length_parameterize(points: list[Point]) -> list[float]:
    """Assign parameter values using relative distances between points."""
    u: list[float] = []
    prev_u = 0.0
    prev_p: Point = points[0]

    for i, p in enumerate(points):
        curr_u = 0.0 if i == 0 else prev_u + Maths.vector_len(Maths.subtract(p, prev_p))
        u.append(curr_u)
        prev_u = curr_u
        prev_p = p

    u = [x / prev_u for x in u]
    return u


def compute_max_error(
    points: list[Point], bez: BezierCurve, parameters: list[float]
) -> tuple[float, int]:
    """Find the maximum squared distance of digitized points to fitted curve."""
    max_dist = 0.0
    split_point = len(points) // 2

    t_dist_map = map_t_to_relative_distances(bez, 10)

    for i, point in enumerate(points):
        # Find 't' for a point on the bez curve that's as close to 'point' as possible:
        t = find_t(bez, parameters[i], t_dist_map, 10)

        v = Maths.subtract(BezierEval.q(bez, t), point)
        dist = v[0] * v[0] + v[1] * v[1]

        if dist > max_dist:
            max_dist = dist
            split_point = i

    return max_dist, split_point


def map_t_to_relative_distances(bez: BezierCurve, b_parts: int) -> list[float]:
    """Sample 't's and map them to relative distances along the curve."""
    b_t_dist = [0.0]
    b_t_prev = bez[0]
    sum_len = 0.0

    for i in range(1, b_parts + 1):
        b_t_curr = BezierEval.q(bez, i / b_parts)
        sum_len += Maths.vector_len(Maths.subtract(b_t_curr, b_t_prev))
        b_t_dist.append(sum_len)
        b_t_prev = b_t_curr

    # Normalize B_length to the same interval as the parameter distances; 0 to 1:
    b_t_dist = [x / sum_len for x in b_t_dist]
    return b_t_dist


def find_t(
    bez: BezierCurve, param: float, t_dist_map: list[float], b_parts: int
) -> float:
    if param < 0:
        return 0
    if param > 1:
        return 1

    #
    # 'param' is a value between 0 and 1 telling us the relative position
    # of a point on the source polyline (linearly from the start (0) to the end (1)).
    # To see if a given curve - 'bez' - is a close approximation of the polyline,
    # we compare such a poly-point to the point on the curve that's the same
    # relative distance along the curve's length.
    #
    # But finding that curve-point takes a little work:
    # There is a function "B(t)" to find points along a curve from the parametric
    # parameter 't' (also relative from 0 to 1), but 't' isn't linear by length.
    #
    # So, we sample some points along the curve using a handful of values for 't'.
    # Then, we calculate the length between those samples via plain euclidean distance;
    # B(t) concentrates the points around sharp turns, so this should give us a
    # good-enough outline of the curve.
    # Thus, for a given relative distance ('param'), we can now find an upper and
    # lower value for the corresponding 't' by searching through those sampled
    # distances. Finally, we just use linear interpolation to find a better value
    # for the exact 't'.
    t = 0.0

    # Find the two t-s that the current param distance lies between,
    # and then interpolate a somewhat accurate value for the exact t:
    for i in range(1, b_parts + 1):
        if param <= t_dist_map[i]:
            t_min = (i - 1) / b_parts
            t_max = i / b_parts
            len_min = t_dist_map[i - 1]
            len_max = t_dist_map[i]
            t = ((param - len_min) / (len_max - len_min)) * (t_max - t_min) + t_min
            break

    return t


def create_tangent(point_a: Sequence[float], point_b: Sequence[float]) -> Point:
    """Create a unit vector in the direction from B to A."""
    return Maths.normalize(Maths.subtract(list(point_a), list(point_b)))


class Maths:
    """Simplified math helpers optimized for numbers and 1x2 [x, y] arrays."""

    @staticmethod
    def zeros_xx2x2(x: int) -> list[list[list[float]]]:
        zs: list[list[list[float]]] = []
        while x:
            zs.append([[0.0, 0.0], [0.0, 0.0]])
            x -= 1
        return zs

    @staticmethod
    def mul_items(items: Sequence[float], multiplier: float) -> Point:
        return [x * multiplier for x in items]

    @staticmethod
    def mul_matrix(m1: Sequence[float], m2: Sequence[float]) -> float:
        # Simplified to only handle 1-dimensional matrices (arrays) of equal length
        return sum(x1 * m2[i] for i, x1 in enumerate(m1))

    @staticmethod
    def subtract(arr1: Sequence[float], arr2: Sequence[float]) -> Point:
        return [x1 - arr2[i] for i, x1 in enumerate(arr1)]

    @staticmethod
    def add_arrays(arr1: Sequence[float], arr2: Sequence[float]) -> Point:
        return [x1 + arr2[i] for i, x1 in enumerate(arr1)]

    @staticmethod
    def add_items(items: Sequence[float], addition: float) -> Point:
        return [x + addition for x in items]

    @staticmethod
    def sum(items: Sequence[float]) -> float:
        total = 0.0
        for x in items:
            total += x
        return total

    @staticmethod
    def dot(m1: Sequence[float], m2: Sequence[float]) -> float:
        return Maths.mul_matrix(m1, m2)

    @staticmethod
    def vector_len(v: Sequence[float]) -> float:
        return math.hypot(*v)

    @staticmethod
    def div_items(items: Sequence[float], divisor: float) -> Point:
        return [x / divisor for x in items]

    @staticmethod
    def square_items(items: Sequence[float]) -> Point:
        return [x * x for x in items]

    @staticmethod
    def normalize(v: Sequence[float]) -> Point:
        return Maths.div_items(v, Maths.vector_len(v))


class BezierEval:
    """Cubic Bezier evaluation helpers."""

    @staticmethod
    def q(ctrl_poly: Sequence[Sequence[float]], t: float) -> Point:
        """Evaluate cubic bezier at t; return point."""
        tx = 1.0 - t
        p_a = Maths.mul_items(ctrl_poly[0], tx * tx * tx)
        p_b = Maths.mul_items(ctrl_poly[1], 3 * tx * tx * t)
        p_c = Maths.mul_items(ctrl_poly[2], 3 * tx * t * t)
        p_d = Maths.mul_items(ctrl_poly[3], t * t * t)
        return Maths.add_arrays(Maths.add_arrays(p_a, p_b), Maths.add_arrays(p_c, p_d))

    @staticmethod
    def qprime(ctrl_poly: Sequence[Sequence[float]], t: float) -> Point:
        """Evaluate cubic bezier first derivative at t; return point."""
        tx = 1.0 - t
        p_a = Maths.mul_items(
            Maths.subtract(ctrl_poly[1], ctrl_poly[0]), 3 * tx * tx
        )
        p_b = Maths.mul_items(
            Maths.subtract(ctrl_poly[2], ctrl_poly[1]), 6 * tx * t
        )
        p_c = Maths.mul_items(
            Maths.subtract(ctrl_poly[3], ctrl_poly[2]), 3 * t * t
        )
        return Maths.add_arrays(Maths.add_arrays(p_a, p_b), p_c)

    @staticmethod
    def qprimeprime(ctrl_poly: Sequence[Sequence[float]], t: float) -> Point:
        """Evaluate cubic bezier second derivative at t; return point."""
        return Maths.add_arrays(
            Maths.mul_items(
                Maths.add_arrays(
                    Maths.subtract(ctrl_poly[2], Maths.mul_items(ctrl_poly[1], 2)),
                    ctrl_poly[0],
                ),
                6 * (1.0 - t),
            ),
            Maths.mul_items(
                Maths.add_arrays(
                    Maths.subtract(ctrl_poly[3], Maths.mul_items(ctrl_poly[2], 2)),
                    ctrl_poly[1],
                ),
                6 * t,
            ),
        )
