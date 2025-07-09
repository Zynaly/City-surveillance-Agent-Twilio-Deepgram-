from flask import Flask, request
import asyncio
import websockets
import os
import json
import threading
import time
from datetime import datetime
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
from common.zf import FUNCTION_DEFINITIONS, FUNCTION_MAP, list_freshdesk_tickets, update_freshdesk_ticket_status
import logging
from common.log_formatter import CustomFormatter
import uuid
import base64
import re

# Handle audioop deprecation gracefully
try:
    import audioop
except ImportError:
    try:
        import audioop3 as audioop
    except ImportError:
        print("Error: audioop not available. This is required for Twilio audio conversion.")
        print("Please install Python < 3.13 or find an audioop alternative.")
        exit(1)

# Configure Flask
app = Flask(__name__, static_folder="./static", static_url_path="/")

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setFormatter(CustomFormatter())
logger.addHandler(console_handler)
logging.getLogger().handlers = []

# Enable WebSocket debugging
# logging.getLogger('websockets').setLevel(logging.DEBUG)
logging.getLogger('websockets').setLevel(logging.WARNING)
logging.getLogger('websockets').addHandler(console_handler)

# Configuration
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")
POLLING_INTERVAL = int(os.environ.get("POLLING_INTERVAL", 30))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEBSOCKET_URL = os.environ.get("WEBSOCKET_URL")
STATUS_CALLBACK_URL = os.environ.get("WEBHOOK_URL", "").replace("/twilio/incoming", "/twilio/status")

# Validate environment variables
if not DEEPGRAM_API_KEY:
    print("Error: DEEPGRAM_API_KEY environment variable is required")
    exit(1)
if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_NUMBER:
    print("Error: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER are required")
    exit(1)
if not WEBHOOK_URL:
    print("Error: WEBHOOK_URL environment variable is required (e.g., https://<your-domain>/twilio/incoming)")
    exit(1)
if not WEBHOOK_URL.endswith("/twilio/incoming"):
    print(f"Error: WEBHOOK_URL must end with '/twilio/incoming', got {WEBHOOK_URL}")
    exit(1)
if not WEBSOCKET_URL:
    print("Error: WEBSOCKET_URL environment variable is required (e.g., wss://<your-domain>)")
    exit(1)
if not TWILIO_PHONE_NUMBER.startswith("+"):
    print(f"Error: TWILIO_PHONE_NUMBER must be in E.164 format (e.g., +1234567890), got {TWILIO_PHONE_NUMBER}")
    exit(1)

VOICE_AGENT_URL = "wss://agent.deepgram.com/v1/agent/converse"

# Improved prompt template with incident-specific guidance
PROMPT_TEMPLATE = """You are Sarah from Safe City Authority Emergency Response System.

Your role: Coordinate emergency responses for AI-detected incidents from surveillance cameras.

IMPORTANT: I will provide initial incident details via InjectAgentMessage. Your job is to:
-Keep you greetings and conversation lines clear and concise avoid too much lengthy sentences.
1. Get the emergency responder's name and agency
2. Request their WhatsApp number for sending evidence images,when user will provide number confirm by repeating the number and dont move further untill he confirm that number is correct.
3. You will be clear and concise.
4. Use send_whatsapp_message function to send incident details and images
5. Ask about their estimated response time
6. Close professionally

Keep each response under 40 words. Be professional, calm, and urgent.

When you get a WhatsApp number, immediately use the send_whatsapp_message function with the incident details and image URLs.

Current date: {current_date}"""

# Simplified settings for Deepgram Voice Agent
TWILIO_SAMPLE_RATE = 8000
DEEPGRAM_INPUT_RATE = 48000
DEEPGRAM_OUTPUT_RATE = 16000

