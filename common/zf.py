import json
import asyncio
import os
import time
import threading
import requests
from datetime import datetime
from typing import Dict, Any
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException
from groq import Groq

FRESHDESK_DOMAIN = os.environ.get("FRESHDESK_DOMAIN", "FRESHDESK_DOMAIN")
API_KEY = os.environ.get("API_KEY", "API_KEY")
CHROME_DRIVER_PATH = os.environ.get("CHROME_DRIVER_PATH", "path/to/chromedriver")
WHATSAPP_GROUP_NAME = os.environ.get("WHATSAPP_GROUP_NAME", "Safe City Emergency Group")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "GROQ_API_KEY")
MODEL_NAME = "llama3-8b-8192"

class GrokAI:
    """Simple Grok AI integration - let AI do what AI does best!"""

    def __init__(self):
        self.client = Groq(api_key=GROQ_API_KEY)

    async def extract_ticket_info(self, ticket_data: Dict[str, Any]) -> Dict[str, Any]:
        """Let Groq handle the extraction - simple and clean"""
        try:
            # Get the ticket text (subject + description)
            subject = ticket_data.get("subject", "")
            description = ticket_data.get("description", "") or ticket_data.get("description_text", "")
            
            ticket_text = f"Subject: {subject}\nDescription: {description}"
            print(f"[DEBUG] Sending to Groq: {ticket_text}")

            response = self.client.chat.completions.create(
                model=MODEL_NAME,
                messages=[{
                    "role": "user", 
                    "content": f"""Extract emergency info from this ticket and return ONLY clean JSON:

{ticket_text}

Return JSON with these exact keys:
{{
    "phone_number": "any numeric number written against 'phone:' key word is phone number",
    "incident_type": "fire/accident/robbery/medical/etc", 
    "address": "complete address",
    "priority": 1-4 (1=Critical, 4=Low),
    "confidence_score": 0.0-1.0,
    "image_urls": ["array of URLs"]
}}"""
                }],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=300
            )

            result = json.loads(response.choices[0].message.content)
            print(f"[DEBUG] Groq extracted: {result}")
            return result

        except Exception as e:
            print(f"[ERROR] Groq failed: {e}")
            return {"error": str(e)}


class WhatsAppBot:
    def __init__(self):
        self.driver = None
        self.lock = threading.Lock()
        self.start_driver()
        self.open_chat()

    def start_driver(self):
        try:
            options = webdriver.ChromeOptions()
            options.add_argument("--user-data-dir=F:/Hiring_Bot/chrome-data")
            options.add_argument("--remote-debugging-port=9222")
            options.add_experimental_option("detach", True)

            service = webdriver.chrome.service.Service(CHROME_DRIVER_PATH)
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.get("https://web.whatsapp.com")

            WebDriverWait(self.driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//div[@aria-label='Chat list']"))
            )
            print("[INFO] WhatsApp loaded")
        except Exception as e:
            print(f"[ERROR] WhatsApp failed: {e}")
            self.driver = None

    def open_chat(self):
        if not self.driver:
            return
        try:
            search_box = WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//div[@contenteditable='true']"))
            )
            search_box.click()
            search_box.send_keys(WHATSAPP_GROUP_NAME)
            time.sleep(2)
            search_box.send_keys(Keys.ENTER)
            time.sleep(3)
            print(f"[INFO] Opened chat: {WHATSAPP_GROUP_NAME}")
        except Exception as e:
            print(f"[ERROR] Chat open failed: {e}")

    def send_message(self, message):
        if not self.driver:
            return False
        with self.lock:
            try:
                message_box = WebDriverWait(self.driver, 20).until(
                    EC.presence_of_element_located((By.XPATH, "//footer//div[@contenteditable='true']"))
                )
                message_box.click()
                message_box.send_keys(Keys.CTRL + "a")
                message_box.send_keys(message)
                message_box.send_keys(Keys.ENTER)
                time.sleep(2)
                print("[INFO] Message sent")
                return True
            except Exception as e:
                print(f"[ERROR] Send failed: {e}")
                return False


# Global instances
whatsapp_bot = None
grok_ai = None

