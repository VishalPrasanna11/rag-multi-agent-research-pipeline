"""Fact/citation store with a look-ahead guard, and the grounding renderer/validator.

Grounding rule: narrative text may contain numbers ONLY through fact references.
Writers emit `{{fact:key}}`; `render()` turns them into `[[fact:key|display]]` tokens the UI hovers;
`uncited_numbers()` returns any digits left outside tokens (minus a small allowlist such as "Q3", "10-K").
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from app.schemas import Citation, Fact, SourceKind

FACT_REF = re.compile(r"\{\{\s*fact:([a-z0-9_.\-]+)\s*\}\}")
FACT_TOKEN = re.compile(r"\[\[fact:([a-z0-9_.\-]+)\|([^\]]*)\]\]")

# Digits that are labels, not claims.
_ALLOWED_NUMERIC = re.compile(
    r"""
    \b(?:FY|CY)\s?\d{2,4}(?:\s?Q[1-4])?\b |      # FY2027, FY27 Q3
    \bQ[1-4](?:\s?(?:FY)?\s?\d{2,4})?\b |       # Q3, Q3 2026
    \b(?:H[12]|[1-4]Q)\b |
    \b(?:10-K|10-Q|8-K|20-F|6-K|S-1|13F|13D|13G|DEF\s14A|Form\s[345])\b |
    \bItem\s\d{1,2}(?:\.\d{2})?[A-Z]?\b |       # Item 2.02, Item 1A
    \bEX-\d{2}(?:\.\d+)?\b |
    \bday[\s-]?\d{1,2}\b |                      # day 1, day-5
    \bS&P\s?500\b | \bT-\d\b |
    \b(?:19|20)\d{2}\b                          # bare years
    """,
    re.VERBOSE | re.IGNORECASE,
)
_NUMBER = re.compile(r"(?<![A-Za-z])[-+]?\$?\d[\d,]*(?:\.\d+)?\s?(?:%|bps|x|[KMBT]\b)?")


class LookAheadError(ValueError):
    pass


def _cid(*parts: str) -> str:
    return "c_" + hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]  # noqa: S324 - id, not security


class FactStore:
    def __init__(self, cutoff: datetime) -> None:
        self.cutoff = cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=UTC)
        self.facts: dict[str, Fact] = {}
        self.citations: dict[str, Citation] = {}

    # ---- citations
    def cite(
        self,
        source: SourceKind | str,
        title: str,
        as_of: datetime,
        url: str | None = None,
        locator: str | None = None,
        license_note: str | None = None,
    ) -> str:
        as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
        if as_of > self.cutoff:
            raise LookAheadError(
                f"citation '{title}' as_of {as_of.isoformat()} is after cutoff {self.cutoff}"
            )
        cid = _cid(str(source), title, url or "", locator or "", as_of.isoformat())
        if cid not in self.citations:
            self.citations[cid] = Citation(
                id=cid,
                source=SourceKind(source),
                title=title,
                url=url,
                as_of=as_of,
                retrieved_at=datetime.now(UTC),
                locator=locator,
                license_note=license_note,
            )
        return cid

    # ---- facts
    def add(
        self,
        key: str,
        label: str,
        value: float | str | None,
        unit: str,
        citation_id: str,
        period: str | None = None,
        derived_from: list[str] | None = None,
        formula: str | None = None,
    ) -> Fact:
        cit = self.citations.get(citation_id)
        if cit is None:
            raise KeyError(f"unknown citation {citation_id}")
        if isinstance(value, float) and value != value:  # NaN
            value = None
        fact = Fact(
            key=key,
            label=label,
            value=value,
            unit=unit,
            period=period,
            as_of=cit.as_of,
            citation_id=citation_id,
            derived_from=derived_from or [],
            formula=formula,
            status="ok" if value is not None else "unavailable",
        )
        self.facts[key] = fact
        return fact

    def derive(
        self,
        key: str,
        label: str,
        value: float | None,
        unit: str,
        inputs: list[str],
        formula: str,
        period: str | None = None,
    ) -> Fact | None:
        """A computed fact; its citation is a DERIVED citation listing inputs; as_of = max(input as_of)."""
        srcs = [self.facts[k] for k in inputs if k in self.facts and self.facts[k].status == "ok"]
        if len(srcs) != len(inputs) or value is None:
            return None
        as_of = max(f.as_of for f in srcs)
        cid = self.cite(SourceKind.DERIVED, f"Computed: {formula}", as_of, locator=",".join(inputs))
        return self.add(
            key, label, round(value, 6), unit, cid, period=period, derived_from=inputs, formula=formula
        )

    def unavailable(self, key: str, label: str, needs: str, unit: str = "") -> Fact:
        cid = self.cite(SourceKind.DERIVED, "Not available", self.cutoff, locator=needs)
        f = Fact(
            key=key,
            label=label,
            value=None,
            unit=unit,
            as_of=self.cutoff,
            citation_id=cid,
            status="unavailable",
            needs=needs,
        )
        self.facts[key] = f
        return f

    def get(self, key: str) -> float | str | None:
        f = self.facts.get(key)
        return f.value if f and f.status == "ok" else None

    def num(self, key: str) -> float | None:
        v = self.get(key)
        return float(v) if isinstance(v, int | float) else None

    def ok_keys(self, prefix: str = "") -> list[str]:
        return [k for k, f in self.facts.items() if f.status == "ok" and k.startswith(prefix)]

    # ---- (de)serialisation for graph state
    def dump(self) -> dict[str, Any]:
        return {
            "facts": {k: f.model_dump(mode="json") for k, f in self.facts.items()},
            "citations": {k: c.model_dump(mode="json") for k, c in self.citations.items()},
        }

    @classmethod
    def load(cls, cutoff: datetime, data: dict[str, Any]) -> FactStore:
        st = cls(cutoff)
        st.citations = {k: Citation.model_validate(v) for k, v in data.get("citations", {}).items()}
        st.facts = {k: Fact.model_validate(v) for k, v in data.get("facts", {}).items()}
        return st


# ---------------------------------------------------------------- grounding


def render(text: str, facts: dict[str, Fact]) -> tuple[str, list[str], list[str]]:
    """Replace {{fact:key}} with hoverable tokens. Returns (text, used_keys, unknown_keys)."""
    used: list[str] = []
    unknown: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        key = m.group(1)
        f = facts.get(key)
        if f is None:
            unknown.append(key)
            return "[not available]"
        used.append(key)
        if f.status != "ok":
            return f"[[fact:{key}|n/a]]"
        return f"[[fact:{key}|{f.display()}]]"

    return FACT_REF.sub(_sub, text), used, unknown


def uncited_numbers(rendered: str) -> list[str]:
    """Numbers in narrative text that are not inside a fact token or an allowed label."""
    stripped = FACT_TOKEN.sub(" ", rendered)
    stripped = re.sub(r"\[[^\]]*\]\([^)]*\)", " ", stripped)  # markdown links
    stripped = _ALLOWED_NUMERIC.sub(" ", stripped)
    stripped = re.sub(r"^\s*\d+[.)]\s", " ", stripped, flags=re.MULTILINE)  # list numbering
    out = []
    for m in _NUMBER.finditer(stripped):
        tok = m.group(0).strip()
        if re.fullmatch(r"[1-9]", tok):  # small counting words like "3 risks" are tolerated
            continue
        out.append(tok)
    return out


def strip_tokens(rendered: str) -> str:
    """Plain text (for exports/LLM judge): token -> display value."""
    return FACT_TOKEN.sub(lambda m: m.group(2), rendered)
