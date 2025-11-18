from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from seed_data import seed_data
from schema import SendMessageRequest
from ai_client import ai_agent


# -------------------------------------------------
# FastAPI Application Initialization
# -------------------------------------------------
app = FastAPI(title="Restaurant Reservation System")
# -------------------------------------------------
# Middleware Configuration
# -------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------
# Startup Event: Create and Populate Database
# -------------------------------------------------
@app.on_event("startup")
async def startup_event():
    """Initializes database tables and populates sample data when app starts. Also initializes the reservation agent with default user_id 1."""
    try:
        print("Initializing database and seeding sample data...")
        seed_data()
        print("Database initialized and ready.")
    except Exception as e:
        print("Error during startup:", str(e))

# -------------------------------------------------
# Routes
# -------------------------------------------------

@app.get("/")
def health_check():
    """Health check endpoint."""
    return {"status": "OK"}

@app.post("/chat/send", summary="Reservation Chat")
async def send_message(request: SendMessageRequest):
    if ai_agent is None:
        raise HTTPException(400, "Chat not started. Call /chat/start first.")
    result = await ai_agent.run_query(request.message)
    return result