async def retrieve_freshdesk_ticket(params: Dict[str, Any]) -> Dict[str, Any]:
    """Retrieve ticket and let Groq extract everything"""
    global grok_ai
    if not grok_ai:
        grok_ai = GrokAI()

    ticket_id = params.get("ticket_id")
    if not ticket_id:
        return {"error": "Ticket ID required"}

    try:
        # Get ticket from Freshdesk
        url = f"https://{FRESHDESK_DOMAIN}/api/v2/tickets/{ticket_id}"
        response = requests.get(url, auth=(API_KEY, "X"))
        
        if response.status_code != 200:
            return {"error": f"Failed to get ticket: {response.status_code}"}

        ticket = response.json()
        
        # Let Groq extract everything
        extracted = await grok_ai.extract_ticket_info(ticket)
        
        if "error" in extracted:
            return extracted

        # Return clean result
        return {
            "ticket_id": ticket.get("id"),
            "phone_number": extracted.get("phone_number"),
            "incident_type": extracted.get("incident_type"),
            "address": extracted.get("address"),
            "priority": extracted.get("priority"),
            "confidence_score": extracted.get("confidence_score"),
            "image_urls": extracted.get("image_urls", []),
            "description": ticket.get("description"),
            "status": ticket.get("status")
        }

    except Exception as e:
        return {"error": f"Request failed: {str(e)}"}


async def send_whatsapp_message(params: Dict[str, Any]) -> Dict[str, Any]:
    """Send emergency alert to WhatsApp"""
    global whatsapp_bot
    if not whatsapp_bot:
        whatsapp_bot = WhatsAppBot()

    incident = params.get("incident_details", {})
    images = params.get("image_urls", [])

    message = f"""🚨 EMERGENCY ALERT 🚨
Type: {incident.get('incident_type', 'Unknown')}
Location: {incident.get('address', 'Unknown')}
Phone: {incident.get('phone_number', 'N/A')}
Priority: {incident.get('priority', 'Unknown')}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

    success = whatsapp_bot.send_message(message)
    
    # Send images if any
    for img_url in images[:3]:  # Max 3 images
        whatsapp_bot.send_message(f"📸 Evidence: {img_url}")

    return {"message": "Alert sent successfully" if success else "Failed to send alert"}


async def update_freshdesk_ticket_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """Update ticket status"""
    ticket_id = params.get("ticket_id")
    status = params.get("status")
    
    if not ticket_id or not status:
        return {"error": "Ticket ID and status required"}

    try:
        url = f"https://{FRESHDESK_DOMAIN}/api/v2/tickets/{ticket_id}"
        response = requests.put(url, auth=(API_KEY, "X"), json={"status": status})
        
        if response.status_code == 200:
            return {"message": f"Ticket {ticket_id} updated to status {status}"}
        else:
            return {"error": f"Update failed: {response.status_code}"}
    except Exception as e:
        return {"error": f"Request failed: {str(e)}"}


async def list_freshdesk_tickets(params: Dict[str, Any]) -> Dict[str, Any]:
    """List all open tickets with AI extraction"""
    global grok_ai
    if not grok_ai:
        grok_ai = GrokAI()

    try:
        url = f"https://{FRESHDESK_DOMAIN}/api/v2/tickets?filter=new_and_my_open"
        response = requests.get(url, auth=(API_KEY, "X"))
        
        if response.status_code != 200:
            return {"error": f"Failed to list tickets: {response.status_code}"}

        tickets = response.json()
        enhanced_tickets = []
        
        for ticket in tickets:
            extracted = await grok_ai.extract_ticket_info(ticket)
            enhanced_tickets.append({
                "ticket_id": ticket.get("id"),
                "subject": ticket.get("subject"),
                "phone_number": extracted.get("phone_number"),
                "incident_type": extracted.get("incident_type"),
                "address": extracted.get("address"),
                "priority": extracted.get("priority"),
                "confidence_score": extracted.get("confidence_score"),
                "status": ticket.get("status")
            })
        
        return {"tickets": enhanced_tickets}
    except Exception as e:
        return {"error": f"Request failed: {str(e)}"}


# Function definitions for external use
FUNCTION_DEFINITIONS = [
    {
        "name": "retrieve_freshdesk_ticket",
        "description": "Get ticket and extract emergency info with AI",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "Ticket ID to retrieve"}
            },
            "required": ["ticket_id"]
        }
    },
    {
        "name": "send_whatsapp_message", 
        "description": "Send emergency alert to WhatsApp group",
        "parameters": {
            "type": "object",
            "properties": {
                "incident_details": {
                    "type": "object",
                    "properties": {
                        "incident_type": {"type": "string"},
                        "address": {"type": "string"},
                        "phone_number": {"type": "string"},
                        "priority": {"type": "integer"}
                    }
                },
                "image_urls": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["incident_details"]
        }
    },
    {
        "name": "update_freshdesk_ticket_status",
        "description": "Update ticket status",
        "parameters": {
            "type": "object", 
            "properties": {
                "ticket_id": {"type": "string"},
                "status": {"type": "integer"}
            },
            "required": ["ticket_id", "status"]
        }
    }
]

FUNCTION_MAP = {
    "retrieve_freshdesk_ticket": retrieve_freshdesk_ticket,
    "send_whatsapp_message": send_whatsapp_message, 
    "update_freshdesk_ticket_status": update_freshdesk_ticket_status,
    "list_freshdesk_tickets": list_freshdesk_tickets
}