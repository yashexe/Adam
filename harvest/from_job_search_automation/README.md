# Agent Instructions: Job Search Automation

This directory contains the tools and context needed to automate Yash Bhavsar's cold outreach to CTOs in the FinTech/AR/AP automation space. **The user expects YOU (the AI agent) to execute this process on their behalf.**

## How to Execute an Outreach Request
When the user asks you to reach out to a company (e.g., "Draft and send an email to company-t7"):
1. **Find the CTO:** Use your `search_web` tool to find the name of the CTO or Head of Engineering at the target company.
2. **Modify the Script:** Read `/Users/yashbhavsar/Code/job_search_automation/send_cold_email.py`. Modify the `target_email` variable (you can use yashbhavsar3602@gmail.com for testing, or the real email if requested). Modify the `html_content` variable to customize the email body for the specific CTO and Company.
3. **Drafting Rules (CRITICAL):**
   - **Keep it brief & founder-focused.** Do not write a 5-paragraph cover letter.
   - **The Hook:** Acknowledge what the company is building (e.g., scaling cash flow pipelines, building AR AI agents).
   - **The Proof:** ALWAYS use the hardcore infrastructure metrics from the July resume. Mention building the Finaptive multi-tenant ETL platform (15+ ERP sources) and the Celery/Redis dispatcher-executor pipeline handling 5M+ records/day with exactly-once delivery. **DO NOT mention Claude Code, MCP, or Prompt Engineering.**
   - **The Close:** Frame Yash as an infrastructure/backend engineer who understands the deep technical challenges of moving high-volume financial data reliably.
4. **Send the Email:** Use your `run_command` tool to execute `python3 /Users/yashbhavsar/Code/job_search_automation/send_cold_email.py`.

## Core Strategy Context
*   **The Resume Shift (July 2026):** Yash saw a massive spike in interviews (company-r, company-n, company-t, company-q) when he stripped out AI coding tools (like Claude/MCP) from his resume and focused entirely on heavy backend distributed systems metrics. This is the technical profile we must present to employers.
*   **Target Domain:** Office of the CFO Tech, B2B FinTech, Accounts Receivable (AR) / Accounts Payable (AP) Automation, ERP Integrations.

## Target Companies
*   **AI Agentic Finance (Direct company-n Peers):** company-t7, company-t8, company-t9, company-t10, company-t11
*   **Modern AR/AP & ERP Integrations:** company-t2, company-t1, company-t3, company-t4, company-t5, company-t6
*   **Enterprise Giants:** company-t12, company-t13

## Execution Environment
*   **Script:** `/Users/yashbhavsar/Code/job_search_automation/send_cold_email.py`
*   **Credentials:** Loaded automatically from `.env` via `python-dotenv`.
*   **Resume Attachment:** Hardcoded in the script to point to `Yash_Bhavsar_Resume_07082026.pdf` located in this exact folder. Do not remove the attachment logic.
