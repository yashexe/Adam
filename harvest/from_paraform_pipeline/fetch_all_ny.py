import csv
import subprocess
import json
import time
import random
import urllib.parse
import os

input_csv = '/Users/yashbhavsar/Code/job-search-help/paraform_ny_roles.csv'
output_dir = '/Users/yashbhavsar/Code/job-search-help/tmp/ny_roles_raw'
os.makedirs(output_dir, exist_ok=True)

cookie_str = 'PARAFORM_COOKIE_REDACTED'

roles = []
with open(input_csv, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        roles.append(row)

print(f"Total roles to process: {len(roles)}")

def run_curl(url):
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
        return result.stdout
    return None

success_count = 0
for i, role in enumerate(roles):
    role_id = role['ID']
    desc_file = os.path.join(output_dir, f"{role_id}_desc.json")
    interview_file = os.path.join(output_dir, f"{role_id}_interview.json")
    
    # Check if we already fetched these to allow resume
    if os.path.exists(desc_file) and os.path.exists(interview_file):
        success_count += 1
        continue
        
    print(f"[{i+1}/{len(roles)}] Fetching {role['Company']} - {role['Title']} ({role_id})")
    
    # 1. Fetch Public Role Details
    if not os.path.exists(desc_file):
        input_obj = {"json": {"role_id": role_id}}
        input_str = urllib.parse.quote(json.dumps(input_obj))
        desc_url = f"https://www.paraform.com/api/trpc/role.getPublicRoleById?input={input_str}"
        
        stdout = run_curl(desc_url)
        if stdout:
            try:
                data = json.loads(stdout)
                json_data = data.get('result', {}).get('data', {}).get('json', {})
                with open(desc_file, 'w') as f:
                    json.dump(json_data, f, indent=2)
            except json.JSONDecodeError:
                pass
        time.sleep(random.uniform(2, 3))
        
    # 2. Fetch Interview Timeline
    if not os.path.exists(interview_file):
        interview_obj = {"json": {"role_id": role_id, "interviewing_only": True, "is_recruiter_or_manager": True}}
        interview_str = urllib.parse.quote(json.dumps(interview_obj))
        interview_url = f"https://www.paraform.com/api/trpc/interviewStage.getInterviewPlanByRoleId?input={interview_str}"
        
        stdout = run_curl(interview_url)
        if stdout:
            try:
                data = json.loads(stdout)
                json_data = data.get('result', {}).get('data', {}).get('json', {})
                with open(interview_file, 'w') as f:
                    json.dump(json_data, f, indent=2)
            except json.JSONDecodeError:
                pass
        time.sleep(random.uniform(2, 3))

print("Finished fetching all NY roles.")
