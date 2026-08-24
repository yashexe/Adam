import os
import json
import csv

input_dir = '/Users/yashbhavsar/Code/job-search-help/tmp/scraped_roles_raw'
output_dir = '/Users/yashbhavsar/Code/job-search-help/tmp/scraped_roles_rich'
os.makedirs(output_dir, exist_ok=True)

# Helper function to extract text cleanly
def clean_html(text):
    if not text:
        return ""
    # Just a very basic replacement for markdown since we don't have bs4 in this script context
    return text.replace('<p>', '').replace('</p>', '\n\n').replace('<ul>', '').replace('</ul>', '').replace('<li>', '- ').replace('</li>', '\n')

roles_metadata = {}
with open('/Users/yashbhavsar/Code/job-search-help/paraform_all_jobs.csv', 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        roles_metadata[row['ID']] = row

all_md_content = ""

for filename in os.listdir(input_dir):
    if not filename.endswith('.json'):
        continue
        
    role_id = filename.replace('.json', '')
    with open(os.path.join(input_dir, filename), 'r') as f:
        data = json.load(f)
        
    meta = roles_metadata.get(role_id, {})
    company_name = meta.get('Company', 'Unknown Company')
    title = meta.get('Title', data.get('name', 'Unknown Title'))
    
    md = f"# {company_name} - {title}\n\n"
    
    # Salary & Equity
    sal_min = data.get('salaryLowerBound')
    sal_max = data.get('salaryUpperBound')
    if sal_min and sal_max:
        md += f"**Salary:** ${sal_min//1000}K - ${sal_max//1000}K\n"
    elif meta.get('Salary Min'):
        md += f"**Salary:** {meta['Salary Min']} - {meta['Salary Max']}\n"
        
    if data.get('equity'):
        md += f"**Equity:** {data['equity']}\n"
        
    # Location & Policy
    md += f"**Location:** {', '.join(data.get('locations', []))}\n"
    if data.get('workPlaceText'):
        md += f"**Work Policy:** {data['workPlaceText']}\n"
    if data.get('employmentType'):
        md += f"**Employment Type:** {data['employmentType']}\n"
    if data.get('visa_text'):
        md += f"**Visa Sponsorship:** {data['visa_text']}\n"
        
    md += "\n"
    
    # Description
    if data.get('description'):
        md += "## About this role\n"
        md += clean_html(data['description']) + "\n\n"
        
    if data.get('experience_info'):
        md += f"**Ideal Candidate:** {data['experience_info']} ({data.get('years_experience_min', '0')}+ years)\n\n"
        
    # Responsibilities
    resps = data.get('responsibilities', [])
    if resps:
        md += "## Role responsibilities\n"
        for r in resps:
            md += f"- {r}\n"
        md += "\n"
        
    # Benefits
    benefits = data.get('benefits', [])
    if benefits:
        md += "## Employee benefits\n"
        for b in benefits:
            md += f"- {b}\n"
        md += "\n"
        
    # Tech Stack
    tech = data.get('tech_stack', [])
    if tech:
        md += "## Tech stack\n"
        md += ", ".join(tech) + "\n\n"
        
    # Company Info
    comp_info = data.get('company', {})
    if comp_info:
        md += f"## About {company_name}\n"
        if comp_info.get('description'):
            md += clean_html(comp_info['description']) + "\n\n"
            
        md += f"- **Team size:** {comp_info.get('size', 'Unknown')}\n"
        md += f"- **Founded:** {comp_info.get('foundingYear', 'Unknown')}\n"
        md += f"- **Total funding:** {comp_info.get('fundingAmount', 'Unknown')}\n"
        md += f"- **Website:** {comp_info.get('websiteUrl', 'Unknown')}\n\n"
        
        if comp_info.get('teamAbout'):
            md += "### About the team\n"
            md += clean_html(comp_info['teamAbout']) + "\n\n"
            
    # Note on Interview Process
    md += "*(Note: The 'Interview Process' section might not be fully present in this JSON, but all available details are included)*\n\n"
    md += "---\n\n"
    
    out_file = os.path.join(output_dir, f"{company_name.replace(' ', '_')}_{role_id}.md")
    with open(out_file, 'w') as f:
        f.write(md)
        
    all_md_content += md

# Save combined artifact
with open('/Users/yashbhavsar/artifacts/scraped_roles_details_rich.md', 'w') as f:
    f.write(all_md_content)

print(f"Generated rich markdown for {len(os.listdir(output_dir))} roles.")
