#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# ///
"""
lexvs - CLI client for the lex.vs.ch API (Valais cantonal law collection).

Bypasses the JavaScript-heavy frontend and talks directly to the REST API.
"""

import argparse
import html
import json
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

BASE = "https://lex.vs.ch/api"
DEFAULT_LANG = "de"
SEARCH_POLL_INTERVAL = 0.5
SEARCH_POLL_MAX_WAIT = 15.0


def _get(path: str, params: dict | None = None) -> dict | list | None:
    url = f"{BASE}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            if not data:
                return None
            return json.loads(data)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _post(path: str, body: dict) -> dict:
    url = f"{BASE}/{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return text.strip()


def _parse_art_number(uid: str) -> str | None:
    """Extract the article number from a node uid like 't-0--t-2--a-181'."""
    match = re.search(r"--a-(\w+)$", uid)
    return match.group(1) if match else None


def _art_matches(uid: str, spec: str) -> bool:
    """Check if an article uid matches a spec like '181', '5a', or '180-182'."""
    art_num = _parse_art_number(uid)
    if art_num is None:
        return False

    if "-" in spec:
        start, end = spec.split("-", 1)
        # Compare numerically for the integer part, allow suffix articles (5a, 180a)
        def num_key(s: str) -> tuple[int, str]:
            m = re.match(r"(\d+)(.*)", s)
            return (int(m.group(1)), m.group(2)) if m else (0, s)
        return num_key(start) <= num_key(art_num) <= num_key(end)
    else:
        return art_num == spec


def _collect_matching_articles(node: dict, spec: str) -> list[dict]:
    """Find all article nodes matching the spec."""
    results = []
    if node.get("type") == "article" and _art_matches(node.get("uid", ""), spec):
        results.append(node)
    for child in node.get("children", []):
        results.extend(_collect_matching_articles(child, spec))
    return results


def _extract_text_from_node(node: dict, lang: str, depth: int = 0) -> str:
    """Recursively extract plain text from a json_content document node."""
    lines = []

    number = node.get("number", {}).get(lang, "")
    number = _strip_html(number) if number else ""

    text_val = node.get("text", {}).get(lang, "")
    html_content = node.get("html_content", {}).get(lang, "")

    node_type = node.get("type", "")
    indent = "  " * depth

    if node_type == "title":
        title_text = text_val or _strip_html(html_content)
        if number and title_text:
            lines.append(f"\n{indent}{number} {title_text}")
        elif title_text:
            lines.append(f"\n{indent}{title_text}")
    elif node_type == "article":
        art_text = _strip_html(html_content)
        if art_text:
            lines.append(f"\n{indent}{art_text}")
    elif node_type == "paragraph":
        para_text = _strip_html(html_content)
        post = node.get("html_content_post", {}).get(lang, "")
        if post:
            para_text += " " + _strip_html(post)
        if para_text:
            lines.append(f"{indent}{para_text}")
    else:
        content_text = _strip_html(html_content)
        if content_text:
            lines.append(f"{indent}{content_text}")

    for child in node.get("children", []):
        lines.append(_extract_text_from_node(child, lang, depth + 1))

    return "\n".join(filter(None, lines))


# Unicode superscript digits for paragraph numbers
_SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _format_para_number(raw: str) -> str:
    """Convert paragraph number like '1bis' to '¹ᵇⁱˢ'."""
    raw = raw.strip()
    # Split into digits and suffix (bis, ter, quater, etc.)
    m = re.match(r"(\d+)(.*)", raw)
    if not m:
        return raw
    digits = m.group(1).translate(_SUPERSCRIPT)
    suffix = m.group(2)
    if suffix:
        sup_map = str.maketrans("abcdefghijklmnopqrstuvwxyz",
                                "ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖqʳˢᵗᵘᵛʷˣʸᶻ")
        suffix = suffix.translate(sup_map)
    return digits + suffix


def _extract_para_number(html_content: str) -> str:
    """Extract paragraph number from HTML like <span class='number'>1<sup>bis</sup></span>."""
    m = re.search(r"<span class='number'>(.*?)</span>", html_content)
    if not m:
        return ""
    return _strip_html(m.group(1))