def create_deepgram_settings(ticket_data=None):
    """Create Deepgram settings with proper formatting"""
    current_date = datetime.now().strftime("%A, %B %d, %Y")
    formatted_prompt = PROMPT_TEMPLATE.format(current_date=current_date)
    
    settings = {
        "type": "Settings",
        "audio": {
            "input": {
                "encoding": "linear16",
                "sample_rate": DEEPGRAM_INPUT_RATE,
            },
            "output": {
                "encoding": "linear16",
                "sample_rate": DEEPGRAM_OUTPUT_RATE,
                "container": "none",
            },
        },
        "agent": {
            "language": "en",
            "listen": {
                "provider": {
                    "type": "deepgram",
                    "model": "nova-2"
                }
            },
            "think": {
                "provider": {
                    "type": "open_ai",
                    "model": "gpt-4o-mini"
                },
                "prompt": formatted_prompt,
                "functions": FUNCTION_DEFINITIONS  # Add back function calling
            },
            "speak": {
                "provider": {
                    "type": "deepgram",
                    "model": "aura-2-andromeda-en"
                }
            },
        }
    }
    
    return settings

# Store active sessions and processed tickets
active_calls = {}
processed_tickets = {}

# Phone number validation regex
PHONE_NUMBER_REGEX = re.compile(r"^\+\d{10,15}$")

