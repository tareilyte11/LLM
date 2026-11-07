"""
“Know your rights” assistant for your Europe rights.
"""

"""
Build the mid-term corpus from ten Your Europe pages (European Commission).

Outputs
  my_documents/<name>.txt   one document per file, one paragraph per chunk,
                            blank line between paragraphs (what the week 7
                            notebook's read_documents() expects)
  youreurope_handbook.json  the same paragraphs in the Lecture 6 format
                            {"passages": [{"id", "section", "lang", "text"}]}
                            so the BM25 / dense / hybrid script runs unchanged
  SOURCES.md                URL, page date, download date, licence, changes made
                            (kept out of my_documents/ so it is never indexed)
  raw_html/<name>.html      cached downloads; delete one to fetch it again

Run:   python build_documents.py
If a page will not download (e.g. 403), open it in your browser, save it as
"Webpage, HTML only" to raw_html/<name>.html, and run the script again.

Licence: Your Europe content is reusable under CC BY 4.0 (Commission Decision
2011/833/EU) with attribution and an indication of changes; SOURCES.md does both.
"""

import json
import re
import statistics
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

BASE = "https://europa.eu/youreurope/citizens/"
LANG = "en"  # every page also exists as index_lt.htm etc.

# File names: lowercase letters only. The notebook's citation regex is
# [a-z]+\.txt#\d+, so "bus-coach.txt" or "doc1.txt" would make citations vanish.
PAGES = {
    "air":        ("Air passenger rights",           "travel/passenger-rights/air/"),
    "rail":       ("Rail passenger rights",          "travel/passenger-rights/rail/"),
    "bus":        ("Bus and coach passenger rights", "travel/passenger-rights/bus-and-coach/"),
    "package":    ("Package travel",                 "travel/holidays/package-travel/"),
    "guarantees": ("Guarantees",                     "consumers/shopping/guarantees/"),
    "returns":    ("Returns",                        "consumers/shopping/returns/"),
    "delivery":   ("Shipping and delivery",          "consumers/shopping/shipping-delivery/"),
    "roaming":    ("Mobile roaming",                 "consumers/internet-telecoms/mobile-roaming-costs/"),
    "healthcare": ("Health cover for temporary stays", "health/unplanned-healthcare/temporary-stays/"),
    "disputes":   ("Consumer dispute resolution",    "consumers/consumers-dispute-resolution/informal-alternative-dispute-resolution/"),
}

# Its own file name, so the course's handbook.json is never touched.
OUT_JSON = Path("youreurope_handbook.json")

# Chunking choices (report these in your parameter table)
HEADING_PATH = True   # prefix each chunk with "Page title – Section heading."
MIN_WORDS = 15        # shorter blocks are merged into the next block in the same section
MAX_WORDS = 130       # longer blocks are split at sentence boundaries

STOP_MARKERS = ("Last checked", "Share this page", "Feedback on quality")
BOILERPLATE = {
    "claim your rights!", "warning", "on this page", "see also:",
    "choose country", "find out more", "read more", "open close", "close",
}
SKIP_HEADINGS = ("FAQs", "EU legislation", "See also", "Need support from assistance services")

EU_COUNTRIES = {
    "austria", "belgium", "bulgaria", "croatia", "cyprus", "czechia", "denmark",
    "estonia", "finland", "france", "germany", "greece", "hungary", "ireland",
    "italy", "latvia", "lithuania", "luxembourg", "malta", "netherlands", "poland",
    "portugal", "romania", "slovakia", "slovenia", "spain", "sweden", "iceland",
    "liechtenstein", "norway", "switzerland",
}


def fetch(name, path, cache=Path("raw_html")):
    cache.mkdir(exist_ok=True)
    file = cache / f"{name}.html"
    if file.exists():
        return file.read_text(encoding="utf-8")
    url = f"{BASE}{path}index_{LANG}.htm"
    r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0 (course RAG project)"})
    r.raise_for_status()
    file.write_text(r.text, encoding="utf-8")
    return r.text