def _extract_para_text(html_content: str) -> str:
    """Extract paragraph body text from HTML."""
    m = re.search(r"<span class='text_content'>(.*?)</span>", html_content, re.DOTALL)
    text = m.group(1) if m else html_content
    # Strip amendment markers (* at end)
    text = _strip_html(text)
    text = re.sub(r"\s*\*\s*$", "", text)
    return text.strip()


def _format_quote(article: dict, lang: str, abbreviation: str,
                  sysno: str, version_info: str) -> str:
    """Format an article as a clean, copy-pasteable citation."""
    lines = []

    # Article number (clean)
    art_num = _parse_art_number(article.get("uid", "")) or "?"
    art_title = _strip_html(article.get("text", {}).get(lang, ""))
    art_title = re.sub(r"\s*\*\s*$", "", art_title)  # strip amendment marker
    art_title = art_title.replace("\n", " - ") if art_title else ""

    # Header: Art. 181 StG (642.1)
    ref = f"Art. {art_num} {abbreviation}" if abbreviation else f"Art. {art_num}"
    lines.append(f"{ref} ({sysno})")

    if art_title:
        lines.append(art_title)

    lines.append("")

    # Paragraphs with superscript numbers
    for child in article.get("children", []):
        if child.get("type") != "paragraph":
            continue
        h = child.get("html_content", {}).get(lang, "")
        para_num = _extract_para_number(h)
        para_text = _extract_para_text(h)

        if para_num:
            lines.append(f"{_format_para_number(para_num)} {para_text}")
        elif para_text:
            lines.append(para_text)

        # Handle sub-items (enumeration: a), b), c))
        for sub in child.get("children", []):
            sub_h = sub.get("html_content", {}).get(lang, "")
            if sub.get("type") == "enumeration":
                # Extract letter and text from table structure
                letter_m = re.search(
                    r"<td class='number'>\s*(.*?)\s*</td>", sub_h, re.DOTALL)
                text_m = re.search(
                    r"<td class='left_col[^']*'[^>]*>\s*(.*?)\s*</td>",
                    sub_h, re.DOTALL)
                letter = _strip_html(letter_m.group(1)) if letter_m else ""
                text = _strip_html(text_m.group(1)) if text_m else ""
                text = re.sub(r"\s*\*\s*$", "", text)
                if letter and text:
                    lines.append(f"  {letter} {text}")
                elif text:
                    lines.append(f"  {text}")
            else:
                sub_text = _strip_html(sub_h).strip()
                sub_text = re.sub(r"\s*\*\s*$", "", sub_text)
                if sub_text:
                    lines.append(f"  {sub_text}")

        # Post content (marginal notes etc.)
        post = child.get("html_content_post", {}).get(lang, "")
        post_text = _strip_html(post).strip()
        if post_text:
            lines.append(post_text)

    # Footer: (Steuergesetz, Stand 01.01.2026)
    lines.append("")
    lines.append(f"({version_info})")

    return "\n".join(lines)


# ── Commands ────────────────────────────────────────────────────────────

def cmd_categories(args):
    """List the systematic categories (table of contents)."""
    data = _get(f"{args.lang}/systematic_categories")
    assert data is not None, "Failed to fetch systematic categories"

    def print_cat(cat, depth=0):
        c = cat["systematic_category"]
        indent = "  " * depth
        print(f"{indent}{c['systematic_number']:>6}  {c['name']}")
        for child in c.get("children", []):
            print_cat(child, depth + 1)

    for cat in data:
        print_cat(cat)


def cmd_index(args):
    """List all laws (lightweight index)."""
    data = _get(f"{args.lang}/texts_of_law/lightweight_index")
    assert data is not None, "Failed to fetch lightweight index"

    all_laws = []
    for cat_id, laws in data.items():
        for law in laws:
            all_laws.append(law)
    all_laws.sort(key=lambda x: x["systematic_number"])

    if args.filter:
        pattern = args.filter.lower()
        all_laws = [
            l for l in all_laws
            if pattern in l["systematic_number"].lower()
            or pattern in l["title"].lower()
        ]

    for law in all_laws:
        status = " [aufgehoben]" if law.get("abrogated") else ""
        print(f"{law['systematic_number']:>10}  {law['title']}{status}")


