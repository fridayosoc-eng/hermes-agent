#!/usr/bin/env python3
"""
Musk Perspective Tool - Hermes Integration
Serves Elon Musk mental models from PostgreSQL
"""
import psycopg2
from psycopg2.extras import RealDictCursor

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "mental_models",
    "user": "hermes",
    "password": "hermes123"
}
ELON_UUID = "00000000-0000-0000-0000-000000000001"

ACTIVATION_PHRASES = [
    "elon perspective", "elon musk perspective", "ask elon", "musk would think",
    "what would elon say", "elon would say", "ask musk", "musk perspective",
    "elon thinks", "musk thinks"
]

def is_activated(query: str) -> bool:
    q = query.lower()
    return any(p in q for p in ACTIVATION_PHRASES)

def get_models(category=None):
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if category:
                cur.execute("""SELECT id, category, model_text, confidence_score 
                    FROM mental_models WHERE thinker_id=%s AND is_current AND category=%s
                    ORDER BY confidence_score DESC""", (ELON_UUID, category))
            else:
                cur.execute("""SELECT id, category, model_text, confidence_score 
                    FROM mental_models WHERE thinker_id=%s AND is_current
                    ORDER BY confidence_score DESC""", (ELON_UUID,))
            return cur.fetchall()
    finally:
        conn.close()

def get_citations(model_id):
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT c.quote_excerpt, s.title, s.date_published
                FROM citations c JOIN sources s ON c.source_id=s.id
                WHERE c.mental_model_id=%s ORDER BY c.relevance_score DESC LIMIT 1""", (model_id,))
            r = cur.fetchone()
            return r if r else {}
    finally:
        conn.close()

def respond(query: str) -> str:
    q = query.lower()
    if any(w in q for w in ['reasoning', 'first principles', 'physics']): cats = ['reasoning']
    elif any(w in q for w in ['decision']): cats = ['decision_heuristic']
    elif any(w in q for w in ['strategy', 'vertical', 'integration']): cats = ['strategy']
    elif any(w in q for w in ['leadership', 'factory']): cats = ['leadership']
    else: cats = None
    
    models = get_models() if cats is None else get_models(cats[0])
    if not models: return "Need more data."
    
    lines = ["**MUSK PERSPECTIVE**\n_As Elon would respond:_\n"]
    for m in models[:3]:
        lines.append(f"**{m['category'].upper()}** ({m['confidence_score']:.0%})")
        lines.append(m['model_text'][:500] + ("..." if len(m['model_text']) > 500 else ""))
        cit = get_citations(m['id'])
        if cit: lines.append(f"_Source: {cit['title']} ({str(cit['date_published'])[:4]})_")
        lines.append("")
    lines.append("*Synthesized from documented sources.*")
    return "\n".join(lines)

MUSK_SCHEMA = {
    "name": "musk_perspective",
    "description": (
        "Channel Elon Musk's mental models and decision-making framework as a consultant.\n\n"
        "Use when user asks about:\n"
        "- 'elon perspective', 'ask elon', 'musk would think', 'what would elon say'\n"
        "- Business strategy, technology, innovation, risk-taking decisions\n"
        "- Tesla, SpaceX, X Corp, xAI, Neuralink decisions or thinking\n"
        "- First principles reasoning, physics-based decision making\n\n"
        "Returns mental models, decision heuristics, and citations from documented sources\n"
        "including Walter Isaacson biography, Joe Rogan podcasts, TED talks, and earnings calls."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The question or topic to ask Elon Musk about"
            }
        },
        "required": ["query"]
    }
}

# --- Registry ---
from tools.registry import registry

registry.register(
    name="musk_perspective",
    toolset="productivity",
    schema=MUSK_SCHEMA,
    handler=lambda args, **kw: respond(args.get("query", "general"))
)

if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "general"
    print(respond(q))