class TwilioVoiceAgent:
    def __init__(self, call_sid, ticket_data):
        self.call_sid = call_sid
        self.ticket_data = ticket_data
        self.session_id = str(uuid.uuid4())
        self.twilio_ws_handler = None
        self.deepgram_ws = None
        self.is_running = False
        self.loop = None
        self.connection_attempts = 0
        self.max_connection_attempts = 3
        self.call_active = True
        self.deepgram_ready = False
        self.initialization_complete = asyncio.Event()
        
    def set_loop(self, loop):
        self.loop = loop

    async def keep_alive(self):
        """Send keep-alive messages to maintain Deepgram connection"""
        while self.call_active and self.deepgram_ws and not self.deepgram_ws.closed:
            try:
                await asyncio.sleep(10)
                if self.call_active and self.deepgram_ws and not self.deepgram_ws.closed:
                    keep_alive_msg = {"type": "KeepAlive"}
                    await self.deepgram_ws.send(json.dumps(keep_alive_msg))
                    logger.debug(f"Sent keep-alive to Deepgram for call {self.call_sid}")
            except Exception as e:
                logger.error(f"Error sending keep-alive for call {self.call_sid}: {e}")
                break

    async def send_initial_greeting(self):
        """Send initial greeting and incident information"""
        if not self.deepgram_ws or self.deepgram_ws.closed:
            logger.warning(f"Cannot send greeting: Deepgram WebSocket not available for call {self.call_sid}")
            return
            
        try:
            # Wait a brief moment for connection to stabilize
            await asyncio.sleep(0.5)
            
            # Create detailed incident greeting message
            if self.ticket_data:
                incident_type = self.ticket_data.get('incident_type', 'security incident')
                address = self.ticket_data.get('address', 'unknown location')
                confidence = int(self.ticket_data.get('confidence_score', 0.8) * 100)
                
                greeting_message = (
                    f"Hello, this is Zain from Safe City Authority Emergency Response. "
                    f"We have detected a {incident_type} at {address} with {confidence}% confidence. "
                    f"May I have your name, agency, and WhatsApp number to send evidence images?"
                )
                
                logger.info(f"Preparing to send incident greeting: {greeting_message}")
            else:
                greeting_message = (
                    "Hello, this is Zain from Safe City Authority Emergency Response. " 
                    "May I have your name?"
                )
                
                logger.info(f"Preparing to send default greeting: {greeting_message}")
            
            # Send greeting using InjectAgentMessage to make agent speak first
            inject_message = {
                "type": "InjectAgentMessage",
                "content": greeting_message
            }
            
            logger.info(f"Sending InjectAgentMessage to Deepgram for call {self.call_sid}")
            await self.deepgram_ws.send(json.dumps(inject_message))
            logger.info(f"✅ Sent incident greeting to Deepgram for call {self.call_sid}")
            
        except Exception as e:
            logger.error(f"Error sending initial greeting for call {self.call_sid}: {e}")
            logger.error(f"Exception details: {type(e).__name__}: {str(e)}")

    async def setup_deepgram(self):
        """Setup connection to Deepgram Voice Agent with retry logic."""
        if not self.call_active:
            logger.info(f"Skipping Deepgram setup for call {self.call_sid}: Call is no longer active")
            return False

        self.loop = asyncio.get_event_loop()
        self.connection_attempts += 1
        
        # Create settings with function calling
        settings = create_deepgram_settings(self.ticket_data)

        try:
            logger.info(f"Connecting to Deepgram for call {self.call_sid}, attempt {self.connection_attempts}")
            self.deepgram_ws = await websockets.connect(
                VOICE_AGENT_URL,
                extra_headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"},
                ping_interval=20,
                ping_timeout=30
            )
            
            logger.info(f"Sending settings to Deepgram for call {self.call_sid}")
            await self.deepgram_ws.send(json.dumps(settings))
            
            # Wait for SettingsApplied message
            logger.info(f"Waiting for Deepgram SettingsApplied message for call {self.call_sid}")
            try:
                settings_applied = False
                timeout_count = 0
                max_timeout = 15  # 15 seconds total
                
                while not settings_applied and timeout_count < max_timeout:
                    try:
                        message = await asyncio.wait_for(self.deepgram_ws.recv(), timeout=1.0)
                        if isinstance(message, str):
                            message_json = json.loads(message)
                            logger.info(f"Deepgram initialization message: {message_json}")
                            
                            if message_json.get("type") == "Welcome":
                                logger.info(f"Deepgram Welcome received for call {self.call_sid}")
                                continue
                            elif message_json.get("type") == "SettingsApplied":
                                settings_applied = True
                                self.deepgram_ready = True
                                logger.info(f"Deepgram SettingsApplied received for call {self.call_sid}")
                                break
                            elif message_json.get("type") == "Error":
                                error_desc = message_json.get("description", "Unknown error")
                                error_code = message_json.get("code", "UNKNOWN")
                                logger.error(f"Deepgram error during setup: {error_desc} (Code: {error_code})")
                                return False
                    except asyncio.TimeoutError:
                        timeout_count += 1
                        logger.debug(f"Waiting for SettingsApplied message, timeout {timeout_count}/{max_timeout}")
                        continue
                    except json.JSONDecodeError:
                        continue
                
                if not settings_applied:
                    logger.error(f"Did not receive SettingsApplied message from Deepgram for call {self.call_sid}")
                    return False
                    
            except Exception as e:
                logger.error(f"Error waiting for SettingsApplied message: {e}")
                return False
            
            logger.info(f"✅ Connected to Deepgram for call {self.call_sid}")

            # Update ticket status only on first successful connection
            if self.ticket_data.get("ticket_id") and self.connection_attempts == 1:
                try:
                    await update_freshdesk_ticket_status({
                        "ticket_id": self.ticket_data["ticket_id"],
                        "status": 3
                    })
                    logger.info(f"Updated ticket {self.ticket_data['ticket_id']} status to 3")
                except Exception as e:
                    logger.error(f"Failed to update ticket {self.ticket_data['ticket_id']} status: {e}")

            self.connection_attempts = 0
            self.initialization_complete.set()
            
            # Start keep-alive task
            asyncio.create_task(self.keep_alive())
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to Deepgram for call {self.call_sid}, attempt {self.connection_attempts}: {e}")
            if self.connection_attempts < self.max_connection_attempts:
                await asyncio.sleep(2)
                return await self.setup_deepgram()
            return False

    def set_twilio_websocket(self, ws_handler):
        self.twilio_ws_handler = ws_handler

    def convert_mulaw_to_linear16(self, mulaw_data):
        """Convert Twilio's mu-law audio to linear16 for Deepgram"""
        try:
            if not mulaw_data:
                return None
            audio_bytes = base64.b64decode(mulaw_data)
            if not audio_bytes:
                return None
            linear_audio = audioop.ulaw2lin(audio_bytes, 2)
            resampled_audio, _ = audioop.ratecv(
                linear_audio, 2, 1, TWILIO_SAMPLE_RATE, DEEPGRAM_INPUT_RATE, None
            )
            return resampled_audio
        except Exception as e:
            logger.error(f"Error converting mu-law to linear16 for call {self.call_sid}: {e}")
            return None

    def convert_linear16_to_mulaw(self, linear_data):
        """Convert Deepgram's linear16 audio to mu-law for Twilio"""
        try:
            if not linear_data:
                return None
            resampled_audio, _ = audioop.ratecv(
                linear_data, 2, 1, DEEPGRAM_OUTPUT_RATE, TWILIO_SAMPLE_RATE, None
            )
            mulaw_audio = audioop.lin2ulaw(resampled_audio, 2)
            encoded_audio = base64.b64encode(mulaw_audio).decode('utf-8')
            return encoded_audio
        except Exception as e:
            logger.error(f"Error converting linear16 to mu-law for call {self.call_sid}: {e}")
            return None

    async def send_audio_to_deepgram(self, linear_audio):
        """Send audio to Deepgram"""
        if not self.call_active or not self.deepgram_ready:
            return

        try:
            if self.deepgram_ws and not self.deepgram_ws.closed:
                await self.deepgram_ws.send(linear_audio)
        except Exception as e:
            logger.error(f"Error sending audio to Deepgram for call {self.call_sid}: {e}")
            await self.cleanup()

    async def process_twilio_audio(self, mulaw_payload):
        """Process incoming audio from Twilio and send to Deepgram"""
        if not self.call_active or not self.deepgram_ready:
            return

        if not mulaw_payload:
            return
        
        linear_audio = self.convert_mulaw_to_linear16(mulaw_payload)
        if linear_audio:
            await self.send_audio_to_deepgram(linear_audio)

    async def handle_deepgram_message(self, message):
        """Handle incoming messages from Deepgram"""
        if not self.call_active:
            return

        try:
            if isinstance(message, str):
                message_json = json.loads(message)
                message_type = message_json.get("type")
                
                if message_type == "UserStartedSpeaking":
                    logger.debug(f"User started speaking for call {self.call_sid}")

                elif message_type == "ConversationText":
                    role = message_json.get("role")
                    content = message_json.get("content")
                    logger.info(f"Conversation - {role}: {content} (call {self.call_sid})")

                elif message_type == "FunctionCallRequest":
                    await self.handle_function_call(message_json)

                elif message_type == "Welcome":
                    logger.info(f"Connected to Deepgram with request ID: {message_json.get('request_id')} for call {self.call_sid}")

                elif message_type == "SettingsApplied":
                    logger.info(f"Deepgram settings applied for call {self.call_sid}")
                    self.deepgram_ready = True
                    # Send initial greeting immediately after settings are applied
                    await self.send_initial_greeting()

                elif message_type == "CloseConnection":
                    logger.info(f"Deepgram closing connection for call {self.call_sid}")
                    await self.cleanup()

            elif isinstance(message, bytes):
                # Handle audio from Deepgram
                if self.twilio_ws_handler:
                    mulaw_audio = self.convert_linear16_to_mulaw(message)
                    if mulaw_audio:
                        await self.twilio_ws_handler.send_media(mulaw_audio)

        except Exception as e:
            logger.error(f"Error handling Deepgram message for call {self.call_sid}: {e}")

    async def handle_function_call(self, message_json):
        """Handle function calls from Deepgram"""
        if not self.call_active:
            return

        function_name = message_json.get("function_name")
        function_call_id = message_json.get("function_call_id")
        parameters = message_json.get("input", {})

        logger.info(f"Function call received: {function_name} for call {self.call_sid}")
        logger.info(f"Parameters: {parameters}")

        try:
            func = FUNCTION_MAP.get(function_name)
            if not func:
                raise ValueError(f"Function {function_name} not found")

            # For send_whatsapp_message, enrich with incident data
            if function_name == "send_whatsapp_message" and self.ticket_data:
                if "incident_details" not in parameters:
                    parameters["incident_details"] = {
                        "incident_type": self.ticket_data.get("incident_type"),
                        "address": self.ticket_data.get("address"),
                        "phone_number": self.ticket_data.get("phone_number"),
                        "priority": self.ticket_data.get("priority"),
                        "confidence_score": self.ticket_data.get("confidence_score"),
                        "ticket_id": self.ticket_data.get("ticket_id")
                    }
                if "image_urls" not in parameters:
                    parameters["image_urls"] = self.ticket_data.get("image_urls", [])
                
                logger.info(f"Enriched WhatsApp parameters with incident data: {parameters}")

            result = await func(parameters)
            response = {
                "type": "FunctionCallResponse",
                "function_call_id": function_call_id,
                "output": json.dumps(result),
            }
            
            if self.call_active and self.deepgram_ws and not self.deepgram_ws.closed:
                await self.deepgram_ws.send(json.dumps(response))
                logger.info(f"Function response sent: {json.dumps(result)} for call {self.call_sid}")

        except Exception as e:
            logger.error(f"Error executing function {function_name} for call {self.call_sid}: {str(e)}")
            result = {"error": str(e)}
            response = {
                "type": "FunctionCallResponse",
                "function_call_id": function_call_id,
                "output": json.dumps(result),
            }
            if self.call_active and self.deepgram_ws and not self.deepgram_ws.closed:
                await self.deepgram_ws.send(json.dumps(response))

    async def run(self):
        """Main run loop for the voice agent"""
        if not self.call_active:
            return

        # Setup Deepgram connection
        if not await self.setup_deepgram():
            logger.error(f"Failed to setup Deepgram after {self.max_connection_attempts} attempts for call {self.call_sid}")
            await self.cleanup()
            return

        # Wait for initialization to complete
        try:
            await asyncio.wait_for(self.initialization_complete.wait(), timeout=20.0)
            logger.info(f"Deepgram initialization complete for call {self.call_sid}")
        except asyncio.TimeoutError:
            logger.error(f"Deepgram initialization timeout for call {self.call_sid}")
            await self.cleanup()
            return

        self.is_running = True
        try:
            async for message in self.deepgram_ws:
                if not self.is_running or not self.call_active:
                    break
                await self.handle_deepgram_message(message)
        except Exception as e:
            logger.error(f"Error in voice agent run for call {self.call_sid}: {e}")
        finally:
            await self.cleanup()

    async def cleanup(self):
        """Clean up resources"""
        self.is_running = False
        self.call_active = False
        self.deepgram_ready = False
        
        if self.deepgram_ws and not self.deepgram_ws.closed:
            try:
                await self.deepgram_ws.close()
                logger.info(f"Closed Deepgram WebSocket for call {self.call_sid}")
            except Exception as e:
                logger.error(f"Error closing Deepgram WebSocket for call {self.call_sid}: {e}")
        
        if self.call_sid in active_calls:
            del active_calls[self.call_sid]
            logger.info(f"Cleaned up resources for call {self.call_sid}")


