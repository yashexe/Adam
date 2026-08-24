import csv, json, os, re

RESUME_KEYWORDS = [
    "python", "javascript", "typescript", "html", "css", "c++", "php",
    "flask", "celery", "pandas", "pydantic", "pytorch", "asyncio", "react", "claude", "llm", "ai",
    "postgresql", "postgres", "sql", "redis", "mongodb", "mysql",
    "azure", "docker", "github actions", "ci/cd", "linux",
    "distributed systems", "data modeling", "pipeline", "observability", "etl", "data ingestion"
]

input_csv = '/Users/yashbhavsar/Code/job-search-help/paraform_ny_roles_detailed.csv'
raw_dir = '/Users/yashbhavsar/Code/job-search-help/tmp/ny_roles_raw'
roles = []
with open(input_csv, 'r') as f:
    for row in csv.DictReader(f):
        roles.append(row)

scored_roles = []
exclude_titles = ['manager', 'director', 'vp', 'head', 'staff', 'principal', 'sales', 'marketing', 'legal', 'counsel', 'attorney', 'recruiter', 'designer']

for role in roles:
    title_lower = role['Title'].lower()
    if any(ex in title_lower for ex in exclude_titles): continue
    
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
                        if int(yoe_min) > 4: score -= 5
                    except ValueError: pass
            except: pass
    if score > 0:
        scored_roles.append({'role': role, 'score': score, 'matches': list(matched_keywords)})

scored_roles.sort(key=lambda x: x['score'], reverse=True)

for i, item in enumerate(scored_roles):
    if "company-p2" in item['role']['Company'].lower():
        print(f"Rank: {i+1} | Company: {item['role']['Company']} | Title: {item['role']['Title']} | Score: {item['score']} | Matches: {', '.join(item['matches'])}")
