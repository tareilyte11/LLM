"""
“Know your rights” assistant for your Europe rights.
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


def extract_blocks(html):
    """Walk the page from the <h1> to the 'Last checked' line; return (heading, text) blocks."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "form", "button", "select", "svg"]):
        tag.decompose()
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

    for el in h1.find_all_next(["h2", "h3", "h4", "p", "li"]):
        text = own_text(el) if el.name == "li" else clean(el.get_text(" "))
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
            if el.find("p"):          # its <p> children are picked up on their own
                continue
            list_items.append(text)
            continue
        if el.find_parent("li"):      # a <p> inside a list item belongs to that list
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
    return parts


def to_chunks(page_title, blocks):
    # merge short blocks into the next block of the same section
    merged = []
    for heading, text in blocks:
        if merged and merged[-1][0] == heading and len(merged[-1][1].split()) < MIN_WORDS:
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