class TwilioWebSocketHandler:
    def __init__(self, voice_agent):
        self.voice_agent = voice_agent
        self.websocket = None
        self.stream_sid = None
        
    async def handle_connection(self, websocket, path):
        self.websocket = websocket
        logger.info(f"New Twilio WebSocket connection: {path} for call {self.voice_agent.call_sid}")
        try:
            async for message in websocket:
                await self.handle_message(message)
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Twilio WebSocket connection closed for call {self.voice_agent.call_sid}")
            self.voice_agent.call_active = False
        except Exception as e:
            logger.error(f"Error in Twilio WebSocket handler for call {self.voice_agent.call_sid}: {e}")
        finally:
            await self.cleanup()
    
    async def handle_message(self, message):
        try:
            data = json.loads(message)
            event = data.get('event')
            
            if event == 'start':
                await self.handle_start(data)
            elif event == 'media':
                await self.handle_media(data)
            elif event == 'stop':
                await self.handle_stop(data)
                
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON received from Twilio for call {self.voice_agent.call_sid}")
        except Exception as e:
            logger.error(f"Error handling Twilio message for call {self.voice_agent.call_sid}: {e}")
    
    async def handle_start(self, data):
        start_data = data.get('start', {})
        call_sid = start_data.get('callSid')
        self.stream_sid = start_data.get('streamSid')
        logger.info(f"Stream started - CallSid: {call_sid}, StreamSid: {self.stream_sid}")
        
        # Wait for Deepgram to be ready
        if not self.voice_agent.deepgram_ready:
            try:
                await asyncio.wait_for(self.voice_agent.initialization_complete.wait(), timeout=20.0)
                logger.info(f"Deepgram ready for call {call_sid}")
            except asyncio.TimeoutError:
                logger.error(f"Timeout waiting for Deepgram initialization for call {call_sid}")
                self.voice_agent.call_active = False
                return
        
        # Backup: Send greeting if not already sent
        if self.voice_agent.deepgram_ready and self.voice_agent.ticket_data:
            logger.info(f"Ensuring greeting is sent for call {call_sid}")
            await asyncio.sleep(1)  # Give a moment for things to settle
            await self.voice_agent.send_initial_greeting()
    
    async def handle_media(self, data):
        if not self.voice_agent.call_active or not self.voice_agent.deepgram_ready:
            return
            
        media_data = data.get('media', {})
        payload = media_data.get('payload')
        if payload:
            await self.voice_agent.process_twilio_audio(payload)
    
    async def handle_stop(self, data):
        logger.info(f"Stream stopped for StreamSid: {self.stream_sid}")
        self.voice_agent.call_active = False
        await self.cleanup()
    
    async def send_media(self, audio_payload):
        if not self.voice_agent.call_active or not self.websocket or not self.stream_sid:
            return
            
        message = {
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {
                "payload": audio_payload
            }
        }
        try:
            await self.websocket.send(json.dumps(message))
        except Exception as e:
            logger.error(f"Failed to send media to Twilio for call {self.voice_agent.call_sid}: {e}")
            self.voice_agent.call_active = False
    
    async def cleanup(self):
        self.voice_agent.call_active = False
        if self.voice_agent:
            await self.voice_agent.cleanup()
        if self.websocket and not self.websocket.closed:
            try:
                await self.websocket.close()
            except:
                pass
            self.websocket = None
        self.stream_sid = None
        logger.info(f"Twilio WebSocket handler cleaned up for call {self.voice_agent.call_sid}")


