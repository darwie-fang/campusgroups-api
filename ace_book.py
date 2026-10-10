"""
Reads the CMC "ACE Book" PDF (advisors, coaches, fellows, executives in residence)
so Claude can match you with the right person ("a fellow who did PE recruiting and
interned at Goldman").

The ACE Book is a CMC document about real people, so it is NEVER stored in this
project or on GitHub. Each student imports their own copy once:

    python3 gsb.py import-ace-book ~/Downloads/MBA_ACE_Book_2026-2027.pdf

and the extracted profiles are kept only on their laptop, in ~/.gsb-api/ace_book.json.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ACE_FILE = Path.home() / ".gsb-api" / "ace_book.json"

# Section headings in the book, in order, and the role each section's people have.
SECTIONS = [("Advisors", "advisor"), ("Coaches", "coach"), ("Fellows", "fellow"),
            ("Executives in Residence", "executive"), ("Key Resources", None),
            ("CMC Club Liaisons Appendix", None), ("Centers & Programs Appendix", None)]

LABELS = ["Functions", "Additional Areas", "Summer Internship", "Pre-MBA Company",
          "Industries", "Previous Companies", "Education", "Conducts Mock Interviews",
          "Preparation", "See Me For", "Club Liaison", "Additional Information",
          "Fun Facts", "Contact"]


def _squash(s: str) -> str:
    """'Product Mg mt.' / 'Start -ups' style breaks come from the PDF; keep words,
    normalise spaces."""
    s = re.sub(r"\s+", " ", s or "").strip()
    s = re.sub(r"\s+([,.;:)])", r"\1", s)
    s = re.sub(r"(\w) -(\w)", r"\1-\2", s)
    return s


def _label_regex(label: str) -> str:
    # Allow the stray spaces the PDF puts inside words ("Additi onal Areas").
    return r"\s*".join(re.escape(ch) for ch in label.replace(" ", "")) + r"\s*:"


LABEL_RE = re.compile("(" + "|".join(_label_regex(l) for l in LABELS) + ")")
NAME_RE = re.compile(r"^[A-Z][A-Za-z.'\-]+(?:\s+(?:[A-Z][A-Za-z.'\-]+|de|van|von|da|del|la))+(?:\s+[‘'’]\d{2})?$")


def _canon_label(raw: str) -> str:
    key = re.sub(r"[\s:]", "", raw).lower()
    for l in LABELS:
        if l.replace(" ", "").lower() == key:
            return l
    return raw.strip(" :")


def parse_pages(pages: list[str]) -> list[dict]:
    """pages: text of each PDF page. Returns [{name, role, fields{label: text}, bio}]."""
    people, role = [], None
    for text in pages:
        # Which section are we in? (Section title pages start with the heading.)
        head = " ".join(text.split()[:6])
        for title, r in SECTIONS:
            if re.match(r"^\d*\s*" + re.escape(title) + r"\b", head):
                role = r
        if not role:
            continue
        raw = [l for l in text.splitlines() if l.strip() and not re.fullmatch(r"\s*\d{1,3}\s*", l)]
        current = None
        label = None
        for i, line_raw in enumerate(raw):
            line = line_raw.strip()
            # A profile starts with an indented name line, followed within a few lines by
            # a labelled field (Functions:, Previous Companies:, Education:, ...).
            soon = raw[i + 1:i + 14]
            if (line_raw[:1] == " " and len(line) < 60 and NAME_RE.match(_squash(line))
                    and not LABEL_RE.match(line)
                    and any(LABEL_RE.match(l.strip()) for l in soon)):
                current = {"name": re.sub(r"\s+[‘'’]\d{2}$", "", _squash(line)),
                           "class_of": (re.search(r"[‘'’](\d{2})$", _squash(line)) or [None, None])[1],
                           "role": role, "fields": {}, "bio": ""}
                people.append(current)
                label = None
                continue
            if current is None:
                continue
            m = LABEL_RE.match(line)
            if m:
                label = _canon_label(m.group(1))
                current["fields"][label] = _squash(line[m.end():])
                continue
            if label:
                current["fields"][label] = _squash(current["fields"][label] + " " + line)
            elif len(current["bio"]) < 1200:
                current["bio"] = _squash(current["bio"] + " " + line)
    # Drop table rows that happened to look like names (no fields and no bio).
    out, seen = [], set()
    for p in people:
        if not p["fields"]:
            continue
        key = (p["name"].lower(), p["role"])
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def import_pdf(path: str) -> int:
    from pypdf import PdfReader          # only needed for importing
    pdf = Path(path).expanduser()
    if not pdf.exists():
        raise FileNotFoundError(f"Can't find {pdf}.")
    pages = [(pg.extract_text() or "") for pg in PdfReader(str(pdf)).pages]
    people = parse_pages(pages)
    if not people:
        raise ValueError("That PDF doesn't look like the CMC ACE Book (no profiles found).")
    ACE_FILE.parent.mkdir(mode=0o700, exist_ok=True)
    ACE_FILE.write_text(json.dumps({"source": pdf.name, "people": people}, ensure_ascii=False), encoding="utf-8")
    try:
        ACE_FILE.chmod(0o600)
    except OSError:
        pass
    return len(people)


def load() -> list[dict]:
    try:
        return json.loads(ACE_FILE.read_text(encoding="utf-8"))["people"]
    except (OSError, ValueError, KeyError):
        raise FileNotFoundError(
            "The ACE Book hasn't been imported yet. In Terminal, run (with your copy of the PDF):\n"
            "  cd ~/Projects/gsb-api && .venv/bin/python gsb.py import-ace-book ~/Downloads/<ACE Book file>.pdf")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def search(people: list[dict], role: str | None = None, industry: str | None = None,
           function: str | None = None, company: str | None = None,
           keyword: str | None = None, name: str | None = None) -> list[dict]:
    """All filters are optional and combined (AND). Matching ignores spacing/case,
    since the PDF text has stray spaces ('Mg mt')."""
    def has(p, fields, q):
        qn = _norm(q)
        blob = "".join(_norm(p["fields"].get(f, "")) for f in fields) if fields else \
               _norm(" ".join(p["fields"].values()) + " " + p["bio"] + " " + p["name"])
        return all(_norm(w) in blob for w in re.split(r"[,/]| and ", q) if _norm(w)) if qn else True

    aliases = {"adviser": "advisor", "eir": "executive", "executive in residence": "executive",
               "executives in residence": "executive", "exec": "executive"}
    r = (role or "").strip().lower()
    r = aliases.get(r, aliases.get(r.rstrip("s"), r.rstrip("s")))
    out = []
    for p in people:
        if r and r not in (p["role"] or ""):
            continue
        if name and _norm(name) not in _norm(p["name"]):
            continue
        if industry and not has(p, ["Industries", "Additional Areas", "Functions", "See Me For"], industry):
            continue
        if function and not has(p, ["Functions", "Additional Areas", "See Me For"], function):
            continue
        if company and not has(p, ["Summer Internship", "Pre-MBA Company", "Previous Companies"], company):
            continue
        if keyword and not has(p, [], keyword):
            continue
        out.append(p)
    return out
