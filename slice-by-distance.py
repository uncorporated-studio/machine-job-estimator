import json
import math
import re
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

# ===== Repo root (where this script lives) =====
REPO_ROOT = Path(__file__).resolve().parent
SVG_NS = {"svg": "http://www.w3.org/2000/svg"}

# Tokenizer: commands or numbers (supports commas, no spaces)
TOKEN_RE = re.compile(r"[MmLlHhVvCcQqZz]|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def resolve_repo_path(p: str) -> Path:
    """Resolve a path from job.json relative to repo root."""
    return (REPO_ROOT / p).resolve()


def derive_output_gcode(input_svg: str) -> Path:
    """output/<same basename>.gcode (always under repo root)."""
    base = Path(input_svg).stem
    return (REPO_ROOT / "output" / f"{base}.gcode").resolve()


def load_job(job_path: str) -> dict:
    job_file = resolve_repo_path(job_path)
    with open(job_file, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_viewbox_or_size(root):
    vb = root.attrib.get("viewBox")
    if vb:
        parts = vb.replace(",", " ").split()
        if len(parts) == 4:
            x0, y0, w, h = map(float, parts)
            return x0, y0, w, h

    def parse_size(val, default=100.0):
        if val is None:
            return default
        num = ""
        for ch in val:
            if ch.isdigit() or ch in ".-":
                num += ch
            else:
                break
        try:
            return float(num)
        except ValueError:
            return default

    w = parse_size(root.attrib.get("width"), 100.0)
    h = parse_size(root.attrib.get("height"), 100.0)
    return 0.0, 0.0, w, h


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def quad_bezier(p0, p1, p2, t):
    a = lerp(p0, p1, t)
    b = lerp(p1, p2, t)
    return lerp(a, b, t)


def cubic_bezier(p0, p1, p2, p3, t):
    a = lerp(p0, p1, t)
    b = lerp(p1, p2, t)
    c = lerp(p2, p3, t)
    d = lerp(a, b, t)
    e = lerp(b, c, t)
    return lerp(d, e, t)


def _is_cmd(tok: str) -> bool:
    return len(tok) == 1 and tok.isalpha()


def path_to_strokes_points(d: str, curve_samples: int = 20):
    """
    Convert SVG path 'd' into STROKES (subpaths), where each stroke is a list of points.
    Supports: M,L,H,V,Z and Q,C (sampled), abs+rel.

    Stroke boundary:
      - first M starts a stroke
      - any subsequent M starts a new stroke
    """
    tokens = TOKEN_RE.findall(d)
    i = 0
    cmd = None

    cur = (0.0, 0.0)
    start = None

    strokes = []
    current_points = None  # list of points for current stroke

    def next_number():
        nonlocal i
        val = float(tokens[i])
        i += 1
        return val

    def start_new_stroke(pt):
        nonlocal current_points
        if current_points and len(current_points) >= 2:
            strokes.append(current_points)
        current_points = [pt]

    def ensure_stroke_started(pt):
        nonlocal current_points
        if current_points is None:
            current_points = [pt]

    def add_point(pt):
        nonlocal current_points
        ensure_stroke_started(cur)
        if not current_points:
            current_points = [pt]
            return
        # avoid exact duplicates
        if pt != current_points[-1]:
            current_points.append(pt)

    while i < len(tokens):
        tok = tokens[i]
        if _is_cmd(tok):
            cmd = tok
            i += 1
        elif cmd is None:
            break

        # Closepath
        if cmd in ("Z", "z"):
            if start is not None:
                # close current stroke by returning to start
                add_point(start)
                cur = start
            start = None
            continue

        # MoveTo (starts a new stroke; subsequent pairs are implicit LineTo)
        if cmd in ("M", "m"):
            x = next_number()
            y = next_number()
            cur = (cur[0] + x, cur[1] + y) if cmd == "m" else (x, y)
            start = cur
            start_new_stroke(cur)

            # subsequent pairs are implicit L (same stroke)
            while i < len(tokens) and not _is_cmd(tokens[i]):
                x = next_number()
                y = next_number()
                nxt = (cur[0] + x, cur[1] + y) if cmd == "m" else (x, y)
                add_point(nxt)
                cur = nxt
            continue

        # LineTo
        if cmd in ("L", "l"):
            while i < len(tokens) and not _is_cmd(tokens[i]):
                x = next_number()
                y = next_number()
                nxt = (cur[0] + x, cur[1] + y) if cmd == "l" else (x, y)
                add_point(nxt)
                cur = nxt
            continue

        # Horizontal
        if cmd in ("H", "h"):
            while i < len(tokens) and not _is_cmd(tokens[i]):
                x = next_number()
                nxt = (cur[0] + x, cur[1]) if cmd == "h" else (x, cur[1])
                add_point(nxt)
                cur = nxt
            continue

        # Vertical
        if cmd in ("V", "v"):
            while i < len(tokens) and not _is_cmd(tokens[i]):
                y = next_number()
                nxt = (cur[0], cur[1] + y) if cmd == "v" else (cur[0], y)
                add_point(nxt)
                cur = nxt
            continue

        # Quadratic Bezier (sampled)
        if cmd in ("Q", "q"):
            while i < len(tokens) and not _is_cmd(tokens[i]):
                x1 = next_number()
                y1 = next_number()
                x2 = next_number()
                y2 = next_number()
                p1 = (cur[0] + x1, cur[1] + y1) if cmd == "q" else (x1, y1)
                p2 = (cur[0] + x2, cur[1] + y2) if cmd == "q" else (x2, y2)

                # sample points along curve (exclude t=0, include t=1)
                for s in range(1, max(1, curve_samples) + 1):
                    t = s / max(1, curve_samples)
                    pt = quad_bezier(cur, p1, p2, t)
                    add_point(pt)

                cur = p2
            continue

        # Cubic Bezier (sampled)
        if cmd in ("C", "c"):
            while i < len(tokens) and not _is_cmd(tokens[i]):
                x1 = next_number()
                y1 = next_number()
                x2 = next_number()
                y2 = next_number()
                x3 = next_number()
                y3 = next_number()

                p1 = (cur[0] + x1, cur[1] + y1) if cmd == "c" else (x1, y1)
                p2 = (cur[0] + x2, cur[1] + y2) if cmd == "c" else (x2, y2)
                p3 = (cur[0] + x3, cur[1] + y3) if cmd == "c" else (x3, y3)

                for s in range(1, max(1, curve_samples) + 1):
                    t = s / max(1, curve_samples)
                    pt = cubic_bezier(cur, p1, p2, p3, t)
                    add_point(pt)

                cur = p3
            continue

        # unsupported path commands: stop safely
        break

    # flush last stroke
    if current_points and len(current_points) >= 2:
        strokes.append(current_points)

    return strokes


def svg_strokes_in_order(svg_path: Path, curve_samples: int, skip_path_classes: list):
    """
    Return strokes in the SVG order.
    Each stroke is a list of points [(x,y), ...] in SVG coordinate space.
    """
    tree = ET.parse(str(svg_path))
    root = tree.getroot()
    strokes = []

    # <line> => 1 stroke each
    for line in root.findall(".//svg:line", SVG_NS):
        x1 = float(line.attrib.get("x1", "0"))
        y1 = float(line.attrib.get("y1", "0"))
        x2 = float(line.attrib.get("x2", "0"))
        y2 = float(line.attrib.get("y2", "0"))
        strokes.append([(x1, y1), (x2, y2)])

    # <path> => can contain multiple strokes (multiple M)
    for path in root.findall(".//svg:path", SVG_NS):
        klass = path.attrib.get("class", "")
        if any(cls in klass for cls in skip_path_classes):
            continue

        d = path.attrib.get("d", "")
        if not d.strip():
            continue

        strokes.extend(path_to_strokes_points(d, curve_samples=curve_samples))

    return strokes, root


# ----------------- Optimization over STROKES -----------------

def stroke_midpoint(stroke):
    xs = [p[0] for p in stroke]
    ys = [p[1] for p in stroke]
    return (0.5 * (min(xs) + max(xs)), 0.5 * (min(ys) + max(ys)))


def jump_distance_mm_strokes(strokes_mm):
    """Sum of pen-up XY distances from end(stroke_i) to start(stroke_{i+1})."""
    if not strokes_mm:
        return 0.0
    total = 0.0
    prev_end = strokes_mm[0][-1]
    for s in strokes_mm[1:]:
        a = s[0]
        total += math.hypot(a[0] - prev_end[0], a[1] - prev_end[1])
        prev_end = s[-1]
    return total


def orient_strokes_greedily(strokes_mm):
    """Flip each stroke to reduce approach distance (uses current end -> next start/end)."""
    if not strokes_mm:
        return strokes_mm

    out = []
    cur = strokes_mm[0][0]  # assume we approach first stroke at its start

    for stroke in strokes_mm:
        a = stroke[0]
        b = stroke[-1]
        da = math.hypot(a[0] - cur[0], a[1] - cur[1])
        db = math.hypot(b[0] - cur[0], b[1] - cur[1])
        if db < da:
            stroke = list(reversed(stroke))
        out.append(stroke)
        cur = stroke[-1]

    return out


def optimize_strokes_scanline_mm(strokes_mm, row_mm: float):
    """
    Scanline ordering by stroke midpoint Y, boustrophedon by row.
    Works best for hatch-like drawings.
    """
    if not strokes_mm or row_mm <= 0:
        return strokes_mm

    items = []
    for stroke in strokes_mm:
        mx, my = stroke_midpoint(stroke)
        row = int(my // row_mm)
        items.append((row, mx, stroke))

    items.sort(key=lambda t: (t[0], t[1]))

    out = []
    current_row = None
    buffer = []

    def flush_row(r, buf):
        if r is None:
            return
        # alternate direction each row
        if (r % 2) == 1:
            buf.reverse()
        out.extend(s for (_, _, s) in buf)

    for row, mx, stroke in items:
        if current_row is None:
            current_row = row
        if row != current_row:
            flush_row(current_row, buffer)
            buffer = []
            current_row = row
        buffer.append((row, mx, stroke))

    flush_row(current_row, buffer)
    return out


def optimize_strokes_nearest_neighbor(strokes_mm):
    """
    Greedy nearest-neighbor ordering, allowing stroke reversal.
    O(n^2): OK for a few thousand strokes.
    """
    if not strokes_mm:
        return strokes_mm

    remaining = strokes_mm[:]
    used = [False] * len(remaining)
    out = []

    # start with first stroke as-is
    used[0] = True
    out.append(remaining[0])
    cur = remaining[0][-1]

    for _ in range(len(remaining) - 1):
        best_i = None
        best_flip = False
        best_d = float("inf")

        for i, stroke in enumerate(remaining):
            if used[i]:
                continue
            a = stroke[0]
            b = stroke[-1]
            da = math.hypot(a[0] - cur[0], a[1] - cur[1])
            if da < best_d:
                best_d = da
                best_i = i
                best_flip = False
            db = math.hypot(b[0] - cur[0], b[1] - cur[1])
            if db < best_d:
                best_d = db
                best_i = i
                best_flip = True

        used[best_i] = True
        stroke = remaining[best_i]
        if best_flip:
            stroke = list(reversed(stroke))
        out.append(stroke)
        cur = stroke[-1]

    return out


# ----------------- Main generator -----------------

def run(job_path: str):
    job = load_job(job_path)

    if "input_svg" not in job:
        raise KeyError('job.json must contain "input_svg"')

    svg_rel = job["input_svg"]
    svg_file = resolve_repo_path(svg_rel)
    if not svg_file.exists():
        raise FileNotFoundError(f"Input SVG not found: {svg_file}")

    g = job.get("generator", {})

    # --- optimization config (must be inside generator) ---
    opt_cfg = g.get("optimize_order", {}) or {}
    opt_enabled = bool(opt_cfg.get("enabled", False))
    opt_mode = str(opt_cfg.get("mode", "scanline")).strip().lower()
    opt_row_mm = float(opt_cfg.get("row_mm", 5.0))

    target_w = float(g.get("target_width_mm", 250.0))
    target_h = float(g.get("target_height_mm", 190.0))
    feedrate = float(g.get("feedrate_mm_min", 3000))
    travel_z = float(g.get("travel_z_mm", 3.0))

    steps = int(g.get("steps", 2))
    decrement = float(g.get("decrement_mm", 0.01))

    sharpen_distance = float(g.get("sharpen_distance_mm", 6000.0))
    sharpen_macro = str(g.get("sharpen_macro", 'M98 P"/macros/sharpen.g"'))
    sharpen_log_file = str(g.get("sharpen_log_file", "sharpen.log"))

    curve_samples = int(g.get("curve_samples", 20))
    skip_path_classes = list(g.get("skip_path_classes", []))

    strokes, root = svg_strokes_in_order(
        svg_path=svg_file,
        curve_samples=curve_samples,
        skip_path_classes=skip_path_classes,
    )
    if not strokes:
        raise RuntimeError("No drawable strokes found in SVG (no <path> or <line>).")

    x0, y0, vb_w, vb_h = parse_viewbox_or_size(root)
    scale_x = target_w / vb_w
    scale_y = target_h / vb_h

    # Build strokes_mm once (scaled + Y-flipped)
    strokes_mm = []
    for stroke in strokes:
        pts_mm = []
        for (x, y) in stroke:
            sx = (x - x0) * scale_x
            sy = (y0 + vb_h - y) * scale_y
            pts_mm.append((sx, sy))
        # keep only valid strokes
        if len(pts_mm) >= 2:
            strokes_mm.append(pts_mm)

    if not strokes_mm:
        raise RuntimeError("No drawable strokes after scaling (all strokes too small?)")

    # ---- optimization (on strokes) ----
    if opt_enabled:
        before = jump_distance_mm_strokes(strokes_mm)

        if opt_mode == "scanline":
            strokes_mm = optimize_strokes_scanline_mm(strokes_mm, row_mm=opt_row_mm)
            strokes_mm = orient_strokes_greedily(strokes_mm)
        elif opt_mode == "nearest":
            strokes_mm = optimize_strokes_nearest_neighbor(strokes_mm)
        else:
            print(f"[opt] unknown mode='{opt_mode}', skipping optimization")

        after = jump_distance_mm_strokes(strokes_mm)
        print(f"[opt] enabled mode={opt_mode} row_mm={opt_row_mm} strokes={len(strokes_mm)}")
        print(f"[opt] jump XY distance: {before:.1f} mm -> {after:.1f} mm")
    else:
        print(f"[opt] disabled (enabled={opt_enabled}, mode={opt_mode}) strokes={len(strokes_mm)}")

    out_gcode = derive_output_gcode(svg_rel)
    out_gcode.parent.mkdir(parents=True, exist_ok=True)

    current_drawing_z = 0.0
    draw_move_count = 0          # counts pen-down drawing segments
    drawing_distance = 0.0       # drawn distance since last sharpening
    sharpen_idx = 0

    with open(out_gcode, "w", encoding="utf-8") as f:
        f.write("G21\n")
        f.write(f"G1 F{feedrate:.0f}\n")
        f.write("G53 G0 Z-20\n")

        for stroke in strokes_mm:
            # Travel to stroke start (pen up)
            x_start, y_start = stroke[0]
            f.write(f"G0 X{x_start:.2f} Y{y_start:.2f}\n")

            # Pen down ONCE for the whole stroke
            f.write(f"G1 X{x_start:.2f} Y{y_start:.2f} Z{current_drawing_z:.2f}\n")

            # Draw through all points in the stroke (no lifting)
            prev = stroke[0]
            for pt in stroke[1:]:
                # gradual descent per drawn SEGMENT (not per stroke)
                draw_move_count += 1
                if steps > 0 and (draw_move_count % steps == 0):
                    current_drawing_z -= decrement

                x, y = pt
                f.write(f"G1 X{x:.2f} Y{y:.2f} Z{current_drawing_z:.2f}\n")

                drawing_distance += math.hypot(x - prev[0], y - prev[1])
                prev = pt

            # Lift ONCE after the stroke
            f.write(f"G1 Z{travel_z:.2f}\n")

            # Sharpen only at stroke boundary (never mid-stroke)
            if drawing_distance >= sharpen_distance:
                sharpen_idx += 1
                f.write(
                    f'echo >>"{sharpen_log_file}" "about to sharpen #{sharpen_idx} at drawn_mm: {drawing_distance:.1f}"\n'
                )
                f.write(f"{sharpen_macro} ; Call sharpening macro\n")

                # reset after macro (macro resets Z0)
                current_drawing_z = 0.0
                draw_move_count = 0
                drawing_distance = 0.0

        f.write("; End\n")

    print(f"✅ Wrote: {out_gcode}")


if __name__ == "__main__":
    job_path = sys.argv[1] if len(sys.argv) > 1 else "jobs/job.json"
    run(job_path)