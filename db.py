"""
Maine YIMBY Housing Watch — Database models (SQLAlchemy + SQLite / Postgres)
Set DATABASE_URL env var for Postgres; defaults to local SQLite for dev.
"""

import os
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, String, Text, create_engine
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///maine_yimby.db")

# Heroku/Render expose Postgres as postgres://, SQLAlchemy needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, echo=False)
Session = sessionmaker(bind=engine)


def get_session():
    return Session()


class Base(DeclarativeBase):
    pass


class RawAgendaItem(Base):
    __tablename__ = "agenda_items"

    id               = Column(Integer, primary_key=True)
    fingerprint      = Column(String(64), unique=True, index=True)   # SHA-256 dedup key
    town             = Column(String(100), index=True)
    county           = Column(String(100), index=True)
    board            = Column(String(200))
    date             = Column(String(20), index=True)                 # YYYY-MM-DD
    title            = Column(String(300))
    summary          = Column(Text)
    tags             = Column(String(400))                            # comma-separated
    urgency          = Column(String(20), index=True)                 # urgent/high/medium/low
    yimby_opportunity= Column(Text)
    opposition_risk  = Column(String(20))
    source_url       = Column(String(500))
    pdf_url          = Column(String(500))
    scraped_at       = Column(DateTime, default=datetime.utcnow)
    classified_at    = Column(DateTime)

    def to_dict(self):
        return {
            "id":               self.id,
            "town":             self.town,
            "county":           self.county,
            "board":            self.board,
            "date":             self.date,
            "title":            self.title,
            "summary":          self.summary,
            "tags":             self.tags.split(",") if self.tags else [],
            "urgency":          self.urgency,
            "yimby_opportunity":self.yimby_opportunity,
            "opposition_risk":  self.opposition_risk,
            "source_url":       self.source_url,
            "pdf_url":          self.pdf_url,
            "scraped_at":       self.scraped_at.isoformat() if self.scraped_at else None,
        }


class ComprehensivePlan(Base):
    __tablename__ = "comprehensive_plans"

    id               = Column(Integer, primary_key=True)
    town             = Column(String(100), unique=True, index=True)
    county           = Column(String(100))
    last_adopted     = Column(Integer)                                # year
    next_due         = Column(Integer)                                # year
    status           = Column(String(20))                             # current/due-soon/overdue
    work_plan_active = Column(Boolean, default=False)
    notes            = Column(Text)
    updated_at       = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "town":             self.town,
            "county":           self.county,
            "lastAdopted":      self.last_adopted,
            "nextDue":          self.next_due,
            "status":           self.status,
            "workPlanActive":   self.work_plan_active,
            "notes":            self.notes or "",
        }


class WatchlistEntry(Base):
    __tablename__ = "watchlist"

    id         = Column(Integer, primary_key=True)
    email      = Column(String(200), index=True)
    town       = Column(String(100))
    frequency  = Column(String(20), default="weekly")                 # daily/weekly/urgent
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    """Create all tables and seed comp plan data."""
    Base.metadata.create_all(engine)
    _seed_comp_plans()


COMP_PLAN_SEED = [
    ("Portland",       "Cumberland",   2019, 2029, "current",   False, "Updated housing chapter 2022."),
    ("Lewiston",       "Androscoggin", 2016, 2026, "overdue",   True,  "Update in progress. Final adoption target: Fall 2026."),
    ("Bangor",         "Penobscot",    2015, 2025, "overdue",   True,  "Consultant hired Jan 2025. Draft expected Q3 2026."),
    ("Auburn",         "Androscoggin", 2018, 2028, "current",   False, ""),
    ("Brunswick",      "Cumberland",   2017, 2027, "due-soon",  False, "Update expected to begin 2026."),
    ("Rockland",       "Knox",         2007, 2017, "overdue",   True,  "Final adoption vote scheduled March 2026."),
    ("Falmouth",       "Cumberland",   2020, 2030, "current",   False, ""),
    ("Scarborough",    "Cumberland",   2014, 2024, "overdue",   False, "No update initiated."),
    ("Saco",           "York",         2021, 2031, "current",   False, ""),
    ("Waterville",     "Kennebec",     2013, 2023, "overdue",   False, "No update initiated. State compliance issue."),
    ("Yarmouth",       "Cumberland",   2018, 2028, "current",   False, ""),
    ("Freeport",       "Cumberland",   2017, 2027, "due-soon",  True,  "Work sessions began Jan 2026."),
    ("Kennebunk",      "York",         2016, 2026, "overdue",   False, "No update initiated."),
    ("Bath",           "Sagadahoc",    2019, 2029, "current",   False, ""),
    ("Ellsworth",      "Hancock",      2012, 2022, "overdue",   False, "No update. State outreach recommended."),
    ("Biddeford",      "York",         2020, 2030, "current",   False, ""),
    ("Sanford",        "York",         2015, 2025, "overdue",   False, "Large population, significant housing pressure."),
    ("Augusta",        "Kennebec",     2013, 2023, "overdue",   True,  "RFP for consultant being prepared."),
    ("South Portland", "Cumberland",   2021, 2031, "current",   False, ""),
    ("Gorham",         "Cumberland",   2016, 2026, "overdue",   False, "Significant growth pressure. No update initiated."),
    ("Windham",        "Cumberland",   2017, 2027, "due-soon",  False, "Fastest-growing town in Cumberland County."),
    ("Westbrook",      "Cumberland",   2019, 2029, "current",   False, ""),
    ("Brewer",         "Penobscot",    2014, 2024, "overdue",   False, ""),
    ("Old Town",       "Penobscot",    2016, 2026, "overdue",   False, ""),
    ("Hampden",        "Penobscot",    2018, 2028, "current",   False, ""),
    ("Camden",         "Knox",         2015, 2025, "overdue",   False, "High-cost coastal market. Update critically needed."),
    ("Belfast",        "Waldo",        2014, 2024, "overdue",   True,  "Update underway. Housing element being drafted."),
    ("Farmington",     "Franklin",     2018, 2028, "current",   False, ""),
    ("Skowhegan",      "Somerset",     2013, 2023, "overdue",   False, "Rural housing shortage severe."),
    ("Dover-Foxcroft", "Piscataquis",  2011, 2021, "overdue",   False, "Most overdue in state. Needs state/consultant support."),
    ("Damariscotta",   "Lincoln",      2015, 2025, "overdue",   False, "Coastal affordability crisis."),
    ("Camden",         "Knox",         2015, 2025, "overdue",   False, "High-cost coastal market."),
    ("Machias",        "Washington",   2014, 2024, "overdue",   False, ""),
    ("Calais",         "Washington",   2012, 2022, "overdue",   False, ""),
    ("Rumford",        "Oxford",       2013, 2023, "overdue",   False, ""),
    ("Norway",         "Oxford",       2016, 2026, "overdue",   False, ""),
    ("Presque Isle",   "Aroostook",    2017, 2027, "due-soon",  False, ""),
]


def _seed_comp_plans():
    session = get_session()
    for row in COMP_PLAN_SEED:
        town = row[0]
        if not session.query(ComprehensivePlan).filter_by(town=town).first():
            session.add(ComprehensivePlan(
                town=town, county=row[1], last_adopted=row[2],
                next_due=row[3], status=row[4], work_plan_active=row[5], notes=row[6],
            ))
    session.commit()
    session.close()
