import os
import json
import uuid  # For generating unique session IDs
import asyncio  # For adding pause before AI responds

from typing import Literal, Optional
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse
from fastapi.websockets import WebSocketDisconnect
from pydantic import BaseModel
from twilio.twiml.voice_response import VoiceResponse, Connect
from twilio.rest import Client  # Twilio REST client for sending SMS
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

# ========================================
# Pydantic Models for WebSocket Message Validation
# ========================================

class WebSocketMessage(BaseModel):
    """
    Uses Pydantic to validate incoming WebSocket messages from Twilio's ConversationRelay.
    Only validates the fields defined below; extra fields from Twilio are ignored.
    """
    model_config = {"extra": "ignore"}

    type: Literal["setup", "prompt", "interrupt", "disconnect"]
    voicePrompt: Optional[str] = None

# ========================================
# STEP 1: Configuration and API Keys
# ========================================

# Anthropic API key for Claude AI
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
PORT = int(os.getenv('PORT', 5050))

# Rubber duck debugging pause - gives user time to think before AI responds
THINKING_PAUSE_SECONDS = 7.0

# Twilio credentials for sending SMS transcripts
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')

# Telling the AI how to behave
SYSTEM_MESSAGE = ("""
System Role: You are a Rubber Duck Debugger, a virtual rubber duck debugging assistant: en.wikipedia.org/wiki/Rubber_duck_debugging. Your job is to help developers solve coding problems through the "Rubber Duck Debugging" method via voice call.

Important Context: There is a deliberate pause built into the system after the developer speaks and before you respond. This pause gives them time to sit with their thoughts and think through their problem. Often, just explaining a problem out loud leads to discovering the solution. Be aware that this pause exists and that it's intentional - the developer has already had a moment to reflect before you speak.

Voice Constraints:
- Be Concise: Keep responses short. In a voice environment, long monologues are hard to follow.
- No Emojis or Markdown: Never use emojis, bolding, or complex lists. Speak in plain, natural sentences.
- Audible Personality: Use your "voice" to convey the duck persona. Start or end thoughts with a "Quack" or a "Waddle." Use duck-related puns that are easy to hear and understand.

The Debugging Strategy:
- The Active Listener: When a developer presents a problem, don't jump to the solution. Ask them to explain the logic of the specific section of code out loud, line-by-line.
- The Probing Bill: Ask simple, clarifying questions to help them spot their own mistakes. For example: "Are you sure that variable is holding what you think it is, or is it just a decoy?"
- The Quick Quip: If there is a pause or the developer sounds frustrated, drop a quick duck joke to lighten the mood.

Example Duck Jokes for Voice:
- "Why did the duck get fired from his news job? Because he kept 'quacking' jokes during the weather."
- "What do you call a clever duck? A wise-quacker."
- "Don't let the bug get you down. Even the best pond has a little algae."

Opening Message:
"Quack! I'm Quack-O-Matic, your debugging waterfowl. I'm all ears—well, all feathers anyway. You sound like you've got a bit of a snag in your code. Why don't you waddle me through the logic step-by-step? What seems to be the problem?"
"""
)

app = FastAPI()

# ========================================
# STEP 2: Initialize API Clients
# ========================================

if not ANTHROPIC_API_KEY:
    raise ValueError("No Anthropic API key present in environment variables")

# Initialize Anthropic client for Claude responses
anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)

# Initialize Twilio client for sending SMS - optional
twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    print("✓ Twilio SMS client initialized - transcripts will be sent")
else:
    print("⚠ Twilio SMS not configured - transcripts will not be sent")

# ========================================
# STEP 3: Session Storage
# ========================================

# Dictionary to store active call sessions
# Key: session_id (unique identifier for each call)
# Value: dict containing:
#   - 'caller_number': Phone number in E.164 format
#   - 'conversation_history': List of message dicts with 'role' and 'content'
#   - 'transcript_sent': Boolean flag to prevent duplicate SMS sends
# This lets us track which phone number belongs to which WebSocket connection
active_sessions = {}


# ========================================
# STEP 4: Helper Functions for SMS Transcripts
# ========================================

