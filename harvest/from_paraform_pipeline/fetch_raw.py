import subprocess
import json
import urllib.parse
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

role_id = "ROLE_ID_REDACTED"
cookie_str = os.getenv("PARAFORM_COOKIE")
if not cookie_str:
    raise RuntimeError("Set PARAFORM_COOKIE in .env (see .env.example)")

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
    data = json.loads(result.stdout)
    json_data = data.get('result', {}).get('data', {}).get('json', {})
    
    with open('/Users/yashbhavsar/Code/job-search-help/tmp/company-m_raw.json', 'w') as f:
        json.dump(json_data, f, indent=2)
    print("Saved to company-m_raw.json")
else:
    print("Failed")
