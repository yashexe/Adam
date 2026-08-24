import csv
import json
import os
import re

RESUME_KEYWORDS = [
    "python", "javascript", "typescript", "html", "css", "c++", "php",
    "flask", "celery", "pandas", "pydantic", "pytorch", "asyncio", "react", "claude",
    "postgresql", "postgres", "sql", "redis", "mongodb", "mysql",
    "azure", "docker", "github actions", "ci/cd", "linux",
    "distributed systems", "api integration", "webhooks", "data modeling", "observability", "etl", "machine learning", "nlp", "llm", "ai"
]

TARGET_COMPANIES = [
    "company-p8", "company-p9", "company-p10", "company-p11", "company-p12", "company-p13", 
    "company-p14", "company-p15", "company-p6", "company-p16", "company-p17", "company-p18",
    "company-p19", "company-p20", "company-p21", "company-p22", "company-p23", "company-p24"
]

input_csv = '/Users/yashbhavsar/Code/job-search-help/paraform_ny_roles_detailed.csv'
raw_dir = '/Users/yashbhavsar/Code/job-search-help/tmp/ny_roles_raw'

roles = []
with open(input_csv, 'r') as f:
    for row in csv.DictReader(f):
        roles.append(row)

scored_roles = []

for role in roles:
    company = role['Company'].lower()
    if not any(c in company for c in TARGET_COMPANIES):
        continue
        
    title = role['Title'].lower()
    if "account executive" in title or "sales" in title or "marketing" in title or "legal" in title:
        continue
        
    score = 0
    matched_keywords = set()
    role_id = role['ID']
    desc_file = os.path.join(raw_dir, f"{role_id}_desc.json")
    
    if os.path.exists(desc_file):
        with open(desc_file, 'r') as f:
            try:
                data = json.load(f)
                desc = data.get('description', '').lower()
                tech_stack = [t.lower() for t in data.get('tech_stack', [])]
                text = desc + " " + " ".join(tech_stack)
                for kw in RESUME_KEYWORDS:
                    if re.search(r'\b' + re.escape(kw) + r'\b', text):
                        matched_keywords.add(kw)
                score = len(matched_keywords)
                yoe_min = data.get('years_experience_min')
                if yoe_min:
                    try:
                        if int(yoe_min) > 4: score -= 3
                        if int(yoe_min) > 6: score -= 5
                    except ValueError: pass
            except: pass
            
    scored_roles.append({
        'role': role,
        'score': score,
        'matches': list(matched_keywords)
    })

scored_roles.sort(key=lambda x: x['score'], reverse=True)

with open('/Users/yashbhavsar/Code/job-search-help/tmp/cat_1_2_matches.md', 'w') as f:
    f.write("# Top Category 1 & 2 Roles for Yash\n\n")
    for i, item in enumerate(scored_roles[:15]):
        r = item['role']
        f.write(f"### {i+1}. {r['Company']} - {r['Title']} (Score: {item['score']})\n")
        f.write(f"- **Matches:** {', '.join(sorted(item['matches'])).title()}\n\n")

print("Done")
