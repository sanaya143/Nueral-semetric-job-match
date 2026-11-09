"""Offline, deterministic CV understanding.

1. Segment the (sanitised, redacted) CV into sections and evidence units.
2. Find skill mentions with the taxonomy and assign an evidence level per sentence:
   - demonstrated: an action verb describes the person doing the work
   - applied:      named in a described role/project or tech-stack line, no detail
   - listed_only:  skills list, summary, or "exposure to / familiar with"
3. Propagate claims through taxonomy relations (is-a, requires, implies).

Every claim quote is an exact substring of its evidence unit (validated later).
"""

from __future__ import annotations

import re
from datetime import date

from nsm.extraction.types import ClaimDraft, ExtractionResult, UnitDraft
from nsm.taxonomy import LEVEL_RANK, Taxonomy, get_taxonomy

HEADINGS: dict[str, list[str]] = {
    "summary": ["summary", "professional summary", "profile", "profile summary", "about me", "objective",
                "career objective", "career summary", "professional profile"],
    "experience": ["experience", "work experience", "professional experience", "employment history",
                   "work history", "career history", "employment", "relevant experience"],
    "projects": ["projects", "key projects", "personal projects", "academic projects", "project experience",
                 "selected projects", "side projects", "notable projects"],
    "skills": ["skills", "technical skills", "core skills", "key skills", "skill set", "skillset", "technologies",
               "tech stack", "core competencies", "competencies", "tools", "tools & technologies",
               "technical expertise", "areas of expertise"],
    "education": ["education", "academic qualifications", "qualifications", "academics",
                  "educational qualifications", "education & training"],
    "certifications": ["certifications", "certificates", "courses", "certifications & courses", "training"],
    "achievements": ["achievements", "awards", "accomplishments", "awards & achievements", "honours", "honors"],
    "preferences": ["preferences", "job preferences", "availability", "work preferences"],
    "other": ["languages", "languages known", "hobbies", "interests", "publications", "volunteering",
              "extra-curricular activities", "references"],
}
_HEADING_LOOKUP = {label: kind for kind, labels in HEADINGS.items() for label in labels}

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
           "oct": 10, "nov": 11, "dec": 12}
_MONTH_RE = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE_RE = rf"(?:{_MONTH_RE}\.?\s*'?(?:19|20)\d{{2}}|(?:0?[1-9]|1[0-2])[/.-](?:19|20)\d{{2}}|(?:19|20)\d{{2}})"
_END_RE = rf"(?:{_DATE_RE}|present|current|now|till date|till now|to date|ongoing|today)"
DATE_RANGE = re.compile(rf"\(?\s*(?P<start>{_DATE_RE})\s*(?:-|–|—|to|till|until)\s*(?P<end>{_END_RE})\s*\)?",
                        re.IGNORECASE)

ACTION = re.compile(
    r"\b(built|build|developed|develop|implemented|implement|designed|architected|created|deployed|integrated|"
    r"engineered|launched|shipped|delivered|migrated|automated|optimi[sz]ed|trained|fine-tuned|led|wrote|authored|"
    r"refactored|scaled|reduced|improved|set up|configured|established|productioni[sz]ed|orchestrated|modell?ed|"
    r"analy[sz]ed|tested|maintained|owned|introduced|prototyped|benchmarked|containeri[sz]ed|instrumented|tuned|"
    r"rebuilt|redesigned|streamlined|cut|added|exposed|evaluated|ran|operated|managed|wired|replaced|converted|"
    r"published|programmed|coded|constructed|piloted|spearheaded|drove|accelerated)\b",
    re.IGNORECASE,
)
WEAK_LISTED = re.compile(r"\b(exposure to|familiar(ity)? with|knowledge of|basic understanding|understanding of|"
                         r"currently learning|learning to|learning about|self-learning|learnt|learned about|"
                         r"introduced to|awareness of|interested in|keen interest)\b",
                         re.IGNORECASE)
WEAK_PARTICIPATION = re.compile(r"\b(worked on|working on|responsible for|involved in|participated in|assisted|"
                                r"supported|helped|part of|attended|coordinat\w*|handled)\b", re.IGNORECASE)
TECH_STACK = re.compile(r"^\s*-?\s*(tech(nology)? stack|technologies( used)?|environment|tools( used)?|stack|"
                        r"skills used|tech used)\s*:", re.IGNORECASE)
OUTCOME = re.compile(r"\d+(\.\d+)?\s*(%|x\b|ms\b|s\b|seconds|minutes|hours|users|customers|requests|rps|qps|"
                     r"lakh|crore|k\b|million|documents|pdfs|tickets|pipelines|services)", re.IGNORECASE)
SELF_PRAISE = re.compile(r"\b(expert|expertise|guru|ninja|rockstar|master of|world-class|thought leader)\b",
                         re.IGNORECASE)
