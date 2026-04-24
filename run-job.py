import subprocess
import sys
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

# indices
ESTIMATOR = "1"
VISUALIZER = "2"

def load_job(job_path: str) -> dict:
    with open((REPO_ROOT / job_path).resolve(), "r", encoding="utf-8") as f:
        return json.load(f)

def derive_output_gcode(input_svg: str) -> Path:
    base = Path(input_svg).stem
    return (REPO_ROOT / "output" / f"{base}.gcode").resolve()

def parse_modes(argv) -> set:
    """
    Modes are optional numeric args after job_path.
    1 = estimator
    2 = visualizer
    If none provided => run both.
    """
    modes = {a for a in argv if a in (ESTIMATOR, VISUALIZER)}
    if not modes:
        modes = {ESTIMATOR, VISUALIZER}
    return modes

def main():
    # usage:
    #   python3 run-job.py jobs/job.json
    #   python3 run-job.py jobs/job.json 1
    #   python3 run-job.py jobs/job.json 2
    #   python3 run-job.py jobs/job.json 1 2
    job_path = sys.argv[1] if len(sys.argv) > 1 else "jobs/job.json"
    modes = parse_modes(sys.argv[2:])

    # 0) Always slice (generate gcode)
    subprocess.check_call([sys.executable, "slice-by-distance.py", job_path])

    # Resolve gcode path once
    job = load_job(job_path)
    gcode_path = derive_output_gcode(job["input_svg"])

    # 1) Estimation
    if ESTIMATOR in modes:
        subprocess.check_call([sys.executable, "estimate-time.py", job_path])

    # 2) Visualization
    if VISUALIZER in modes:
        subprocess.check_call([sys.executable, "visualize-gcode.py", str(gcode_path)])

if __name__ == "__main__":
    main()