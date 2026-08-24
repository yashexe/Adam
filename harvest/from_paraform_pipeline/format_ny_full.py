import os
import json
import csv

input_csv = '/Users/yashbhavsar/Code/job-search-help/paraform_ny_roles.csv'
raw_dir = '/Users/yashbhavsar/Code/job-search-help/tmp/ny_roles_raw'
output_md_dir = '/Users/yashbhavsar/Code/job-search-help/ny_roles_mds'
master_csv = '/Users/yashbhavsar/Code/job-search-help/paraform_ny_roles_detailed.csv'

os.makedirs(output_md_dir, exist_ok=True)

# Read metadata from the original CSV
roles_meta = {}
with open(input_csv, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        roles_meta[row['ID']] = row

def clean_html(text):
    if not text:
        return ""
    return text.replace('<p>', '').replace('</p>', '\n\n').replace('<ul>', '').replace('</ul>', '').replace('<li>', '- ').replace('</li>', '\n')

csv_headers = ['ID', 'Company', 'Title', 'Salary Min', 'Salary Max', 'Location', 'Visa Text', 'Interview Timeline', 'Tech Stack']
csv_rows = []

for role_id, meta in roles_meta.items():
    desc_file = os.path.join(raw_dir, f"{role_id}_desc.json")
    interview_file = os.path.join(raw_dir, f"{role_id}_interview.json")
    
    if not os.path.exists(desc_file):
        continue
        
    with open(desc_file, 'r') as f:
        desc_data = json.load(f)
        
    interview_data = {}
    if os.path.exists(interview_file):
        with open(interview_file, 'r') as f:
            interview_data = json.load(f)
            
    company_name = meta['Company']
    title = meta['Title']
    
    md = f"# {company_name} - {title}\n\n"
    
    # Salary & Equity
    sal_min = desc_data.get('salaryLowerBound')
    sal_max = desc_data.get('salaryUpperBound')
    if sal_min and sal_max:
        md += f"**Salary:** ${sal_min//1000}K - ${sal_max//1000}K\n"
    else:
        md += f"**Salary:** {meta['Salary Min']} - {meta['Salary Max']}\n"
        
    if desc_data.get('equity'):
        md += f"**Equity:** {desc_data['equity']}\n"
        
    md += f"**Location:** {meta['Location']}\n"
    if desc_data.get('workPlaceText'):
        md += f"**Work Policy:** {desc_data['workPlaceText']}\n"
    if desc_data.get('visa_text'):
        md += f"**Visa Sponsorship:** {desc_data['visa_text']}\n\n"
        
    # Interview Process
    stages = interview_data.get('stages', [])
    interview_str = ""
    if stages:
        md += "## Interview Process\n"
        stage_names = [s.get('name', 'Stage') for s in stages]
        for i, name in enumerate(stage_names):
            md += f"{i+1}. {name}\n"
        md += "\n"
        interview_str = " -> ".join(stage_names)
        
    # Description
    if desc_data.get('description'):
        md += "## About this role\n"
        md += clean_html(desc_data['description']) + "\n\n"
        
    # Tech Stack
    tech = desc_data.get('tech_stack', [])
    tech_str = ""
    if tech:
        md += "## Tech stack\n"
        md += ", ".join(tech) + "\n\n"
        tech_str = ", ".join(tech)
        
    # Company Info
    comp = desc_data.get('company', {})
    if comp:
        md += f"## About {company_name}\n"
        md += f"- **Team size:** {comp.get('size', 'Unknown')}\n"
        md += f"- **Founded:** {comp.get('foundingYear', 'Unknown')}\n"
        md += f"- **Total funding:** {comp.get('fundingAmount', 'Unknown')}\n\n"
        if comp.get('description'):
            md += clean_html(comp['description']) + "\n\n"
            
    # Save individual MD
    safe_comp = company_name.replace('/', '_').replace(' ', '_')
    out_file = os.path.join(output_md_dir, f"{safe_comp}_{role_id}.md")
    with open(out_file, 'w') as f:
        f.write(md)
        
    # Save to CSV rows
    csv_rows.append([
        role_id, company_name, title, meta['Salary Min'], meta['Salary Max'], 
        meta['Location'], meta['Visa Text'], interview_str, tech_str
    ])

# Save master CSV
with open(master_csv, 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(csv_headers)
    writer.writerows(csv_rows)

print(f"Successfully processed {len(csv_rows)} roles.")