def _version_stand(version_dates_str: str, lang: str) -> str:
    """Extract 'Stand DD.MM.YYYY' from version_dates_str."""
    # German: "... in Kraft seit: 01.01.2023 ..."
    # French: "... en vigueur depuis: 01.01.2023 ..."
    m = re.search(r"(?:seit|depuis):\s*(\d{2}\.\d{2}\.\d{4})", version_dates_str)
    if not m:
        return version_dates_str
    date = m.group(1)
    return f"Stand {date}" if lang == "de" else f"version du {date}"


def cmd_get(args):
    """Fetch and display a specific law text."""
    sysno = args.number

    if args.html:
        data = _get(f"{args.lang}/texts_of_law/{sysno}")
    else:
        data = _get(f"{args.lang}/texts_of_law/{sysno}/show_as_json")

    if data is None:
        print(f"Law {sysno} not found.", file=sys.stderr)
        sys.exit(1)

    tol = data["text_of_law"]
    sv = tol["selected_version"]

    # Quote mode: clean citation format, no chrome
    if args.quote:
        if args.html or "json_content" not in sv:
            print("Quote mode requires JSON format (don't use --html).", file=sys.stderr)
            sys.exit(1)
        if not args.article:
            print("Quote mode requires --article/-a.", file=sys.stderr)
            sys.exit(1)

        doc = sv["json_content"]["document"]
        content = doc.get("content", doc)
        articles = _collect_matching_articles(content, args.article)
        if not articles:
            print(f"No article matching '{args.article}' found.", file=sys.stderr)
            sys.exit(1)

        abbreviation = tol.get("abbreviation", "")
        title = tol["title"]
        stand = _version_stand(sv["version_dates_str"], args.lang)
        version_info = f"{title}, {stand}"

        for i, art in enumerate(articles):
            if i > 0:
                print()
            print(_format_quote(art, args.lang, abbreviation, sysno, version_info))
        return

    # Header
    print(f"{'=' * 72}")
    print(f"  {tol['systematic_number']}  {tol['title']}")
    if tol.get("abbreviation"):
        print(f"  ({tol['abbreviation']})")
    print(f"  {tol['text_of_law_dates_str']}")
    print(f"  {sv['version_dates_str']}")
    print(f"{'=' * 72}")

    # Law text
    if not args.html and "json_content" in sv:
        doc = sv["json_content"]["document"]
        content = doc.get("content", doc)

        if args.article:
            articles = _collect_matching_articles(content, args.article)
            if not articles:
                print(f"No article matching '{args.article}' found.", file=sys.stderr)
                sys.exit(1)
            for art in articles:
                print(_extract_text_from_node(art, args.lang))
        else:
            print(_extract_text_from_node(content, args.lang))

        if args.footnotes:
            footnotes = sv["json_content"].get("footnotes", {})
            fn_html = footnotes.get(args.lang, "")
            if fn_html:
                print(f"\n{'─' * 40}")
                print(_strip_html(fn_html))
    elif "xhtml_tol" in sv:
        if args.article:
            print("Article filtering requires JSON format (don't use --html).", file=sys.stderr)
            sys.exit(1)
        print(_strip_html(sv["xhtml_tol"]))
    else:
        print("(No text content in this response format)")

    # Optional sections
    if args.versions:
        print(f"\n{'─' * 40}")
        print("Versionen:")
        print(f"  Aktuell: {sv['version_dates_str']} (ID {sv['id']})")
        for ov in tol.get("old_versions", []):
            print(f"  Alt:     {ov['version_dates_str']} (ID {ov['id']})")
        for fv in tol.get("future_versions", []):
            print(f"  Künftig: {fv['version_dates_str']} (ID {fv['id']})")

    if args.meta:
        print(f"\n{'─' * 40}")
        print("Metadaten:")
        print(f"  PDF: {tol.get('pdf_link', 'n/a')}")
        print(f"  Kanonisch: {tol.get('canonical_link', 'n/a')}")
        print(f"  Aufgehoben: {tol.get('abrogated', False)}")
        print(f"  Typ-ID: {tol.get('text_of_law_type_id')}")
        langs = sv.get("available_languages", [])
        if langs:
            print(f"  Sprachen: {', '.join(l['language']['name_native'] for l in langs)}")