async def handle_twilio_websocket(websocket, path):
    """Handle new Twilio WebSocket connections"""
    logger.info(f"New Twilio WebSocket connection: {path}")
    call_sid = None
    voice_agent = None
    voice_agent_task = None
    
    try:
        # Wait for the start message to get call_sid
        async for message in websocket:
            data = json.loads(message)
            if data.get('event') == 'start':
                call_sid = data['start']['callSid']
                logger.info(f"Received start event - CallSid: {call_sid}")
                break
        
        if not call_sid:
            logger.error("No call_sid received")
            return
            
        # Get call data
        call_data = active_calls.get(call_sid, {})
        ticket_data = call_data.get("ticket_data")
        
        if not ticket_data:
            logger.error(f"No ticket data found for CallSid: {call_sid}")
            return
        
        # Create voice agent
        voice_agent = TwilioVoiceAgent(call_sid, ticket_data)
        active_calls[call_sid]["voice_agent"] = voice_agent
        
        # Create WebSocket handler
        ws_handler = TwilioWebSocketHandler(voice_agent)
        ws_handler.websocket = websocket
        ws_handler.stream_sid = data['start']['streamSid']
        
        # Set the websocket handler in voice agent
        voice_agent.set_twilio_websocket(ws_handler)
        
        # Start voice agent
        voice_agent_task = asyncio.create_task(voice_agent.run())
        
        # Process the start message
        await ws_handler.handle_start(data)
        
        # Continue handling messages
        async for message in websocket:
            await ws_handler.handle_message(message)
            
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"WebSocket connection closed for call {call_sid}")
    except Exception as e:
        logger.error(f"Error handling Twilio WebSocket: {e}")
    finally:
        if voice_agent_task:
            voice_agent_task.cancel()
            try:
                await voice_agent_task
            except asyncio.CancelledError:
                pass
        if voice_agent:
            await voice_agent.cleanup()


