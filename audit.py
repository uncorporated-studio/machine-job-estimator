import math, re, sys
from pathlib import Path

RE_CMD = re.compile(r'^\s*(G0|G00|G1|G01)\b', re.IGNORECASE)
RE_XY = re.compile(r'\b([XY])(-?\d+(?:\.\d+)?)\b', re.IGNORECASE)

def strip_comment(line): return line.split(";",1)[0].strip()

def parse_xy(line):
    out = {}
    for ax,val in RE_XY.findall(line):
        out[ax.upper()] = float(val)
    return out

def audit_g0(gcode_path: Path, rapid_mm_min: float = 10000.0):
    x = y = None
    g0_dist = 0.0
    g0_count = 0

    with open(gcode_path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = strip_comment(raw)
            if not line: 
                continue
            m = RE_CMD.match(line)
            if not m:
                continue
            cmd = m.group(1).upper()
            if cmd not in ("G0","G00"):
                # still update modal XY if present, because it affects next G0 distance
                xy = parse_xy(line)
                if "X" in xy: x = xy["X"]
                if "Y" in xy: y = xy["Y"]
                continue

            xy = parse_xy(line)
            nx = xy.get("X", x)
            ny = xy.get("Y", y)

            if x is not None and y is not None and nx is not None and ny is not None:
                d = math.hypot(nx - x, ny - y)
                g0_dist += d
                g0_count += 1

            x, y = nx, ny

    g0_time_s = g0_dist / (rapid_mm_min / 60.0)
    print(f"G0 moves: {g0_count}")
    print(f"G0 distance: {g0_dist:.1f} mm")
    print(f"G0 time @ {rapid_mm_min:.0f} mm/min: {g0_time_s:.2f} s ({g0_time_s/60:.2f} min)")

if __name__ == "__main__":
    audit_g0(Path(sys.argv[1]).resolve(), rapid_mm_min=10000.0)