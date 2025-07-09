City Monitor Agent
The City Monitor Agent is a Python-based emergency response system that integrates with Twilio for voice communication, Deepgram for real-time voice processing, and Freshdesk for ticket management. It automates coordination with emergency responders for AI-detected incidents from surveillance cameras.
Features

Automated Voice Calls: Initiates calls to emergency responders using Twilio.
Real-Time Voice Processing: Uses Deepgram's Voice Agent for natural language understanding and response.
Ticket Management: Polls Freshdesk for new incident tickets and updates their status.
WhatsApp Integration: Sends incident details and evidence images to responders via WhatsApp.
WebSocket Communication: Handles real-time audio streams between Twilio and Deepgram.
Robust Error Handling: Includes retry logic, logging, and cleanup for reliability.

Prerequisites

Python 3.12 or earlier (due to audioop compatibility)
Twilio account with a phone number in E.164 format (e.g., +1234567890)
Deepgram API key
Freshdesk account for ticket management
Environment variables configured (see below)

Installation

Clone the repository:
git clone <repository-url>
cd city-monitor-agent


Install dependencies:
pip install -r requirements.txt


Set up environment variables in a .env file or export them:
export DEEPGRAM_API_KEY="your-deepgram-api-key"
export TWILIO_ACCOUNT_SID="your-twilio-account-sid"
export TWILIO_AUTH_TOKEN="your-twilio-auth-token"
export TWILIO_PHONE_NUMBER="+1234567890"
export WEBHOOK_URL="https://your-domain/twilio/incoming"
export WEBSOCKET_URL="wss://your-domain"
export POLLING_INTERVAL="30"


If Python 3.13+ is used, install an audioop alternative or downgrade to Python 3.12.


Usage

Start the application:
python app.py


The system will:

Start a Flask server on port 5000 (or the specified PORT).
Run a WebSocket server on port 8080 for Twilio connections.
Poll Freshdesk every 30 seconds (or as set by POLLING_INTERVAL) for new tickets.
Initiate calls to responders for open tickets and coordinate via voice.


Access endpoints:

/: General status
/health: Health check
/status: Detailed system status
/twilio/incoming: Twilio webhook for incoming calls
/twilio/status: Twilio call status callbacks



Configuration



Variable
Description
Required



DEEPGRAM_API_KEY
Deepgram API key for voice processing
Yes


TWILIO_ACCOUNT_SID
Twilio account SID
Yes


TWILIO_AUTH_TOKEN
Twilio auth token
Yes


TWILIO_PHONE_NUMBER
Twilio phone number (E.164 format)
Yes


WEBHOOK_URL
Public URL for Twilio webhook (ends with /twilio/incoming)
Yes


WEBSOCKET_URL
WebSocket URL for Twilio streams (e.g., wss://your-domain)
Yes


POLLING_INTERVAL
Interval (seconds) to poll Freshdesk tickets
No (default: 30)


How It Works

Polling: The system polls Freshdesk for open tickets (status 2) with valid responder phone numbers.
Call Initiation: For each new ticket, it uses Twilio to call the responder, passing incident details.
Voice Interaction:
Twilio streams audio to a WebSocket server.
Audio is converted from mu-law to linear16 and sent to Deepgram.
Deepgram processes the audio, responds via its Voice Agent, and triggers functions (e.g., sending WhatsApp messages).
Responses are converted back to mu-law and streamed to Twilio.


Ticket Updates: Ticket statuses are updated in Freshdesk (e.g., 3 for in-progress, 5 for resolved/failed).
Cleanup: Resources are cleaned up when calls end or errors occur.

Logging

Uses a custom formatter for color-coded logs.
Logs are output to the console with levels: INFO, WARNING, ERROR, DEBUG.
WebSocket debugging is disabled by default but can be enabled by uncommenting the relevant line.

Known Limitations

Requires Python < 3.13 due to audioop deprecation. Alternatives may be needed for newer versions.
Phone numbers must be in E.164 format (+ followed by 10-15 digits).
WebSocket and webhook URLs must be publicly accessible (e.g., via ngrok for testing).
Deepgram connection retries are limited to 3 attempts.

Testing
For local testing, use a tunneling service like ngrok:
ngrok http 5000
ngrok http 8080

Update WEBHOOK_URL and WEBSOCKET_URL with the ngrok URLs.
Contributing

Fork the repository.
Create a feature branch (git checkout -b feature/new-feature).
Commit changes (git commit -m 'Add new feature').
Push to the branch (git push origin feature/new-feature).
Open a pull request.

License
This project is licensed under the MIT License. See the LICENSE file for details.
