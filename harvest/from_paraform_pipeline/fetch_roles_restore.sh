#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a; source "$SCRIPT_DIR/../../.env"; set +a
: "${PARAFORM_COOKIE:?Set PARAFORM_COOKIE in .env (see .env.example)}"

curl --url 'https://www.paraform.com/api/trpc/applicantUser.getAllRoles?input=%7B%22json%22%3A%7B%22query%22%3A%22%22%2C%22location%22%3A%5B%5D%2C%22workplace%22%3A%5B%5D%2C%22role_type%22%3A%5B%5D%2C%22industry%22%3A%5B%5D%2C%22posted_at%22%3A%7B%22min%22%3Anull%2C%22max%22%3Anull%7D%2C%22tech_stack%22%3A%5B%5D%2C%22size%22%3A%5B%5D%2C%22salary%22%3A%7B%22min%22%3Anull%2C%22max%22%3Anull%7D%2C%22yoe_experience%22%3A%7B%22min%22%3Anull%2C%22max%22%3Anull%7D%2C%22visa%22%3A%5B%5D%2C%22investors%22%3A%5B%5D%2C%22last_funding_round%22%3A%5B%5D%7D%7D' \
  -H 'accept: */*' \
  -H 'accept-language: en-US,en;q=0.9' \
  -b "$PARAFORM_COOKIE" \
  -H 'priority: u=1, i' \
  -H 'referer: https://www.paraform.com/applicant' \
  -H 'sec-ch-ua: "Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"' \
  -H 'sec-ch-ua-mobile: ?1' \
  -H 'sec-ch-ua-platform: "Android"' \
  -H 'sec-fetch-dest: empty' \
  -H 'sec-fetch-mode: cors' \
  -H 'sec-fetch-site: same-origin' \
  -H 'user-agent: Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36' > /Users/yashbhavsar/Code/job-search-help/tmp/getAllRoles.json