def cmd_search(args):
    """Fulltext search across the law collection."""
    if args.type == "fulltext":
        endpoint = "fulltext_searches"
        body = {
            "fulltext_search": {
                "text": args.query,
                "fields": "fulltext,title,abbreviation,systematic_number",
                "current_law": not args.include_abrogated,
                "version_date_restriction_type": 3 if args.include_abrogated else 2,
            }
        }
        result_key = "fulltext_search"
    elif args.type == "title":
        endpoint = "fulltext_searches"
        body = {
            "fulltext_search": {
                "text": args.query,
                "fields": "title,abbreviation",
                "current_law": not args.include_abrogated,
                "version_date_restriction_type": 3 if args.include_abrogated else 2,
            }
        }
        result_key = "fulltext_search"
    elif args.type == "direct":
        endpoint = "direct_searches"
        body = {
            "direct_search": {
                "text": args.query,
            }
        }
        result_key = "direct_search"
    else:
        print(f"Unknown search type: {args.type}", file=sys.stderr)
        sys.exit(1)

    # Create search
    resp = _post(f"{args.lang}/{endpoint}", body)
    search = resp[result_key]
    search_id = search["id"]
    session_id = search["session_id"]

    # Poll for results (server needs processing time)
    elapsed = 0.0
    results_data = None
    while elapsed < SEARCH_POLL_MAX_WAIT:
        time.sleep(SEARCH_POLL_INTERVAL)
        elapsed += SEARCH_POLL_INTERVAL
        results_data = _get(
            f"{args.lang}/{endpoint}/{search_id}",
            {"session_id": session_id, "page": args.page, "per_page": args.per_page},
        )
        if results_data is not None:
            break

    if results_data is None:
        print("Search timed out.", file=sys.stderr)
        sys.exit(1)

    result = results_data["result"]
    total = result["total_entries"]
    page_count = result["page_count"]
    page = result["page"]

    print(f"Treffer: {total} (Seite {page}/{page_count})")
    print(f"{'─' * 60}")

    for item in result["results"]:
        tol = item["text_of_law"]
        abr = " [aufgehoben]" if tol.get("abrogated") else ""
        snippet = ""
        if tol.get("snippet"):
            snippet = f"\n    {_strip_html(tol['snippet'])[:200]}"
        print(f"  {tol['systematic_number']:>10}  {tol['title']}{abr}")
        if tol.get("abbreviation"):
            print(f"              ({tol['abbreviation']})")
        print(f"              {tol.get('text_of_law_dates_str', '')}")
        if snippet:
            print(f"              {snippet.strip()}")
        print()


def cmd_pdf(args):
    """Download the PDF of a law text."""
    sysno = args.number

    # Use the JSON endpoint (smaller) to get PDF link and metadata
    data = _get(f"{args.lang}/texts_of_law/{sysno}/show_as_json")
    if data is None:
        print(f"Law {sysno} not found.", file=sys.stderr)
        sys.exit(1)

    tol = data["text_of_law"]
    sv = tol["selected_version"]

    if args.annexes:
        pdf_url = sv.get("pdf_link_tol_with_annexes") or tol.get("pdf_link")
    else:
        pdf_url = sv.get("pdf_link_tol") or tol.get("pdf_link")

    if not pdf_url:
        print("No PDF available for this law.", file=sys.stderr)
        sys.exit(1)

    output = args.output or f"{sysno.replace('.', '_')}.pdf"
    print(f"{tol['systematic_number']}  {tol['title']}")
    print(f"{sv['version_dates_str']}")
    print(f"Downloading: {pdf_url}")
    urllib.request.urlretrieve(pdf_url, output)
    size = Path(output).stat().st_size
    print(f"Saved: {output} ({size:,} bytes)")


