"""Cleaning rules for messy research data: names, grades, specialties, countries, emails."""

from __future__ import annotations

import re
import unicodedata

TITLES = {"dr", "mr", "mrs", "ms", "miss", "mx", "prof", "professor"}

SPECIALTIES = {
    "cardiology": "Cardiology",
    "cardiologist": "Cardiology",
    "emergency medicine": "Emergency Medicine",
    "emergency physician": "Emergency Medicine",
    "a&e": "Emergency Medicine",
    "accident and emergency": "Emergency Medicine",
    "paediatrics": "Paediatrics",
    "paediatrician": "Paediatrics",
    "pediatrics": "Paediatrics",
    "pediatrician": "Paediatrics",
    "anaesthetics": "Anaesthetics",
    "anaesthesia": "Anaesthetics",
    "anaesthetist": "Anaesthetics",
    "anesthesiology": "Anaesthetics",
    "anesthetist": "Anaesthetics",
    "general practice": "General Practice",
    "general practitioner": "General Practice",
    "gp": "General Practice",
    "general surgery": "General Surgery",
    "general surgeon": "General Surgery",
    "dermatology": "Dermatology",
    "dermatologist": "Dermatology",
    "general medicine": "General Medicine",
    "general internal medicine": "General Medicine",
}

COUNTRIES = {
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "united kingdom": "United Kingdom",
    "great britain": "United Kingdom",
    "gb": "United Kingdom",
    "england": "United Kingdom",
    "scotland": "United Kingdom",
    "wales": "United Kingdom",
    "northern ireland": "United Kingdom",
}


def clean(value: str | None) -> str:
    """Trim and collapse whitespace."""
    return " ".join((value or "").split())


def ascii_lower(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return text.lower()


def name_key(full_name: str | None) -> str:
    """'Dr Sarah O'Neill' and 'Sarah ONeill' both become 'sarah oneill'."""
    text = re.sub(r"[^a-z\s]", "", ascii_lower(full_name))
    return " ".join(part for part in text.split() if part not in TITLES)


def employer_key(employer: str | None) -> str:
    """"St Aldric's Children's Hospital" and "St Aldrics Childrens Hospital" match."""
    text = re.sub(r"[^a-z0-9\s]", "", ascii_lower(employer))
    return " ".join(text.split())


def split_name(full_name: str | None) -> tuple[str, str]:
    parts = [p for p in clean(full_name).split() if p.lower().strip(".") not in TITLES]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return "", parts[0]
    return parts[0], parts[-1]


def normalise_email(email: str | None) -> str:
    return clean(email).lower()


def normalise_country(country: str | None) -> str:
    value = clean(country)
    return COUNTRIES.get(value.lower(), value)


def normalise_specialty(specialty: str | None) -> str | None:
    """None means we don't recognise it, which is different from 'not targeted'."""
    return SPECIALTIES.get(clean(specialty).lower())


# Every grade normalise_grade can return, for the dropdown a person picks from.
CANONICAL_GRADES = [
    "Consultant", "Associate Specialist", "Registrar (ST3+)", "GP Partner", "Salaried GP",
    "Specialty Doctor", "Junior trainee", "Foundation doctor",
]


def normalise_grade(grade: str | None) -> str | None:
    """Map free-text NHS grades onto a few canonical ones. None means unrecognised."""
    text = clean(grade).lower()
    if not text:
        return None
    if "consultant" in text:
        return "Consultant"
    if "associate specialist" in text:
        return "Associate Specialist"
    if "specialty doctor" in text or text == "sas doctor":
        return "Specialty Doctor"
    if "gp partner" in text:
        return "GP Partner"
    if "salaried gp" in text:
        return "Salaried GP"
    if re.search(r"\bfy\s*[12]\b|\bfoundation\b", text):
        return "Foundation doctor"
    match = re.search(r"\bst\s*([1-9])\b", text)
    if match:
        return "Registrar (ST3+)" if int(match.group(1)) >= 3 else "Junior trainee"
    if re.search(r"\bct\s*[1-3]\b", text):
        return "Junior trainee"
    if "registrar" in text or re.search(r"\bspr\b", text):
        return "Registrar (ST3+)"
    return None


def salutation(title: str | None, last_name: str) -> str:
    """'Dear Mr Price', not 'Dear Dr Daniel Price'. UK surgeons often use Mr/Ms, so the title comes from the data."""
    clean_title = clean(title).rstrip(".") or "Dr"
    return f"{clean_title} {last_name}".strip()
