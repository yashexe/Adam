import subprocess
import json
import csv

old_csv = '/Users/yashbhavsar/Code/job-search-help/paraform_all_jobs.csv'
try:
    with open(old_csv, 'r') as f:
        reader = csv.reader(f)
        old_count = sum(1 for row in reader) - 1 # subtract header
except Exception:
    old_count = 1118

cookie_str = 'PARAFORM_COOKIE_REDACTED'

url = "https://www.paraform.com/api/trpc/applicantUser.getAllRoles?input=%7B%22json%22%3A%7B%22query%22%3A%22%22%2C%22location%22%3A%5B%5D%2C%22workplace%22%3A%5B%5D%2C%22role_type%22%3A%5B%5D%2C%22industry%22%3A%5B%5D%2C%22posted_at%22%3A%7B%22min%22%3Anull%2C%22max%22%3Anull%7D%2C%22tech_stack%22%3A%5B%5D%2C%22size%22%3A%5B%5D%2C%22salary%22%3A%7B%22min%22%3Anull%2C%22max%22%3Anull%7D%2C%22yoe_experience%22%3A%7B%22min%22%3Anull%2C%22max%22%3Anull%7D%2C%22visa%22%3A%5B%5D%2C%22investors%22%3A%5B%5D%2C%22last_funding_round%22%3A%5B%5D%7D%7D"

curl_cmd = [
    'curl', '-s', '--url', url,
    '-H', 'accept: */*',
    '-H', 'accept-language: en-US,en;q=0.9',
    '-b', cookie_str,
    '-H', 'priority: u=1, i',
    '-H', 'referer: https://www.paraform.com/applicant',
    '-H', 'user-agent: Mozilla/5.0'
]

result = subprocess.run(curl_cmd, capture_output=True, text=True)
try:
    data = json.loads(result.stdout)
    if 'result' in data:
        roles = data['result']['data']['json']
    elif isinstance(data, list) and len(data) > 0 and 'result' in data[0]:
        roles = data[0]['result']['data']['json']
    else:
        roles = data
        
    new_count = len(roles)
    print(f"OLD COUNT: {old_count}")
    print(f"NEW COUNT: {new_count}")
except Exception as e:
    print(f"Error parsing: {e}")
    print(result.stdout[:200])
