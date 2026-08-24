import json
import os

with open("/Users/yashbhavsar/Code/job-search-help/tmp/jobs.json") as f:
    jobs = json.load(f)

md = "# Extracted Jobs\n\n"
md += f"Here are the jobs extracted from the HTML file you provided. Since the file was a static snapshot of the page you were viewing, there are **{len(jobs)} jobs** present in this snapshot. If there is an infinite scroll on the live site, there may be more jobs not captured in this file.\n\n"
md += "| Company | Role | Location | Salary |\n"
md += "|---------|------|----------|--------|\n"

for j in jobs:
    md += f"| {j['company']} | {j['role']} | {j['location']} | {j['salary']} |\n"

artifact_path = "/Users/yashbhavsar/artifacts/extracted_jobs.md"
with open(artifact_path, "w") as f:
    f.write(md)
