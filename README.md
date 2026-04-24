# Machine Job Estimator

A small Python tool for preparing and estimating machine drawing jobs from SVG files. The pipeline reads an SVG file, converts it into G-code, and estimates basic machine job information based on the generated output.

## How to run

1. Put your source SVG file into the `input/` folder.
2. Open or duplicate one of the JSON files in the `jobs/` folder.
3. In the JSON file, update the SVG path so it points to your input SVG file
4. In terminal python run-job.py jobs/your-job-file.json

## Run Specific operations
1. run time estimator and visualizer: python run-job.py jobs/your-job-file.json
2. run time estimator only: python run-job.py jobs/your-job-file.json 1
3. run visualizer only: python run-job.py jobs/your-job-file.json 2
