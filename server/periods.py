"""Date-range handling shared by the UI filters and the NL parser.

A range is stored in UI/filter state as a string: either a preset id ("last_12_months") or "YYYY-MM-DD..YYYY-MM-DD".
'last N months' = N calendar-month buckets ending with the current month *to date* (so records created today are
always visible); the analytics layer flags that partial bucket and keeps it out of trend statistics. 'last month /
quarter / year' are complete periods; 'this month / quarter' and ytd are period-to-date.
"""
import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
          "december"]
MON_ABBR = {m[:3]: i + 1 for i, m in enumerate(MONTHS)} | {"sept": 9}
MON_NAME = {i + 1: m for i, m in enumerate(MONTHS)}

PRESETS = [
    ("last_7_days", "Last 7 days"), ("last_30_days", "Last 30 days"), ("last_90_days", "Last 90 days"),
    ("this_month", "This month"), ("last_month", "Last month"), ("this_quarter", "This quarter"),
    ("last_quarter", "Last quarter"), ("last_6_months", "Last 6 months"), ("last_12_months", "Last 12 months"),
    ("last_24_months", "Last 24 months"), ("ytd", "Year to date"), ("last_year", "Last calendar year"),
]


@dataclass
class Period:
    start: date
    end: date
    label: str
    kind: str = "custom"          # month | quarter | year | rolling | custom
    span: tuple = (0, 0)          # character span in the source text (parser only)

    @property
    def days(self):
        return (self.end - self.start).days + 1

    def value(self) -> str:
        """Filter-state string for this period."""
        return f"{self.start.isoformat()}..{self.end.isoformat()}"

    def to_dict(self):
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "label": self.label, "kind": self.kind,
                "value": self.value()}


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, m + 1, 1)


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def month_period(y: int, m: int, as_of: date) -> Period:
    s = date(y, m, 1)
    e = min(_month_end(s), as_of)
    return Period(s, e, f"{calendar.month_abbr[m]} {y}", "month")


def quarter_period(y: int, q: int, as_of: date) -> Period:
    s = date(y, 3 * q - 2, 1)
    e = min(_month_end(date(y, 3 * q, 1)), as_of)
    return Period(s, e, f"Q{q} {y}", "quarter")


def year_period(y: int, as_of: date) -> Period:
    return Period(date(y, 1, 1), min(date(y, 12, 31), as_of), str(y), "year")


def _fmt_range(s: date, e: date) -> str:
    if s.year == e.year and s.month == e.month and s.day == 1 and e == _month_end(s):
        return f"{calendar.month_abbr[s.month]} {s.year}"
    if s.year == e.year:
        return f"{s.day} {calendar.month_abbr[s.month]} – {e.day} {calendar.month_abbr[e.month]} {e.year}"
    return f"{s.day} {calendar.month_abbr[s.month]} {s.year} – {e.day} {calendar.month_abbr[e.month]} {e.year}"


def full_months(n: int, as_of: date) -> Period:
    """The last N calendar-month buckets *including the current month to date* (so today's records are always in range)."""
    s = _add_months(as_of.replace(day=1), -(n - 1))
    return Period(s, as_of, f"Last {n} months ({calendar.month_abbr[s.month]} {s.year} – {calendar.month_abbr[as_of.month]} {as_of.year}, to date)",
                  "rolling")


def rolling_days(n: int, as_of: date) -> Period:
    return Period(as_of - timedelta(days=n - 1), as_of, f"Last {n} days", "rolling")