def format_transcript(conversation_history):
    """
    Converts conversation history into a readable transcript.

    Args:
        conversation_history: List of dicts with 'role' and 'content' keys

    Returns:
        Formatted string suitable for SMS
    """
    # Start with a header
    transcript = "🦆 Rubber Duck Debugging Session Transcript\n\n"

    # Loop through each message in the conversation
    for message in conversation_history:
        role = message.get('role', '')
        content = message.get('content', '')

        # Format based on who said it
        if role == 'user':
            # User's messages are prefixed with "You:"
            transcript += f"You: {content}\n\n"
        elif role == 'assistant':
            # Duck's messages are prefixed with "Duck Debugger:"
            transcript += f"Duck: {content}\n\n"

    # Add a footer
    transcript += "--- End of Transcript ---"

    return transcript


def send_sms_transcript(phone_number, conversation_history):
    """
    Send the conversation transcript via SMS to the caller.

    Args:
        phone_number: The phone number to send the SMS to (E.164 format)
        conversation_history: List of conversation messages to format
    """
    print(f"🔍 send_sms_transcript called with phone: {phone_number}")

    # Check if Twilio is configured
    if not twilio_client:
        print("⚠ Cannot send SMS - Twilio not configured")
        print(f"   TWILIO_ACCOUNT_SID: {'set' if TWILIO_ACCOUNT_SID else 'NOT SET'}")
        print(f"   TWILIO_AUTH_TOKEN: {'set' if TWILIO_AUTH_TOKEN else 'NOT SET'}")
        print(f"   TWILIO_PHONE_NUMBER: {TWILIO_PHONE_NUMBER if TWILIO_PHONE_NUMBER else 'NOT SET'}")
        return

    try:
        # Format the conversation into readable text
        transcript = format_transcript(conversation_history)
        print(f"🔍 Transcript formatted, length: {len(transcript)} characters")

        # Twilio SMS has a 1600 character limit
        # Split into multiple messages if needed
        MAX_SMS_LENGTH = 1500  # Leave buffer for message numbering

        if len(transcript) <= MAX_SMS_LENGTH:
            # Single message - send as is
            print(f"🔍 Attempting to send SMS from {TWILIO_PHONE_NUMBER} to {phone_number}...")
            message = twilio_client.messages.create(
                body=transcript,
                from_=TWILIO_PHONE_NUMBER,
                to=phone_number
            )
            print(f"✓ Transcript sent successfully to {phone_number}")
            print(f"  Message SID: {message.sid}")
        else:
            # Multiple messages needed - split intelligently
            print(f"📩 Transcript is {len(transcript)} chars - splitting into multiple messages...")

            # Split on double newlines (between messages) to avoid breaking mid-thought
            chunks = []
            current_chunk = ""

            for line in transcript.split("\n"):
                # If adding this line would exceed limit, start new chunk
                if len(current_chunk) + len(line) + 1 > MAX_SMS_LENGTH:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                        current_chunk = line + "\n"
                else:
                    current_chunk += line + "\n"

            # Add remaining chunk
            if current_chunk:
                chunks.append(current_chunk.strip())

            # Send each chunk with numbering
            total_parts = len(chunks)
            for i, chunk in enumerate(chunks, 1):
                message_body = f"({i}/{total_parts})\n\n{chunk}"

                print(f"🔍 Sending part {i}/{total_parts} from {TWILIO_PHONE_NUMBER} to {phone_number}...")
                message = twilio_client.messages.create(
                    body=message_body,
                    from_=TWILIO_PHONE_NUMBER,
                    to=phone_number
                )
                print(f"✓ Part {i}/{total_parts} sent - SID: {message.sid}")

            print(f"✓ All {total_parts} transcript parts sent successfully to {phone_number}")

    except Exception as e:
        # Log error but don't crash if SMS fails
        print(f"✗ Error sending SMS transcript: {e}")
        import traceback
        print(f"   Full traceback:\n{traceback.format_exc()}")

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """
    Handle incoming phone calls with TwiML.

    This endpoint is called by Twilio when someone calls your Twilio number.
    We capture the caller's phone number and create a unique session ID.
    """
    # ========================================
    # STEP 5: Capture Caller's Phone Number
    # ========================================

    # Get form data from Twilio's webhook
    # Twilio sends the caller's number in the 'From' parameter
    form_data = await request.form()
    caller_number = form_data.get('From')

    # Generate a unique session ID for this call
    # This ID will be passed to the WebSocket to link the phone number to the connection
    session_id = str(uuid.uuid4())

    # Store the session information with all required fields
    # - caller_number: Used to send SMS transcript after call ends
    # - conversation_history: Accumulates all messages during the call
    # - transcript_sent: Prevents duplicate SMS if cleanup runs multiple times
    active_sessions[session_id] = {
        'caller_number': caller_number,
        'conversation_history': [],
        'transcript_sent': False
    }

    # ========================================
    # STEP 6: Build TwiML Response
    # ========================================

    # Begin building the TwiML
    response = VoiceResponse()

    # Connect to WebSocket with session ID in the URL
    # The session_id query parameter will be available in the WebSocket handler
    host = request.url.hostname
    connect = Connect()
    connect.conversation_relay(
        url=f'wss://{host}/ws?session_id={session_id}',  # Pass session ID to WebSocket
        voice='Joanna-Neural',
        language='en-US',
        tts_provider='amazon'
    )

    response.say("You're connected to the Rubber Duck Debugger, powered by Twilio and Claude", voice="Polly.Joanna-Neural")

    response.append(connect)


    return HTMLResponse(content=str(response), media_type="application/xml")


