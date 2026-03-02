import os
import json
import uuid  # For generating unique session IDs

from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect
from twilio.rest import Client  # Twilio REST client for sending SMS
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

# ========================================
# STEP 1: Configuration and API Keys
# ========================================

# Anthropic API key for Claude AI
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
PORT = int(os.getenv('PORT', 5050))

# Twilio credentials for sending SMS transcripts
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')

# Telling the AI how to behave
SYSTEM_MESSAGE = ("""
System Role: You are a Rubber Duck Debugger, a virtual rubber duck debugging assistant: en.wikipedia.org/wiki/Rubber_duck_debugging. Your job is to help developers solve coding problems through the "Rubber Duck Debugging" method via voice call.

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

# Initialize Anthropic client for AI responses
anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)

# Initialize Twilio client for sending SMS (only if credentials are provided)
# This is optional - if not configured, transcripts just won't be sent
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
# Value: dict containing 'caller_number' and 'conversation_history'
# This allows us to track which phone number belongs to which WebSocket connection
active_sessions = {}


# ========================================
# STEP 4: Helper Function to Format Transcript
# ========================================

def format_transcript(conversation_history):
    """
    Convert conversation history into a readable SMS transcript.

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
            # Duck's messages are prefixed with "Duck:"
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
    print(f"🔍 send_sms_transcript called with phone: {phone_number}, history items: {len(conversation_history)}")

    # Check if Twilio is configured
    if not twilio_client:
        print("⚠ Cannot send SMS - Twilio not configured")
        print(f"   TWILIO_ACCOUNT_SID: {'set' if TWILIO_ACCOUNT_SID else 'NOT SET'}")
        print(f"   TWILIO_AUTH_TOKEN: {'set' if TWILIO_AUTH_TOKEN else 'NOT SET'}")
        print(f"   TWILIO_PHONE_NUMBER: {TWILIO_PHONE_NUMBER if TWILIO_PHONE_NUMBER else 'NOT SET'}")
        return

    # Check if we have any conversation to send
    if not conversation_history:
        print("⚠ No conversation to send - history is empty")
        return

    try:
        # Format the conversation into readable text
        transcript = format_transcript(conversation_history)
        print(f"🔍 Transcript formatted, length: {len(transcript)} characters")

        # Twilio SMS has a 1600 character limit
        # If transcript is too long, we'll truncate and add a note
        if len(transcript) > 1500:
            transcript = transcript[:1500] + "\n\n... (Transcript truncated due to SMS length limits)"
            print(f"⚠ Transcript truncated to 1500 characters")

        # Send the SMS using Twilio's API
        print(f"🔍 Attempting to send SMS from {TWILIO_PHONE_NUMBER} to {phone_number}...")
        message = twilio_client.messages.create(
            body=transcript,
            from_=TWILIO_PHONE_NUMBER,  # Your Twilio number
            to=phone_number              # Caller's number
        )

        print(f"✓ Transcript sent successfully to {phone_number}")
        print(f"  Message SID: {message.sid}")
        print(f"  Status: {message.status}")

    except Exception as e:
        # Log error but don't crash if SMS fails
        print(f"✗ Error sending SMS transcript: {e}")
        import traceback
        print(f"   Full traceback:\n{traceback.format_exc()}")


@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio ConversationRelay with Claude is running!"}


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
    caller_number = form_data.get('From')  # E.164 format like +15551234567
    print(caller_number)

    # Generate a unique session ID for this call
    # This ID will be passed to the WebSocket to link the phone number to the connection
    session_id = str(uuid.uuid4())

    # Store the session information
    # We'll use this in the WebSocket handler to know who to send the transcript to
    active_sessions[session_id] = {
        'caller_number': caller_number,
        'conversation_history': []  # Will be populated during the call
    }

    print(f"📞 Incoming call from {caller_number}")
    print(f"   Session ID: {session_id}")

    # ========================================
    # STEP 6: Build TwiML Response
    # ========================================

    response = VoiceResponse()
    response.say("Please wait while we connect your call to the Rubber Duck Debugger, powered by Twilio and Claude")
    response.pause(length=1)
    response.say("O.K. you can start talking now!")

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
    response.append(connect)

    return HTMLResponse(content=str(response), media_type="application/xml")


@app.websocket("/ws")
async def handle_websocket(websocket: WebSocket):
    """
    Handle WebSocket connection for ConversationRelay.

    This maintains the real-time conversation with the caller and sends
    the transcript via SMS when the call ends.
    """
    print("=== Client connected to ConversationRelay WebSocket ===")
    await websocket.accept()
    print("=== WebSocket connection accepted ===")

    # ========================================
    # STEP 7: Extract Session Information
    # ========================================

    # Get the session_id from query parameters
    # This was passed in the URL from the /incoming-call endpoint
    session_id = websocket.query_params.get('session_id')

    # Retrieve session data (contains caller_number and conversation_history)
    session_data = active_sessions.get(session_id)

    if not session_data:
        print(f"⚠ Warning: No session found for ID {session_id}")
        # Create a fallback session if something went wrong
        session_data = {
            'caller_number': None,
            'conversation_history': []
        }

    # Get reference to the conversation history for this call
    # This is shared with the session, so updates here persist in active_sessions
    conversation_history = session_data['conversation_history']

    print(f"📱 Session {session_id[:8]}... connected")
    if session_data['caller_number']:
        print(f"   Caller: {session_data['caller_number']}")

    try:
        async for message in websocket.iter_text():
            print(f"=== Raw message received: {message[:200]}...")
            data = json.loads(message)
            print(f"=== Parsed data: {json.dumps(data, indent=2)}")

            # Log incoming messages for debugging
            event_type = data.get('type')
            print(f"=== Received event type: {event_type} ===")

            # DEBUG: Log all event types we receive
            print(f"🔍 EVENT DEBUG - Type: '{event_type}' | Known types: setup, prompt, interrupt, disconnect")

            # Handle setup event
            if event_type == 'setup':
                # No response needed for setup, just log it
                print("Setup received - ready for prompts")

            # Handle prompt event - this is where we call Claude
            elif event_type == 'prompt':
                user_message = data.get('voicePrompt', '')
                print(f"User said: {user_message}")

                # Add user message to conversation history
                conversation_history.append({
                    "role": "user",
                    "content": user_message
                })

                try:
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
                                    'interruptible': True
                                }
                                await websocket.send_json(token_message)

                        elif event.type == "content_block_stop":
                            print(f"Stream complete. Full response: {full_response}")

                    # Send final message indicating completion
                    final_message = {
                        'type': 'text',
                        'token': '',
                        'last': True,
                        'interruptible': True
                    }
                    await websocket.send_json(final_message)

                    # Add complete assistant response to conversation history
                    conversation_history.append({
                        "role": "assistant",
                        "content": full_response
                    })
                    print("Response sent and saved to history")

                except Exception as e:
                    print(f"Error calling Claude API: {e}")
                    error_response = {
                        'type': 'text',
                        'token': "I'm sorry, I'm having trouble processing that right now.",
                        'last': True,
                        'interruptible': True
                    }
                    await websocket.send_json(error_response)

            # Handle interrupt event
            elif event_type == 'interrupt':
                print("Conversation interrupted by user")

            # Handle disconnect event
            elif event_type == 'disconnect':
                print("📴 Call ended - preparing to send transcript")

                # ========================================
                # STEP 8: Send SMS Transcript on Disconnect
                # ========================================

                # Get the caller's phone number from this session
                caller_number = session_data.get('caller_number')

                # DEBUG: Print detailed information about what we have
                print(f"🔍 DEBUG - Session ID: {session_id}")
                print(f"🔍 DEBUG - Caller number: {caller_number}")
                print(f"🔍 DEBUG - Conversation history length: {len(conversation_history)}")
                print(f"🔍 DEBUG - Twilio client initialized: {twilio_client is not None}")

                # Only send SMS if we have a valid phone number and conversation history
                if caller_number and conversation_history:
                    print(f"📤 Sending transcript to {caller_number}...")
                    send_sms_transcript(caller_number, conversation_history)
                elif not caller_number:
                    print("⚠ Cannot send transcript - caller_number is missing or empty")
                elif not conversation_history:
                    print("⚠ Cannot send transcript - conversation_history is empty")

                # Clean up the session from memory
                # No need to keep it around after the call ends
                if session_id in active_sessions:
                    del active_sessions[session_id]
                    print(f"🧹 Session {session_id[:8]}... cleaned up")

                break

    except WebSocketDisconnect:
        print("🔌 WebSocket disconnected (this is normal when call ends)")

        # ========================================
        # STEP 9: Handle WebSocket Disconnects
        # ========================================
        # This is likely where we end up when the call ends
        # Twilio may close the WebSocket without sending a 'disconnect' event

    except Exception as e:
        print(f"❌ Error in WebSocket handler: {e}")
        import traceback
        print(traceback.format_exc())

    finally:
        # ========================================
        # FINAL CLEANUP - ALWAYS RUNS
        # ========================================
        # This block runs no matter how the WebSocket closes
        # (normal disconnect event, WebSocketDisconnect exception, or error)

        print("🧹 WebSocket closing - final cleanup")

        # Get the caller's phone number from this session
        caller_number = session_data.get('caller_number')

        # DEBUG: Show what we have
        print(f"🔍 FINAL - Caller number: {caller_number}")
        print(f"🔍 FINAL - Conversation history length: {len(conversation_history)}")

        # Try to send transcript if we have the necessary data
        if caller_number and conversation_history:
            print(f"📤 Sending transcript to {caller_number}...")
            send_sms_transcript(caller_number, conversation_history)
        elif not caller_number:
            print("⚠ Cannot send transcript - no caller number")
        elif not conversation_history:
            print("⚠ Cannot send transcript - conversation history is empty")

        # Clean up session from memory
        if session_id and session_id in active_sessions:
            del active_sessions[session_id]
            print(f"✅ Session {session_id[:8]}... cleaned up")

        print("👋 WebSocket handler complete")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
