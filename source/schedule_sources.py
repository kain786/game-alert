import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from bs4 import BeautifulSoup


DEFAULT_TIMEOUT_SECONDS = 12
DEFAULT_NAME_LOOKUP_TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class UpdateEntry:
    version: str
    update_date: Optional[date]
    pickup_characters: List[str]
    end_date: Optional[date] = None
    source_id: str = ""

@dataclass(frozen=True)
class BroadcastEntry:
    version: str
    broadcast_date: Optional[date]
    broadcast_time: str
    broadcast_tz: str
    title: str


def _dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _parse_date_maybe(text: str) -> Optional[date]:
    text = (text or "").strip()
    if not text or text.upper() == "TBA":
        return None
    # Normalize whitespace and strip any trailing annotation.
    # Examples:
    # - March 3, 2026
    # - January 14, 2026 11:00 GMT+8
    # - 28 January, 2026 12:00 (Server Time)
    text = text.split("(")[0].strip()
    # Some pages include timezone after the time.
    text = re.sub(r"\bGMT[+-]\d+\b", "", text).strip()
    text = re.sub(r"\s+(?:UTC|GMT)[+-]\d+(?::\d+)?", "", text).strip()
    # Try a few common fandom formats.
    candidates = [
        "%B %d, %Y",
        "%B %d, %Y %H:%M",
        "%d %B, %Y",
        "%d %B, %Y %H:%M",
    ]
    for fmt in candidates:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _fetch_fandom_parse_html(
    domain: str,
    page: str,
    *,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    sess = session or requests.Session()
    sess.headers.setdefault(
        "User-Agent",
        "GameAlertDistribution/1.0",
    )
    resp = sess.get(
        f"https://{domain}/api.php",
        params={
            "action": "parse",
            "page": page,
            "prop": "text",
            "format": "json",
            "formatversion": "2",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload["parse"]["text"]


def _strip_soft_hyphen(text: str) -> str:
    # MediaWiki output can contain soft hyphens (\xad) for line wrapping.
    return (text or "").replace("\xad", "")


def _sanitize_korean_official_name(text: str) -> str:
    text = _strip_soft_hyphen(text).strip()
    if not text:
        return ""
    # Many fandom pages append romanization, e.g. "바르카 Bareuka", "엘렌 조 Ellen Jo".
    text = re.split(r"[A-Za-z]", text, 1)[0].strip()
    # Drop parentheticals (often contains hanzi/kanji or alt names).
    text = re.sub(r"\([^)]*\)", " ", text)
    # Keep mostly Hangul + a small set of separators used in names.
    text = re.sub(r"[^\uac00-\ud7a3\s·•'’\-–—]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_official_name_ko(
    domain: str,
    page_title: str,
    *,
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    """
    Resolve a page title to its official Korean name using the standard
    'Language | Official Name' table present on many Fandom wiki pages.
    Returns None when not found.
    """
    html = _fetch_fandom_parse_html(
        domain,
        page_title,
        session=session,
        timeout=DEFAULT_NAME_LOOKUP_TIMEOUT_SECONDS,
    )
    soup = BeautifulSoup(html, "html.parser")

    for table in soup.find_all("table"):
        ths = [th.get_text(" ", strip=True) for th in table.find_all("th")][:2]
        if ths != ["Language", "Official Name"]:
            continue

        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            lang = _strip_soft_hyphen(cells[0].get_text(" ", strip=True)).strip().lower()
            if lang not in {"korean", "한국어"}:
                continue
            name = _sanitize_korean_official_name(cells[1].get_text(" ", strip=True))
            return name or None

    return None


def _find_table_by_headers(
    soup: BeautifulSoup,
    expected_headers: Sequence[str],
) -> Optional["bs4.element.Tag"]:
    expected = list(expected_headers)
    for table in soup.find_all("table"):
        ths = [th.get_text(" ", strip=True) for th in table.find_all("th")]
        if ths[: len(expected)] == expected:
            return table
    return None


def fetch_zzz_updates(session: Optional[requests.Session] = None) -> List[UpdateEntry]:
    html = _fetch_fandom_parse_html(
        "zenless-zone-zero.fandom.com",
        "Exclusive_Channel/History",
        session=session,
    )
    soup = BeautifulSoup(html, "html.parser")
    table = _find_table_by_headers(
        soup, ["Signal Search", "Featured", "Date Start", "Date End", "Version"]
    )
    if table is None:
        raise RuntimeError("ZZZ: expected history table not found")

    by_phase: Dict[Tuple[str, Optional[date]], Dict[str, object]] = {}
    for row in table.find_all("tr")[1:]:
        tds = row.find_all("td")
        if len(tds) < 5:
            continue

        start = _parse_date_maybe(tds[2].get_text(" ", strip=True))
        version = tds[4].get_text(" ", strip=True)
        if not version:
            continue

        featured_titles = [
            a.get("title", "")
            for a in tds[1].find_all("a", title=True)
            if not a.get("title", "").startswith("File:")
        ]
        featured_titles = _dedupe(featured_titles)
        limited_agent = featured_titles[0] if featured_titles else ""

        entry = by_phase.setdefault(
            (version, start),
            {"update_date": start, "characters": [], "end_date": _parse_date_maybe(tds[3].get_text(" ", strip=True)), "source_ids": []},
        )
        occurrence = tds[0].find("a", title=True)
        if occurrence:
            entry["source_ids"].append(occurrence.get("title", ""))
        if limited_agent:
            entry["characters"] = _dedupe(
                list(entry.get("characters") or []) + [limited_agent]
            )

    updates: List[UpdateEntry] = []
    for (version, _start), info in by_phase.items():
        updates.append(
            UpdateEntry(
                version=version,
                update_date=info.get("update_date") if isinstance(info.get("update_date"), date) else None,
                pickup_characters=list(info.get("characters") or []),
                end_date=info.get("end_date"),
                source_id="|".join(sorted(set(info.get("source_ids") or []))),
            )
        )

    updates.sort(key=lambda x: x.update_date or date.min, reverse=True)
    return updates


def _parse_genshin_character_event_wish_map(
    session: Optional[requests.Session] = None,
) -> Dict[str, str]:
    html = _fetch_fandom_parse_html(
        "genshin-impact.fandom.com",
        "Character_Event_Wish",
        session=session,
    )
    soup = BeautifulSoup(html, "html.parser")
    table = _find_table_by_headers(soup, ["Wish", "Character", "Runs"])
    if table is None:
        raise RuntimeError("Genshin: expected Character Event Wish mapping table not found")

    mapping: Dict[str, str] = {}
    for row in table.find_all("tr")[1:]:
        tds = row.find_all("td")
        if len(tds) < 2:
            continue

        wish_link = tds[0].find("a", title=True)
        wish = (wish_link.get("title") if wish_link else "") or tds[0].get_text(" ", strip=True)
        char_link = tds[1].find("a", title=True)
        char = (char_link.get("title") if char_link else "") or tds[1].get_text(" ", strip=True)

        wish = (wish or "").strip()
        char = (char or "").strip()
        if not wish or not char:
            continue
        mapping[wish] = char
    return mapping


def _parse_version_row_label(label: str) -> Tuple[str, Optional[date], Optional[date]]:
    # Examples:
    # - Version Luna IV : January 14, 2026 — February 03, 2026
    # - Version 5.8 : July 30, 2025 — August 19, 2025
    # - Version Luna V : February 25, 2026 — TBA
    label = (label or "").strip()
    if " : " in label:
        version_part, rest = label.split(" : ", 1)
    elif ":" in label:
        version_part, rest = label.split(":", 1)
    else:
        return label, None, None

    # Range separator is an em dash on fandom.
    if "—" in rest:
        start_s, end_s = rest.split("—", 1)
    elif "-" in rest:
        start_s, end_s = rest.split("-", 1)
    else:
        start_s, end_s = rest, ""

    version = version_part.strip()
    start = _parse_date_maybe(start_s.strip())
    end = _parse_date_maybe(end_s.strip())
    return version, start, end


def fetch_genshin_updates(session: Optional[requests.Session] = None) -> List[UpdateEntry]:
    wish_to_char = _parse_genshin_character_event_wish_map(session=session)
    html = _fetch_fandom_parse_html(
        "genshin-impact.fandom.com",
        "Wish/List",
        session=session,
    )
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        raise RuntimeError("Genshin: expected Wish/List tables not found")

    by_phase: Dict[Tuple[str, Optional[date]], Dict[str, object]] = {}
    current_version: Optional[str] = None
    current_start: Optional[date] = None
    current_end: Optional[date] = None

    for table in tables:
        current_version = None
        current_start = None
        current_end = None
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if not cells:
                continue
            label = cells[0].get_text(" ", strip=True)
            if label.startswith("Version"):
                version, start, end = _parse_version_row_label(label)
                current_version = version
                current_start = start
                current_end = end
                continue

            if label != "Character Event":
                continue
            if not current_version:
                continue
            if len(cells) < 2:
                continue

            occurrence_by_series: Dict[str, str] = {}
            for a in cells[1].find_all("a", title=True):
                title = (a.get("title", "") or "").strip()
                if not title or title.startswith("File:"):
                    continue
                series = title.split("/", 1)[0].strip()
                if not series:
                    continue
                occurrence_by_series.setdefault(series, title)

            characters: List[str] = []
            for series, occurrence_page in occurrence_by_series.items():
                mapped = wish_to_char.get(series)
                if mapped:
                    characters.append(mapped)
                    continue
                # If the mapping table hasn't been updated yet, try to pull the
                # promoted character directly from the occurrence page.
                resolved = _parse_genshin_promoted_characters(
                    occurrence_page,
                    session=session,
                )
                if resolved:
                    # For Character Event Wishes, the first entry is usually the
                    # limited 5-star. Keep the full list anyway (dedupe later).
                    characters.extend(resolved)

            entry = by_phase.setdefault(
                (current_version, current_start),
                {"update_date": current_start, "characters": [], "end_date": current_end, "source_ids": []},
            )
            entry["source_ids"].extend(occurrence_by_series.values())
            entry["characters"] = _dedupe(
                list(entry.get("characters") or []) + characters
            )

    updates: List[UpdateEntry] = []
    for (version, _start), info in by_phase.items():
        updates.append(
            UpdateEntry(
                version=version,
                update_date=info.get("update_date") if isinstance(info.get("update_date"), date) else None,
                pickup_characters=list(info.get("characters") or []),
                end_date=info.get("end_date"),
                source_id="|".join(sorted(set(info.get("source_ids") or []))),
            )
        )

    updates.sort(key=lambda x: x.update_date or date.min, reverse=True)
    return updates


def _parse_genshin_promoted_characters(
    page_title: str,
    *,
    session: Optional[requests.Session] = None,
) -> List[str]:
    try:
        html = _fetch_fandom_parse_html(
            "genshin-impact.fandom.com",
            page_title,
            session=session,
        )
    except Exception:
        return []

    soup = BeautifulSoup(html, "html.parser")
    headline = soup.find(
        "span",
        class_="mw-headline",
        string="Promoted or Featured with a Drop-Rate Boost",
    )
    if not headline:
        return []

    heading = headline.find_parent(["h2", "h3", "h4"])
    if not heading:
        return []

    # The first table after the headline is a compact summary:
    # Type | Items | Characters (for Character Event Wishes)
    summary = heading.find_next_sibling("table")
    if not summary:
        return []

    # Find the "Characters" row in that summary table.
    for tr in summary.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        if cells[0].get_text(" ", strip=True) != "Characters":
            continue

        titles: List[str] = []
        for a in tr.find_all("a", title=True):
            title = (a.get("title") or "").strip()
            if not title or title.startswith("File:"):
                continue
            if title in {"Character", "Characters"}:
                continue
            if title == "Unknown Character":
                continue
            titles.append(title)
        titles = _dedupe(titles)
        # For Character Event Wishes, the first entry is the limited 5-star.
        return titles[:1]

    return []


def _parse_hsr_warp_occurrence(
    page_title: str,
    *,
    session: Optional[requests.Session] = None,
) -> Tuple[Optional[str], List[str]]:
    html = _fetch_fandom_parse_html(
        "honkai-star-rail.fandom.com",
        page_title,
        session=session,
    )
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    version_match = re.search(r"\bVersion\s+(\d+(?:\.\d+)*)\b", text)
    version = version_match.group(1) if version_match else None

    chars: List[str] = []
    headline = None
    for span in soup.select("span.mw-headline"):
        if "Drop Rate Boost: 5" in span.get_text(" ", strip=True):
            headline = span
            break
    if headline:
        heading = headline.find_parent(["h2", "h3", "h4"])
        if heading:
            ul = heading.find_next_sibling("ul")
            if ul:
                for li in ul.find_all("li", recursive=False):
                    a = li.find("a", title=True)
                    if not a:
                        continue
                    title = (a.get("title") or "").strip()
                    if not title or title.startswith("File:"):
                        continue
                    chars.append(title)

    return version, _dedupe(chars)


def fetch_hsr_updates(
    session: Optional[requests.Session] = None,
    *,
    max_occurrences: int = 60,
    max_workers: int = 8,
) -> List[UpdateEntry]:
    html = _fetch_fandom_parse_html(
        "honkai-star-rail.fandom.com",
        "Character_Event_Warp",
        session=session,
    )
    soup = BeautifulSoup(html, "html.parser")
    table = _find_table_by_headers(soup, ["Image", "Name", "Start", "End"])
    if table is None:
        raise RuntimeError("HSR: expected Character Event Warp table not found")

    occurrences: List[Tuple[str, Optional[date]]] = []
    ends_by_page = {}
    for row in table.find_all("tr")[1 : 1 + max_occurrences]:
        tds = row.find_all("td")
        if len(tds) < 4:
            continue
        a = tds[1].find("a", title=True)
        page_title = (a.get("title") if a else "") or ""
        if not page_title:
            continue
        start = _parse_date_maybe(tds[2].get_text(" ", strip=True))
        occurrences.append((page_title, start))
        ends_by_page[page_title] = _parse_date_maybe(tds[3].get_text(" ", strip=True))

    # Dedupe by page title.
    start_by_page: Dict[str, Optional[date]] = {}
    for page_title, start in occurrences:
        if page_title not in start_by_page:
            start_by_page[page_title] = start

    pages = list(start_by_page.items())

    def parse_page(item: Tuple[str, Optional[date]]) -> Tuple[str, Optional[date], Optional[str], List[str]]:
        page_title, start = item
        # requests.Session is not thread-safe. Use a short-lived session per
        # worker call when parsing occurrence pages concurrently.
        version, chars = _parse_hsr_warp_occurrence(page_title)
        return page_title, start, version, chars

    if max_workers > 1 and len(pages) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            parsed_pages = list(executor.map(parse_page, pages))
    else:
        parsed_pages = []
        for page_title, start in pages:
            version, chars = _parse_hsr_warp_occurrence(page_title, session=session)
            parsed_pages.append((page_title, start, version, chars))

    by_phase: Dict[Tuple[str, Optional[date]], Dict[str, object]] = {}
    for _page_title, start, version, chars in parsed_pages:
        if not version:
            continue
        entry = by_phase.setdefault(
            (version, start),
            {"update_date": start, "characters": [], "end_date": ends_by_page.get(_page_title), "source_ids": []},
        )
        entry["source_ids"].append(_page_title)
        entry["characters"] = _dedupe(
            list(entry.get("characters") or []) + chars
        )

    updates: List[UpdateEntry] = []
    for (version, _start), info in by_phase.items():
        updates.append(
            UpdateEntry(
                version=f"Version {version}",
                update_date=info.get("update_date") if isinstance(info.get("update_date"), date) else None,
                pickup_characters=list(info.get("characters") or []),
                end_date=info.get("end_date"),
                source_id="|".join(sorted(set(info.get("source_ids") or []))),
            )
        )

    updates.sort(key=lambda x: x.update_date or date.min, reverse=True)
    return updates


def _normalize_version_label(text: str) -> str:
    # Examples:
    # - Version 5.8
    # - Version "Luna V"
    # - Version "Luna IV"
    text = _strip_soft_hyphen(text).strip()
    text = text.replace('Version "', "Version ").replace('"', "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_first_datetime_and_tz(text: str) -> Tuple[Optional[date], str, str]:
    """
    Extract the first occurrence of:
      YYYY-MM-DD HH:MM (UTC+N)
    Returns (date, time, tz) where time/tz can be empty strings.
    """
    text = _strip_soft_hyphen(text)
    # Prefer entries with explicit timezone in parentheses.
    m = re.search(
        r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*\((UTC[+-]\d+)\)",
        text,
    )
    def _iso(s: str) -> Optional[date]:
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None

    if m:
        return _iso(m.group(1)), m.group(2), m.group(3)

    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", text)
    if m:
        return _iso(m.group(1)), m.group(2), ""

    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return _iso(m.group(1)), "", ""

    return None, "", ""


def _parse_special_program_table(domain: str, session: Optional[requests.Session] = None) -> List[BroadcastEntry]:
    html = _fetch_fandom_parse_html(domain, "Special_Program", session=session)
    soup = BeautifulSoup(html, "html.parser")

    # Tables are formatted slightly differently across wikis, but the usable rows
    # have at least 2 cells and the first cell starts with "Version".
    candidates = []
    for candidate in soup.find_all("table"):
        count = 0
        for tr in candidate.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) >= 2 and cells[0].get_text(" ", strip=True).startswith("Version"):
                d, t, _tz = _parse_first_datetime_and_tz(cells[1].get_text(" ", strip=True))
                if d or t:
                    count += 1
        if count:
            candidates.append((count, candidate))
    table = max(candidates, key=lambda pair: pair[0])[1] if candidates else None
    if table is None:
        raise RuntimeError(f"{domain}: Special_Program table not found")

    entries: List[BroadcastEntry] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) < 2:
            continue

        first_lines = cells[0].get_text("\n", strip=True).splitlines()
        first = _strip_soft_hyphen(first_lines[0] if first_lines else "")
        if not first.startswith("Version"):
            continue

        version = _normalize_version_label(first)
        title = ""
        if len(first_lines) >= 2:
            title = _strip_soft_hyphen(first_lines[1]).strip()
        else:
            # Fall back: strip the leading "Version ..." chunk.
            title = re.sub(r"^Version\s+[^\\s]+(?:\s+[^\\s]+)?\s*", "", first).strip()
        title = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()

        schedule_text = cells[1].get_text(" ", strip=True)
        d, t, tz = _parse_first_datetime_and_tz(schedule_text)
        entries.append(
            BroadcastEntry(
                version=version,
                broadcast_date=d,
                broadcast_time=t,
                broadcast_tz=tz,
                title=title,
            )
        )

    # Sort newest first, unknown dates last.
    entries = list({(e.version, e.broadcast_date, e.broadcast_time, e.broadcast_tz): e for e in entries}.values())
    if not entries:
        raise RuntimeError("No broadcast rows parsed")
    entries.sort(key=lambda x: (x.broadcast_date is not None, x.broadcast_date or date.max), reverse=True)
    return entries


def fetch_genshin_broadcasts(session: Optional[requests.Session] = None) -> List[BroadcastEntry]:
    return _parse_special_program_table("genshin-impact.fandom.com", session=session)


def fetch_zzz_broadcasts(session: Optional[requests.Session] = None) -> List[BroadcastEntry]:
    return _parse_special_program_table("zenless-zone-zero.fandom.com", session=session)


def fetch_hsr_broadcasts(session: Optional[requests.Session] = None) -> List[BroadcastEntry]:
    return _parse_special_program_table("honkai-star-rail.fandom.com", session=session)