async def poll_freshdesk_tickets():
    """Poll Freshdesk for new tickets and make calls"""
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    ticket_timeout = 300
    
    while True:
        try:
            current_time = time.time()
            
            # Clean up expired tickets
            for ticket_id in list(processed_tickets.keys()):
                if current_time - processed_tickets[ticket_id] > ticket_timeout:
                    del processed_tickets[ticket_id]

            # Get tickets from Freshdesk
            result = await list_freshdesk_tickets({})
            if "tickets" in result:
                sorted_tickets = sorted(
                    result["tickets"],
                    key=lambda x: x.get("priority", 0),
                    reverse=True
                )
                
                for ticket in sorted_tickets:
                    ticket_id = ticket.get("ticket_id")
                    
                    # Skip if already processed
                    if ticket_id in processed_tickets:
                        continue
                        
                    # Skip if not open status
                    if ticket.get("status") != 2:
                        continue
                    
                    # Validate phone number
                    phone_number = ticket.get("phone_number")
                    if phone_number and not phone_number.startswith("+"):
                        phone_number = f"+{phone_number}"
                        ticket["phone_number"] = phone_number
                        
                    if not phone_number or not PHONE_NUMBER_REGEX.match(phone_number):
                        logger.info(f"Skipping ticket {ticket_id}: Invalid phone number ({phone_number})")
                        try:
                            await update_freshdesk_ticket_status({"ticket_id": ticket_id, "status": 5})
                        except Exception as e:
                            logger.error(f"Failed to update ticket {ticket_id} status: {e}")
                        continue

                    logger.info(f"Processing ticket {ticket_id}: {ticket.get('incident_type')} at {ticket.get('address')}")
                    
                    try:
                        # Make the call
                        call = client.calls.create(
                            to=phone_number,
                            from_=TWILIO_PHONE_NUMBER,
                            url=WEBHOOK_URL,
                            method="POST",
                            status_callback=STATUS_CALLBACK_URL,
                            status_callback_method="POST",
                            status_callback_event=["initiated", "ringing", "answered", "completed"],
                            timeout=60
                        )
                        
                        logger.info(f"✅ Call initiated to {phone_number} for ticket {ticket_id}, CallSid: {call.sid}")
                        
                        # Store call data
                        active_calls[call.sid] = {"ticket_data": ticket}
                        processed_tickets[ticket_id] = current_time
                        
                        # Small delay between calls
                        await asyncio.sleep(5)
                        
                    except Exception as e:
                        logger.error(f"Failed to initiate call for ticket {ticket_id}: {str(e)}")
                        try:
                            await update_freshdesk_ticket_status({"ticket_id": ticket_id, "status": 5})
                        except Exception as e:
                            logger.error(f"Failed to update ticket status: {e}")
            else:
                logger.error(f"Failed to list tickets: {result.get('error')}")
                
        except Exception as e:
            logger.error(f"Error polling Freshdesk tickets: {str(e)}")
        
        await asyncio.sleep(POLLING_INTERVAL)


