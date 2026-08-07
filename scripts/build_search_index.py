"""Build Tantivy full-text search indexes from all AKN XML files in data/.

Builds one index per language (de, fr, it) in:
    data/search_index/de/
    data/search_index/fr/
    data/search_index/it/

Usage:
    cd web && uv run python ../scripts/build_search_index.py
    cd web && uv run python ../scripts/build_search_index.py --data-dir /path/to/data
    cd web && uv run python ../scripts/build_search_index.py --lang de  # single language
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

import tantivy
from lxml.etree import XMLSyntaxError

from lex_akn.metadata import extract_metadata
from lex_akn.parse import akn_tag, parse_file, text_content
from lex_akn.search import (
    SUPPORTED_INDEX_LANGS,
    build_analyzers, build_schema, classify_doc_type,
    fold_text, load_compound_dict,
)

DEFAULT_DATA_DIR = Path(os.environ.get(
    "LEX_DATA_DIR",
    Path(__file__).parent.parent / "data",
)).resolve()


def extract_body_text(tree) -> str:
    """Extract searchable text from an AKN document.

    Includes <body>, <preamble>, <preface>, and <components> (annexes).
    Components contain sub-documents (<doc>) with their own <mainBody>
    that hold annex text — important for cross-references.
    """
    parts: list[str] = []

    act = tree.find(f".//{akn_tag('act')}")
    if act is None:
        return ""

    for tag_name in ("preface", "preamble", "body", "components"):
        el = act.find(akn_tag(tag_name))
        if el is not None:
            txt = text_content(el)
            if txt:
                parts.append(txt)

    return " ".join(parts)


def create_index(
    index_dir: Path,
    compound_words: list[str] | None = None,
    *,
    lang: str = "de",
) -> tantivy.Index:
    """Create a fresh Tantivy index with registered analyzers."""
    if index_dir.exists():
        shutil.rmtree(index_dir)
    index_dir.mkdir(parents=True)

    schema = build_schema(lang)
    index = tantivy.Index(schema, path=str(index_dir))
    for name, analyzer in build_analyzers(compound_words, lang=lang).items():
        index.register_tokenizer(name, analyzer)
    return index


def _read_xml(xml_path: Path):
    """Parse an XML file, returning (tree, metadata, body_text) or None on error."""
    try:
        tree = parse_file(xml_path)
        meta = extract_metadata(tree)
        body_text = extract_body_text(tree)
        return tree, meta, body_text
    except (XMLSyntaxError, ValueError, FileNotFoundError):
        return None


def index_federal(
    writer, data_dir: Path, lang: str,
) -> tuple[int, int, int]:
    """Index all federal (CH) AKN documents.

    Returns (indexed, skipped, repealed).  Repealed acts (status != in_force)
    are deliberately excluded — they stay resolvable at their ELI URI but must
    not pollute the search index for daily users (URI-MODEL.md).
    """
    ch_dir = data_dir / "ch"
    index_path = ch_dir / "sync_index.json"
    if not index_path.exists():
        index_path = ch_dir / "index.json"
    indexed = skipped = repealed = 0

    if not index_path.exists():
        print(f"  No federal index at {index_path}")
        return 0, 0, 0

    ch_index = json.loads(index_path.read_text(encoding="utf-8"))

    for sr, info in ch_index.items():
        if info.get("status", "in_force") != "in_force":
            repealed += 1
            continue
        xml_path = ch_dir / sr / f"{lang}.xml"
        if not xml_path.exists():
            skipped += 1
            continue

        result = _read_xml(xml_path)
        if result is None:
            print(f"  SKIP ch/{sr}/{lang}: parse error")
            skipped += 1
            continue
        _, meta, body_text = result

        # Title: prefer AKN metadata (HTML-stripped by extract_metadata).
        # Fallback to sync_index only if AKN metadata has no title.
        # This matches the original German-only behavior.
        if "titles" in info:
            fallback_title = info["titles"].get(lang, info["titles"].get("de", ""))
            fallback_abbr = info.get("abbreviations", {}).get(
                lang, info.get("abbreviations", {}).get("de", ""),
            )
        else:
            fallback_title = info.get("title", "")
            fallback_abbr = info.get("abbreviation", "")

        title = meta.title or fallback_title
        abbr = meta.abbreviation or fallback_abbr

        # doc_type always classified from German title (language-independent)
        de_title = ""
        if "titles" in info:
            de_title = info["titles"].get("de", "")
        else:
            de_title = info.get("title", "")
        if not de_title:
            # Read from de.xml metadata as fallback
            de_xml = ch_dir / sr / "de.xml"
            if de_xml.exists():
                de_result = _read_xml(de_xml)
                if de_result is not None:
                    de_title = de_result[1].title
        doc_type = classify_doc_type(
            title=de_title or title, sr_number=sr, level="ch",
        )

        doc = tantivy.Document(
            title=[title],
            title_ae=[fold_text(title, lang)],
            body=[body_text],
            body_ae=[fold_text(body_text, lang)],
            body_prefix=[body_text],
            body_prefix_ae=[fold_text(body_text, lang)],
            abbreviation=[abbr],
            eli_path=[f"/eli/ch/{sr}"],
            sr_number=[sr],
            level=["ch"],
            doc_type=[doc_type],
        )
        doc.add_facet("classification", tantivy.Facet.from_string("/ch"))
        writer.add_document(doc)
        indexed += 1

        if indexed % 500 == 0:
            print(f"  CH/{lang}: {indexed} indexed...")

    return indexed, skipped, repealed


def index_cantonal(
    writer, data_dir: Path, lang: str,
) -> tuple[int, int, int]:
    """Index all cantonal (VS) AKN documents.

    Returns (indexed, skipped, repealed).  Repealed laws (meta.json
    status != in_force) are excluded — they stay resolvable at their ELI URI
    but are kept out of search (URI-MODEL.md).
    """
    vs_dir = data_dir / "vs"
    indexed = skipped = repealed = 0

    if not vs_dir.is_dir():
        print(f"  No cantonal directory at {vs_dir}")
        return 0, 0, 0

    # Enumerate all sysno directories
    for sysno_dir in sorted(vs_dir.iterdir()):
        if not sysno_dir.is_dir():
            continue
        sysno = sysno_dir.name

        # Read meta.json once: lifecycle status (to exclude repealed) + law_type.
        law_type = ""
        status = "in_force"
        meta_json_path = sysno_dir / "meta.json"
        if meta_json_path.exists():
            try:
                vs_meta = json.loads(meta_json_path.read_text(encoding="utf-8"))
                law_type = vs_meta.get("law_type", "")
                status = vs_meta.get("status", "in_force")
            except (json.JSONDecodeError, OSError):
                pass

        if status != "in_force":
            repealed += 1
            continue

        xml_path = sysno_dir / f"{lang}.xml"
        if not xml_path.exists():
            skipped += 1
            continue

        result = _read_xml(xml_path)
        if result is None:
            print(f"  SKIP vs/{sysno}/{lang}: parse error")
            skipped += 1
            continue
        _, meta, body_text = result

        # doc_type from German title (always use de for classification)
        de_title = meta.title
        if lang != "de":
            de_xml = sysno_dir / "de.xml"
            if de_xml.exists():
                de_result = _read_xml(de_xml)
                if de_result is not None:
                    de_title = de_result[1].title

        doc_type = classify_doc_type(
            title=de_title, sr_number=sysno, level="vs",
            law_type=law_type,
        )

        doc = tantivy.Document(
            title=[meta.title],
            title_ae=[fold_text(meta.title, lang)],
            body=[body_text],
            body_ae=[fold_text(body_text, lang)],
            body_prefix=[body_text],
            body_prefix_ae=[fold_text(body_text, lang)],
            abbreviation=[meta.abbreviation],
            eli_path=[f"/eli/vs/{sysno}"],
            sr_number=[sysno],
            level=["vs"],
            doc_type=[doc_type],
        )
        doc.add_facet("classification", tantivy.Facet.from_string("/vs"))
        writer.add_document(doc)
        indexed += 1

    return indexed, skipped, repealed


def index_municipal(
    writer, data_dir: Path, lang: str,
) -> tuple[int, int, int]:
    """Index all municipal AKN documents. Returns (indexed, skipped, repealed).

    Municipal laws are German-only — only indexed into the DE index.
    """
    if lang != "de":
        return 0, 0, 0

    mun_dir = data_dir / "mun"
    indexed = skipped = repealed = 0

    if not mun_dir.is_dir():
        print(f"  No municipal directory at {mun_dir}")
        return 0, 0, 0

    for xml_path in sorted(mun_dir.glob("**/de.xml")):
        # Path: data/mun/{bfs}/{entity}/{reg_id}/de.xml
        rel = xml_path.relative_to(mun_dir)
        parts = rel.parts  # (bfs, entity, reg_id, 'de.xml')
        if len(parts) != 4:
            skipped += 1
            continue

        bfs, entity, reg_id = parts[0], parts[1], parts[2]

        # Read law_type + lifecycle status from meta.json if available.
        law_type = ""
        status = "in_force"
        meta_json_path = xml_path.parent / "meta.json"
        if meta_json_path.exists():
            try:
                mun_meta = json.loads(meta_json_path.read_text(encoding="utf-8"))
                law_type = mun_meta.get("law_type", "")
                status = mun_meta.get("status", "in_force")
            except (json.JSONDecodeError, OSError):
                pass

        if status != "in_force":
            repealed += 1
            continue

        result = _read_xml(xml_path)
        if result is None:
            print(f"  SKIP mun/{bfs}/{entity}/{reg_id}: parse error")
            skipped += 1
            continue
        _, meta, body_text = result

        doc_type = classify_doc_type(
            title=meta.title, sr_number=reg_id, level="mun",
            law_type=law_type,
        )

        doc = tantivy.Document(
            title=[meta.title],
            title_ae=[fold_text(meta.title, lang)],
            body=[body_text],
            body_ae=[fold_text(body_text, lang)],
            body_prefix=[body_text],
            body_prefix_ae=[fold_text(body_text, lang)],
            abbreviation=[meta.abbreviation],
            eli_path=[f"/eli/mun/{bfs}/{entity}/{reg_id}"],
            sr_number=[reg_id],
            level=["mun"],
            doc_type=[doc_type],
        )
        doc.add_facet(
            "classification",
            tantivy.Facet.from_string(f"/mun/{bfs}"),
        )
        writer.add_document(doc)
        indexed += 1

    return indexed, skipped, repealed


def dir_size_mb(path: Path) -> float:
    """Total size of a directory in MB."""
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def build_one_index(
    data_dir: Path,
    base_index_dir: Path,
    lang: str,
    compound_words: list[str] | None,
) -> None:
    """Build a single language index."""
    index_dir = base_index_dir / lang

    print(f"{'=' * 60}")
    print(f"Building {lang.upper()} index → {index_dir}")
    print(f"{'=' * 60}")

    t_start = time.monotonic()

    # Compound dict only for German
    lang_compounds = compound_words if lang == "de" else None
    index = create_index(index_dir, lang_compounds, lang=lang)
    writer = index.writer(heap_size=128_000_000, num_threads=1)

    # Federal
    print(f"Indexing federal laws (CH/{lang})...")
    t0 = time.monotonic()
    ch_indexed, ch_skipped, ch_repealed = index_federal(writer, data_dir, lang)
    t_ch = time.monotonic() - t0
    print(f"  CH: {ch_indexed} indexed, {ch_skipped} skipped, "
          f"{ch_repealed} repealed (excluded) ({t_ch:.1f}s)")

    # Cantonal
    print(f"Indexing cantonal laws (VS/{lang})...")
    t0 = time.monotonic()
    vs_indexed, vs_skipped, vs_repealed = index_cantonal(writer, data_dir, lang)
    t_vs = time.monotonic() - t0
    print(f"  VS: {vs_indexed} indexed, {vs_skipped} skipped, "
          f"{vs_repealed} repealed (excluded) ({t_vs:.1f}s)")

    # Municipal (German-only)
    if lang == "de":
        print("Indexing municipal regulations (MUN)...")
        t0 = time.monotonic()
        mun_indexed, mun_skipped, mun_repealed = index_municipal(writer, data_dir, lang)
        t_mun = time.monotonic() - t0
        print(f"  MUN: {mun_indexed} indexed, {mun_skipped} skipped, "
              f"{mun_repealed} repealed (excluded) ({t_mun:.1f}s)")
    else:
        mun_indexed = mun_skipped = mun_repealed = 0

    # Commit
    print("Committing index...")
    t0 = time.monotonic()
    writer.commit()
    writer.wait_merging_threads()
    t_commit = time.monotonic() - t0
    print(f"  Commit: {t_commit:.1f}s")

    t_total = time.monotonic() - t_start
    total_indexed = ch_indexed + vs_indexed + mun_indexed
    total_skipped = ch_skipped + vs_skipped + mun_skipped
    total_repealed = ch_repealed + vs_repealed + mun_repealed
    size_mb = dir_size_mb(index_dir)

    print()
    print(f"  {lang.upper()} total indexed:  {total_indexed}")
    print(f"  {lang.upper()} total skipped:  {total_skipped}")
    print(f"  {lang.upper()} total repealed: {total_repealed} (excluded from search)")
    print(f"  {lang.upper()} index size:     {size_mb:.1f} MB")
    print(f"  {lang.upper()} total time:     {t_total:.1f}s")
    print(f"  {lang.upper()} throughput:     {total_indexed / max(t_total, 0.001):.0f} docs/s")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Tantivy search indexes")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Path to data directory containing ch/, vs/, mun/",
    )
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=None,
        help="Base path for search index output (default: <data-dir>/search_index)",
    )
    parser.add_argument(
        "--lang",
        type=str,
        default=None,
        help="Build only this language index (de, fr, or it). Default: all.",
    )
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    base_index_dir = (args.index_dir or data_dir / "search_index").resolve()

    assert data_dir.is_dir(), f"Data directory not found: {data_dir}"

    # Determine languages to build
    if args.lang:
        assert args.lang in SUPPORTED_INDEX_LANGS, (
            f"Unsupported lang: {args.lang}. Must be one of {SUPPORTED_INDEX_LANGS}"
        )
        langs = [args.lang]
    else:
        langs = list(SUPPORTED_INDEX_LANGS)

    # Load compound dictionary for German compound word splitting
    dict_path = data_dir / "compound_dict.txt"
    compound_words = load_compound_dict(dict_path)

    print(f"Data directory:  {data_dir}")
    print(f"Index base dir:  {base_index_dir}")
    print(f"Languages:       {', '.join(langs)}")
    print(f"Compound dict:   {len(compound_words)} words from {dict_path}")
    print()

    t_grand = time.monotonic()

    for lang in langs:
        build_one_index(data_dir, base_index_dir, lang, compound_words)

    t_total = time.monotonic() - t_grand

    # Summary
    total_size = sum(
        dir_size_mb(base_index_dir / l)
        for l in langs
        if (base_index_dir / l).is_dir()
    )
    print("=" * 60)
    print(f"All indexes built in {t_total:.1f}s")
    print(f"Total disk usage: {total_size:.1f} MB")
    print("=" * 60)


if __name__ == "__main__":
    main()
