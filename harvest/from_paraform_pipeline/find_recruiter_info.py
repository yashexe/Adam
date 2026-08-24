import os
import json

raw_dir = '/Users/yashbhavsar/Code/job-search-help/tmp/ny_roles_raw'

recruiter_findings = {}

for filename in os.listdir(raw_dir):
    if not filename.endswith('_desc.json'):
        continue
        
    with open(os.path.join(raw_dir, filename), 'r') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            continue
            
    role_id = filename.split('_')[0]
    
    # Check for anything interesting
    role_questions = data.get('role_question', [])
    recruiter_only_qs = [q['question'] for q in role_questions if q.get('recruiter_only') is True]
    
    selling_points = data.get('role_selling_points', [])
    company_selling = data.get('company_selling_points', [])
    
    if recruiter_only_qs or selling_points or company_selling:
        recruiter_findings[role_id] = {
            'company': data.get('company', {}).get('name', 'Unknown'),
            'title': data.get('name', 'Unknown'),
            'recruiter_only_questions': recruiter_only_qs,
            'role_selling_points': [sp.get('title') for sp in selling_points] if selling_points else [],
            'company_selling_points': [sp.get('title') for sp in company_selling] if company_selling else []
        }

out_file = '/Users/yashbhavsar/Code/job-search-help/tmp/recruiter_findings.json'
with open(out_file, 'w') as f:
    json.dump(recruiter_findings, f, indent=2)
    
print(f"Found {len(recruiter_findings)} roles with recruiter-specific info or selling points.")
