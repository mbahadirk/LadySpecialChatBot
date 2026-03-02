from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Mount Static Files
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "my_secure_token")

# --- Database Manager ---
from database_manager import DatabaseManager
db_manager = DatabaseManager(check_interval=30)
# Start watching on startup (Best practice: explicit startup event)
@app.on_event("startup")
async def startup_event():
    db_manager.start_watching()
    
@app.on_event("shutdown")
async def shutdown_event():
    db_manager.stop()

# --- UI Endpoints ---
class ChatRequest(BaseModel):
    text: str

@app.get("/")
async def read_index():
    from fastapi.responses import FileResponse
    return FileResponse('static/index.html')

# Global Orchestrator Instance for Memory
from orchestrator import Orchestrator
global_orchestrator = Orchestrator()

@app.post("/api/chat")
async def api_chat(request: ChatRequest):
    print(f"UI Text: {request.text}")
    
    # Check if link
    if "http" in request.text:
         response = global_orchestrator.handle_link(request.text)
    else:
         response = global_orchestrator.handle_text(request.text)
    
    # Update Memory
    global_orchestrator.AddToHistory("user", request.text)
    global_orchestrator.AddToHistory("assistant", response)
         
    return {"response": response}

@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    print(f"UI Image Upload: {file.filename}")
    
    contents = await file.read()
    response = global_orchestrator.handle_image(contents)
    
    # Update Memory for image too? Maybe just response
    global_orchestrator.AddToHistory("user", "[Görsel Yüklendi]")
    global_orchestrator.AddToHistory("assistant", response)
    
    return {"response": response}

# --- Meta Webhooks ---
@app.get("/webhook")
async def verify_webhook(request: Request):
    """
    Meta verification endpoint.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            print("WEBHOOK_VERIFIED")
            return int(challenge)
        else:
            raise HTTPException(status_code=403, detail="Verification token mismatch")
    
    raise HTTPException(status_code=400, detail="Missing parameters")

@app.post("/webhook")
async def receive_webhook(request: Request):
    """
    Endpoint to receive messages from WhatsApp/Instagram.
    """
    data = await request.json()
    print("Received Webhook Data:", data)
    
    # Initialize Orchestrator (Lazy verify)
    from orchestrator import Orchestrator
    orchestrator = Orchestrator()
    
    # In reality, Meta webhook structure is nested: entry -> changes -> value -> messages
    # We'll simplisticly assume we extract the message object here or iterate
    # For prototype: assume 'data' contains the relevant message info directly or we parse it
    # Simplified parsing for 'entry' array from Meta:
    try:
        if "entry" in data:
            for entry in data["entry"]:
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    if "messages" in value:
                        for msg in value["messages"]:
                            msg_type, msg_content = orchestrator.analyze_message(msg)
                            print(f"Message Type: {msg_type}")
                            
                            response = None
                            if msg_type == "text":
                                response = orchestrator.handle_text(msg_content)
                            elif msg_type == "image":
                                response = orchestrator.handle_image(msg_content) # TODO: Need to fetch URL from ID
                            elif msg_type == "link":
                                response = orchestrator.handle_link(msg_content)
                            
                            print(f"Generated Response: {response}")
                            # TODO: Send response back via Meta API
                            
    except Exception as e:
        print(f"Error processing webhook: {e}")
        
    return {"status": "received"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
