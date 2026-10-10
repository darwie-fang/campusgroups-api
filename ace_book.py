"""
Reads the CMC "ACE Book" PDF (advisors, coaches, fellows, executives in residence)
so Claude can match you with the right person ("a fellow who did PE recruiting and
interned at Goldman").

The book is downloaded from CMC's own public link (ACE_URL) during setup and
refreshed automatically when it's over a month old. It is never stored in this
project or on GitHub; the extracted profiles are kept only on each laptop, in
~/.gsb-api/ace_book.json. You can also import a local copy:

    python3 gsb.py import-ace-book [path or URL]
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ACE_FILE = Path.home() / ".gsb-api" / "ace_book.json"
# CMC's published copy. Update this when CMC posts a new year's book.
ACE_URL = ("https://business.columbia.edu/sites/default/files-efs/imce-uploads/CMC/"
           "MBA%20ACE%20Book%202026-2027_0.pdf")
MAX_AGE_DAYS = 30

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


def import_pdf(source: str | None = None) -> int:
    """Read the ACE Book from a local path or URL (default: CMC's link) and save the
    profiles locally. Returns how many people were found."""
    import io
    import time
    from pypdf import PdfReader          # only needed for importing
    src = source or ACE_URL
    if re.match(r"^https?://", src):
        import requests
        r = requests.get(src, timeout=60, headers={"User-Agent": "Mozilla/5.0 (gsb-api)"})
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            raise RuntimeError(f"Couldn't download the ACE Book from CMC ({r.status_code}). "
                               "The link may have changed for the new year.")
        reader, name = PdfReader(io.BytesIO(r.content)), src.rsplit("/", 1)[-1]
    else:
        pdf = Path(src).expanduser()
        if not pdf.exists():
            raise FileNotFoundError(f"Can't find {pdf}.")
        reader, name = PdfReader(str(pdf)), pdf.name
    people = parse_pages([(pg.extract_text() or "") for pg in reader.pages])
    if not people:
        raise ValueError("That PDF doesn't look like the CMC ACE Book (no profiles found).")
    ACE_FILE.parent.mkdir(mode=0o700, exist_ok=True)
    ACE_FILE.write_text(json.dumps({"source": name, "fetched": time.time(), "people": people},
                                   ensure_ascii=False), encoding="utf-8")
    try:
        ACE_FILE.chmod(0o600)
    except OSError:
        pass
    return len(people)


def load() -> list[dict]:
    """Profiles from the local copy; downloads/refreshes from CMC when missing or old."""
    import time
    data = None
    try:
        data = json.loads(ACE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    stale = not data or time.time() - data.get("fetched", 0) > MAX_AGE_DAYS * 86400
    if stale:
        try:
            import_pdf()
            data = json.loads(ACE_FILE.read_text(encoding="utf-8"))
        except Exception:
            if not data:
                raise FileNotFoundError("Couldn't get the ACE Book from CMC's website right now. "
                                        "Try again later.")
    return data["people"]


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