@app.websocket("/ws")
async def handle_websocket(websocket: WebSocket):
    """
    Handle WebSocket connection for ConversationRelay.

    This function:
    1. Maintains real-time conversation with the caller via Claude AI
    2. Validates all incoming messages using Pydantic model `WebSocketMessage`
    3. Streams AI responses back to Twilio for text-to-speech
    4. Sends conversation transcript via SMS in the finally block
    """
    print("=== Client connected to ConversationRelay WebSocket ===")
    await websocket.accept()
    print("=== WebSocket connection accepted ===")

    # ========================================
    # STEP 7: Extract Session Information
    # ========================================

    # Get the session_id from query parameters passed by /incoming-call endpoint
    session_id = websocket.query_params.get('session_id')

    # Retrieve session data containing caller_number, conversation_history, and transcript_sent flag
    session_data = active_sessions.get(session_id)

    # Get reference to conversation history for this call
    # This is shared with the session, so updates here will persist in active_sessions dict
    conversation_history = session_data['conversation_history']

    # Flag to track if user interrupted AI response. Used to stop streaming from Claude back to user
    interrupted = False

    print(f"📱 Session {session_id[:8]}... connected")
    if session_data['caller_number']:
        print(f"   Caller: {session_data['caller_number']}")

    try:
        async for message in websocket.iter_text():
            # ========================================
            # STEP 8: Validate Incoming Messages
            # ========================================
            try:
                # Parse JSON and validate ws message with Pydantic
                data = json.loads(message)
                print(f"🔍 Raw Twilio message: {json.dumps(data, indent=2)}")

                ws_message = WebSocketMessage(**data)
                event_type = ws_message.type
                print(f"=== Received event type: {event_type} ===")

            except json.JSONDecodeError as e:
                # Skip messages that aren't valid JSON
                print(f"❌ Invalid JSON received: {e}")
                continue
            except Exception as e:
                # Skip messages that fail WebSocketMessage validation
                print(f"❌ Message validation failed: {e}")
                continue

            # ========================================
            # STEP 9: Handle Different Event Types
            # ========================================

            # Setup event: Connection established, no response needed
            if event_type == 'setup':
                print("Setup received - ready for prompts")

            # Prompt event: User spoke, call Claude for response
            elif event_type == 'prompt':
                print("Recieved prompt event")
                user_message = ws_message.voicePrompt or ''
                print(f"User said: {user_message}")

                # Add user message to conversation history
                conversation_history.append({
                    "role": "user",
                    "content": user_message
                })

                # # Pause before responding - this gives the user time to think
                # # The rubber duck debugging method works because explaining the problem
                # # often leads to discovering the solution. This pause encourages that.
                print(f"⏸️  Pausing for {THINKING_PAUSE_SECONDS} seconds to let user think...")
                await asyncio.sleep(THINKING_PAUSE_SECONDS)
                print("▶️  Pause complete, generating AI response...")

                try:
                    # Reset interrupt flag before starting new response
                    interrupted = False

                    # Call Claude API with streaming enabled for lower latency
                    stream = anthropic_client.messages.create(
                        model="claude-sonnet-4-6",
                        max_tokens=512,
                        system=SYSTEM_MESSAGE,
                        messages=conversation_history,
                        stream=True
                    )

                    # Accumulate the full assistant response for conversation history
                    full_response = ""

                    # Stream tokens back to Twilio as they arrive from Claude
                    for event in stream:
                        # Check if user interrupted
                        if interrupted:
                            print("🛑 Streaming cancelled due to user interrupt")
                            stream.close()
                            break

                        if event.type == "content_block_start":
                            print("Stream started")

                        elif event.type == "content_block_delta":
                            if hasattr(event.delta, 'text'):
                                token = event.delta.text
                                full_response += token

                                # Send each token immediately to Twilio
                                # Setting 'last': False allows more tokens to follow
                                token_message = {
                                    'type': 'text',
                                    'token': token,
                                    'last': False,
                                    'interruptible': True #Send interruptible=True with every token so that user can interrupt at any time during the response
                                }
                                await websocket.send_json(token_message)

                        elif event.type == "content_block_stop":
                            print(f"Stream complete. Full response: {full_response}")

                    # Send final message indicating completion (or cancellation)
                    if not interrupted:
                        final_message = {
                            'type': 'text',
                            'token': '',
                            'last': True,
                            'interruptible': True
                        }
                        await websocket.send_json(final_message)
                        print("Response sent and saved to history")
                    else:
                        print("Response interrupted - not saving to history")

                    # Add complete assistant response to conversation history (only if not interrupted)
                    if not interrupted and full_response:
                        conversation_history.append({
                            "role": "assistant",
                            "content": full_response
                        })

                except Exception as e:
                    print(f"Error calling Claude API: {e}")
                    error_response = {
                        'type': 'text',
                        'token': "I'm sorry, I'm having trouble processing that right now.",
                        'last': True,
                        'interruptible': True
                    }
                    await websocket.send_json(error_response)

            # Interrupt event: User interrupted AI response (e.g., by speaking)
            elif event_type == 'interrupt':
                print("Conversation interrupted by user - setting interrupt flag")
                interrupted = True

    except Exception as e:
        # Catch any unexpected errors that occur during the WebSocket loop
        # This prevents crashes and logs the error for debugging
        print(f"❌ Error in WebSocket handler: {e}")
        import traceback
        print(traceback.format_exc())

    finally:
        # ========================================
        # STEP 10: Cleanup and Send Transcript
        # ========================================
        # This block runs when the WebSocket connection closes
        # (the async iterator exits naturally when the connection ends)
        # The transcript_sent flag ensures SMS is only sent once

        print("🧹 WebSocket closing - final cleanup")

        # Extract session data for SMS sending
        caller_number = session_data.get('caller_number')
        transcript_sent = session_data.get('transcript_sent', False)

        print(f"🔍 FINAL - Caller number: {caller_number}")
        print(f"🔍 FINAL - Conversation history length: {len(conversation_history)}")
        print(f"🔍 FINAL - Transcript already sent: {transcript_sent}")

        # Send SMS transcript if:
        # 1. We have a valid phone number
        # 2. Conversation history is not empty
        # 3. We haven't already sent the transcript (prevents duplicates)
        if caller_number and conversation_history and not transcript_sent:
            print(f"📤 Sending transcript to {caller_number}...")
            send_sms_transcript(caller_number, conversation_history)
            session_data['transcript_sent'] = True  # Mark as sent
        elif transcript_sent:
            print("✓ Transcript already sent - skipping duplicate")
        elif not caller_number:
            print("⚠ Cannot send transcript - no caller number")
        elif not conversation_history:
            print("⚠ Cannot send transcript - No conversation to send because the history is empty.")

        # Remove session from memory - no longer needed after call ends
        if session_id and session_id in active_sessions:
            del active_sessions[session_id]
            print(f"✅ Session {session_id[:8]}... cleaned up")

        print("👋 WebSocket handler complete")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
