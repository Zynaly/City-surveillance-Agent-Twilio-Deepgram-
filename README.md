# City Monitor Agent

**City Monitor Agent** is a Python-based emergency response system that integrates AI voice and ticketing services to streamline incident response from surveillance systems.

---

## 🚀 Features

* **Automated Voice Calls** via Twilio
* **Real-Time Voice Processing** using Deepgram
* **Incident Ticket Polling** with Freshdesk
* **WhatsApp Alerts** with incident details & media
* **WebSocket Audio Handling** between Twilio & Deepgram
* **Robust Logging & Error Handling**

---

## 🔧 Prerequisites

* Python 3.12 (audioop compatible)
* Twilio Account (phone in E.164 format)
* Deepgram API Key
* Freshdesk Account
* `.env` configured with required variables

---

## ⚙️ Installation

```bash
git clone <repository-url>
cd city-monitor-agent
pip install -r requirements.txt
```

### Environment Variables (`.env`)

```env
DEEPGRAM_API_KEY=your-deepgram-api-key
TWILIO_ACCOUNT_SID=your-twilio-account-sid
TWILIO_AUTH_TOKEN=your-twilio-auth-token
TWILIO_PHONE_NUMBER=+1234567890
WEBHOOK_URL=https://your-domain/twilio/incoming
WEBSOCKET_URL=wss://your-domain
POLLING_INTERVAL=30
```

> **Note:** Downgrade to Python 3.12 if using 3.13+ (due to `audioop` deprecation)

---

## ▶️ Usage

```bash
python app.py
```

Starts:

* Flask server on `:5000`
* WebSocket server on `:8080`
* Freshdesk poller (every `POLLING_INTERVAL` seconds)

---

## 🔀 How It Works

1. **Poll Tickets:** Fetches Freshdesk tickets (status `2`)
2. **Initiate Calls:** Contacts responders via Twilio
3. **Voice Flow:**

   * Audio streamed via WebSocket → Deepgram
   * Deepgram voice agent processes & replies
   * Commands trigger WhatsApp alerts if needed
4. **Ticket Updates:** Marks as `in-progress` (3) or `resolved` (5)

---

## 🔍 Endpoints

| Route              | Description                     |
| ------------------ | ------------------------------- |
| `/`                | General system status           |
| `/health`          | Health check                    |
| `/status`          | System diagnostics              |
| `/twilio/incoming` | Twilio webhook for voice stream |
| `/twilio/status`   | Twilio call status callback     |

---

## 🔪 Local Testing

Use [ngrok](https://ngrok.com/) to expose local servers:

```bash
ngrok http 5000
ngrok http 8080
```

Update `.env` `WEBHOOK_URL` & `WEBSOCKET_URL` with ngrok URLs.

---

## 📋 Contributing

1. Fork & branch (`feature/your-feature`)
2. Commit & push
3. Submit PR

---

## 📄 License

MIT License – See `LICENSE` file for details.
