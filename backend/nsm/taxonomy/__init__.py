"""Skill taxonomy: alias matching and relation-based claim propagation.

Relations (see skills.json):
- parents  (is-a):   evidence for the child counts at the SAME level for the parent
                     (e.g. demonstrated pgvector => demonstrated vector database)
- requires:          using the child means using the parent, capped at 'applied'
                     (e.g. demonstrated FastAPI => applied Python)
- implies:           conceptual inference => 'inferred' (always labelled, low weight)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

LEVELS = ["demonstrated", "applied", "inferred", "listed_only", "missing"]
LEVEL_RANK = {lvl: i for i, lvl in enumerate(LEVELS)}  # lower = stronger


def stronger(a: str, b: str) -> str:
    return a if LEVEL_RANK.get(a, 99) <= LEVEL_RANK.get(b, 99) else b


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    category: str
    aliases: tuple[str, ...]
    parents: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    implies: tuple[str, ...] = ()


@dataclass(frozen=True)
class Match:
    skill_id: str
    start: int
    end: int
    alias: str


@dataclass
class Taxonomy:
    version: str
    skills: dict[str, Skill]
    _alias_to_skill: dict[str, str] = field(default_factory=dict)
    _pattern: re.Pattern | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "Taxonomy":
        path = path or Path(__file__).with_name("skills.json")
        raw = json.loads(path.read_text(encoding="utf-8"))
        skills = {
            s["id"]: Skill(
                id=s["id"], name=s["name"], category=s.get("category", "other"),
                aliases=tuple(a.lower() for a in s.get("aliases", [])),
                parents=tuple(s.get("parents", [])), requires=tuple(s.get("requires", [])),
                implies=tuple(s.get("implies", [])),
            )
            for s in raw["skills"]
        }
        tax = cls(version=raw.get("version", "unknown"), skills=skills)
        tax._build()
        return tax

    def _build(self) -> None:
        for s in self.skills.values():
            for rel in (*s.parents, *s.requires, *s.implies):
                if rel not in self.skills:
                    raise ValueError(f"taxonomy: {s.id} references unknown skill {rel}")
            for a in (*s.aliases, s.name.lower()):
                self._alias_to_skill.setdefault(a, s.id)
        aliases = sorted(self._alias_to_skill, key=len, reverse=True)
        body = "|".join(re.escape(a) for a in aliases)
        self._pattern = re.compile(rf"(?<![A-Za-z0-9])({body})(?![A-Za-z0-9+#])", re.IGNORECASE)

    # ------------------------------------------------------------------ matching
    def find(self, text: str) -> list[Match]:
        """Longest-first, non-overlapping alias matches."""
        assert self._pattern is not None
        out: list[Match] = []
        for m in self._pattern.finditer(text):
            alias = m.group(1).lower()
            sid = self._alias_to_skill.get(alias)
            if sid:
                out.append(Match(sid, m.start(1), m.end(1), alias))
        return out

    def skill_ids_in(self, text: str) -> list[str]:
        seen: list[str] = []
        for m in self.find(text):
            if m.skill_id not in seen:
                seen.append(m.skill_id)
        return seen

    # ------------------------------------------------------------------ relations
    def name(self, skill_id: str) -> str:
        s = self.skills.get(skill_id)
        return s.name if s else skill_id

    def ancestors(self, skill_id: str) -> list[str]:
        """Transitive is-a parents."""
        out: list[str] = []
        stack = list(self.skills[skill_id].parents) if skill_id in self.skills else []
        while stack:
            p = stack.pop()
            if p not in out:
                out.append(p)
                stack.extend(self.skills[p].parents)
        return out

    def descendants(self, skill_id: str) -> list[str]:
        return [s.id for s in self.skills.values() if skill_id in self.ancestors(s.id)]

    def propagate(self, skill_id: str, level: str) -> list[tuple[str, str, str]]:
        """Derived claims from one direct claim: [(skill_id, level, via)]."""
        derived: list[tuple[str, str, str]] = []
        chain = [skill_id, *self.ancestors(skill_id)]
        for anc in chain[1:]:
            derived.append((anc, level, f"is_a:{skill_id}"))
        if level in ("demonstrated", "applied"):
            seen_req: set[str] = set()
            queue = [(req, sid) for sid in chain for req in self.skills[sid].requires]
            while queue:  # transitive: pgvector -> postgresql -> sql
                req, src = queue.pop(0)
                if req in seen_req:
                    continue
                seen_req.add(req)
                for target in (req, *self.ancestors(req)):
                    derived.append((target, "applied", f"requires:{src}"))
                queue.extend((r, req) for r in self.skills[req].requires)
            for sid in chain:
                for imp in self.skills[sid].implies:
                    derived.append((imp, "inferred", f"implies:{sid}"))
        return derived

    def query_terms(self, skill_id: str, max_aliases: int = 4) -> str:
        s = self.skills.get(skill_id)
        if not s:
            return skill_id
        return " ".join([s.name, *s.aliases[:max_aliases]])


@lru_cache
def get_taxonomy() -> Taxonomy:
    return Taxonomy.load()
