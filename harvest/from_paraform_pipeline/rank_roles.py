import csv
import json
import os

input_csv = '/Users/yashbhavsar/Code/job-search-help/paraform_ny_roles_detailed.csv'
raw_dir = '/Users/yashbhavsar/Code/job-search-help/tmp/ny_roles_raw'

user_skills = ['python', 'javascript', 'typescript', 'c++', 'react', 'flask', 'celery', 'pytorch', 'postgres', 'postgresql', 'sql', 'redis', 'azure', 'docker', 'ci/cd', 'etl', 'machine learning', 'ai']
user_titles = ['software engineer', 'backend engineer', 'full stack engineer', 'forward deployed engineer', 'ai engineer', 'machine learning engineer', 'data engineer', 'founding engineer']
exclude_titles = ['manager', 'director', 'vp', 'head', 'staff', 'principal', 'sales', 'marketing', 'legal', 'counsel', 'attorney', 'recruiter', 'designer', 'account executive', 'chief', 'paralegal', 'business development', 'product manager']

roles = []
with open(input_csv, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        roles.append(row)

scored_roles = []

for role in roles:
    title_lower = role['Title'].lower()
    
    # Exclude non-engineering or highly senior roles
    if any(ex in title_lower for ex in exclude_titles):
        continue
        
    score = 0
    reasons = []
    
    # 1. Title match
    if any(t in title_lower for t in user_titles):
        score += 10
        if 'senior' in title_lower:
            score -= 5 # user has ~1 yoe full time
            reasons.append("Senior title (might be a stretch, but startups are flexible)")
        else:
            score += 5
            reasons.append("Perfect title match")
            
    # Load raw description to check for skills and YoE
    role_id = role['ID']
    desc_file = os.path.join(raw_dir, f"{role_id}_desc.json")
    
    if os.path.exists(desc_file):
        with open(desc_file, 'r') as f:
            try:
                data = json.load(f)
                desc = data.get('description', '').lower()
                yoe_min = data.get('years_experience_min')
                
                # Penalize high YoE
                if yoe_min:
                    try:
                        yoe = int(yoe_min)
                        if yoe > 4:
                            score -= 10
                        elif yoe <= 3:
                            score += 5
                            reasons.append(f"Requires {yoe} YoE (matches your profile)")
                    except ValueError:
                        pass
                        
                # Check tech stack overlap
                tech_stack_raw = data.get('tech_stack', [])
                tech_stack = [t.lower() for t in tech_stack_raw]
                
                matched_skills = []
                for skill in user_skills:
                    if skill in tech_stack or skill in desc:
                        matched_skills.append(skill)
                        
                if matched_skills:
                    score += len(set(matched_skills)) * 2
                    reasons.append(f"Tech stack overlap: {', '.join(set(matched_skills)).title()}")
                    
                # AI / ETL overlap
                if 'etl' in desc or 'pipeline' in desc:
                    score += 5
                    reasons.append("Mentions ETL/Pipelines (strong match for your Finaptive experience)")
                if 'llm' in desc or 'claude' in desc or 'openai' in desc or 'generative' in desc:
                    score += 5
                    reasons.append("Mentions LLMs/Generative AI (strong match for your ML/AI projects)")
                    
            except json.JSONDecodeError:
                pass
                
    if score > 0:
        scored_roles.append({
            'role': role,
            'score': score,
            'reasons': reasons
        })

# Sort by score descending
scored_roles.sort(key=lambda x: x['score'], reverse=True)

# Generate markdown output
out_md = '/Users/yashbhavsar/Code/job-search-help/tmp/top_relevant_roles.md'
with open(out_md, 'w') as f:
    f.write("# Top Relevant NY Roles for Yash Bhavsar\n\n")
    f.write("Based on your resume (Software Engineer, ~1-2 YoE, Python/Celery/Postgres/AI, ETL pipelines), here are the most relevant roles out of the 442 New York roles:\n\n")
    
    for i, item in enumerate(scored_roles[:15]):
        r = item['role']
        f.write(f"### {i+1}. {r['Company']} - {r['Title']} (Score: {item['score']})\n")
        f.write(f"- **Salary:** {r['Salary Min']} - {r['Salary Max']}\n")
        f.write(f"- **Location:** {r['Location']}\n")
        f.write(f"- **Why it's a match:**\n")
        for reason in item['reasons']:
            f.write(f"  - {reason}\n")
        f.write("\n")
        
print(f"Evaluated roles, saved top matches to {out_md}")
