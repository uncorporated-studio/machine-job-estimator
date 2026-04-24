import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET

# ===== Repo root =====
REPO_ROOT = Path(__file__).resolve().parent


# --- regex helpers ---
RE_CMD = re.compile(r'^\s*([GMT]\d+)\b', re.IGNORECASE)
RE_F = re.compile(r'\bF(-?\d+(?:\.\d+)?)\b', re.IGNORECASE)
RE_XYZ = re.compile(r'\b([XYZ])(-?\d+(?:\.\d+)?)\b', re.IGNORECASE)
RE_G4_S = re.compile(r'^\s*G4\b.*\bS(-?\d+(?:\.\d+)?)\b', re.IGNORECASE)
RE_M98 = re.compile(r'^\s*M98\b.*\bP"([^"]+)"', re.IGNORECASE)

@dataclass
class Estimate:
    motion_seconds: float = 0.0
    dwell_seconds: float = 0.0
    macro_seconds: float = 0.0
    macro_counts: Optional[dict] = None
    total_motion_mm: float = 0.0   
    drawn_mm: float = 0.0
    stroke_count: int = 0
    z_only_mm: float = 0.0
    z_only_seconds: float = 0.0     

    def total(self) -> float:
        return self.motion_seconds + self.dwell_seconds + self.macro_seconds


def resolve_repo_path(p: str) -> Path:
    return (REPO_ROOT / p).resolve()


def derive_output_gcode(input_svg: str) -> Path:
    base = Path(input_svg).stem
    return (REPO_ROOT / "output" / f"{base}.gcode").resolve()


def load_job(job_path: str) -> dict:
    job_file = resolve_repo_path(job_path)
    with open(job_file, "r", encoding="utf-8") as f:
        return json.load(f)


def strip_comment(line: str) -> str:
    if ';' in line:
        line = line.split(';', 1)[0]
    return line.strip()


def parse_xyz(line: str) -> dict:
    out = {}
    for ax, val in RE_XYZ.findall(line):
        out[ax.upper()] = float(val)
    return out


def parse_feedrate(line: str) -> Optional[float]:
    m = RE_F.search(line)
    return float(m.group(1)) if m else None


