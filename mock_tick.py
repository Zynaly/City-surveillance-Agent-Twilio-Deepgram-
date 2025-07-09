import requests
import json
from datetime import datetime

# Freshdesk Configuration
FRESHDESK_DOMAIN = "7kctech-helpdesk.freshdesk.com"
API_KEY = "OD0Xui3ulUeXKxTwJpUL"

# Ticket Data
ticket_data = {
    "email": "safe.city@example.com",
    "subject": f"""1- Incident Type: Fire Incident Detected
2- Address: 123 Main Street, Lahore
3- Phone: 923013225853
4- Confidence Score: 95%
5- Image URL: 'https://example.com/roboi.jpg' 
6- Incident Time: 2025-06-30 11:44:08 
""",
"description": "Incident is critical",
    "priority": 4,   # Urgent
    "status": 2      # Open
}

# API Request
url = f"https://{FRESHDESK_DOMAIN}/api/v2/tickets"
headers = {"Content-Type": "application/json"}

response = requests.post(
    url,
    auth=(API_KEY, "X"),
    headers=headers,
    data=json.dumps(ticket_data)
)

# Response Handling
if response.status_code == 201:
    ticket = response.json()
    print(f"✅ Ticket created successfully: Ticket ID {ticket.get('id')}")
    print(json.dumps(ticket, indent=2))
else:
    print(f"❌ Failed to create ticket: {response.status_code} - {response.text}")



 