"""The four duplicate checks from PLAN.md section 3.2, and grouping a run's jobs.

    1. Same URL          normalized URL hash
    2. Same source ID    (source, source_job_id)
    3. Same fingerprint  normalized company + title + city
    4. Fuzzy match       same company, compatible place, very similar title
                         (see title_rule for what "very similar" means), and
                         from different boards

A missed duplicate only shows a job twice; a wrong merge hides a job. So every rule
here leans towards keeping jobs apart when in doubt.
"""

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from werkbot.dedup.normalize import (
    fingerprint,
    normalize_company,
    normalize_title,
    normalize_url,
    parse_location,
    sha256,
)
from werkbot.models import Job

# The plan said "token_set_ratio >= 90". Testing showed two problems with that alone:
# - it is 100 whenever one title's words are a subset of the other's, so
#   "Werkstudent (m/w/d)" would equal "Werkstudent Data Science";
# - it is >= 90 when titles differ in one SHORT word: "Werkstudent HR Backend" vs
#   "Werkstudent Design Backend" scores 93.
# So title_rule looks at the words that differ instead.
SUBSET_GUARD_MIN = 80  # extra words only: token_sort_ratio (compares length) at least this
SPELLING_MIN = 80  # differing words on both sides: fuzz.ratio of those words at least this
BORDERLINE_MIN = 85  # otherwise: token_set_ratio >= 85 AND near-identical descriptions
MIN_DESCRIPTION_CHARS = 300
DESCRIPTION_MIN = 90


@dataclass(frozen=True)
class Keys:
    """Everything the duplicate checks compare, computed once per job."""

    url_hash: str
    source_id: tuple[str, str] | None
    company: str  # '' = unknown: never matched on levels 3 and 4
    title: str
    city: str  # first city, 'remote', or ''
    cities: frozenset[str]
    remote: bool
    fingerprint: str | None
    boards: frozenset[str] = frozenset()  # boards this job was seen on

    @classmethod
    def of(cls, job: Job, aliases=(), boards: dict[str, str] | None = None) -> "Keys":
        """`boards` maps source name -> board, so linkedin_local and linkedin_remote
        (two searches on one site) count as one board. Default: each source is a board."""
        cities, says_remote = parse_location(job.location)
        remote = job.remote or says_remote
        company = normalize_company(job.company, aliases)
        title = normalize_title(job.title)
        city = cities[0] if cities else ("remote" if remote else "")
        return cls(
            url_hash=sha256(normalize_url(str(job.url))),
            source_id=(job.source, job.source_job_id) if job.source_job_id else None,
            company=company,
            title=title,
            city=city,
            cities=frozenset(cities),
            remote=remote,
            fingerprint=fingerprint(company, title, city),
            boards=frozenset({(boards or {}).get(job.source, job.source)}),
        )


def same_place(a: Keys, b: Keys) -> bool:
    """One job's main city is among the other's cities, one is remote, or one is unknown.

    Only the main (first) city counts on each side, so a shared word like
    "Württemberg" in "Leonberg, Württemberg" / "Fellbach, Württemberg" is not a match,
    while "Hamburg" still matches a list like "Berlin, Hamburg, München".
    """
    if a.remote or b.remote or not a.cities or not b.cities:
        return True
    return a.city in b.cities or b.city in a.cities


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()[:3000]


def similar_descriptions(a: str, b: str) -> bool:
    """Near-identical descriptions (the same ad copied to another board).

    Two different jobs of one company share boilerplate, so this needs a high score on
    the text itself. Phase 4 can swap in embeddings (cosine >= 0.95, as in the plan).
    """
    a, b = _squash(a or ""), _squash(b or "")
    if min(len(a), len(b)) < MIN_DESCRIPTION_CHARS:
        return False
    return fuzz.ratio(a, b) >= DESCRIPTION_MIN


DescriptionCheck = Callable[[str, str], bool]


def title_rule(a: str, b: str) -> str | None:
    """Why two normalized titles are the same job title, or None.

    - same words, any order
    - one title has extra words (a board added or dropped "- C/C++", a year, a city)
    - the words that differ are spelling variants of each other
    """
    words_a, words_b = a.split(), b.split()
    if not words_a or not words_b:
        return None
    if set(words_a) == set(words_b):
        return "same words"
    only_a = [w for w in words_a if w not in words_b]
    only_b = [w for w in words_b if w not in words_a]
    if not only_a or not only_b:
        if fuzz.token_sort_ratio(a, b) >= SUBSET_GUARD_MIN:
            return "extra words"
        return None
    if fuzz.ratio("".join(only_a), "".join(only_b)) >= SPELLING_MIN:
        return "spelling variant"
    return None


