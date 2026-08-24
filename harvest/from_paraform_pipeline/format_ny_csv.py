import json
import csv

with open('/Users/yashbhavsar/Code/job-search-help/tmp/ny_roles.json', 'r') as f:
    data = json.load(f)

if 'result' in data:
    roles = data['result']['data']['json']
elif isinstance(data, list) and len(data) > 0 and 'result' in data[0]:
    roles = data[0]['result']['data']['json']
else:
    roles = data

output_csv = '/Users/yashbhavsar/Code/job-search-help/paraform_ny_roles.csv'
with open(output_csv, 'w', newline='', encoding='utf-8') as csvfile:
    writer = csv.writer(csvfile)
    writer.writerow(['ID', 'Company', 'Title', 'Salary Min', 'Salary Max', 'Location', 'Visa Text'])
    
    for r in roles:
        role_id = r.get('id', '')
        company = r.get('company', {}).get('name', '') if isinstance(r.get('company'), dict) else ''
        title = r.get('name', '')
        salary_min = r.get('salaryLowerBound', '')
        salary_max = r.get('salaryUpperBound', '')
        visa = r.get('visa_text', '')
        
        locs = r.get('locations', [])
        if isinstance(locs, list):
            loc_str = ', '.join([loc.get('name', '') for loc in locs if isinstance(loc, dict)])
        else:
            loc_str = str(locs)
            
        writer.writerow([role_id, company, title, salary_min, salary_max, loc_str, visa])
        
print(f"CSV generated with {len(roles)} NY roles.")
