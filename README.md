# 🦆 Rubber Duck Debugger

[Rubber duck debugging](https://en.wikipedia.org/wiki/Rubber_duck_debugging) is a tried-and-tested method: explain your code problem to a rubber duck, walk through your code line-by-line, and the solution often reveals itself. But what if the duck could talk back?

The Rubber Duck Debugger is a hotline built with [Twilio](https://www.twilio.com) and [Claude AI](https://claude.com/). Call in, explain your problem, and get help finding the solution!

I built this as a fun [Track Jacket](https://www.twilio.com/en-us/blog/developers/tutorials/product/sms-email-responder-python-flask) project - a Twilio tradition for understanding our developer experience through hands-on building.

## What It Does

1. **Call the hotline** - Dial your Twilio number to connect with the rubber duck
2. **Talk through your problem** - Explain your code issue, if inspiration hasn't struck after a few seconds, the duck will ask questions and suggest solutions
3. **Get the transcript** - Receive the full conversation via SMS when the call ends

## Tech Stack

- **FastAPI** - WebSocket server for real-time voice conversation
- **Twilio ConversationRelay** - Bidirectional voice streaming
- **Claude AI (Sonnet 4.6)** - Conversational AI with streaming responses
- **Twilio SMS** - Post-call transcript delivery

## Prerequisites

Before you begin, ensure you have:

- **uv** package manager - [Install uv](https://docs.astral.sh/uv/getting-started/installation/)
- **Twilio account** with:
  - A [phone number](https://www.twilio.com/en-us/phone-numbers) that supports voice
  - Account SID and Auth Token (find them in the [Twilio Console](https://console.twilio.com/))
- **Anthropic API key** - Get one from [Anthropic Console](https://console.anthropic.com/)
- **Grok** (for local deployment and testing) - [Install Grok](https://grok.app/)

## Setup

1. Install dependencies:
```bash
uv sync
```

2. Create `.env` file:
```bash
ANTHROPIC_API_KEY=your_claude_api_key
TWILIO_ACCOUNT_SID=your_twilio_sid
TWILIO_AUTH_TOKEN=your_twilio_token
TWILIO_PHONE_NUMBER=your_twilio_number
PORT=5050
```

3. Run the server:
```bash
python main.py
```

4. Expose your local server with Grok (for local development):
```bash
grok http 5050
```
Copy the `https://` forwarding URL from the Grok output (e.g., `https://abc123.grok.app`)

5. Configure Twilio webhook:
   - Go to your Twilio phone number settings
   - Set the webhook URL (e.g., `https://abc123.grok.app`)
   - Save the configuration

## Features

- Real-time voice conversation with streaming AI responses
- Duck-themed personality with debugging puns
- SMS transcript delivery after each call
