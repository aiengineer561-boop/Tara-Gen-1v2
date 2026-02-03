from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
import uvicorn
import json
from datetime import datetime

app = FastAPI(title="Event API", version="2.0.0")

# CORS configuration for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For production, specify your frontend domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass


manager = ConnectionManager()


@app.get("/")
async def root():
    """Root endpoint - API info"""
    return {
        "name": "Event API",
        "version": "2.0.0",
        "status": "running",
        "endpoints": {
            "GET /event": "Get event info or query events",
            "POST /event": "Send events",
            "WebSocket /ws": "Real-time event streaming",
            "GET /health": "Health check",
            "GET /docs": "API documentation"
        }
    }


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
    timestamp: str


# GET /event - Query or get event info
@app.get("/event")
async def get_event(
    eventname: Optional[str] = Query(None, description="Filter by event name"),
    limit: int = Query(10, ge=1, le=100, description="Number of events to return")
):
    """
    GET endpoint for querying events or getting event information.
    
    Examples:
    - GET /event - Returns API info about events
    - GET /event?eventname=handshake - Filter by event name
    - GET /event?limit=5 - Limit results
    """
    if eventname:
        return {
            "status": "success",
            "query": {
                "eventname": eventname,
                "limit": limit
            },
            "message": f"Query for events with name '{eventname}'",
            "note": "This is a placeholder - implement your event storage/retrieval logic here"
        }
    
    return {
        "status": "success",
        "message": "Event API ready",
        "available_events": ["handshake", "ping", "user_action", "custom"],
        "usage": {
            "POST /event": "Send events with any JSON payload containing 'eventname'",
            "GET /event?eventname=xyz": "Query events by name",
            "WebSocket /ws": "Connect for real-time event streaming"
        }
    }


# POST /event - Send events
@app.post("/event", response_model=EventResponse)
async def handle_event(event: EventRequest) -> EventResponse:
    """
    Process any event with flexible payload.
    
    The eventname field is required, all other fields are optional.
    Events are broadcast to all WebSocket connections.
    """
    # Extract event name
    event_name = event.eventname
    
    # Get all additional fields passed in the request
    event_data = event.model_dump(exclude={"eventname"})
    
    timestamp = datetime.utcnow().isoformat()
    
    # Prepare response
    response_data = None
    message = ""
    
    # Route based on event type (add your custom logic here)
    match event_name.lower():
        case "handshake":
            message = "Handshake acknowledged"
            response_data = event_data if event_data else None
        case "ping":
            message = "Pong"
            response_data = event_data if event_data else None
        case _:
            # Handle any other event type
            message = f"Event '{event_name}' received"
            response_data = event_data if event_data else None
    
    # Broadcast to all WebSocket connections
    broadcast_message = {
        "type": "event",
        "event": event_name,
        "data": event_data,
        "timestamp": timestamp
    }
    await manager.broadcast(broadcast_message)
    
    return EventResponse(
        status="success",
        event=event_name,
        message=message,
        data=response_data,
        timestamp=timestamp
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time event streaming.
    
    Connect to receive all events in real-time.
    You can also send events through this connection.
    """
    await manager.connect(websocket)
    
    # Send welcome message
    await manager.send_personal_message({
        "type": "connection",
        "status": "connected",
        "message": "WebSocket connection established",
        "timestamp": datetime.utcnow().isoformat()
    }, websocket)
    
    try:
        while True:
            # Receive data from WebSocket
            data = await websocket.receive_text()
            
            try:
                # Parse incoming message
                message = json.loads(data)
                
                # Check if it's an event
                if "eventname" in message:
                    event_name = message["eventname"]
                    event_data = {k: v for k, v in message.items() if k != "eventname"}
                    
                    # Process event
                    timestamp = datetime.utcnow().isoformat()
                    
                    # Broadcast to all connections
                    broadcast_message = {
                        "type": "event",
                        "event": event_name,
                        "data": event_data,
                        "timestamp": timestamp,
                        "source": "websocket"
                    }
                    await manager.broadcast(broadcast_message)
                    
                    # Send confirmation to sender
                    await manager.send_personal_message({
                        "type": "confirmation",
                        "status": "success",
                        "event": event_name,
                        "message": f"Event '{event_name}' broadcast to all connections",
                        "timestamp": timestamp
                    }, websocket)
                else:
                    # Echo back if not an event
                    await manager.send_personal_message({
                        "type": "echo",
                        "data": message,
                        "timestamp": datetime.utcnow().isoformat()
                    }, websocket)
                    
            except json.JSONDecodeError:
                await manager.send_personal_message({
                    "type": "error",
                    "message": "Invalid JSON format",
                    "timestamp": datetime.utcnow().isoformat()
                }, websocket)
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        # Broadcast disconnection
        await manager.broadcast({
            "type": "notification",
            "message": "A client disconnected",
            "timestamp": datetime.utcnow().isoformat()
        })


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "websocket_connections": len(manager.active_connections),
        "timestamp": datetime.utcnow().isoformat()
    }


if __name__ == "__main__":
    import os
    
    port = int(os.getenv("PORT", 8000))  # Render sets PORT env var
    
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=port,
        reload=False,  # Disabled for production
        log_level="info"
    )