def start_websocket_server():
    """Start the WebSocket server for Twilio connections"""
    async def run_server():
        global ws_server
        ws_server = await websockets.serve(
            handle_twilio_websocket, 
            "0.0.0.0", 
            8080
        )
        logger.info("Twilio WebSocket server started on port 8080")
        await ws_server.wait_closed()
    
    thread = threading.Thread(target=lambda: asyncio.run(run_server()), daemon=True)
    thread.start()
    return thread


def start_polling():
    """Start the polling thread"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(poll_freshdesk_tickets())


# Flask routes
@app.route("/", methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        logger.error(f"Unexpected POST request to root endpoint")
        return {"error": "Method Not Allowed, use /twilio/incoming for POST requests"}, 405
    return {
        "status": "City Monitor Agent Active",
        "timestamp": datetime.now().isoformat(),
        "active_calls": len(active_calls),
        "endpoints": {
            "health": "/health",
            "status": "/status",
            "twilio_incoming": "/twilio/incoming",
            "twilio_status": "/twilio/status"
        }
    }


@app.route("/twilio/incoming", methods=['POST'])
def handle_incoming_call():
    """Handle incoming call webhook from Twilio"""
    try:
        response = VoiceResponse()
        connect = response.connect()
        connect.stream(url=WEBSOCKET_URL)
        logger.info(f"Redirecting call to WebSocket: {WEBSOCKET_URL}")
        return str(response)
    except Exception as e:
        logger.error(f"Error generating TwiML: {e}")
        return {"error": "Failed to generate TwiML"}, 500


@app.route("/twilio/status", methods=['POST'])
def handle_call_status():
    """Handle call status callbacks from Twilio"""
    status_data = request.form.to_dict()
    call_sid = status_data.get("CallSid", "unknown")
    call_status = status_data.get("CallStatus", "unknown")
    
    logger.info(f"Call status update - CallSid: {call_sid}, Status: {call_status}")
    
    # Handle call completion
    if call_status in ["completed", "failed", "no-answer", "busy"]:
        if call_sid in active_calls:
            voice_agent = active_calls[call_sid].get("voice_agent")
            if voice_agent:
                voice_agent.call_active = False
                
            # Update ticket status
            try:
                ticket_data = active_calls[call_sid].get("ticket_data")
                if ticket_data:
                    ticket_id = ticket_data.get("ticket_id")
                    if ticket_id:
                        asyncio.run(update_freshdesk_ticket_status({
                            "ticket_id": ticket_id,
                            "status": 5
                        }))
                        logger.info(f"Updated ticket {ticket_id} status to 5")
            except Exception as e:
                logger.error(f"Failed to update ticket status: {e}")
                
            # Safe cleanup
            try:
                del active_calls[call_sid]
                logger.info(f"Cleaned up call {call_sid}")
            except KeyError:
                logger.warning(f"Call {call_sid} already cleaned up")
        else:
            logger.warning(f"Call {call_sid} not found in active_calls")
    
    return {"status": "received"}, 200


@app.route("/health")
def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy", 
        "timestamp": datetime.now().isoformat(),
        "deepgram_key_present": bool(DEEPGRAM_API_KEY),
        "twilio_credentials_present": bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN),
        "active_calls": len(active_calls)
    }


@app.route("/status")
def status():
    """Status endpoint"""
    return {
        "active_calls": len(active_calls),
        "processed_tickets": list(processed_tickets.keys()),
        "timestamp": datetime.now().isoformat(),
        "mode": "city_monitor"
    }


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("🚨 City Monitor Agent Starting!")
    print("=" * 70)
    
    print(f"\n🔧 Configuration:")
    print(f"   - WEBHOOK_URL: {WEBHOOK_URL}")
    print(f"   - WEBSOCKET_URL: {WEBSOCKET_URL}")
    print(f"   - TWILIO_PHONE_NUMBER: {TWILIO_PHONE_NUMBER}")
    print(f"   - POLLING_INTERVAL: {POLLING_INTERVAL}s")
    
    print("\n🚀 Starting services...")
    
    # Start WebSocket server
    start_websocket_server()
    
    # Start polling thread
    threading.Thread(target=start_polling, daemon=True).start()
    
    # Start Flask app
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)