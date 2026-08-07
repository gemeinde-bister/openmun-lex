"""Interactive compound dictionary builder for German legal text.

Extracts unique words from the AKN corpus, shows how the current
dictionary splits them, and helps build a constituent word list
for tantivy's split_compound filter.

Workflow: start with the longest words and work down.

    cd web
    uv run python ../scripts/build_compound_dict.py --length 30
    # review output, add constituents to data/compound_dict.txt
    uv run python ../scripts/build_compound_dict.py --length 30  # verify splits
    uv run python ../scripts/build_compound_dict.py --length 29  # next round
    ...
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path

import tantivy

# Unicode-aware word splitting: sequences of letters (including umlauts)
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# XML tag stripping
_TAG_RE = re.compile(r"<[^>]+>")


def extract_words(data_dir: Path) -> Counter[str]:
    """Extract all lowercased words from XML files in the corpus."""
    counts: Counter[str] = Counter()
    for xml_path in sorted(data_dir.rglob("de.xml")):
        try:
            raw = xml_path.read_text(encoding="utf-8")
        except OSError:
            continue
        text = _TAG_RE.sub(" ", raw)
        for m in _WORD_RE.finditer(text):
            word = m.group().lower()
            if len(word) >= 3:
                counts[word] += 1
    return counts


def load_dict(dict_path: Path) -> list[str]:
    """Load constituent words from dictionary file (one per line)."""
    if not dict_path.exists():
        return []
    words = []
    for line in dict_path.read_text(encoding="utf-8").splitlines():
        w = line.strip().lower()
        if w and not w.startswith("#"):
            words.append(w)
    return sorted(set(words))


def build_splitter(dict_words: list[str]) -> tantivy.TextAnalyzerBuilder | None:
    """Build a tantivy analyzer with split_compound for preview."""
    if not dict_words:
        return None
    return (
        tantivy.TextAnalyzerBuilder(tantivy.Tokenizer.simple())
        .filter(tantivy.Filter.remove_long(100))
        .filter(tantivy.Filter.lowercase())
        .filter(tantivy.Filter.split_compound(dict_words))
        .build()
    )


def main() -> None:
    default_data = Path(os.environ.get(
        "LEX_DATA_DIR",
        Path(__file__).parent.parent / "data",
    )).resolve()

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--length", type=int, required=True, help="Show words of exactly this character length")
    parser.add_argument("--min-freq", type=int, default=2, help="Minimum word frequency (default: 2)")
    parser.add_argument("--limit", type=int, default=50, help="Max words to display (default: 50)")
    parser.add_argument("--data-dir", type=Path, default=default_data, help="Corpus data directory")
    parser.add_argument("--dict", type=Path, default=default_data / "compound_dict.txt", dest="dict_path",
                        help="Path to dictionary file (default: data/compound_dict.txt)")
    parser.add_argument("--all-lengths", action="store_true",
                        help="Show summary for all word lengths (no splitting detail)")
    args = parser.parse_args()

    print(f"Scanning corpus in {args.data_dir} ...")
    word_counts = extract_words(args.data_dir)
    print(f"  {len(word_counts)} unique words (>= 3 chars)\n")

    if args.all_lengths:
        # Summary mode: show word count per length
        by_length: dict[int, int] = {}
        by_length_freq: dict[int, int] = {}
        for w, c in word_counts.items():
            if c >= args.min_freq:
                wl = len(w)
                by_length[wl] = by_length.get(wl, 0) + 1
                by_length_freq[wl] = by_length_freq.get(wl, 0) + c
        print(f"{'len':>4s}  {'unique':>7s}  {'total freq':>10s}")
        print(f"{'---':>4s}  {'------':>7s}  {'----------':>10s}")
        for wl in sorted(by_length.keys(), reverse=True):
            print(f"{wl:4d}  {by_length[wl]:7d}  {by_length_freq[wl]:10d}")
        return

    # Load dictionary
    dict_words = load_dict(args.dict_path)
    splitter = build_splitter(dict_words)
    print(f"Dictionary: {args.dict_path}")
    print(f"  {len(dict_words)} constituent words loaded")
    print()

    # Filter words by length and frequency
    candidates = [
        (word, freq) for word, freq in word_counts.items()
        if len(word) == args.length and freq >= args.min_freq
    ]
    candidates.sort(key=lambda wf: wf[1], reverse=True)

    total = len(candidates)
    shown = candidates[:args.limit]

    split_count = 0
    unsplit_count = 0

    print(f"=== {args.length} chars, {total} words (freq >= {args.min_freq}), showing {len(shown)} ===\n")

    for word, freq in shown:
        if splitter:
            tokens = splitter.analyze(word)
        else:
            tokens = [word]

        is_split = len(tokens) > 1
        if is_split:
            split_count += 1
        else:
            unsplit_count += 1

        marker = " " if is_split else "*"
        tokens_str = " + ".join(tokens) if is_split else tokens[0]
        print(f"  {marker} {word:>{args.length}s}  ({freq:5d}x)  → {tokens_str}")

    if total > len(shown):
        print(f"\n  ... {total - len(shown)} more words not shown (use --limit {total})")

    print(f"\n  Split: {split_count}  |  Unsplit: {unsplit_count}")


if __name__ == "__main__":
    main()
