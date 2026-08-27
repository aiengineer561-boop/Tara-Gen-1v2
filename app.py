from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
import uvicorn
import json
import re
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
            "POST /map_poi/{robot_id}",
            "POST /navigation/create/",
            "GET /map_poi",
            "GET /map_poi/{robot_id}",
            "GET /map_poi/{robot_id}/{poi_name}",
            "DELETE /map_poi/{robot_id}",
            "DELETE /map_poi/{robot_id}/{poi_name}",
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
# MAP POI store
# robot_id -> list of poi records (keyed/upserted on navigation_id)
# A single class can hold several points (c1p1, c1p2 -> class_1), so POIs
# are kept as a list rather than a name -> poi map.
# -----------------------------
POI_STORE: Dict[str, List[Dict[str, Any]]] = {}


class POIUploadRequest(BaseModel):
    """Upload POI names, the same list startup/py collects from the robot.

    {"names": ["c1p1", "c1p2", "home"]}
    """
    names: List[str] = Field(..., min_length=1)
    replace: bool = False


class POICreateRequest(BaseModel):
    """Payload sent by the robot startup script, one POI per request."""
    name: str = Field(..., min_length=1)
    robot: str = Field(..., min_length=1)
    navigation_id: Optional[str] = None


# -----------------------------
# POI helpers
# -----------------------------
def format_poi_name(name: str) -> str:
    """c1p1 / c1p2 -> class_1, same rule the startup script uses."""
    cleaned = name.strip().lower()
    match = re.match(r"^c(\d+)p\d+$", cleaned)
    if match:
        return f"class_{match.group(1)}"
    return cleaned


def make_navigation_id(robot_id: str, index: int) -> str:
    return f"{robot_id}-{index:02d}"


def store_poi(robot_id: str, poi: Dict[str, Any]) -> Dict[str, Any]:
    """Insert the POI, replacing any existing one with the same navigation_id."""
    pois = POI_STORE.setdefault(robot_id, [])
    for i, existing in enumerate(pois):
        if existing["navigation_id"] == poi["navigation_id"]:
            pois[i] = poi
            return poi
    pois.append(poi)
    return poi


def match_poi(poi: Dict[str, Any], key: str) -> bool:
    """A POI is addressable by its formatted name or its navigation_id."""
    return poi["navigation_id"] == key or poi["name"] == format_poi_name(key)


# -----------------------------
# Upload map POIs
# -----------------------------
@app.post("/map_poi/{robot_id}")
async def upload_map_pois(robot_id: str, payload: POIUploadRequest):
    if payload.replace:
        POI_STORE[robot_id] = []

    timestamp = datetime.utcnow().isoformat()
    start = len(POI_STORE.get(robot_id, []))
    saved: List[Dict[str, Any]] = []

    for i, name in enumerate(payload.names):
        saved.append(store_poi(robot_id, {
            "name": format_poi_name(name),
            "original_name": name,
            "robot": robot_id,
            "navigation_id": make_navigation_id(robot_id, start + i + 1),
            "timestamp": timestamp,
        }))

    await manager.broadcast({
        "type": "map_poi_upload",
        "robot": robot_id,
        "pois": saved,
        "count": len(saved),
        "timestamp": timestamp,
    })

    return {
        "status": "success",
        "robot": robot_id,
        "uploaded": len(saved),
        "total": len(POI_STORE.get(robot_id, [])),
        "pois": saved,
        "timestamp": timestamp,
    }


@app.post("/navigation/create/")
async def create_navigation_poi(payload: POICreateRequest):
    """Single-POI endpoint matching what startup/py posts to /navigation/create/."""
    robot_id = payload.robot
    timestamp = datetime.utcnow().isoformat()
    formatted = format_poi_name(payload.name)
    index = len(POI_STORE.get(robot_id, [])) + 1

    poi = store_poi(robot_id, {
        "name": formatted,
        "original_name": payload.name,
        "robot": robot_id,
        "navigation_id": payload.navigation_id or make_navigation_id(robot_id, index),
        "timestamp": timestamp,
    })

    await manager.broadcast({
        "type": "map_poi_upload",
        "robot": robot_id,
        "pois": [poi],
        "count": 1,
        "timestamp": timestamp,
    })

    return {"status": "success", "robot": robot_id, "poi": poi, "timestamp": timestamp}


# -----------------------------
# Get map POIs
# -----------------------------
@app.get("/map_poi")
async def get_all_map_pois():
    return {
        "robots": POI_STORE,
        "count": sum(len(p) for p in POI_STORE.values()),
    }


@app.get("/map_poi/{robot_id}")
async def get_map_pois(robot_id: str, names_only: bool = Query(False)):
    pois = POI_STORE.get(robot_id, [])
    return {
        "robot": robot_id,
        "pois": [p["name"] for p in pois] if names_only else pois,
        "count": len(pois),
    }


@app.get("/map_poi/{robot_id}/{poi_name}")
async def get_map_poi(robot_id: str, poi_name: str):
    matches = [p for p in POI_STORE.get(robot_id, []) if match_poi(p, poi_name)]
    if not matches:
        raise HTTPException(status_code=404, detail=f"POI '{poi_name}' not found for robot {robot_id}")
    return {"robot": robot_id, "pois": matches, "count": len(matches)}


# -----------------------------
# Delete map POIs
# -----------------------------
@app.delete("/map_poi/{robot_id}")
async def delete_map_pois(robot_id: str):
    removed = len(POI_STORE.pop(robot_id, []))
    timestamp = datetime.utcnow().isoformat()

    await manager.broadcast({
        "type": "map_poi_delete",
        "robot": robot_id,
        "deleted": removed,
        "timestamp": timestamp,
    })

    return {"status": "success", "robot": robot_id, "deleted": removed, "timestamp": timestamp}


@app.delete("/map_poi/{robot_id}/{poi_name}")
async def delete_map_poi(robot_id: str, poi_name: str):
    pois = POI_STORE.get(robot_id, [])
    removed = [p for p in pois if match_poi(p, poi_name)]
    if not removed:
        raise HTTPException(status_code=404, detail=f"POI '{poi_name}' not found for robot {robot_id}")

    POI_STORE[robot_id] = [p for p in pois if not match_poi(p, poi_name)]
    timestamp = datetime.utcnow().isoformat()

    await manager.broadcast({
        "type": "map_poi_delete",
        "robot": robot_id,
        "poi": poi_name,
        "deleted": len(removed),
        "timestamp": timestamp,
    })

    return {
        "status": "success",
        "robot": robot_id,
        "pois": removed,
        "deleted": len(removed),
        "timestamp": timestamp,
    }


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
        "poi_robots": len(POI_STORE),
        "pois": sum(len(p) for p in POI_STORE.values()),
        "connections": len(manager.active_connections)
    }


# -----------------------------
# Run
# -----------------------------
if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)

