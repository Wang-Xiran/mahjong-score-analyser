"""SVG geometry for the placement-trend line graph. Kept separate from
stats.py since this is rendering math, not data querying."""

import math

CHART_WIDTH = 600
CHART_HEIGHT = 160
PAD_X = 24
PAD_Y = 16

# How long the left-to-right draw-in animation takes, in seconds.
ANIMATION_DURATION = 0.5


def _y_for_placement(placement: int) -> float:
    # 1st at the top, 4th at the bottom.
    return PAD_Y + (placement - 1) * (CHART_HEIGHT - 2 * PAD_Y) / 3


def build_placement_trend(results: list[dict]) -> dict:
    """results must be oldest-to-newest (as stats.get_player_results
    returns) — the graph reads left-to-right, oldest to most recent."""
    n = len(results)

    if n == 0:
        xs: list[float] = []
    elif n == 1:
        xs = [CHART_WIDTH / 2]
    else:
        xs = [PAD_X + i * (CHART_WIDTH - 2 * PAD_X) / (n - 1) for i in range(n)]

    points = [
        {
            "x": round(xs[i], 1),
            "y": round(_y_for_placement(r["placement"]), 1),
            "placement": r["placement"],
            "game_number": r["game_number"],
            "date": r["date"],
        }
        for i, r in enumerate(results)
    ]

    # Real path length (not just the x-span), so the stroke-dasharray "draw
    # in" animation below traces the actual zigzagging line rather than
    # under/overshooting it. Each dot's reveal is delayed in proportion to
    # how far along that path it sits, so dots pop in right as the line
    # reaches them instead of all appearing at once.
    path_length = 0.0
    for i in range(1, n):
        dx = points[i]["x"] - points[i - 1]["x"]
        dy = points[i]["y"] - points[i - 1]["y"]
        path_length += math.hypot(dx, dy)

    cumulative = 0.0
    for i, p in enumerate(points):
        if i > 0:
            dx = p["x"] - points[i - 1]["x"]
            dy = p["y"] - points[i - 1]["y"]
            cumulative += math.hypot(dx, dy)
        p["delay"] = round((cumulative / path_length) * ANIMATION_DURATION, 3) if path_length else 0.0

    return {
        "width": CHART_WIDTH,
        "height": CHART_HEIGHT,
        "pad_x": PAD_X,
        "points": points,
        "polyline": " ".join(f"{p['x']},{p['y']}" for p in points),
        "gridlines": [{"placement": p, "y": round(_y_for_placement(p), 1)} for p in (1, 2, 3, 4)],
        "path_length": round(path_length, 1),
        "animation_duration": ANIMATION_DURATION,
    }