def preset(name: str, as_of: date) -> Period | None:
    q_start = date(as_of.year, 3 * ((as_of.month - 1) // 3) + 1, 1)
    if name == "last_7_days":
        return rolling_days(7, as_of)
    if name == "last_30_days":
        return rolling_days(30, as_of)
    if name == "last_90_days":
        return rolling_days(90, as_of)
    if name == "this_month":
        return Period(as_of.replace(day=1), as_of, f"This month ({calendar.month_abbr[as_of.month]} {as_of.year}, to date)", "month")
    if name == "last_month":
        s = _add_months(as_of.replace(day=1), -1)
        p = month_period(s.year, s.month, as_of)
        p.label = f"Last month ({p.label})"
        return p
    if name == "this_quarter":
        return Period(q_start, as_of, f"This quarter (Q{(as_of.month - 1) // 3 + 1} {as_of.year}, to date)", "quarter")
    if name == "last_quarter":
        s = _add_months(q_start, -3)
        p = quarter_period(s.year, (s.month - 1) // 3 + 1, as_of)
        p.label = f"Last quarter ({p.label})"
        return p
    if name == "last_6_months":
        return full_months(6, as_of)
    if name == "last_12_months":
        return full_months(12, as_of)
    if name == "last_24_months":
        return full_months(24, as_of)
    if name == "ytd":
        return Period(date(as_of.year, 1, 1), as_of, f"Year to date ({as_of.year})", "year")
    if name == "last_year":
        p = year_period(as_of.year - 1, as_of)
        p.label = f"Last year ({p.label})"
        return p
    return None


def resolve(value: str | None, as_of: date, default="last_12_months") -> Period:
    value = value or default
    p = preset(value, as_of)
    if p:
        return p
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})", value)
    if m:
        s, e = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
        if s > e:
            s, e = e, s
        kind = "month" if (s.day == 1 and e == _month_end(s) and s.month == e.month and s.year == e.year) else "custom"
        return Period(s, e, _fmt_range(s, e), kind)
    return preset(default, as_of)


def preset_id_for(p: Period, as_of: date) -> str:
    """Return the preset id when the period equals a preset, else its explicit range string."""
    if p.end == as_of and p.kind == "quarter" and p.start == preset("this_quarter", as_of).start:
        return "this_quarter"
    if p.end == as_of and p.kind == "year" and p.start == date(as_of.year, 1, 1):
        return "ytd"
    for pid, _ in PRESETS:
        q = preset(pid, as_of)
        if q and q.start == p.start and q.end == p.end:
            return pid
    return p.value()


def previous(p: Period, as_of: date) -> Period:
    """The comparison period: previous month/quarter/year (same elapsed days when the current one is partial)."""
    if p.kind == "month" and p.start.day == 1:
        ps = _add_months(p.start, -1)
        pe = min(_month_end(ps), ps + timedelta(days=p.days - 1))
        q = month_period(ps.year, ps.month, as_of)
        return Period(ps, pe, q.label, "month")
    if p.kind == "quarter" and p.start.day == 1:
        ps = _add_months(p.start, -3)
        full_end = _month_end(_add_months(ps, 2))
        pe = min(full_end, ps + timedelta(days=p.days - 1))
        return Period(ps, pe, f"Q{(ps.month - 1) // 3 + 1} {ps.year}", "quarter")
    if p.kind == "year" and p.start.month == 1 and p.start.day == 1:
        ps = date(p.start.year - 1, 1, 1)
        pe = min(date(ps.year, 12, 31), ps + timedelta(days=p.days - 1))
        return Period(ps, pe, str(ps.year), "year")
    if p.kind == "rolling" and p.label.startswith("Last") and "months" in p.label:
        n = round((p.days + 15) / 30.4)
        ps = _add_months(p.start, -n)
        pe = min(ps + timedelta(days=p.days - 1), p.start - timedelta(days=1))
        return Period(ps, pe, f"Previous {n} months ({calendar.month_abbr[ps.month]} {ps.year} – {calendar.month_abbr[pe.month]} {pe.year})", "rolling")
    pe = p.start - timedelta(days=1)
    ps = pe - timedelta(days=p.days - 1)
    return Period(ps, pe, _fmt_range(ps, pe), "custom")


def same_period_last_year(p: Period, as_of: date) -> Period:
    s = date(p.start.year - 1, p.start.month, min(p.start.day, 28 if p.start.month == 2 else p.start.day))
    e_y = p.end.year - 1
    e = date(e_y, p.end.month, min(p.end.day, calendar.monthrange(e_y, p.end.month)[1]))
    return Period(s, e, _fmt_range(s, e), p.kind)


def month_keys(start: date, end: date):
    out, cur = [], start.replace(day=1)
    while cur <= end:
        out.append(cur.strftime("%Y-%m"))
        cur = _add_months(cur, 1)
    return out


# ---------------------------------------------------------------------------------------------------------------
# natural-language parsing
# ---------------------------------------------------------------------------------------------------------------
_MON_RE = r"(january|february|march|april|may|june|july|august|september|sept|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
_NUMWORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
             "ten": 10, "twelve": 12, "fifteen": 15, "eighteen": 18, "twenty": 20, "twenty-four": 24, "thirty": 30,
             "sixty": 60, "ninety": 90}
_NUM = r"(\d+|" + "|".join(sorted(_NUMWORDS, key=len, reverse=True)) + ")"


def _num(tok: str) -> int:
    return int(tok) if tok.isdigit() else _NUMWORDS[tok]


def _recent_month(mon: int, as_of: date, year: int | None) -> tuple[int, int]:
    if year is None:
        year = as_of.year if mon <= as_of.month else as_of.year - 1
    return year, mon


