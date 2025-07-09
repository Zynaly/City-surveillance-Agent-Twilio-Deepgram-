import json
from datetime import datetime, timedelta
import asyncio
import requests
from typing import Dict, Any
from .business_logic import (
    get_customer,
)


async def find_customer(params):
    """Look up a customer by phone, email, or ID."""
    phone = params.get("phone")
    email = params.get("email")
    customer_id = params.get("customer_id")

    result = await get_customer(phone=phone, email=email, customer_id=customer_id)
    return result

async def email_verification(params):
    """Verify if an email address is authorized. Also consider digit as digit not a words like 7 should be imprinted as "7" not seven. """
    email = params.get("email")
    authorized_emails = ["majinibu2@gmail.com", "ibrahim@7kctech.com", "bsf2005401@ue.edu.pk"]

    if not email:
        return {"error": "Please provide an email address."}

    if email not in authorized_emails:
        return {"error": f"Email '{email}' is not recognized or authorized."}

    return {"message": f"Email '{email}' is verified and authorized."}

FRESHDESK_DOMAIN = "7kctech-helpdesk.freshdesk.com"  # Removed trailing slash
API_KEY = "OD0Xui3ulUeXKxTwJpUL"

async def create_freshdesk_ticket(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a new Freshdesk support ticket.

    Required Fields:
    - email: Must be a verified email address(consider any kind of numerical word an integer and show it as digit if ibrahim@sevenkctech.com is similar to ibrahim@7kctech.com ).
    - subject: Short summary of the issue.
    - description: Detailed description of the issue.
    - priority: 1 (Low), 2 (Medium), 3 (High), 4 (Urgent)
    - status: 2 (Open), 3 (Pending), 4 (Resolved), 5 (Closed)
    """
    email = params.get("email")
    subject = params.get("subject")
    description = params.get("description")
    priority = params.get("priority")
    status = params.get("status")

    authorized_emails = ["majinibu2@gmail.com", "ibrahim@7kctech.com", "bsf2005401@ue.edu.pk"]
    
    if not email:
        return {"error": "Please provide an email address."}

    if email not in authorized_emails:
        return {"error": "Email you provided is not verified in our system. Please provide a verified email."}

    if not priority:
        return {"error": "Please provide a priority level (1=Low, 2=Medium, 3=High, 4=Urgent)."}

    ticket_data = {
        "email": email,
        "subject": subject,
        "description": description,
        "priority": priority,
        "status": status
    }

    url = f"https://{FRESHDESK_DOMAIN}/api/v2/tickets"

    try:
        response = requests.post(
            url,
            auth=(API_KEY, "X"),
            headers={"Content-Type": "application/json"},
            data=json.dumps(ticket_data)
        )

        if response.status_code == 201:
            ticket = response.json()
            return {
                "message": "Ticket created successfully.",
                "ticket_id": ticket.get("id"),
                "subject": ticket.get("subject"),
                "description": ticket.get("description_text"),
                "priority": ticket.get("priority"),
                "status": ticket.get("status"),
                "created_at": ticket.get("created_at"),
                "updated_at": ticket.get("updated_at"),
                "requester_id": ticket.get("requester_id")
            }
        else:
            return {
                "error": f"Failed to create ticket: {response.status_code}",
                "details": response.text
            }
    except requests.exceptions.RequestException as e:
        return {"error": f"Request failed: {str(e)}"}

# Function definitions that will be sent to the Voice Agent API
FUNCTION_DEFINITIONS = [
    {
        "name": "find_customer",
        "description": """Look up a customer's account information. Use context clues to determine what type of identifier the user is providing:

        Customer ID formats:
        - Numbers only (e.g., '169', '42') → Format as 'CUST0169', 'CUST0042'
        - With prefix (e.g., 'CUST169', 'customer 42') → Format as 'CUST0169', 'CUST0042'
        
        Phone number recognition:
        - Standard format: '555-123-4567' → Format as '+15551234567'
        - With area code: '(555) 123-4567' → Format as '+15551234567'
        - Spoken naturally: 'five five five, one two three, four five six seven' → Format as '+15551234567'
        - International: '+1 555-123-4567' → Use as is
        - Always add +1 country code if not provided
        
        Email address recognition:
        - Spoken naturally: 'my email is john dot smith at example dot com' → Format as 'john.smith@example.com'
        - With domain: 'john@example.com' → Use as is
        - Spelled out: 'j o h n at example dot com' → Format as 'john@example.com'""",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "Customer's ID. Format as CUSTXXXX where XXXX is the number padded to 4 digits with leading zeros. Example: if user says '42', pass 'CUST0042'",
                },
                "phone": {
                    "type": "string",
                    "description": """Phone number with country code. Format as +1XXXXXXXXXX:
                    - Add +1 if not provided
                    - Remove any spaces, dashes, or parentheses
                    - Convert spoken numbers to digits
                    Example: 'five five five one two three four five six seven' → '+15551234567'""",
                },
                "email": {
                    "type": "string",
                    "description": """Email address in standard format:
                    - Convert 'dot' to '.'
                    - Convert 'at' to '@'
                    - Remove spaces between spelled out letters
                    Example: 'j dot smith at example dot com' → 'j.smith@example.com'""",
                },
            },
        },
    },
    {
    "name": "email_verification",
    "description": """Verify if the provided email address is authorized to access the system.
    
    Email recognition:
    - Spoken naturally: 'john dot smith seven at example dot com' → Format as 'john.smith7@example.com'
    - With domain: 'john@example.com' → Use as is
    - Spelled out: 'j o h n at example dot com' → Format as 'john@example.com'
    """,
    "parameters": {
        "type": "object",
        "properties": {
            "email": {
                "type": "string",
                "description": "Email address to verify. Ensure it is properly formatted (e.g., 'user@example.com').",
            }
        },
        "required": ["email"]
    }
},
{
    "name": "create_freshdesk_ticket",
    "description": """Create a support ticket in Freshdesk for issues or service requests.

    Required:
    - Verified email address (ask user to provide one if not given or not recognized)
    - Subject and description of the issue
    - Priority (1=Low, 2=Medium, 3=High, 4=Urgent)
    - Status (2=Open, 3=Pending, 4=Resolved, 5=Closed)
    """,
    "parameters": {
        "type": "object",
        "properties": {
            "email": {
                "type": "string",
                "description": "Verified email address of the requester. Must be one of the known emails."
            },
            "subject": {
                "type": "string",
                "description": "Brief summary of the issue (e.g., 'Login not working')"
            },
            "description": {
                "type": "string",
                "description": "Detailed explanation of the problem"
            },
            "priority": {
                "type": "integer",
                "description": "Priority level: 1 (Low), 2 (Medium), 3 (High), 4 (Urgent)"
            },
            "status": {
                "type": "integer",
                "description": "Ticket status: 2 (Open), 3 (Pending), 4 (Resolved), 5 (Closed)"
            }
        },
        "required": ["email", "subject", "description", "priority", "status"]
    }
}   
]

# Map function names to their implementations
FUNCTION_MAP = {
    "find_customer": find_customer,
    "email_verification": email_verification,
    "create_freshdesk_ticket": create_freshdesk_ticket,
}
