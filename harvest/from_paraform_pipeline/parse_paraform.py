import sys
sys.path.insert(0, '/Users/yashbhavsar/Code/job-search-help/tmp/deps')

from bs4 import BeautifulSoup
import json
import re

with open('/Users/yashbhavsar/Code/job-search-help/paraform.html', 'r', encoding='utf-8') as f:
    html = f.read()

soup = BeautifulSoup(html, 'html.parser')

jobs = []
# Find all divs that look like rows. They have classes flex, hover:bg-gray-100, cursor-pointer
rows = soup.find_all('div', class_=lambda c: c and 'flex' in c and 'hover:bg-gray-100' in c and 'cursor-pointer' in c)

for row in rows:
    # Each row has columns (usually also divs with border-r or just direct children)
    cols = row.find_all('div', recursive=False)
    if len(cols) >= 4:
        company = cols[0].get_text(separator=" ", strip=True)
        role = cols[1].get_text(separator=" ", strip=True)
        location = cols[2].get_text(separator=" ", strip=True)
        salary = cols[3].get_text(separator=" ", strip=True)
        # some nested divs might duplicate text, so we can clean it up
        jobs.append({
            "company": company,
            "role": role,
            "location": location,
            "salary": salary
        })

# Clean up duplicated text in role and location (e.g. "Founding Engineer Founding Engineer")
def dedup(text):
    words = text.split()
    half = len(words) // 2
    if half > 0 and words[:half] == words[half:]:
        return " ".join(words[:half])
    return text

for j in jobs:
    j["role"] = dedup(j["role"])
    j["location"] = dedup(j["location"])

with open('/Users/yashbhavsar/Code/job-search-help/tmp/jobs.json', 'w') as f:
    json.dump(jobs, f, indent=2)

print(f"Extracted {len(jobs)} jobs.")
