"""Clasificación determinista y explicable. No se envían titulares a una IA."""
import re
import unicodedata
from datetime import date


def norm(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", s)
                               .encode("ascii", "ignore").decode().lower()))


TOPICS = {
    1: ("artificial intelligence", "inteligencia artificial", "generative ai", "ai", "inference", "agentic", "llm", "multimodal", "rag", "foundation model", "open weights", "machine learning"),
    2: ("automation", "automatización", "power automate", "power platform", "process mining", "workflow", "low code", "no code", "rpa", "orchestration"),
    3: ("earnings", "financial results", "margin", "operating profit", "capital expenditure", "resultados financieros", "strategy", "acquisition", "investment", "economic outlook", "productivity", "tco"),
    4: ("cloud", "architecture", "kubernetes", "platform engineering", "devops", "developer", "api", "saas", "integration", "software", "continuous delivery"),
    5: ("governance", "cybersecurity", "gobernanza", "privacy", "protección de datos", "identity", "authentication", "nist", "regulación", "regulatory", "security", "data protection"),
    6: ("personal systems", "printer", "printing", "processor", "workstation", "ai pc", "pc market", "laptop", "notebook", "computadores", "hardware", "supply chain"),
    7: ("business intelligence", "power bi", "analytics", "microsoft fabric", "data platform", "data warehouse", "lakehouse", "forecasting", "data engineering", "data governance"),
    8: ("leadership", "liderazgo", "change management", "skills", "workforce", "upskilling", "adoption", "adopción", "talento", "organizational", "operating model"),
    9: ("hp inc", "personal systems", "hp printing", "lenovo", "dell technologies", "pc shipments", "pc market", "pc market share"),
}
INDUSTRIES = {
    "Retail": ("retail", "minorista"), "Banca y servicios financieros": ("banking", "banca", "financial services"),
    "Manufactura": ("manufacturing", "manufactura", "factory"), "Logística": ("logistics", "logística", "supply chain"),
    "Sector público": ("public sector", "sector público", "government"), "Educación": ("education", "educación"),
    "Salud": ("healthcare", "salud", "health care"), "Telecomunicaciones": ("telecom", "telecommunications"),
}
COMPANIES = {
    "HP Inc.": ("hp inc", "hp personal systems", "hp printing"), "HPE": ("hpe", "hewlett packard enterprise"),
    "Lenovo": ("lenovo",), "Dell Technologies": ("dell",), "Microsoft": ("microsoft", "azure"),
    "OpenAI": ("openai",), "Alphabet": ("alphabet", "google", "google cloud"),
    "Amazon": ("amazon", "aws"), "NVIDIA": ("nvidia",), "GitHub": ("github",),
    "Anthropic": ("anthropic",), "Intel": ("intel",), "AMD": ("amd",), "Apple": ("apple",),
}


def matches(text, terms):
    return sum(f" {norm(term)} " in text for term in terms)


def classify(candidate: dict, source: dict, on: date) -> dict | None:
    title = " " + norm(candidate["title"]) + " "
    if matches(title, ("weekly roundup", "weekly round up", "news roundup", "week in review",
                       "press clippings", "in the media", "resumen de prensa", "noticias de la semana")):
        return None
    body = " " + norm(candidate["body"][:7000]) + " "
    org = source["org"]
    companies = [k for k, v in COMPANIES.items() if matches(title + body, v)]
    if org in COMPANIES and org not in companies:
        companies.append(org)
    scores = {k: matches(title, v) * 4 + min(matches(body, v), 4) for k, v in TOPICS.items()}
    if org == "HP Inc.":
        scores[9] += 10
    elif org in {"Dell Technologies", "Lenovo"} and (scores[3] or scores[6]):
        scores[9] += 6
    if ("HPE" in companies and not {"HP Inc.", "Lenovo", "Dell Technologies"}.intersection(companies)):
        scores[9] = 0
    if not any(scores.values()):
        return None
    # Los campos del registro son una señal débil, nunca por sí solos una noticia.
    for k in source["fields"]:
        if scores[k]:
            scores[k] += 2
    primary = max(scores, key=scores.get)
    secondary = sorted((k for k in scores if k != primary and scores[k] >= 3),
                       key=scores.get, reverse=True)[:3]
    age = (on - date.fromisoformat(candidate["published_at"])).days
    score = min(100, 42 + min(23, scores[primary] * 2) + (14 if primary in (1, 2, 3, 9) else 5)
                + max(0, 12 - age * 2) + (8 if matches(title + body, ("latam", "latin america", "américa latina")) else 0))
    depth = ("profundo" if primary in (1, 3, 9) and score >= 85 and len(candidate["body"].split()) >= 600
             else "estándar" if score >= 69 and len(candidate["body"].split()) >= 220 else "breve")
    return {"primary": primary, "secondary": secondary, "companies": companies,
            "industries": [k for k, v in INDUSTRIES.items() if matches(title + body, v)] or ["Tecnología"],
            "relevance": score, "depth": depth}
