"""
Maine YIMBY Housing Watch — AI Classifier
Uses Claude claude-sonnet-4-20250514 to classify, tag, and summarize scraped agenda items.
"""

import json
import logging
import os
import re
from typing import Optional

import anthropic

log = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = """You are a housing policy analyst specializing in Maine municipal land use.
Your job is to classify municipal meeting agenda items for a YIMBY (Yes In My Backyard) 
advocacy organization tracking housing-related actions across Maine.

Respond ONLY with valid JSON — no preamble, no markdown fences, no explanation.
"""

CLASSIFY_PROMPT = """Classify this Maine municipal meeting agenda item.

Town: {town}
County: {county}
Date: {date}
Board/meeting: {title}
Agenda text (truncated):
{text}

Respond with this exact JSON structure:
{{
  "relevant": true or false,
  "title": "concise 8-12 word agenda item title",
  "urgency": "urgent" | "high" | "medium" | "low",
  "tags": ["tag1", "tag2"],
  "summary": "2-3 sentence plain-English summary of what is being considered and why it matters for housing production",
  "yimby_opportunity": "1 sentence action recommendation for advocates, or null if no clear opportunity",
  "opposition_risk": "high" | "medium" | "low" | "none"
}}

URGENCY DEFINITIONS:
- urgent: vote happening within 14 days, or NIMBY counter-petition filed, or comp plan final adoption vote
- high: public hearing within 30 days, first reading, or significant upzoning/downzoning proposal
- medium: work session, study, or introductory discussion — vote 30-90 days away
- low: routine review, informational item, or early-stage planning

VALID TAGS (use only from this list, pick 1-4):
rezoning, comp plan update, affordable housing, density bonus, subdivision,
zoning amendment, ADU, inclusionary zoning, NIMBY opposition, public hearing,
variance, site plan review, housing trust fund, TOD, workforce housing

RELEVANT = true if the item touches: housing production, zoning changes, density,
affordability, ADUs, comprehensive plan updates, land use, growth management,
or organized opposition to housing. Relevant = false for routine items like 
road maintenance, budget amendments unrelated to housing, or personnel matters.
"""


async def classify_item(
    title: str,
    text: str,
    town: str,
    county: str,
    date: str,
) -> dict:
    """
    Call Claude to classify a single agenda item.
    Returns a dict with: relevant, title, urgency, tags, summary,
    yimby_opportunity, opposition_risk.
    Falls back to a safe default dict on error.
    """
    prompt = CLASSIFY_PROMPT.format(
        town=town,
        county=county,
        date=date,
        title=title,
        text=text[:4000],
    )
    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        log.debug(f"Classified: {town} / {title[:50]} → relevant={result.get('relevant')} urgency={result.get('urgency')}")
        return result
    except json.JSONDecodeError as e:
        log.warning(f"JSON parse error for {town}/{title[:40]}: {e}")
        return _fallback(title)
    except anthropic.APIError as e:
        log.error(f"Anthropic API error for {town}/{title[:40]}: {e}")
        return _fallback(title)


def _fallback(title: str) -> dict:
    return {
        "relevant": False,
        "title": title[:80],
        "urgency": "low",
        "tags": [],
        "summary": "",
        "yimby_opportunity": None,
        "opposition_risk": "none",
    }


# ---------------------------------------------------------------------------
# Batch classifier — for backfilling or re-classifying existing records
# ---------------------------------------------------------------------------
async def reclassify_all(session) -> None:
    """Re-run classification on all stored items (e.g. after prompt update)."""
    from db import RawAgendaItem
    items = session.query(RawAgendaItem).all()
    log.info(f"Re-classifying {len(items)} stored items...")
    for item in items:
        result = await classify_item(
            title=item.title,
            text=item.summary,
            town=item.town,
            county=item.county,
            date=item.date,
        )
        if result.get("relevant"):
            item.urgency = result.get("urgency", item.urgency)
            item.tags = ",".join(result.get("tags", []))
            item.summary = result.get("summary", item.summary)
            item.yimby_opportunity = result.get("yimby_opportunity", "")
            session.commit()
    log.info("Re-classification complete.")


# ---------------------------------------------------------------------------
# Advocacy content generator — used by the dashboard "AI tools" panel
# ---------------------------------------------------------------------------
def generate_advocacy_content(mode: str, item: dict) -> str:
    """
    Generate advocacy content for a classified item.
    mode: "briefing" | "comment" | "talking_points"
    item: dict with keys town, county, board, date, title, summary
    """
    prompts = {
        "briefing": f"""You are a YIMBY housing advocate assistant. Write a crisp 3-bullet 
advocacy briefing for this Maine municipal meeting item:
- Bullet 1: Key facts (what is being decided, by whom, when)
- Bullet 2: Why it matters for housing production in Maine
- Bullet 3: Recommended advocate action

Item: {item['title']}
Town: {item['town']}, {item['county']} County | Board: {item['board']} | Date: {item['date']}
Context: {item['summary']}

Be specific, direct, and under 200 words total.""",

        "comment": f"""You are a YIMBY housing advocate. Draft a compelling public comment 
(200-250 words) in support of pro-housing outcomes for this Maine municipal meeting item.
Be specific, cite the housing need in Maine, reference local context where possible, 
and be respectful but persuasive. Address likely opposition arguments briefly.

Item: {item['title']}
Town: {item['town']}, {item['county']} County
Context: {item['summary']}""",

        "talking_points": f"""You are a YIMBY housing advocate trainer. Generate 4 sharp 
talking points an advocate could use when speaking at this Maine town meeting.
Each point should be one sentence, evidence-grounded where possible, and designed 
to counter expected opposition. Number each point 1-4.

Item: {item['title']}
Town: {item['town']}
Context: {item['summary']}""",
    }

    if mode not in prompts:
        return "Unknown mode."

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            messages=[{"role": "user", "content": prompts[mode]}],
        )
        return message.content[0].text.strip()
    except anthropic.APIError as e:
        log.error(f"Advocacy content generation failed: {e}")
        return "Unable to generate content — API error."
