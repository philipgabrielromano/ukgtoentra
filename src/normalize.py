"""
Name normalization utilities.

Normalization makes "José  García-López", "Jose Garcia Lopez", and "GARCIA LOPEZ, Jose"
comparable, and a nickname map lets "Bob" match "Robert".
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Optional

from rapidfuzz import fuzz

# A pragmatic, extendable nickname map. Keys are canonical-ish; values are aliases.
# Matching is symmetric: we expand both names to their alias sets and check for overlap.
NICKNAMES = {
    "robert": {"rob", "bob", "bobby", "bert"},
    "william": {"will", "bill", "billy", "liam"},
    "richard": {"rick", "rich", "dick", "ricky"},
    "james": {"jim", "jimmy", "jamie"},
    "john": {"jon", "johnny", "jack"},
    "michael": {"mike", "mick", "mikey"},
    "joseph": {"joe", "joey"},
    "charles": {"charlie", "chuck", "chas"},
    "thomas": {"tom", "tommy"},
    "christopher": {"chris", "topher"},
    "daniel": {"dan", "danny"},
    "matthew": {"matt", "matty"},
    "anthony": {"tony", "ant"},
    "edward": {"ed", "eddie", "ted", "ned"},
    "andrew": {"andy", "drew"},
    "david": {"dave", "davey"},
    "donald": {"don", "donnie"},
    "ronald": {"ron", "ronnie"},
    "kenneth": {"ken", "kenny"},
    "steven": {"steve", "stevie"},
    "stephen": {"steve", "stevie"},
    "timothy": {"tim", "timmy"},
    "nicholas": {"nick", "nicky"},
    "benjamin": {"ben", "benny", "benji"},
    "samuel": {"sam", "sammy"},
    "alexander": {"alex", "al", "xander", "sandy"},
    "elizabeth": {"liz", "beth", "betty", "eliza", "lisa", "betsy", "libby"},
    "katherine": {"kate", "katie", "kathy", "kat", "katy", "kit"},
    "catherine": {"cathy", "cat", "kate", "katie"},
    "margaret": {"maggie", "meg", "peggy", "marge", "greta"},
    "patricia": {"pat", "patty", "trish", "tricia"},
    "jennifer": {"jen", "jenny", "jenn"},
    "jessica": {"jess", "jessie"},
    "deborah": {"deb", "debbie"},
    "rebecca": {"becca", "becky", "reba"},
    "susan": {"sue", "susie", "suzy"},
    "barbara": {"barb", "babs"},
    "victoria": {"vicky", "vic", "tori"},
    "kimberly": {"kim"},
    "cynthia": {"cindy", "cyn"},
    "theodore": {"ted", "teddy", "theo"},
    "frederick": {"fred", "freddie", "rick"},
    "gregory": {"greg"},
    "joshua": {"josh"},
    "zachary": {"zach", "zack"},
    "nathaniel": {"nate", "nathan"},
    "francisco": {"paco", "cisco", "frank"},
    "guillermo": {"guille", "memo"},
    "jose": {"pepe"},
    # --- added from observed UKG/Entra data ---
    "philip": {"phil", "pip"},
    "phillip": {"phil", "pip"},
    "jeffrey": {"jeff", "jef"},
    "jeffery": {"jeff", "jef"},
    "lawrence": {"larry", "lars"},
    "christine": {"christy", "chris", "tina", "chrissy"},
    "christina": {"christy", "chris", "tina", "chrissy"},
    "willie": {"william", "will", "bill"},
    "vincent": {"vince", "vinny", "vin"},
    "sandra": {"sandy", "sandi"},
    "toni": {"antoinette", "antonia", "tony"},
    "grace": {"gracie"},
    "diana": {"di", "diane"},
    "louis": {"lou", "louie"},
    "thomas": {"tom", "tommy", "thom"},
    "jessica": {"jess", "jessie", "jay"},
    "allison": {"alli", "ally", "al", "allie"},
    "alexandra": {"alex", "lexie", "lexi", "sandra", "alexa", "sandy"},
}

# Known SPELLING VARIANTS treated as equivalent (not nicknames, just alt spellings).
SPELLING_VARIANTS = [
    {"april", "aprile"}, {"nichole", "nicole"}, {"teresa", "theresa"},
    {"jeffrey", "jeffery", "jeffry"}, {"micheal", "michael"}, {"aime", "amie", "amy"},
    {"cinthya", "cinthiya", "cynthia"}, {"abigial", "abigail"},
    {"envyonna", "envyvonna"}, {"yanitza", "yanita"}, {"kurdtis", "kurdis"},
    {"clishina", "clinisha"}, {"sara", "sarah"}, {"cathy", "kathy"},
]

_VARIANT_FAMILY: dict[str, frozenset[str]] = {}
for _grp in SPELLING_VARIANTS:
    _fs = frozenset(_grp)
    for _t in _fs:
        _VARIANT_FAMILY[_t] = _VARIANT_FAMILY.get(_t, frozenset()) | _fs

# Nicknames that do NOT share a stem but are nonetheless well-established and safe
# to auto-apply (low risk of being a different person). Curated allow-list.
TRUSTED_NONSTEM_NICKNAMES = [
    {"lawrence", "larry"}, {"thomas", "tom"}, {"cynthia", "cindy"},
    {"richard", "dick"}, {"robert", "bob"}, {"william", "bill"},
    {"charles", "chuck"}, {"margaret", "peggy"}, {"john", "jack"},
    {"henry", "hank"}, {"james", "jim"}, {"edward", "ned"},
    {"sarah", "sally"}, {"dorothy", "dot"}, {"theodore", "ted"},
]
# NOTE: deliberately EXCLUDES Elizabeth/Lisa, Jessica/Jay, etc. - too ambiguous,
# left to manual review.
_TRUSTED_FAMILY: dict[str, frozenset[str]] = {}
for _grp in TRUSTED_NONSTEM_NICKNAMES:
    _fs = frozenset(_grp)
    for _t in _fs:
        _TRUSTED_FAMILY[_t] = _TRUSTED_FAMILY.get(_t, frozenset()) | _fs


def trusted_nickname(a, b) -> bool:
    """True only for curated, well-established non-stem nickname pairs."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    ta, tb = na.split()[0], nb.split()[0]
    fam = _TRUSTED_FAMILY.get(ta)
    return bool(fam) and tb in fam

