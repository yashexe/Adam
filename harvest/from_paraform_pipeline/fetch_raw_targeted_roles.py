import csv
import subprocess
import json
import time
import random
import urllib.parse
import os

target_companies = ["company-m", "company-p2", "company-p4", "company-p3", "company-p5", "company-p6", "company-p7"]

roles_to_fetch = []
with open('/Users/yashbhavsar/Code/job-search-help/paraform_all_jobs.csv', 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        comp = row['Company'].strip().lower()
        if comp in target_companies:
            roles_to_fetch.append({
                'id': row['ID'],
                'company': row['Company'],
                'title': row['Title']
            })

output_dir = '/Users/yashbhavsar/Code/job-search-help/tmp/scraped_roles_raw'
os.makedirs(output_dir, exist_ok=True)

# Using the cookies from the original cURL
cookie_str = 'PARAFORM_COOKIE_REDACTED'

for role in roles_to_fetch:
    role_id = role['id']
    out_file = os.path.join(output_dir, f"{role_id}.json")
    
    if os.path.exists(out_file):
        continue # skip if we already fetched it
        
    print(f"Fetching raw {role['company']} - {role['title']} ({role_id})")
    
    input_obj = {"json": {"role_id": role_id}}
    input_str = urllib.parse.quote(json.dumps(input_obj))
    url = f"https://www.paraform.com/api/trpc/role.getPublicRoleById?input={input_str}"
    
    curl_cmd = [
        'curl', '-s', '--url', url,
        '-H', 'accept: */*',
        '-H', 'accept-language: en-US,en;q=0.9',
        '-b', cookie_str,
        '-H', 'priority: u=1, i',
        '-H', 'referer: https://www.paraform.com/applicant',
        '-H', 'sec-ch-ua: "Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
        '-H', 'sec-ch-ua-mobile: ?1',
        '-H', 'sec-ch-ua-platform: "Android"',
        '-H', 'sec-fetch-dest: empty',
        '-H', 'sec-fetch-mode: cors',
        '-H', 'sec-fetch-site: same-origin',
        '-H', 'user-agent: Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36'
    ]
    
    result = subprocess.run(curl_cmd, capture_output=True, text=True)
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            json_data = data.get('result', {}).get('data', {}).get('json', {})
            with open(out_file, 'w') as f:
                json.dump(json_data, f, indent=2)
        except json.JSONDecodeError:
            print("Failed to parse JSON for", role_id)
    else:
        print("cURL command failed for", role_id)
        
    time.sleep(random.uniform(2, 4))
    
print("Finished fetching raw targeted roles.")