def format_hms(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"

def estimate_time_from_gcode(
    gcode_path: Path,
    speed_factor: float,
    macro_seconds_map: dict,
    default_feedrate_mm_min: Optional[float] = None,
    rapid_mm_min: Optional[float] = None,
) -> Estimate:
    est = Estimate(macro_counts={})

    # modal state
    x = y = z = None
    feed_mm_min = default_feedrate_mm_min

    def add_macro(path: str):
        base = macro_seconds_map.get(path)
        if base is None:
            return
        est.macro_counts[path] = est.macro_counts.get(path, 0) + 1
        est.macro_seconds += (float(base) / max(speed_factor, 1e-9))

    with open(gcode_path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = strip_comment(raw)
            if not line:
                continue

            # dwell
            m = RE_G4_S.match(line)
            if m:
                est.dwell_seconds += float(m.group(1))
                continue

            # macro
            m = RE_M98.match(line)
            if m:
                add_macro(m.group(1))
                continue

            # feedrate updates (modal)
            f_val = parse_feedrate(line)
            if f_val is not None:
                feed_mm_min = f_val

            cmdm = RE_CMD.match(line)
            if not cmdm:
                continue
            cmd = cmdm.group(1).upper()

            if cmd not in ("G0", "G00", "G1", "G01"):
                continue

            coords = parse_xyz(line)
            new_x = coords.get("X", x)
            new_y = coords.get("Y", y)
            new_z = coords.get("Z", z)

            if new_x == x and new_y == y and new_z == z:
                x, y, z = new_x, new_y, new_z
                continue

            dx = 0.0 if (x is None or new_x is None) else (new_x - x)
            dy = 0.0 if (y is None or new_y is None) else (new_y - y)
            dz = 0.0 if (z is None or new_z is None) else (new_z - z)

            dist = math.hypot(math.hypot(dx, dy), dz)
            xy_dist = math.hypot(dx, dy)

            # Choose speed for THIS move
            if cmd in ("G0", "G00") and rapid_mm_min is not None:
                speed_mm_min = rapid_mm_min
            else:
                speed_mm_min = feed_mm_min if feed_mm_min else default_feedrate_mm_min

            if speed_mm_min is None or speed_mm_min <= 0:
                x, y, z = new_x, new_y, new_z
                continue

            eff_mm_min = speed_mm_min * speed_factor
            eff_mm_s = eff_mm_min / 60.0
            move_seconds = dist / max(eff_mm_s, 1e-9)

            # Total motion
            est.total_motion_mm += dist
            est.motion_seconds += move_seconds

            # Drawn length (XY only when pen-down on G1)
            z_after = new_z if new_z is not None else z
            is_g1 = cmd in ("G1", "G01")
            if is_g1 and z_after is not None and z_after <= 0 and xy_dist > 0:
                est.drawn_mm += xy_dist

            # NEW: Z-only moves (XY unchanged, Z changed)
            z_only = (abs(dx) < 1e-12 and abs(dy) < 1e-12 and abs(dz) > 1e-12)
            if z_only:
                est.z_only_mm += abs(dz)     # Z travel distance only
                est.z_only_seconds += move_seconds

            x, y, z = new_x, new_y, new_z

    return est

SVG_NS = {"svg": "http://www.w3.org/2000/svg"}

def count_svg_paths(svg_path: Path, skip_classes=None) -> int:
    """Counts <path> elements (Inkscape-like 'paths'). Optionally skips background classes."""
    skip_classes = skip_classes or []
    tree = ET.parse(str(svg_path))
    root = tree.getroot()

    count = 0
    for p in root.findall(".//svg:path", SVG_NS):
        klass = p.attrib.get("class", "")
        if any(cls in klass for cls in skip_classes):
            continue
        count += 1
    return count


def main():
    job_path = sys.argv[1] if len(sys.argv) > 1 else "jobs/job.json"
    job = load_job(job_path)
    
    input_svg_path = resolve_repo_path(job["input_svg"])
    skip_classes = job.get("generator", {}).get("skip_path_classes", ["svg-export-bg"])
    svg_path_count = count_svg_paths(input_svg_path, skip_classes=skip_classes)

    if "input_svg" not in job:
        raise KeyError('job.json must contain "input_svg"')

    gcode_path = derive_output_gcode(job["input_svg"])
    if not gcode_path.exists():
        raise FileNotFoundError(f"G-code not found: {gcode_path}")

    gen = job.get("generator", {})
    est_cfg = job.get("estimator", {})

    speed_factor = float(est_cfg.get("speed_factor", 1.0))
    macro_seconds_map = est_cfg.get("macro_seconds", {})
    if not isinstance(macro_seconds_map, dict):
        macro_seconds_map = {}

    default_feedrate = gen.get("feedrate_mm_min", None)
    default_feedrate = float(default_feedrate) if default_feedrate is not None else None

    rapid_mm_min = est_cfg.get("rapid_mm_min", None)
    rapid_mm_min = float(rapid_mm_min) if rapid_mm_min is not None else None
    

    est = estimate_time_from_gcode(
        gcode_path=gcode_path,
        speed_factor=speed_factor,
        macro_seconds_map=macro_seconds_map,
        default_feedrate_mm_min=default_feedrate,
        rapid_mm_min=rapid_mm_min,
    )
    
    
    print(f"\nG-code: {gcode_path}")
    
    print(f"SVG paths: {svg_path_count}")
    print(f"Drawn length:  {est.drawn_mm:.0f} mm ({est.drawn_mm/1000:.1f} m)")
    print(f"Z up/down:     {est.z_only_mm:.0f} mm ({est.z_only_mm/1000:.1f} m)") 
    print(f"Total travel:  {est.total_motion_mm:.0f} mm ({est.total_motion_mm/1000:.1f} m)")
    
    print(f"Speed factor:  {speed_factor} ({int(speed_factor*100)}%)\n")

    print(f"Motion time:   {format_hms(est.motion_seconds)}")
    print(f"Dwell time:    {format_hms(est.dwell_seconds)}")
    print(f"Service time:  {format_hms(est.macro_seconds)}")

    

    if est.macro_counts:
        print("\nSevice Ops:")
        for k, v in sorted(est.macro_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            base = macro_seconds_map.get(k, 0.0)
            print(f"  {k}: {v} × {float(base):.1f}s (scaled)")


    print(f"\nTOTAL:         {format_hms(est.total())}\n")


if __name__ == "__main__":
    main()