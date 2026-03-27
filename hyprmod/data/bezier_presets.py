"""Bezier curve math and CSS easing presets."""

# Immutable CSS easing presets: name -> (x1, y1, x2, y2)
BUILTIN_PRESETS: dict[str, tuple[float, float, float, float]] = {
    "ease": (0.25, 0.1, 0.25, 1.0),
    "easeIn": (0.42, 0.0, 1.0, 1.0),
    "easeOut": (0.0, 0.0, 0.58, 1.0),
    "easeInOut": (0.42, 0.0, 0.58, 1.0),
    "easeInSine": (0.12, 0.0, 0.39, 0.0),
    "easeOutSine": (0.61, 1.0, 0.88, 1.0),
    "easeInOutSine": (0.37, 0.0, 0.63, 1.0),
    "easeInQuad": (0.11, 0.0, 0.5, 0.0),
    "easeOutQuad": (0.5, 1.0, 0.89, 1.0),
    "easeInOutQuad": (0.45, 0.0, 0.55, 1.0),
    "easeInCubic": (0.32, 0.0, 0.67, 0.0),
    "easeOutCubic": (0.33, 1.0, 0.68, 1.0),
    "easeInOutCubic": (0.65, 0.0, 0.35, 1.0),
    "easeInExpo": (0.7, 0.0, 0.84, 0.0),
    "easeOutExpo": (0.16, 1.0, 0.3, 1.0),
    "easeInOutExpo": (0.87, 0.0, 0.13, 1.0),
    "easeInBack": (0.36, 0.0, 0.66, -0.56),
    "easeOutBack": (0.34, 1.56, 0.64, 1.0),
    "easeInOutBack": (0.68, -0.6, 0.32, 1.6),
}


def cubic_bezier(t: float, p1: float, p2: float) -> float:
    """Evaluate cubic bezier component at parameter t. P0=0, P3=1."""
    return 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t**2 * p2 + t**3


def _solve_t_for_x(x: float, x1: float, x2: float, epsilon: float = 1e-6) -> float:
    """Newton's method to find t for a given x value."""
    t = x  # initial guess
    for _ in range(20):
        x_at_t = cubic_bezier(t, x1, x2)
        dx = 3 * (1 - t) ** 2 * x1 + 6 * (1 - t) * t * (x2 - x1) + 3 * t**2 * (1 - x2)
        if abs(dx) < epsilon:
            break
        t -= (x_at_t - x) / dx
        t = max(0.0, min(1.0, t))
    return t


def ease(progress: float, x1: float, y1: float, x2: float, y2: float) -> float:
    """CSS-style cubic bezier easing: given linear progress [0,1], return eased value."""
    t = _solve_t_for_x(progress, x1, x2)
    return cubic_bezier(t, y1, y2)
