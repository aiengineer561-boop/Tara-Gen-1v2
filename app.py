from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
import uvicorn
import json
from datetime import datetime

app = FastAPI(title="Robot Event API", version="2.2.0")

# -----------------------------
# CORS
# -----------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# In-memory event store
# robot_id -> list of events
# -----------------------------
EVENT_STORE: Dict[str, List[Dict[str, Any]]] = {}

# -----------------------------
# WebSocket Manager
# -----------------------------
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for ws in self.active_connections:
            try:
                await ws.send_json(message)
            except:
                pass


manager = ConnectionManager()

# -----------------------------
# Models
# -----------------------------
class EventRequest(BaseModel):
    eventname: str = Field(..., min_length=1)

    class Config:
        extra = "allow"


class EventPayload(BaseModel):
    class Config:
        extra = "allow"


class EventResponse(BaseModel):
    status: str
    event: str
    message: str
    data: Dict[str, Any] | None
    timestamp: str


# -----------------------------
# Helpers
# -----------------------------
def store_event(robot_id: str, event_name: str, data: Dict[str, Any], timestamp: str):
    EVENT_STORE.setdefault(robot_id, []).append({
        "event": event_name,
        "data": data,
        "timestamp": timestamp
    })


# -----------------------------
# Root
# -----------------------------
@app.get("/")
async def root():
    return {
        "name": "Robot Event API",
        "version": "2.2.0",
        "endpoints": [
            "POST /event/{robot_id}/{event_name}",
            "GET /event/{robot_id}",
            "GET /event/{robot_id}/{event_name}",
            "WebSocket /ws"
        ]
    }


# -----------------------------
# GET robot events
# -----------------------------
@app.get("/event/{robot_id}")
async def get_robot_events(
    robot_id: str,
    limit: int = Query(20, ge=1, le=100)
):
    events = EVENT_STORE.get(robot_id, [])[-limit:]
    return {
        "robot": robot_id,
        "events": events,
        "count": len(events)
    }


@app.get("/event/{robot_id}/{event_name}")
async def get_robot_event_by_name(
    robot_id: str,
    event_name: str,
    limit: int = Query(20, ge=1, le=100)
):
    events = [
        e for e in EVENT_STORE.get(robot_id, [])
        if e["event"] == event_name
    ][-limit:]

    return {
        "robot": robot_id,
        "event": event_name,
        "events": events,
        "count": len(events)
    }


# -----------------------------
# POST robot event
# -----------------------------
@app.post("/event/{robot_id}/{event_name}", response_model=EventResponse)
async def post_robot_event(
    robot_id: str,
    event_name: str,
    payload: EventPayload
):
    timestamp = datetime.utcnow().isoformat()
    data = payload.model_dump()

    store_event(robot_id, event_name, data, timestamp)

    await manager.broadcast({
        "type": "event",
        "robot": robot_id,
        "event": event_name,
        "data": data,
        "timestamp": timestamp
    })

    return EventResponse(
        status="success",
        event=event_name,
        message=f"Robot {robot_id}: event received",
        data={"robot": robot_id, **data} if data else {"robot": robot_id},
        timestamp=timestamp
    )


# -----------------------------
# WebSocket
# -----------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# -----------------------------
# Health
# -----------------------------
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "robots": len(EVENT_STORE),
        "connections": len(manager.active_connections)
    }


# -----------------------------
# Run
# -----------------------------
if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
