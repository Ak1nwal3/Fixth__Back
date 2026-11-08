import re
from difflib import SequenceMatcher

from django.db import IntegrityError, transaction

from profiles.models import (
    Discipline,
    DisciplineAlias,
    Skill,
    SkillAlias,
)
from profiles.normalization import clean_display, normalize_term

MAX_TERM_LENGTH = 120
MAX_SKILLS = 30
DISCIPLINE_FUZZY_THRESHOLD = 0.90
_SKILL_SEPARATOR_RE = re.compile(r"[,;\u00b7\n]+")


class InvalidTerm(ValueError):
    """Raised when a user-supplied skill or discipline cannot be normalized."""


def _prepare(raw) -> tuple[str, str]:
    if not isinstance(raw, str):
        raise InvalidTerm("Enter a name.")
    display = clean_display(raw)
    if not display:
        raise InvalidTerm("Enter a name.")
    if len(display) > MAX_TERM_LENGTH:
        raise InvalidTerm(
            f"Names must be {MAX_TERM_LENGTH} characters or fewer."
        )
    key = normalize_term(display)
    if not key:
        raise InvalidTerm("Enter a name containing letters or numbers.")
    return display, key


def _create_term(model, display: str, key: str):
    try:
        with transaction.atomic():
            return model.objects.create(name=display, normalized_name=key)
    except IntegrityError:
        # Another request created the same normalized term between our lookup
        # and insert. Reuse that row rather than surfacing a race condition.
        return model.objects.get(normalized_name=key)


def _tokens_abbreviate(shorter: str, longer: str) -> bool:
    short_tokens = shorter.split()
    long_tokens = longer.split()
    if len(short_tokens) < 2 or len(short_tokens) != len(long_tokens):
        return False
    return all(
        len(short_token) >= 2 and long_token.startswith(short_token)
        for short_token, long_token in zip(short_tokens, long_tokens)
    )


def _find_fuzzy_discipline(key: str):
    """Find a high-confidence existing discipline for a new spelling.

    The comparison is intentionally conservative: either the strings are very
    similar (typo-level), or every token in the shorter name is an abbreviation
    of the corresponding token in the longer name. This catches variants such
    as "Computer Eng." -> "Computer Engineering" without merging distinct
    fields like "Civil Engineering" and "Environmental Engineering".
    """
    best_match = None
    best_score = 0.0

    for candidate in Discipline.objects.only("id", "name", "normalized_name"):
        candidate_key = candidate.normalized_name
        if _tokens_abbreviate(key, candidate_key) or _tokens_abbreviate(
            candidate_key, key
        ):
            return candidate

        score = SequenceMatcher(None, key, candidate_key).ratio()
        if score > best_score:
            best_score = score
            best_match = candidate

    if best_match is not None and best_score >= DISCIPLINE_FUZZY_THRESHOLD:
        return best_match
    return None


def resolve_discipline(raw) -> Discipline:
    """Resolve a discipline name to a canonical row, creating it when needed."""
    if isinstance(raw, Discipline):
        return raw

    display, key = _prepare(raw)

    existing = Discipline.objects.filter(normalized_name=key).first()
    if existing is not None:
        return existing

    alias = (
        DisciplineAlias.objects.select_related("discipline")
        .filter(alias_key=key)
        .first()
    )
    if alias is not None:
        return alias.discipline

    match = _find_fuzzy_discipline(key)
    if match is not None:
        alias, _ = DisciplineAlias.objects.get_or_create(
            alias_key=key,
            defaults={"name": display, "discipline": match},
        )
        return alias.discipline

    return _create_term(Discipline, display, key)


def resolve_skill(raw) -> Skill:
    """Resolve a skill name to a canonical row, creating it when needed.

    Skills deliberately do not use fuzzy matching. Short technical names such
    as Java/JavaScript and C/C++/C# must remain distinct even when their
    strings are similar.
    """
    if isinstance(raw, Skill):
        return raw

    display, key = _prepare(raw)

    existing = Skill.objects.filter(normalized_name=key).first()
    if existing is not None:
        return existing

    alias = (
        SkillAlias.objects.select_related("skill")
        .filter(alias_key=key)
        .first()
    )
    if alias is not None:
        return alias.skill

    return _create_term(Skill, display, key)


def split_skill_input(values) -> list:
    if isinstance(values, str):
        return [
            part.strip()
            for part in _SKILL_SEPARATOR_RE.split(values)
            if part.strip()
        ]
    if isinstance(values, (list, tuple, set)):
        return list(values)
    raise InvalidTerm("Skills must be a list of names.")


def resolve_skills(values) -> list[Skill]:
    """Resolve a list (or delimited string) of skill names without duplicates."""
    if values in (None, ""):
        return []

    raw_values = split_skill_input(values)
    if len(raw_values) > MAX_SKILLS:
        raise InvalidTerm(f"Select no more than {MAX_SKILLS} skills.")

    resolved: dict[str, Skill] = {}
    for raw in raw_values:
        if isinstance(raw, Skill):
            resolved.setdefault(raw.normalized_name, raw)
            continue
        display, key = _prepare(raw)
        if key in resolved:
            continue
        resolved[key] = resolve_skill(display)
    return list({skill.pk: skill for skill in resolved.values()}.values())