SENIOR_TITLE = re.compile(r"\b(senior|sr\.?|lead|principal|architect|head|manager|director)\b", re.IGNORECASE)

CITIES = ["Bengaluru", "Bangalore", "Hyderabad", "Pune", "Chennai", "Noida", "Gurugram", "Gurgaon", "Mumbai",
          "Navi Mumbai", "Kolkata", "Kochi", "Ahmedabad", "Jaipur", "Delhi", "New Delhi", "Coimbatore",
          "Indore", "Chandigarh", "Thiruvananthapuram", "Bhubaneswar", "Mysuru", "Nagpur", "Remote"]
_CITY_RE = re.compile(r"\b(" + "|".join(re.escape(c) for c in CITIES) + r")\b", re.IGNORECASE)
WORK_MODE = re.compile(r"(?:work ?mode\s*:\s*|open to\s+|prefer(?:s|red)?\s*:?\s*|looking for\s+(?:a\s+)?)"
                       r"(remote|hybrid|on-?site|office|in-office|wfo|wfh)\b", re.IGNORECASE)
PREF_LOC = re.compile(r"(preferred location|location preference|current location|location)\s*:\s*([A-Za-z ,/]+)",
                      re.IGNORECASE)
NOTICE = re.compile(r"notice period\s*:\s*(immediate|immediately|(\d+)\s*(day|days|week|weeks|month|months))",
                    re.IGNORECASE)


def _now_year() -> float:
    today = date.today()
    return today.year + (today.month - 1) / 12


def parse_date_token(tok: str, is_end: bool = False) -> float | None:
    t = tok.strip().lower().replace("'", "")
    if t in ("present", "current", "now", "till date", "till now", "to date", "ongoing", "today"):
        return _now_year()
    m = re.match(rf"({_MONTH_RE})\.?\s*((?:19|20)\d{{2}})", t)
    if m:
        return int(m.group(2)) + (_MONTHS[m.group(1)[:3]] - 1) / 12
    m = re.match(r"(\d{1,2})[/.-]((?:19|20)\d{2})", t)
    if m:
        return int(m.group(2)) + (int(m.group(1)) - 1) / 12
    m = re.match(r"((?:19|20)\d{2})", t)
    if m:
        return int(m.group(1)) + (11 / 12 if is_end else 0.0)
    return None


def _heading_kind(line: str) -> str | None:
    s = re.sub(r"[=_#*~|:]+", " ", line).strip().lower()
    s = re.sub(r"\s+", " ", s)
    if not s or len(s.split()) > 5:
        return None
    return _HEADING_LOOKUP.get(s)