def cmd_types(args):
    """List all text of law types."""
    data = _get(f"{args.lang}/text_of_law_types")
    assert data is not None, "Failed to fetch text of law types"
    for item in data:
        t = item["text_of_law_type"]
        print(f"  {t['id']:>3}  {t['name']} ({t['name_singularized']})")


def cmd_changes(args):
    """List recent change documents (Amtsblatt)."""
    data = _get(f"{args.lang}/change_document_categories")
    assert data is not None, "Failed to fetch change document categories"

    # Show the requested year or most recent
    target = args.year or data[0]["change_document_category"]["category_name"]
    for item in data:
        cat = item["change_document_category"]
        if cat["category_name"] == str(target):
            print(f"Änderungssammlungen {cat['category_name']}:")
            for child in cat.get("children", []):
                print(f"  {child['id']:>10}  {child['category_name']}")
            return

    print(f"Year {target} not found. Available:")
    for item in data[:10]:
        print(f"  {item['change_document_category']['category_name']}")


def cmd_raw(args):
    """Raw API request (for debugging/exploration)."""
    path = args.path.lstrip("/")
    if not path.startswith(("de/", "fr/")):
        path = f"{args.lang}/{path}"
    data = _get(path)
    if data is None:
        print("null")
    else:
        json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
        print()


# ── Main ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="lexvs",
        description="CLI client for lex.vs.ch (Valais cantonal law)",
    )
    parser.add_argument(
        "-l", "--lang", default=DEFAULT_LANG, choices=["de", "fr"],
        help="Language (default: de)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # categories
    sub.add_parser("categories", aliases=["cat"], help="Show systematic categories")

    # index
    p = sub.add_parser("index", aliases=["ls"], help="List all laws")
    p.add_argument("filter", nargs="?", help="Filter by number or title substring")

    # get
    p = sub.add_parser("get", help="Fetch a specific law text")
    p.add_argument("number", help="Systematic number (e.g. 175.1)")
    p.add_argument("-a", "--article", help="Article number or range (e.g. 181, 5a, 180-182)")
    p.add_argument("-q", "--quote", action="store_true", help="Output clean citation (requires -a)")
    p.add_argument("--html", action="store_true", help="Use HTML format instead of structured JSON")
    p.add_argument("-f", "--footnotes", action="store_true", help="Include footnotes")
    p.add_argument("-v", "--versions", action="store_true", help="Show version history")
    p.add_argument("-m", "--meta", action="store_true", help="Show metadata")

    # search
    p = sub.add_parser("search", aliases=["s"], help="Search the law collection")
    p.add_argument("query", help="Search query")
    p.add_argument(
        "-t", "--type", default="fulltext", choices=["fulltext", "title", "direct"],
        help="Search type (default: fulltext)",
    )
    p.add_argument("-a", "--include-abrogated", action="store_true")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--per-page", type=int, default=25)

    # pdf
    p = sub.add_parser("pdf", help="Download PDF of a law")
    p.add_argument("number", help="Systematic number")
    p.add_argument("-o", "--output", help="Output filename")
    p.add_argument("-a", "--annexes", action="store_true", help="Include annexes")

    # types
    sub.add_parser("types", help="List text of law types")

    # changes
    p = sub.add_parser("changes", help="List change document categories")
    p.add_argument("year", nargs="?", help="Year (default: most recent)")

    # raw
    p = sub.add_parser("raw", help="Raw API GET request")
    p.add_argument("path", help="API path (e.g. de/links)")

    args = parser.parse_args()

    commands = {
        "categories": cmd_categories,
        "cat": cmd_categories,
        "index": cmd_index,
        "ls": cmd_index,
        "get": cmd_get,
        "search": cmd_search,
        "s": cmd_search,
        "pdf": cmd_pdf,
        "types": cmd_types,
        "changes": cmd_changes,
        "raw": cmd_raw,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
