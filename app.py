from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Any, Dict
import uvicorn

app = FastAPI(title="Event API", version="1.0.0")

# CORS configuration for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For production, specify your frontend domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class EventRequest(BaseModel):
    eventname: str = Field(..., min_length=1, description="Event identifier")
    # Allow arbitrary additional fields
    
    class Config:
        extra = "allow"  # Permits any additional fields beyond eventname


class EventResponse(BaseModel):
    status: str
    event: str
    message: str
    data: Dict[str, Any] | None = None


@app.post("/event", response_model=EventResponse)
async def handle_event(event: EventRequest) -> EventResponse:
    """
    Process any event with flexible payload.
    
    The eventname field is required, all other fields are optional.
    """
    # Extract event name
    event_name = event.eventname
    
    # Get all additional fields passed in the request
    event_data = event.model_dump(exclude={"eventname"})
    
    # Route based on event type (add your custom logic here)
    match event_name.lower():
        case "handshake":
            return EventResponse(
                status="success",
                event=event_name,
                message="Handshake acknowledged",
                data=event_data if event_data else None
            )
        case "ping":
            return EventResponse(
                status="success",
                event=event_name,
                message="Pong",
                data=event_data if event_data else None
            )
        case _:
            # Handle any other event type
            return EventResponse(
                status="success",
                event=event_name,
                message=f"Event '{event_name}' received",
                data=event_data if event_data else None
            )


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    import os
    
    port = int(os.getenv("PORT", 8000))  # Render sets PORT env var
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False,  # Disabled for production
        log_level="info"
    )