def fuzzy_reason(
    a: Keys,
    b: Keys,
    desc_a: str = "",
    desc_b: str = "",
    confirm: DescriptionCheck = similar_descriptions,
) -> str | None:
    """Level 4. Returns why two jobs are the same, or None."""
    if not a.company or a.company != b.company or not a.title or not b.title:
        return None
    # One board listing two IDs with different titles = two jobs (a company posting
    # "Werkstudent Software" and "Werkstudent Software Embedded"). The fuzzy rule is only
    # for the same job copied to ANOTHER board, where titles get shortened or reworded.
    if a.boards & b.boards:
        return None
    if not same_place(a, b):
        return None
    if rule := title_rule(a.title, b.title):
        return f"similar title ({rule})"
    score = fuzz.token_set_ratio(a.title, b.title)
    if (
        score >= BORDERLINE_MIN
        and fuzz.token_sort_ratio(a.title, b.title) >= SUBSET_GUARD_MIN
        and confirm(desc_a, desc_b)
    ):
        return f"similar title ({score:.0f}) + same description"
    return None


@dataclass
class Group:
    """Jobs that are one posting. `indexes` point into the list given to group_jobs."""

    indexes: list[int]
    reasons: list[str] = field(default_factory=list)


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> bool:
        ri, rj = self.find(i), self.find(j)
        if ri == rj:
            return False
        self.parent[max(ri, rj)] = min(ri, rj)
        return True


def group_jobs(
    jobs: list[Job],
    keys: list[Keys],
    confirm: DescriptionCheck = similar_descriptions,
) -> list[Group]:
    """Split one run's jobs into groups of duplicates (levels 1 to 4)."""
    uf = _UnionFind(len(jobs))
    reasons: list[tuple[int, int, str]] = []

    def link(i: int, j: int, why: str) -> None:
        if uf.union(i, j):
            reasons.append((i, j, why))

    # Levels 1-3: exact keys
    for level, key_of in (
        ("same URL", lambda k: k.url_hash),
        ("same source ID", lambda k: k.source_id),
        ("same company + title + city", lambda k: k.fingerprint),
    ):
        first: dict = {}
        for i, k in enumerate(keys):
            key = key_of(k)
            if key is None:
                continue
            if key in first:
                link(first[key], i, level)
            else:
                first[key] = i

    # Level 4: only within one company, so it stays fast with thousands of jobs.
    # Two groups are only joined if EVERY pair across them is similar. Otherwise a short
    # title like "Werkstudent Software" could chain "... Frontend" and "... Backend".
    by_company: dict[str, list[int]] = defaultdict(list)
    for i, k in enumerate(keys):
        if k.company:
            by_company[k.company].append(i)
    for members in by_company.values():
        for pos, i in enumerate(members):
            for j in members[pos + 1 :]:
                if uf.find(i) == uf.find(j):
                    continue
                why = fuzzy_reason(
                    keys[i], keys[j], jobs[i].description, jobs[j].description, confirm
                )
                if why and _all_pairs_similar(uf, members, i, j, jobs, keys, confirm):
                    link(i, j, why)

    groups: dict[int, Group] = {}
    for i in range(len(jobs)):
        groups.setdefault(uf.find(i), Group(indexes=[])).indexes.append(i)
    for i, j, why in reasons:
        groups[uf.find(i)].reasons.append(f"{why} ({jobs[i].source} = {jobs[j].source})")
    return sorted(groups.values(), key=lambda g: g.indexes[0])


def _all_pairs_similar(uf, members, i, j, jobs, keys, confirm) -> bool:
    root_i, root_j = uf.find(i), uf.find(j)
    side_i = [m for m in members if uf.find(m) == root_i]
    side_j = [m for m in members if uf.find(m) == root_j]
    for a in side_i:
        for b in side_j:
            if keys[a].title == keys[b].title and same_place(keys[a], keys[b]):
                continue
            if not fuzzy_reason(
                keys[a], keys[b], jobs[a].description, jobs[b].description, confirm
            ):
                return False
    return True
