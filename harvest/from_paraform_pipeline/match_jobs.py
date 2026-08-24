import csv
import json
import os
import re

# Keywords extracted directly from your resume
RESUME_KEYWORDS = [
    "python", "javascript", "typescript", "html", "css", "c++", "php",
    "flask", "celery", "pandas", "pydantic", "pytorch", "asyncio", "react", "claude", "llm", "ai",
    "postgresql", "postgres", "sql", "redis", "mongodb", "mysql",
    "azure", "docker", "github actions", "ci/cd", "linux",
    "distributed systems", "data modeling", "pipeline", "observability", "etl", "data ingestion"
]

input_csv = '/Users/yashbhavsar/Code/job-search-help/paraform_ny_roles_detailed.csv'
raw_dir = '/Users/yashbhavsar/Code/job-search-help/tmp/ny_roles_raw'
out_md = '/Users/yashbhavsar/Code/job-search-help/tmp/resume_keyword_matches.md'

roles = []
with open(input_csv, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        roles.append(row)

scored_roles = []
exclude_titles = ['manager', 'director', 'vp', 'head', 'staff', 'principal', 'sales', 'marketing', 'legal', 'counsel', 'attorney', 'recruiter', 'designer']

for role in roles:
    title_lower = role['Title'].lower()
    
    # Exclude highly senior or non-engineering roles
    if any(ex in title_lower for ex in exclude_titles):
        continue
        
    score = 0
    matched_keywords = set()
    
    # Load raw description
    role_id = role['ID']
    desc_file = os.path.join(raw_dir, f"{role_id}_desc.json")
    
    if os.path.exists(desc_file):
        with open(desc_file, 'r') as f:
            try:
                data = json.load(f)
                desc = data.get('description', '').lower()
                tech_stack_raw = data.get('tech_stack', [])
                tech_stack = [t.lower() for t in tech_stack_raw]
                
                # Check tech stack and description against resume keywords
                text_to_search = desc + " " + " ".join(tech_stack)
                
                for kw in RESUME_KEYWORDS:
                    # Use regex for word boundaries to avoid partial matches like 'css' in 'access'
                    pattern = r'\b' + re.escape(kw) + r'\b'
                    if re.search(pattern, text_to_search):
                        matched_keywords.add(kw)
                        
                score = len(matched_keywords)
                
                # Penalize high YoE
                yoe_min = data.get('years_experience_min')
                if yoe_min:
                    try:
                        yoe = int(yoe_min)
                        if yoe > 4:
                            score -= 5 # Heavy penalty for needing a lot of experience
                    except ValueError:
                        pass
                        
            except json.JSONDecodeError:
                pass
                
    if score > 0:
        scored_roles.append({
            'role': role,
            'score': score,
            'matches': list(matched_keywords)
        })

# Sort by highest score
scored_roles.sort(key=lambda x: x['score'], reverse=True)

with open(out_md, 'w') as f:
    f.write("# Top Roles by Exact Resume Keyword Match\n\n")
    
    for i, item in enumerate(scored_roles[:15]):
        r = item['role']
        f.write(f"### {i+1}. {r['Company']} - {r['Title']} (Keyword Matches: {len(item['matches'])})\n")
        f.write(f"- **Salary:** {r['Salary Min']} - {r['Salary Max']}\n")
        f.write(f"- **Matched Keywords:** {', '.join(sorted(item['matches'])).title()}\n\n")

print(f"Extraction complete! Top keyword matches saved to {out_md}")
