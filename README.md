# openmun-lex

Swiss law viewer and editor for federal, cantonal, and municipal legislation. Parses [Akoma Ntoso](http://docs.oasis-open.org/legaldocml/akn-core/v1.0/akn-core-v1.0-part1-vocabulary.html) XML and renders it as browseable HTML with optional inline editing via ProseMirror.

## Features

- **Trilingual** (de/fr/it) — language toggle, per-language search indexes, i18n UI
- **Full-text search** via [Tantivy](https://github.com/quickwit-oss/tantivy) with German compound splitting, umlaut handling, and language-specific stemming
- Browse and view **federal law** (4,700+ in-force acts from [Fedlex](https://www.fedlex.admin.ch/))
- Browse and view **cantonal law** (685 Valais/Wallis acts from [lex.vs.ch](https://lex.vs.ch/))
- View **municipal regulations** (Akoma Ntoso XML)
- ELI-compliant URI scheme (`/eli/ch/{sr}`, `/eli/vs/{sysno}`, `/eli/mun/{bfs}/{entity}/{id}`)
- Doc type classification and level-based ranking (treaties de-boosted, municipal law boosted)
- ProseMirror-based inline editor via the shared `openmun-editor` package (opt-in via `LEX_EDIT_ENABLED=1`)
- Reverse-proxy-aware (reads `X-Forwarded-Prefix`)

## Project structure

```
lex/
├── web/            # Web application (Starlette + Jinja2 + Tantivy)
│   ├── src/
│   │   ├── lex_akn/    # AKN XML parsing + Fedlex SPARQL + search
│   │   └── lex_web/    # Web app (routes, templates, static, i18n)
│   └── tests/
├── sync/           # lex.vs.ch → local AKN XML sync tool
│   ├── src/lex_sync/
│   └── tests/
├── sync/fedlex/    # Fedlex → local AKN XML sync tool (trilingual)
│   ├── src/lex_fedlex_sync/
│   └── tests/
├── data/           # Law data (AKN XML + metadata)
│   ├── ch/             # 4,700+ federal laws (de/fr/it XML per SR number)
│   ├── vs/             # 685 Valais cantonal laws (de/fr XML)
│   └── mun/            # Municipal regulations
├── scripts/        # Index building, compound dictionary
├── pocs/           # Proof-of-concept code (numbering schemes)
└── lexvs.py        # CLI client for lex.vs.ch REST API
```

## Quick start

```bash
# Web app
cd web
uv sync
uv run uvicorn lex_web.app:app --port 8001

# Run tests
uv run pytest tests/ -v
```

### Routes

| Route | Description |
|-------|-------------|
| `/` | Landing page with search |
| `/api/search` | Search API (JSON, supports `q`, `lang`, `level`, `doc_type`, `page`) |
| `/eli/ch/` | Federal law index (grouped by SR category) |
| `/eli/ch/{sr}` | Federal law viewer (e.g. `/eli/ch/101` for the constitution) |
| `/eli/vs/` | Cantonal law index (grouped by law type) |
| `/eli/vs/{sysno}` | Cantonal law viewer (latest version) |
| `/eli/vs/{sysno}/{date}` | Cantonal law version (e.g. `/eli/vs/175.1/2021-05-01`) |
| `/eli/mun/{bfs}/{entity}/{id}` | Municipal regulation viewer |
| `/doc/{doc_id}` | Document viewer (standalone documents) |
| `/set-lang` | Language toggle (POST, sets cookie) |

### Sync tools

The wrapper scripts are the intended entry points: they sync, validate every
XML file, cross-check language coverage, append a summary to
`data/sync_report.log` and write one detailed log per run to
`data/sync_logs/{timestamp}_{vs|ch}.log`. Exit code 1 on any failure.

Re-sync cantonal laws from lex.vs.ch (de + fr, all versions):

```bash
cd sync
uv sync
uv run python ../scripts/sync_vs.py            # incremental
uv run python ../scripts/sync_vs.py --force    # re-convert everything
```

Sync federal laws from Fedlex (de + fr + it, all in-force versions):

```bash
cd sync/fedlex
uv sync
uv run python ../../scripts/sync_fedlex.py                 # incremental
uv run python ../../scripts/sync_fedlex.py --mode latest   # current versions only
```

The bare CLIs (`uv run lex-sync sync --store ../data`, `uv run fedlex-sync
sync --store ../../data --mode include-history`) do the same without the
validation pass; they also write a run log. What is on disk is the truth:
a missing or lost file is fetched again on the next run, a failed or
invalid download fails the whole law and is retried next time, and a
language the source does not offer for a version is reported as a gap.

### Search index

Build per-language Tantivy indexes (~6,100 docs, ~17s):

```bash
cd web
uv run python ../scripts/build_search_index.py
```

### CLI tool

Query lex.vs.ch directly from the command line (zero dependencies):

```bash
./lexvs.py index                          # List all VS laws
./lexvs.py get 642.1 -a 181 --quote       # Quote Art. 181 StG
./lexvs.py search "Baureglement"          # Full-text search
```

## License

The **source code** in this repository is licensed under the
[European Union Public Licence v. 1.2](LICENSE) (EUPL-1.2).

Copyright (c) 2026 Gemeinde Bister OR Firntec GmbH.

Commercial licences are available from either copyright holder
independently — contact Gemeinde Bister or Firntec GmbH.

### Data

The `data/` directory contains law texts from public sources:

| Directory | Source | License |
|-----------|--------|---------|
| `data/ch/` | [Fedlex](https://www.fedlex.admin.ch/) | [OGD Switzerland](https://opendata.swiss/en/terms-of-use) |
| `data/vs/` | [lex.vs.ch](https://lex.vs.ch/) | [OGD Switzerland](https://opendata.swiss/en/terms-of-use) |
| `data/mun/` | Municipal governments | Public law, openly published |

All law texts are public government documents published under Swiss Open Government Data terms.
