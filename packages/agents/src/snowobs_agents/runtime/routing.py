"""Deterministic keyword→metric routing (BUILD_PROMPT §19).

This is what makes the platform useful with no LLM key at all: a question is
matched against metric ids, names, and synonyms, and the best-matching governed
metric is run. It is not a fallback that apologises — it answers the question,
and says only that it will not *narrate* the answer.

Routing is also used as a sanity check on the LLM path: if the model picks a
wildly different metric from the deterministic router on an eval question, that
is worth knowing.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from snowobs_semantics.model import Metric, SemanticModel

DEFAULT_LOOKBACK_DAYS = 30

#: A word rare enough across the catalogue that a metric ignoring it is
#: probably answering a different question. Weights run from ~0 (a word in
#: every metric) to 1.0 (a word in one).
_DISTINCTIVE_WORD = 0.7

#: Words that carry no routing signal.
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "in",
        "on",
        "for",
        "to",
        "by",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "what",
        "which",
        "how",
        "much",
        "many",
        "show",
        "me",
        "my",
        "our",
        "give",
        "list",
        "tell",
        "did",
        "do",
        "does",
        "last",
        "this",
        "that",
        "we",
        "i",
        "it",
        "over",
        "from",
        "with",
        "per",
        "at",
        "be",
        "get",
        # Generic verbs and quantifiers. They read as content words but appear
        # across unrelated metric descriptions, so they add noise to ranking
        # without ever distinguishing one metric from another.
        "used",
        "use",
        "have",
        "has",
        "had",
        "ran",
        "run",
        "got",
        "any",
        "there",
        "into",
        "about",
        "still",
        "just",
        "now",
        "current",
    }
)

#: Phrases that name a dimension to slice by.
_DIMENSION_HINTS: dict[str, tuple[str, ...]] = {
    "team": ("by team", "per team", "each team", "team breakdown", "which team"),
    "warehouse": ("by warehouse", "per warehouse", "each warehouse", "which warehouse"),
    "service_type": ("by service", "per service", "service type"),
    "user": ("by user", "per user", "which user", "each user"),
    "fingerprint": ("by fingerprint", "by query", "which query", "which queries"),
    "database": ("by database", "per database"),
    "error_class": ("by error", "error class", "error code"),
}

_PERIOD_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\btoday\b|\byesterday\b", re.I), 1),
    (re.compile(r"\blast\s+(\d+)\s+days?\b", re.I), 0),  # captured
    (re.compile(r"\bthis\s+week\b|\blast\s+week\b|\b7\s+days?\b", re.I), 7),
    (re.compile(r"\bthis\s+month\b|\blast\s+month\b|\b30\s+days?\b|\bmtd\b", re.I), 30),
    (re.compile(r"\bthis\s+quarter\b|\b90\s+days?\b", re.I), 90),
    (re.compile(r"\bthis\s+year\b|\byear\b|\b12\s+months?\b", re.I), 365),
)


@dataclass(frozen=True)
class RoutedQuestion:
    metric_id: str
    metric_name: str
    score: float
    dimensions: list[str] = field(default_factory=list)
    last_days: int = DEFAULT_LOOKBACK_DAYS
    #: Runner-up metrics, so an ambiguous question can be clarified.
    alternatives: list[str] = field(default_factory=list)


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9_.]+", text.lower())
        if token not in _STOPWORDS and len(token) > 1
    ]


def _stem(word: str) -> str:
    """A deliberately crude singulariser.

    Users type "which warehouse costs the most" and the catalogue says "cost",
    so matching raw tokens against raw catalogue words misses the obvious
    answer. Nothing more elaborate is warranted here: routing is a ranking, and
    over-stemming would start collapsing genuinely different metric names.
    """
    for suffix in ("ies", "es", "s"):
        if len(word) > 4 and word.endswith(suffix):
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def _words(text: str) -> set[str]:
    """Individual words, with underscores treated as separators.

    Metric ids are snake_case, so keeping the underscore made ``total_tokens``
    a single opaque word that the question "how many tokens have we used"
    could never match.
    """
    return {_stem(word) for word in re.findall(r"[a-z0-9]+", text.lower())}


def _phrase_present(phrase: str, question: str) -> bool:
    """Is this phrase in the question as whole words?

    Substring matching finds "spend" inside "auto-suspend" and hands a spend
    metric a top score on a warehouse-configuration question.
    """
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase.lower())}(?![a-z0-9])", question) is not None


def _identity_words(metric: Metric) -> set[str]:
    """Every word that names a metric: its id, its name, and its synonyms."""
    return _words(" ".join([metric.id.replace(".", " "), metric.name, *metric.synonyms]))


def _name_words(metric: Metric) -> set[str]:
    """The words in a metric's *name* — the strongest statement of its subject.

    Separated from the rest of its identity because where a word matches says
    how much it means. "How many queries ran?" matched `q.volume` ("Query
    volume"), `cost.attributed_credits` (whose synonym is "query-attributed
    credits"), and `wh.zombie_credits` ("credits with no queries") all equally,
    because every one of them contains the word somewhere. Only the first is
    about queries; in the others the word is describing what the credits are
    attributed to.
    """
    return _words(metric.name)


def _token_weights(model: SemanticModel) -> dict[str, float]:
    """Weight each catalogue word by how much it narrows the catalogue down.

    Nearly every cost metric is named with the word "credits", so matching it
    says almost nothing; "idle" and "queueing" appear in a handful and say
    almost everything. Weighting every word equally let "idle credits by
    warehouse" be answered with credits-by-warehouse — the metric that matched
    the two common words and ignored the one that mattered.

    Only words the catalogue actually uses appear here. A word no metric knows
    ("ran", "used", "the most") carries no information about which metric to
    pick, so it is left out entirely rather than given a weight that would
    penalise every candidate equally and drag the whole field below the
    matching threshold.
    """
    frequency: Counter[str] = Counter()
    for metric in model.metrics.values():
        frequency.update(_identity_words(metric))
    total = max(len(model.metrics), 1)
    # 1.0 for a word unique to one metric, tending to 0 for one in all of them.
    return {
        word: math.log(total / seen + 1) / math.log(total + 1)
        for word, seen in frequency.items()
        if seen > 0
    }


#: What a user calls each domain, against the abbreviation its metric ids use.
#: `wh.query_count` and `q.queue_share` are the warehouse and query domains and
#: neither id contains the word, so "how many queries ran per warehouse" could
#: not tell `wh.query_count` from `cost.by_warehouse_credits`.
#:
#: Applied as a small separate bonus rather than folded into the metric's
#: identity words: an identity word feeds the inverse-frequency weighting, and
#: adding "query" to all fourteen query metrics drove its weight to almost
#: nothing — which made "how many queries ran?" match no metric at all.
_DOMAIN_WORDS: dict[str, tuple[str, ...]] = {
    "cost": ("cost", "spend", "billing"),
    "warehouse": ("warehouse", "compute"),
    "query": ("query", "queries", "statement"),
    "storage": ("storage",),
    "pipeline": ("pipeline", "task", "ingestion"),
    "quality": ("quality",),
    "security": ("security", "access"),
    "ai": ("cortex",),
    "chargeback": ("chargeback", "allocation"),
}
_DOMAIN_BONUS = 0.9


def _domain_bonus(metric: Metric, tokens: list[str]) -> float:
    """Credit a metric when the question names the subject area it belongs to.

    Matched against stemmed tokens rather than the raw question: users write
    "which tasks fail", and a word-boundary search for "task" does not find
    "tasks".
    """
    words = {_stem(word) for word in _DOMAIN_WORDS.get(metric.domain, ())}
    return _DOMAIN_BONUS if any(_stem(token) in words for token in tokens) else 0.0


def _score(
    metric: Metric,
    question: str,
    tokens: list[str],
    weights: dict[str, float] | None = None,
) -> float:
    """How well a metric answers this question. Synonyms carry the most weight.

    Synonyms are what a user actually types ("spend", "waste", "queueing"), so
    an exact synonym hit outranks incidental word overlap with a description.
    """
    lowered = question.lower()
    score = 0.0

    # A multi-word synonym or name phrase is the strongest signal available.
    # Single-word synonyms are deliberately *not* scored here: they are already
    # counted once by the token loop below, and a bare common word is weak
    # evidence — the synonym "spend" was scoring a full phrase match on
    # "auto-suspend", which is not a spend question at all. Boundaries matter
    # for the same reason.
    matched_phrase = False
    for synonym in metric.synonyms:
        if _phrase_present(synonym, lowered) and len(synonym.split()) > 1:
            score += 6.0
            matched_phrase = True
    if _phrase_present(metric.name, lowered):
        score += 6.0
        matched_phrase = True
    if metric.id.lower() in lowered:
        score += 10.0

    identity = _identity_words(metric)
    named = _name_words(metric)
    described = _words(metric.description)
    weights = weights or {}
    for token in tokens:
        stemmed = _stem(token)
        weight = weights.get(stemmed, 1.0)
        if stemmed in named:
            score += 2.0 * weight
        elif stemmed in identity:
            # In the id or a synonym: real evidence, but weaker than the name.
            # This is what separates "Total credits" from "Cortex credits",
            # whose id also happens to contain the word "total".
            score += 1.4 * weight
        elif stemmed in described:
            score += 0.4 * weight
        elif matched_phrase and weight > _DISTINCTIVE_WORD:
            # This metric won a phrase bonus while ignoring a word that would
            # have narrowed the catalogue sharply. "Idle credits by warehouse"
            # contains the exact name of "Credits by warehouse", which took the
            # full phrase bonus and silently dropped "idle" — and idle credits
            # are not total credits. The penalty is scoped to phrase winners on
            # purpose: applied to every candidate it dragged whole fields under
            # the matching threshold, for words like "ran" that distinguish
            # nothing.
            score -= 3.0 * weight

    # A small nudge towards a metric that can actually be sliced the way the
    # question asks. It stays small on purpose: naming a dimension says how to
    # break the answer down, not what to measure. "Idle credits by warehouse"
    # is a question about idle credits, and a larger bonus here answered it
    # with credits-by-warehouse instead. The slice itself is applied
    # separately, by `_dimensions`.
    asked_for_a_breakdown = False
    for dimension, hints in _DIMENSION_HINTS.items():
        if any(hint in lowered for hint in hints) and dimension in metric.dimensions:
            asked_for_a_breakdown = True
            score += 0.5
            if dimension in metric.id.lower():
                score += 1.0

    # A breakdown answers "how is it distributed", not "what is it". When the
    # question asks for no distribution, one is the wrong shape of answer.
    #
    # This decides ties between metrics that share a common word but nothing
    # else: "why did spend change week over week?" put `ai.credits_by_model`
    # level with `cost.top5_concentration`, because "spend" appears in the
    # identity of both, and id length then handed it to the AI breakdown — a
    # question about the whole bill answered with a Cortex model split.
    if not asked_for_a_breakdown and _is_a_breakdown(metric):
        score -= 0.5

    return score + _domain_bonus(metric, tokens)


#: Id fragments that mark a metric as a slice of a larger figure rather than
#: the figure itself.
_BREAKDOWN_MARKERS = ("by_", "_by_", "top5", "top_", "_mix", "per_")


def _is_a_breakdown(metric: Metric) -> bool:
    name = metric.id.lower().split(".", 1)[-1]
    return any(marker in name for marker in _BREAKDOWN_MARKERS)


def _dimensions(question: str, metric: Metric) -> list[str]:
    lowered = question.lower()
    found = [
        dimension
        for dimension, hints in _DIMENSION_HINTS.items()
        if dimension in metric.dimensions and any(hint in lowered for hint in hints)
    ]
    return found[:2]


def _period(question: str) -> int:
    match = re.search(r"\blast\s+(\d+)\s+days?\b", question, re.I)
    if match:
        return max(int(match.group(1)), 1)
    for pattern, days in _PERIOD_PATTERNS:
        if days and pattern.search(question):
            return days
    return DEFAULT_LOOKBACK_DAYS


#: Phrasings that ask *why* something changed rather than what it is. These
#: want a contribution decomposition, not a total.
_CAUSAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bwhy\s+(?:did|is|has|are|was)\b", re.I),
    re.compile(r"\bwhat\s+(?:drove|caused|is driving|explains)\b", re.I),
    re.compile(r"\bresponsible for\b", re.I),
    re.compile(r"\b(?:week over week|month over month|day over day)\b", re.I),
)

#: Comparison words that mean "how did this move" only when the question also
#: names a period. "What is attributed compute versus idle?" compares two
#: *components* of one total and wants a straight lookup; "billed credits for
#: the last 7 days versus the 7 before" compares two *periods* and wants a
#: decomposition. The word alone cannot tell them apart.
_COMPARATIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:compare|compared to|versus|vs\.?|against)\b", re.I),
    re.compile(r"\bbetter or worse\b", re.I),
    re.compile(r"\b(?:increase|decrease|change|changed|spike|drop|growth|rose|fell)\b", re.I),
)

#: A reference to a period, which is what turns a comparison into a comparison
#: *over time*.
_TIME_REFERENCE = re.compile(
    r"\b(?:last|previous|prior|this|past)\s+(?:\d+\s+)?(?:day|days|week|month|quarter|year)s?\b"
    r"|\b\d+\s+(?:day|days|week|weeks|month|months)\b"
    r"|\bthe\s+\d+\s+before\b|\bmonth before\b|\byesterday\b|\bmtd\b|\bqtd\b|\bytd\b"
    r"|\b(?:january|february|march|april|may|june|july|august|september|october"
    r"|november|december)\b",
    re.I,
)

#: Calendar month names, for "between July and August".
_MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}


@dataclass(frozen=True)
class ComparisonWindows:
    """Two periods to decompose a change across, and how they were derived."""

    period_a_start: date
    period_a_end: date
    period_b_start: date
    period_b_end: date
    #: Plain-language description of the window choice, quoted in the answer so
    #: a default is never applied silently.
    basis: str


def is_causal(question: str) -> bool:
    """Does this question ask why something changed *over time*?"""
    if any(pattern.search(question) for pattern in _CAUSAL_PATTERNS):
        return True
    return bool(_TIME_REFERENCE.search(question)) and any(
        pattern.search(question) for pattern in _COMPARATIVE_PATTERNS
    )


def comparison_windows(question: str, *, today: date | None = None) -> ComparisonWindows | None:
    """Derive two comparable periods from the question's own words.

    Returns None when the question is not a comparison at all. When it is one
    but names no period, a symmetric default is used and named in ``basis``:
    "which warehouse is responsible for the cost increase" is a real question
    with no window in it, and answering it over the last 30 days against the 30
    before is far more useful than refusing until the user supplies dates.
    """
    if not is_causal(question):
        return None

    reference = today or date.today()  # noqa: DTZ011 — account-date granularity

    named = _named_month_window(question, reference)
    if named is not None:
        return named

    explicit = re.search(r"\blast\s+(\d+)\s+days?\b", question, re.I)
    if explicit:
        span = max(int(explicit.group(1)), 1)
    elif re.search(
        r"\b(?:week over week|last week|prior week|previous week|7\s+days?)\b", question, re.I
    ):
        span = 7
    elif re.search(
        r"\b(?:month over month|last month|prior month|previous month)\b", question, re.I
    ):
        span = 30
    elif re.search(r"\b(?:quarter|90\s+days?)\b", question, re.I):
        span = 90
    else:
        span = DEFAULT_LOOKBACK_DAYS

    recent_end = reference
    recent_start = recent_end - timedelta(days=span - 1)
    prior_end = recent_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=span - 1)
    basis = (
        f"the last {span} days ({recent_start} to {recent_end}) against the "
        f"{span} before them ({prior_start} to {prior_end})"
    )
    return ComparisonWindows(
        period_a_start=prior_start,
        period_a_end=prior_end,
        period_b_start=recent_start,
        period_b_end=recent_end,
        basis=basis,
    )


def _named_month_window(question: str, reference: date) -> ComparisonWindows | None:
    """ "between July and August" — two calendar months, most recent occurrence."""
    found = [
        (match.start(), _MONTHS[match.group(0).lower()])
        for match in re.finditer(
            r"\b(" + "|".join(_MONTHS) + r")\b",
            question,
            re.I,
        )
    ]
    if len(found) < 2:
        return None
    first, second = found[0][1], found[1][1]

    def bounds(month: int) -> tuple[date, date]:
        # The most recent occurrence of that month that has already started.
        year = reference.year if month <= reference.month else reference.year - 1
        start = date(year, month, 1)
        end = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(
            days=1
        )
        return start, min(end, reference)

    a_start, a_end = bounds(first)
    b_start, b_end = bounds(second)
    return ComparisonWindows(
        period_a_start=a_start,
        period_a_end=a_end,
        period_b_start=b_start,
        period_b_end=b_end,
        basis=f"{a_start:%B} ({a_start} to {a_end}) against {b_start:%B} ({b_start} to {b_end})",
    )


def account_named(question: str, accounts: Sequence[str]) -> str | None:
    """The account this question names, when it names exactly one of ours.

    Deliberately strict. Naming two accounts ("compare PROD and SANDBOX") is
    not a request to scope to either of them, and naming none is a question
    about the organization — in both cases the answer stays organization-wide,
    where the response reports every contributing account and the reader can
    see the breakdown for themselves. Guessing which of two accounts was meant
    would put one account's figure under a question about both.
    """
    if not accounts:
        return None
    lowered = question.lower()
    found = {
        account
        for account in accounts
        # Word-bounded so an account called PROD is not matched inside
        # "production", and escaped because an account name is data here.
        if re.search(rf"(?<![a-z0-9_]){re.escape(account.lower())}(?![a-z0-9_])", lowered)
    }
    return found.pop() if len(found) == 1 else None


def route(question: str, model: SemanticModel, *, threshold: float = 0.8) -> RoutedQuestion | None:
    """Match a question to a governed metric, or return None rather than guess."""
    tokens = _tokens(question)
    if not tokens:
        return None

    # Id length breaks ties — a shorter id is usually the headline figure
    # rather than a specialised variant — and the id itself makes the order
    # total, so routing never depends on dictionary ordering. Length is kept
    # out of the score itself: subtracting it there pushed legitimate
    # single-word matches under the threshold and made the router answer
    # "nothing matched" to questions as ordinary as "how many queries ran?".
    weights = _token_weights(model)
    scored = sorted(
        ((_score(metric, question, tokens, weights), metric) for metric in model.metrics.values()),
        key=lambda pair: (-pair[0], len(pair[1].id), pair[1].id),
    )
    best_score, best = scored[0]
    if best_score < threshold:
        return None

    return RoutedQuestion(
        metric_id=best.id,
        metric_name=best.name,
        score=round(best_score, 2),
        dimensions=_dimensions(question, best),
        last_days=_period(question),
        alternatives=[metric.id for _, metric in scored[1:4]],
    )