# Build a reverse lookup so any token maps to its full alias family.
_ALIAS_FAMILY: dict[str, frozenset[str]] = {}
for canon, aliases in NICKNAMES.items():
    family = frozenset({canon} | aliases)
    for token in family:
        _ALIAS_FAMILY[token] = _ALIAS_FAMILY.get(token, frozenset()) | family


def strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_name(name: Optional[str]) -> str:
    """Lowercase, strip accents, remove punctuation, collapse whitespace."""
    if not name:
        return ""
    name = strip_accents(name)
    name = name.lower()
    # Drop common suffixes/titles that pollute matching.
    name = re.sub(r"\b(jr|sr|ii|iii|iv|md|phd|mr|mrs|ms|dr)\b\.?", "", name)
    name = re.sub(r"[^a-z\s]", " ", name)          # remove apostrophes, hyphens, etc.
    name = re.sub(r"\s+", " ", name).strip()
    return name


def name_key(first: Optional[str], last: Optional[str]) -> str:
    """Canonical 'first|last' key used for exact-match bucketing."""
    return f"{normalize_name(first)}|{normalize_name(last)}"


def alias_set(token: str) -> frozenset[str]:
    """All known nickname aliases for a single given-name token (incl. itself)."""
    token = normalize_name(token)
    return _ALIAS_FAMILY.get(token, frozenset({token}))


def variant_set(token: str) -> frozenset[str]:
    """Known spelling-variant family for a token (incl. itself)."""
    token = normalize_name(token)
    return _VARIANT_FAMILY.get(token, frozenset({token}))


def first_name_relationship(a, b):
    """Classify how two first names relate. Returns one of:
       'exact', 'nickname', 'variant', 'high_similarity', 'initial', 'none'.
    Used by the matcher to decide whether a fuzzy match is SAFE to auto-apply."""
    from rapidfuzz import fuzz
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return "none"
    ta, tb = na.split()[0], nb.split()[0]
    if ta == tb:
        return "exact"

    # Shared-stem test: a SAFE nickname/abbreviation shares the beginning of the
    # name (Phil<-Philip, Jeff<-Jeffrey, Vince<-Vincent, Greg<-Gregory, Kim<-Kimberly).
    # This deliberately EXCLUDES nickname-map entries that don't share a stem
    # (Elizabeth/Lisa, Jessica/Jay, Margaret/Peggy) because in real HR data those
    # are far more likely to be different people (relatives) than a goes-by name.
    short, long = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    shares_stem = len(short) >= 2 and long.startswith(short[:max(2, len(short) - 1)])

    if variant_set(ta) & variant_set(tb):
        return "variant"
    # near-identical spelling (typo): high char similarity AND same first letter
    if ta[0] == tb[0] and fuzz.ratio(ta, tb) >= 85:
        return "high_similarity"
    if (alias_set(ta) & alias_set(tb)) and shares_stem:
        return "nickname"
    if trusted_nickname(ta, tb):
        return "nickname"
    if (len(ta) == 1 and tb.startswith(ta)) or (len(tb) == 1 and ta.startswith(tb)):
        return "initial"
    return "none"


def first_names_compatible(a: Optional[str], b: Optional[str]) -> bool:
    """
    True if two first names are the same OR known nicknames of each other.
    Also handles compound first names by comparing the leading token.
    """
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = na.split()[0], nb.split()[0]
    if ta == tb:
        return True
    # nickname families overlap?
    if alias_set(ta) & alias_set(tb):
        return True
    if trusted_nickname(ta, tb):
        return True
    # spelling-variant families overlap (April/Aprile, Nichole/Nicole)?
    if variant_set(ta) & variant_set(tb):
        return True
    # shared stem (Phil/Philip, Vince/Vincent) - lets candidate gathering find them
    short, long = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(short) >= 3 and long.startswith(short[:3]):
        return True
    # close typo (same first letter, high similarity)
    if ta[0] == tb[0] and fuzz.ratio(ta, tb) >= 85:
        return True
    # initial match (e.g. "J" vs "John") — weak, used only as a fuzzy hint
    if len(ta) == 1 and tb.startswith(ta):
        return True
    if len(tb) == 1 and ta.startswith(tb):
        return True
    return False


def parse_date(value) -> Optional[str]:
    """Normalize a date-ish value to ISO YYYY-MM-DD or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                "%m/%d/%y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:len(fmt) + 5], fmt).date().isoformat()
        except ValueError:
            continue
    # last resort: grab a YYYY-MM-DD substring
    m = re.search(r"\d{4}-\d{2}-\d{2}", s)
    return m.group(0) if m else None