def parse_periods(text: str, as_of: date) -> list[Period]:
    """Return the time references found in `text`, in order of appearance."""
    t = text.lower()
    found: list[Period] = []
    taken = [False] * len(t)

    def claim(m, p: Period):
        if any(taken[m.start():m.end()]):
            return False
        for i in range(m.start(), m.end()):
            taken[i] = True
        p.span = (m.start(), m.end())
        found.append(p)
        return True

    # explicit ISO ranges / dates
    for m in re.finditer(r"(\d{4}-\d{2}-\d{2})\s*(?:to|-|\.\.|through|until)\s*(\d{4}-\d{2}-\d{2})", t):
        claim(m, resolve(f"{m.group(1)}..{m.group(2)}", as_of))
    # from <mon> [yyyy] to <mon> [yyyy]
    for m in re.finditer(r"(?:from|between)\s+" + _MON_RE + r"(?:\s+(\d{4}))?\s+(?:to|and|through|-)\s+" + _MON_RE + r"(?:\s+(\d{4}))?", t):
        m1, y1, m2, y2 = MON_ABBR[m.group(1)[:3]], m.group(2), MON_ABBR[m.group(3)[:3]], m.group(4)
        ye = int(y2) if y2 else (int(y1) if y1 else _recent_month(m2, as_of, None)[0])
        ys = int(y1) if y1 else (ye if m1 <= m2 else ye - 1)
        s, e = date(ys, m1, 1), _month_end(date(ye, m2, 1))
        claim(m, Period(s, min(e, as_of), _fmt_range(s, min(e, as_of)), "custom"))
    # rolling windows: last/past/previous N unit
    for m in re.finditer(r"\b(?:last|past|previous|trailing)\s+" + _NUM + r"\s*(day|week|month|quarter|year)s?\b", t):
        n, unit = _num(m.group(1)), m.group(2)
        if unit == "day":
            p = rolling_days(n, as_of)
        elif unit == "week":
            p = rolling_days(7 * n, as_of)
        elif unit == "month":
            p = full_months(n, as_of)
        elif unit == "quarter":
            p = full_months(3 * n, as_of)
            p.label = f"Last {n} quarter{'s' if n > 1 else ''}"
        else:
            p = full_months(12 * n, as_of)
        claim(m, p)
    # named presets
    named = [
        (r"\b(?:year[- ]to[- ]date|ytd)\b", "ytd"), (r"\b(?:month[- ]to[- ]date|mtd)\b", "this_month"),
        (r"\b(?:this|current)\s+month\b", "this_month"), (r"\b(?:last|previous|prior)\s+month\b", "last_month"),
        (r"\b(?:this|current)\s+quarter\b", "this_quarter"), (r"\b(?:last|previous|prior)\s+quarter\b", "last_quarter"),
        (r"\b(?:this|current)\s+year\b", "ytd"), (r"\b(?:last|previous|prior)\s+year\b", "last_year"),
        (r"\b(?:last|past|previous)\s+week\b", "last_7_days"), (r"\bthis\s+week\b", "last_7_days"),
        (r"\blast\s+30\s+days\b", "last_30_days"),
    ]
    for pat, name in named:
        for m in re.finditer(pat, t):
            claim(m, preset(name, as_of))
    # quarters: q3 2026 / q2
    for m in re.finditer(r"\bq([1-4])(?:\s*(?:of\s+)?'?(\d{4}))?\b", t):
        q = int(m.group(1))
        if m.group(2):
            y = int(m.group(2))
        else:
            y = as_of.year
            if date(y, 3 * q - 2, 1) > as_of:
                y -= 1
        claim(m, quarter_period(y, q, as_of))
    # month [year]
    for m in re.finditer(r"\b" + _MON_RE + r"\b(?:[ ,]+'?(\d{4}))?", t):
        name = m.group(1)
        yr = int(m.group(2)) if m.group(2) else None
        if name in ("may", "mar") and yr is None:
            before = t[:m.start()].rstrip()
            if not re.search(r"\b(in|during|for|of|vs|versus|and|than|to|from|since|between|compare|against|on|with)$", before):
                continue
        y, mo = _recent_month(MON_ABBR[name[:3]], as_of, yr)
        claim(m, month_period(y, mo, as_of))
    # bare calendar year
    for m in re.finditer(r"\b(20[12]\d)\b", t):
        claim(m, year_period(int(m.group(1)), as_of))

    found.sort(key=lambda p: p.span[0])
    # a month without a year that follows an explicit-year period inherits that year ("March 2025 vs April")
    for i in range(1, len(found)):
        a, b = found[i - 1], found[i]
        if a.kind == "month" and b.kind == "month" and b.span[0] - a.span[1] < 20:
            frag = t[b.span[0]:b.span[1]]
            if not re.search(r"\d{4}", frag) and re.search(r"\d{4}", t[a.span[0]:a.span[1]]):
                y = a.start.year
                found[i] = month_period(y, b.start.month, as_of)
                found[i].span = b.span
    return found
