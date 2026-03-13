"""
Maine YIMBY Housing Watch — FastAPI backend
Serves agenda items, comp plan data, and advocacy content to the React dashboard.

Run locally:  uvicorn api:app --reload
Deploy:       see Dockerfile / render.yaml
"""

import asyncio
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from classifier import generate_advocacy_content
from db import ComprehensivePlan, RawAgendaItem, WatchlistEntry, get_session, init_db

app = FastAPI(title="Maine YIMBY Housing Watch API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # lock down to your dashboard domain in production
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()


# ---------------------------------------------------------------------------
# Agenda items
# ---------------------------------------------------------------------------
@app.get("/api/agendas")
def list_agendas(
    county:  Optional[str] = None,
    tag:     Optional[str] = None,
    urgency: Optional[str] = None,
    days:    int = Query(90, description="Items within next N days"),
    limit:   int = Query(100, le=500),
):
    session = get_session()
    q = session.query(RawAgendaItem)
    if county:
        q = q.filter(RawAgendaItem.county == county)
    if urgency:
        q = q.filter(RawAgendaItem.urgency == urgency)
    if tag:
        q = q.filter(RawAgendaItem.tags.contains(tag))
    items = q.order_by(RawAgendaItem.date.asc()).limit(limit).all()
    session.close()
    return [i.to_dict() for i in items]


@app.get("/api/agendas/{item_id}")
def get_agenda(item_id: int):
    session = get_session()
    item = session.query(RawAgendaItem).filter_by(id=item_id).first()
    session.close()
    if not item:
        raise HTTPException(404, "Item not found")
    return item.to_dict()


# ---------------------------------------------------------------------------
# Comp plans
# ---------------------------------------------------------------------------
@app.get("/api/comp-plans")
def list_comp_plans(
    county: Optional[str] = None,
    status: Optional[str] = None,
):
    session = get_session()
    q = session.query(ComprehensivePlan)
    if county:
        q = q.filter(ComprehensivePlan.county == county)
    if status:
        q = q.filter(ComprehensivePlan.status == status)
    plans = q.order_by(ComprehensivePlan.next_due.asc()).all()
    session.close()
    return [p.to_dict() for p in plans]


# ---------------------------------------------------------------------------
# Stats (summary cards)
# ---------------------------------------------------------------------------
@app.get("/api/stats")
def stats():
    session = get_session()
    urgent   = session.query(RawAgendaItem).filter_by(urgency="urgent").count()
    overdue  = session.query(ComprehensivePlan).filter_by(status="overdue").count()
    active   = session.query(ComprehensivePlan).filter_by(work_plan_active=True).count()
    due_soon = session.query(ComprehensivePlan).filter_by(status="due-soon").count()
    session.close()
    return {"urgent": urgent, "overdue": overdue, "active": active, "due_soon": due_soon}


# ---------------------------------------------------------------------------
# AI advocacy content
# ---------------------------------------------------------------------------
class AdvocacyRequest(BaseModel):
    mode:    str     # "briefing" | "comment" | "talking_points"
    item_id: int


@app.post("/api/advocate")
async def advocate(req: AdvocacyRequest):
    session = get_session()
    item = session.query(RawAgendaItem).filter_by(id=req.item_id).first()
    session.close()
    if not item:
        raise HTTPException(404, "Item not found")
    if req.mode not in ("briefing", "comment", "talking_points"):
        raise HTTPException(400, "Invalid mode")
    content = await asyncio.to_thread(
        generate_advocacy_content, req.mode, item.to_dict()
    )
    return {"content": content}


# ---------------------------------------------------------------------------
# Watchlist / digest preferences
# ---------------------------------------------------------------------------
class WatchlistRequest(BaseModel):
    email:     str
    towns:     list[str]
    frequency: str = "weekly"


@app.post("/api/watchlist")
def save_watchlist(req: WatchlistRequest):
    session = get_session()
    session.query(WatchlistEntry).filter_by(email=req.email).delete()
    for town in req.towns:
        session.add(WatchlistEntry(
            email=req.email, town=town, frequency=req.frequency,
            created_at=datetime.utcnow(),
        ))
    session.commit()
    session.close()
    return {"saved": len(req.towns)}


@app.get("/api/watchlist/{email}")
def get_watchlist(email: str):
    session = get_session()
    entries = session.query(WatchlistEntry).filter_by(email=email).all()
    session.close()
    return {"towns": [e.town for e in entries], "frequency": entries[0].frequency if entries else "weekly"}


# ---------------------------------------------------------------------------
# Trigger manual scrape run (protect with a secret header in production)
# ---------------------------------------------------------------------------
@app.post("/api/scrape")
async def trigger_scrape(x_scrape_key: str = Query(...)):
    import os
    if x_scrape_key != os.environ.get("SCRAPE_SECRET", "changeme"):
        raise HTTPException(403, "Invalid scrape key")
    from scraper import run_scraper
    asyncio.create_task(run_scraper())
    return {"status": "scrape started"}