def clean(text):
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def own_text(li):
    """Text of a list item without its nested lists (those are visited separately)."""
    parts = []
    for child in li.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag) and child.name not in ("ul", "ol"):
            parts.append(child.get_text(" "))
    return clean(" ".join(parts))


def li_text(li):
    """A list item with its sub-bullets attached: 'parent (sub 1; sub 2)'."""
    subs = [li_text(s) for s in li.find_all("li", recursive=False) +
            [s for ul in li.find_all(["ul", "ol"], recursive=False) for s in ul.find_all("li", recursive=False)]]
    subs = [s for s in subs if s]
    return own_text(li) + (f" ({'; '.join(subs)})" if subs else "")


def table_text(table):
    """A table as one sentence per row, each cell labelled with its column header."""
    rows = [[clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])] for tr in table.find_all("tr")]
    rows = [r for r in rows if any(r)]
    if len(rows) < 2:                 # header only (e.g. an interactive price tool): nothing to keep
        return ""
    header, body = rows[0], rows[1:]
    lines = []
    for r in body:
        if len(r) == len(header):
            lines.append(", ".join(f"{h}: {v}" for h, v in zip(header, r)))
        else:
            lines.append(", ".join(r))
    return "; ".join(lines) + "."


def extract_blocks(html):
    """Walk the page from the <h1> to the 'Last checked' line; return (heading, text) blocks."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "form", "button", "select", "svg"]):
        tag.decompose()
    # the "Last checked: dd/mm/yyyy" box is a <div id="last-checked"><span>; turn it into a <p>
    # so the walk below stops there (everything after it is share buttons and feedback forms)
    box = soup.find(id="last-checked")
    if box is not None:
        marker = soup.new_tag("p")
        marker.string = clean(box.get_text(" "))
        box.replace_with(marker)
    h1 = soup.find("h1")
    if h1 is None:
        raise ValueError("no <h1> found: is this the right page?")
    last_checked = None
    heading, blocks, list_items = None, [], []

    def flush_list():
        nonlocal list_items
        if list_items:
            items = [i.rstrip(";.,") for i in list_items]
            if not all(i.lower() in EU_COUNTRIES for i in items):  # drop country pickers
                joined = "; ".join(items) + "."
                if blocks and blocks[-1][0] == heading:          # a list continues the paragraph above it
                    blocks[-1] = (heading, blocks[-1][1].rstrip(":") + ": " + joined)
                else:
                    blocks.append((heading, joined))
            list_items = []

    for el in h1.find_all_next(["h2", "h3", "h4", "p", "li", "table"]):
        if el.find_parent("li"):      # nested bullets and <p> inside bullets: already in the parent's li_text
            continue
        if el.find_parent("table"):   # cells are handled by table_text
            continue
        if el.name == "table":
            flush_list()
            text = table_text(el)
            if text:
                blocks.append((heading, text))
            continue
        text = li_text(el) if el.name == "li" else clean(el.get_text(" "))
        if not text:
            continue
        if text.startswith(STOP_MARKERS):
            m = re.search(r"\d{2}/\d{2}/\d{4}", text)
            last_checked = m.group(0) if m else None
            break
        if text.lower() in BOILERPLATE:
            continue
        if el.name in ("h2", "h3", "h4"):
            flush_list()
            heading = text.rstrip(":")
            continue
        if el.name == "li":
            list_items.append(text)
            continue
        flush_list()
        blocks.append((heading, text))
    flush_list()
    return clean(h1.get_text(" ")), blocks, last_checked


def split_sentences(text, limit):
    sentences = re.split(r"(?<=[.!?;])\s+(?=[A-Z0-9(])", text)
    parts, current = [], []
    for s in sentences:
        if current and len(" ".join(current + [s]).split()) > limit:
            parts.append(" ".join(current))
            current = []
        current.append(s)
    if current:
        parts.append(" ".join(current))
    # never leave a short tail on its own: glue it back onto the part before it
    if len(parts) > 1 and len(parts[-1].split()) < 2 * MIN_WORDS:
        tail = parts.pop()
        parts[-1] += " " + tail
    return parts


def to_chunks(page_title, blocks):
    # drop navigation-only sections (FAQs, EU legislation, See also, ...)
    blocks = [(h, t) for h, t in blocks if not (h and h.startswith(SKIP_HEADINGS))]
    # drop a block whose words already sit inside another block (duplicate fragments);
    # compare words only, so stray brackets or full stops don't hide a duplicate
    words = lambda s: " " + re.sub(r"[^a-z0-9]+", " ", s.lower()).strip() + " "
    norm = [words(t) for _, t in blocks]
    blocks = [b for i, b in enumerate(blocks)
              if not any(j != i and norm[i] != norm[j] and norm[i] in norm[j]
                         for j in range(len(blocks)))]
    # merge short blocks into the next block of the same section
    merged = []
    for heading, text in blocks:
        prev_short_or_unfinished = merged and (
            len(merged[-1][1].split()) < MIN_WORDS
            or not re.search(r"[.!?)\"”]$", merged[-1][1]))     # e.g. "...cannot be higher than"
        if prev_short_or_unfinished and merged[-1][0] == heading:
            merged[-1] = (heading, merged[-1][1] + " " + text)
        else:
            merged.append((heading, text))
    chunks, seen = [], set()
    for heading, text in merged:
        for part in split_sentences(text, MAX_WORDS):
            if part in seen or len(part.split()) < 5:
                continue
            seen.add(part)
            if HEADING_PATH:
                label = f"{page_title} – {heading}" if heading else page_title
                sep = " " if label[-1] in ".?!" else ". "
                part = f"{label}{sep}{part}"
            chunks.append(part)
    return chunks


def main():
    out = Path("my_documents")
    out.mkdir(exist_ok=True)
    passages, sources = [], []
    today = date.today().isoformat()

    for name, (title, path) in PAGES.items():
        try:
            html = fetch(name, path)
        except Exception as e:
            print(f"!! {name}: download failed ({e}); save the page to raw_html/{name}.html and rerun")
            continue
        page_title, blocks, last_checked = extract_blocks(html)
        chunks = to_chunks(title, blocks)
        (out / f"{name}.txt").write_text("\n\n".join(chunks) + "\n", encoding="utf-8")

        for i, text in enumerate(chunks, start=1):
            passages.append({"id": len(passages) + 1, "section": name, "lang": LANG,
                             "chunk_id": f"{name}.txt#{i}", "text": text})
        sources.append(f"| {name}.txt | {page_title} | {BASE}{path}index_{LANG}.htm | "
                       f"{last_checked or 'not found'} | {today} |")

        words = [len(c.split()) for c in chunks]
        print(f"{name:11} {len(chunks):3} chunks   words min/median/max "
              f"{min(words)}/{int(statistics.median(words))}/{max(words)}   page checked {last_checked}")

    OUT_JSON.write_text(json.dumps(
        {"description": "Your Europe pages (European Commission), CC BY 4.0, split into paragraphs; see SOURCES.md.",
         "passages": passages}, ensure_ascii=False, indent=2), encoding="utf-8")

    Path("SOURCES.md").write_text(
        "# Sources\n\n"
        "| file | page title | URL | page 'last checked' | downloaded |\n|---|---|---|---|---|\n"
        + "\n".join(sources) + "\n\n"
        "© European Union, reused under CC BY 4.0 (Commission Decision 2011/833/EU).\n"
        "Changes: main text extracted from HTML; navigation, buttons and feedback forms removed; "
        f"split into paragraphs of at most {MAX_WORDS} words; "
        + ("page title and section heading prepended to each paragraph.\n" if HEADING_PATH else "\n"),
        encoding="utf-8")

    print(f"\n{len(passages)} chunks in {len(sources)} files -> my_documents/, {OUT_JSON}, SOURCES.md")
    print("Now read every file: no menu text, no answer split across two chunks, every chunk makes sense alone.")


if __name__ == "__main__":
    main()