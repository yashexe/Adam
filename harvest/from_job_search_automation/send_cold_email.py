import smtplib
from email.message import EmailMessage
import sys
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

if not GMAIL_USER or not GMAIL_APP_PASSWORD:
    print("Error: Missing GMAIL_USER or GMAIL_APP_PASSWORD in .env file.")
    sys.exit(1)

# Configuration
target_email = "founder@example.com"
resume_path = "/Users/yashbhavsar/Downloads/Yash_Bhavsar_Resume_08192026.pdf"

if not os.path.exists(resume_path):
    print(f"Error: Resume file not found at {resume_path}")
    sys.exit(1)

msg = EmailMessage()

# The HTML Email Template
html_content = """\
<html>
  <body style="font-family: Arial, sans-serif; font-size: 14px; color: #333;">
    <p>Hey [name],</p>
    <p>I saw what you're building at company-t1 and wanted to reach out. I'm a backend engineer looking for my next role, and my background is a direct fit for your team.</p>
    <p>I built our company's production financial data platform from the first commit. I own the entire lifecycle: the core APIs, the database scaling, and the LLM classification engines that normalize client data. I also run the customer integrations, embedding with client teams to hook up platforms like PointClickCare and SAP. I want to build at a place that takes engineering quality seriously and handles real transaction scale.</p>
    <p>I've attached my resume. Let me know if you have 10 minutes for a quick call this week.</p>
    <br>
    <p>Best,<br>Yash Bhavsar</p>
    <p style="color: #666;">-- <br>
    <b>Yash Bhavsar</b><br>
    Software Engineer<br>
    <a href="https://www.linkedin.com/in/yash-bhav"><img src="https://cdn-icons-png.flaticon.com/16/174/174857.png" alt="linkedin icon" style="vertical-align: middle; margin-right: 5px; text-decoration: none; border: none;"></a>
    <a href="https://github.com/yashexe"><img src="https://cdn-icons-png.flaticon.com/16/25/25231.png" alt="github icon" style="vertical-align: middle; margin-left: 5px; margin-right: 5px; text-decoration: none; border: none;"></a><br>
    Phone: +1 647-774-3765<br>
    Email: <a href="mailto:yashbhavsar3602@gmail.com" style="color: #1155cc;">yashbhavsar3602@gmail.com</a><br>
    Portfolio: <a href="https://yashexe.github.io" style="color: #1155cc;">yashexe.github.io</a>
    </p>
  </body>
</html>
"""

msg.set_content("Please enable HTML to view this email.")
msg.add_alternative(html_content, subtype='html')

msg['Subject'] = "Scaling ERP integrations / Finaptive Backend Engineer"
msg['From'] = f"Yash Bhavsar <{GMAIL_USER}>"
msg['To'] = target_email

# Attach the PDF resume
with open(resume_path, 'rb') as f:
    pdf_data = f.read()

msg.add_attachment(pdf_data, maintype='application', subtype='pdf', filename=os.path.basename(resume_path))

# Send the email
try:
    server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
    server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    server.send_message(msg)
    server.quit()
    print("Cold outreach email successfully sent with resume attached!")
except Exception as e:
    print(f"Failed to send email: {e}")
    sys.exit(1)