def _lines_with_offsets(text: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for line in text.split("\n"):
        out.append((pos, line))
        pos += len(line) + 1
    return out


def _is_bullet(line: str) -> bool:
    return bool(re.match(r"^\s*[-*•]\s+", line))


def _sentences(text: str, base: int) -> list[tuple[int, str]]:
    """Split a block into sentences/bullets with absolute offsets."""
    out: list[tuple[int, str]] = []
    for off, line in _lines_with_offsets(text):
        if not line.strip():
            continue
        start = 0
        for m in re.finditer(r"(?<=[a-z0-9)%])\.\s+(?=[A-Z])|;\s+", line):
            seg = line[start:m.start() + 1]
            if seg.strip():
                out.append((base + off + start, seg))
            start = m.end()
        seg = line[start:]
        if seg.strip():
            out.append((base + off + start, seg))
    return out


def _split_title_company(header: str) -> tuple[str, str]:
    h = DATE_RANGE.sub("", header).strip(" |,-–—()")
    parts = [p.strip() for p in re.split(r"\s*\|\s*|\s+[—–-]\s+|,\s+|\s+at\s+|\s+@\s+", h) if p.strip()]
    if not parts:
        return header.strip(), ""
    return parts[0], parts[1] if len(parts) > 1 else ""


class _Entry:
    def __init__(self, start: int, header: str):
        self.start = start
        self.header_lines = [header]
        self.header_end = start + len(header)
        self.end = self.header_end
        self.has_body = False
        self.start_year: float | None = None
        self.end_year: float | None = None
        self._dates_from(header)

    def _dates_from(self, line: str) -> bool:
        m = DATE_RANGE.search(line)
        if m and self.start_year is None:
            self.start_year = parse_date_token(m.group("start"))
            self.end_year = parse_date_token(m.group("end"), is_end=True)
            return True
        return False


def _parse_entries(section_lines: list[tuple[int, str]], dated: bool) -> list[_Entry]:
    entries: list[_Entry] = []
    cur: _Entry | None = None
    for off, line in section_lines:
        s = line.strip()
        if not s:
            continue
        if _is_bullet(line) or TECH_STACK.match(line):
            if cur is None:
                cur = _Entry(off, line)
                entries.append(cur)
            cur.has_body = True
            cur.end = off + len(line)
            continue
        has_range = bool(DATE_RANGE.search(line))
        if cur is not None and not cur.has_body and has_range and cur.start_year is None:
            cur.header_lines.append(line)  # two-line header: title line, then dates line
            cur._dates_from(line)
            cur.header_end = cur.end = off + len(line)
            continue
        if cur is None or cur.has_body or (dated and has_range):
            cur = _Entry(off, line)
            entries.append(cur)
            continue
        if not cur.has_body and len(cur.header_lines) < 2 and not dated:
            cur.has_body = True  # project description paragraph
            cur.end = off + len(line)
            continue
        if not cur.has_body and len(cur.header_lines) < 2:
            cur.header_lines.append(line)
            cur.header_end = cur.end = off + len(line)
            continue
        cur.has_body = True
        cur.end = off + len(line)
    return entries


def segment(text: str) -> tuple[list[UnitDraft], str, list[str]]:
    """Returns (units, headline, flags)."""
    lines = _lines_with_offsets(text)
    sections: list[tuple[str, int, list[tuple[int, str]]]] = []  # (kind, heading_offset, lines)
    header_block: list[tuple[int, str]] = []
    current: tuple[str, int, list[tuple[int, str]]] | None = None
    for off, line in lines:
        kind = _heading_kind(line)
        if kind:
            current = (kind, off, [])
            sections.append(current)
        elif current is None:
            header_block.append((off, line))
        else:
            current[2].append((off, line))

    flags: list[str] = []
    headline = ""
    for _, line in header_block:
        s = line.strip()
        if NOTICE.search(s) or WORK_MODE.search(s) or PREF_LOC.search(s):
            continue  # a logistics line ("Pune | Notice period: 30 days") is not a headline
        if s and not re.fullmatch(r"[\[\]|,\s\-a-z]*", s.replace("[email]", "").replace("[phone]", "")
                                  .replace("[link]", "")):
            headline = re.sub(r"\s*\|?\s*\[(email|phone|link)\]", "", s).strip(" |")
            break

    units: list[UnitDraft] = []
    n = 0

    def add(kind: str, start: int, end: int, title: str, header_len: int = 0,
            sy: float | None = None, ey: float | None = None, ctx: str = "") -> None:
        nonlocal n
        body = text[start:end]
        if not body.strip():
            return
        units.append(UnitDraft(key=f"u{n}", kind=kind, title=title[:250], context_header=(ctx or title)[:500],
                               start=start, end=end, text=body, start_year=sy, end_year=ey, header_len=header_len))
        n += 1

    if not sections:
        flags.append("unstructured_cv")
        add("other", 0, len(text), "Full CV text")
        return units, headline, flags

    for kind, _hoff, sec_lines in sections:
        content = [(o, ln) for o, ln in sec_lines if ln.strip()]
        if not content:
            continue
        sec_start, sec_end = content[0][0], content[-1][0] + len(content[-1][1])
        if kind in ("experience", "projects"):
            for e in _parse_entries(content, dated=(kind == "experience")):
                title, company = _split_title_company(e.header_lines[0])
                yrs = ""
                if e.start_year:
                    yrs = f" ({int(e.start_year)}–{int(e.end_year) if e.end_year else 'present'})"
                ctx = f"{title} at {company}{yrs}" if company else f"{title}{yrs}"
                ukind = "experience" if kind == "experience" else "project"
                if not e.has_body and kind == "experience":
                    continue
                add(ukind, e.start, e.end, title, header_len=e.header_end - e.start,
                    sy=e.start_year, ey=e.end_year, ctx=("Project: " + ctx) if ukind == "project" else ctx)
        elif kind == "achievements":
            add("achievement", sec_start, sec_end, "Achievements")
        elif kind == "other":
            continue
        else:
            add(kind, sec_start, sec_end, kind.capitalize())
    return units, headline, flags


def _level_for_sentence(sentence: str, unit_kind: str) -> tuple[str, float]:
    if unit_kind in ("skills", "summary", "certifications"):
        return "listed_only", 0.5
    if unit_kind == "education":
        return "listed_only", 0.5
    if TECH_STACK.match(sentence):
        return "applied", 0.6
    if WEAK_LISTED.search(sentence):
        return "listed_only", 0.5
    if WEAK_PARTICIPATION.search(sentence):
        return "applied", 0.55
    if ACTION.search(sentence):
        return "demonstrated", 0.9 if OUTCOME.search(sentence) else 0.8
    return "applied", 0.6


def extract_claims(units: list[UnitDraft], taxonomy: Taxonomy) -> list[ClaimDraft]:
    best: dict[tuple[str, str], ClaimDraft] = {}

    def keep(c: ClaimDraft) -> None:
        k = (c.unit_key, c.skill_id)
        old = best.get(k)
        if old is None or LEVEL_RANK[c.level] < LEVEL_RANK[old.level] or (
                c.level == old.level and c.via == "direct" and old.via != "direct"):
            best[k] = c

    for u in units:
        if u.kind in ("preferences", "other") and u.title != "Full CV text":
            continue
        body_offset = u.header_len if u.kind in ("experience", "project") else 0
        body = u.text[body_offset:]
        for s_off, sentence in _sentences(body, body_offset):
            matches = taxonomy.find(sentence)
            if not matches:
                continue
            level, conf = _level_for_sentence(sentence, u.kind)
            quote = re.sub(r"^[-*•]\s+", "", sentence.strip())
            for m in matches:
                skill = taxonomy.skills[m.skill_id]
                lvl = level
                if skill.category == "education":
                    lvl = "applied" if u.kind == "education" else "listed_only"
                keep(ClaimDraft(u.key, m.skill_id, lvl, conf, quote, "direct"))
    direct = list(best.values())
    for c in direct:
        for sid, lvl, via in taxonomy.propagate(c.skill_id, c.level):
            keep(ClaimDraft(c.unit_key, sid, lvl, round(c.confidence * (0.9 if lvl == c.level else 0.7), 2),
                            c.quote, via))
    return list(best.values())


def _merge_years(intervals: list[tuple[float, float]]) -> float:
    total, last_end = 0.0, None
    for s, e in sorted(intervals):
        if e <= s:
            continue
        if last_end is not None and s < last_end:
            s = last_end
        if e > s:
            total += e - s
            last_end = e if last_end is None else max(last_end, e)
    return round(total, 1)


def _header_logistics_city(text: str) -> re.Match | None:
    """City stated on a header logistics line, e.g. 'Bengaluru | Open to hybrid | Notice period: 30 days'."""
    for line in text.splitlines()[:8]:
        if (NOTICE.search(line) or WORK_MODE.search(line)) and (m := _CITY_RE.search(line)):
            return m
    return None


def extract(text: str, taxonomy: Taxonomy | None = None) -> ExtractionResult:
    tax = taxonomy or get_taxonomy()
    units, headline, flags = segment(text)
    claims = extract_claims(units, tax)

    exp_units = [u for u in units if u.kind == "experience" and u.start_year]
    total_years = _merge_years([(u.start_year, u.end_year or _now_year()) for u in exp_units])  # type: ignore[misc]
    if not headline and exp_units:
        headline = exp_units[0].title

    # logistics: explicit statements only
    location = work_mode = None
    notice: int | None = None
    pref_text = "\n".join(u.text for u in units if u.kind == "preferences") or text[:600]
    if m := PREF_LOC.search(pref_text):
        location = m.group(2).strip().split("|")[0].strip().rstrip(",")
    elif m := (_CITY_RE.search(headline) or _header_logistics_city(text)):
        location = m.group(1)
    if m := WORK_MODE.search(pref_text):
        wm = m.group(1).lower()
        work_mode = "remote" if wm in ("remote", "wfh") else "hybrid" if wm == "hybrid" else "onsite"
    if m := NOTICE.search(pref_text):
        if m.group(1).lower().startswith("immediate"):
            notice = 0
        else:
            n, unit = int(m.group(2)), m.group(3).lower()
            notice = n * (30 if unit.startswith("month") else 7 if unit.startswith("week") else 1)

    # unsupported-claim flags (shown as "needs verification", never auto-penalised)
    demonstrated = {c.skill_id for c in claims if c.level == "demonstrated"}
    listed = {c.skill_id for c in claims if c.level == "listed_only"} - demonstrated
    summary = " ".join(u.text for u in units if u.kind == "summary")
    if SELF_PRAISE.search(summary):
        praised = [s for s in tax.skill_ids_in(summary) if s not in demonstrated]
        if len(praised) >= 2:
            flags.append("unsupported_expertise_claims:" + ",".join(praised[:6]))
    if len(listed) >= 12 and len(demonstrated) <= 3:
        flags.append("skills_list_without_evidence")
    if exp_units:
        latest = max(exp_units, key=lambda u: u.start_year or 0)
        if SENIOR_TITLE.search(latest.title) and total_years < 3:
            flags.append("senior_title_low_tenure")
        spans = sorted((u.start_year or 0, u.end_year or 0) for u in exp_units)
        for (s1, e1), (s2, _e2) in zip(spans, spans[1:]):
            if e1 - s2 > 0.5:
                flags.append("overlapping_roles")
                break
    return ExtractionResult(units=units, claims=claims, headline=headline[:250], location=location,
                            work_mode=work_mode, notice_period_days=notice, total_years=total_years,
                            flags=flags, mode="heuristic")
