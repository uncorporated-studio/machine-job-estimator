import math
import re
import sys
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt

RE_CMD = re.compile(r'^\s*([GMT]\d+)\b', re.IGNORECASE)
RE_XYZ = re.compile(r'\b([XYZ])(-?\d+(?:\.\d+)?)\b', re.IGNORECASE)


def strip_comment(line: str) -> str:
    if ";" in line:
        line = line.split(";", 1)[0]
    return line.strip()


def parse_xyz(line: str):
    out = {}
    for ax, val in RE_XYZ.findall(line):
        out[ax.upper()] = float(val)
    return out


def view_gcode(gcode_path: Path, title: Optional[str] = None):
    # Accumulate segments as ((x0,y0),(x1,y1)) for each class
    draw_segs = []
    travel_segs = []

    x = y = z = None
    pen_down = False  # inferred from Z<=0 after a move
    have_xy = False

    with open(gcode_path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = strip_comment(raw)
            if not line:
                continue

            m = RE_CMD.match(line)
            if not m:
                continue
            cmd = m.group(1).upper()

            if cmd not in ("G0", "G00", "G1", "G01"):
                continue

            coords = parse_xyz(line)
            new_x = coords.get("X", x)
            new_y = coords.get("Y", y)
            new_z = coords.get("Z", z)

            # Only plot when we have XY movement (or first XY set)
            if new_x is None or new_y is None:
                x, y, z = new_x, new_y, new_z
                continue

            # Determine pen state *after* this line
            z_after = new_z if new_z is not None else z
            now_down = (z_after is not None and z_after <= 0)

            # Build XY segment from previous XY to new XY if we already had a point
            if have_xy and (new_x != x or new_y != y):
                seg = ((x, y), (new_x, new_y))

                # Classify:
                # - Any G0 is travel
                # - Any time pen is up (now_down == False), treat as travel
                # - Otherwise drawing
                if cmd in ("G0", "G00") or (not now_down):
                    travel_segs.append(seg)
                else:
                    draw_segs.append(seg)

            # Update state
            x, y, z = new_x, new_y, new_z
            have_xy = True
            pen_down = now_down

    # Plot
    fig, ax = plt.subplots()
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    ax.set_title(title or f"G-code visualization: {gcode_path.name}")

    # Draw travel first (so drawing sits on top)
    for (a, b) in travel_segs:
        ax.plot([a[0], b[0]], [a[1], b[1]], "r-", linewidth=0.5)

    for (a, b) in draw_segs:
        ax.plot([a[0], b[0]], [a[1], b[1]], "b-", linewidth=0.8)

    # Optional: flip Y if you prefer "SVG-like" orientation
    # ax.invert_yaxis()

    plt.show(block=True)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 visualize-gcode.py <path/to/file.gcode>")
        raise SystemExit(2)

    gcode_path = Path(sys.argv[1]).expanduser().resolve()
    if not gcode_path.exists():
        raise FileNotFoundError(f"G-code not found: {gcode_path}")

    view_gcode(gcode_path)


if __name__ == "__main__":
    main()