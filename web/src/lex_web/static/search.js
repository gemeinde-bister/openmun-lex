/* search.js — live search for openmunlex */
(function () {
    "use strict";

    var ROOT = document.body.dataset.rootPath || "";
    var MIN_CHARS = 3;
    var DEBOUNCE_MS = 200;
    var PAGE_SIZE = 25;

    var input = document.getElementById("search-input");
    var filtersBar = document.getElementById("search-filters");
    var typeFiltersBar = document.getElementById("search-type-filters");
    var resultsDiv = document.getElementById("search-results");
    var browseDiv = document.getElementById("browse-content");
    if (!input || !resultsDiv || !browseDiv) return;

    var chips = filtersBar ? filtersBar.querySelectorAll(".filter-chip") : [];
    var activeLevels = []; // empty = all
    var activeDocTypes = []; // empty = default (excludes treaty)
    var currentOffset = 0;
    var timer = null;
    var controller = null;

    // Read i18n labels from server-rendered JSON block (falls back to German)
    var i18n = {
        level_labels: { ch: "Bund", vs: "Kanton", mun: "Gemeinde" },
        doc_type_labels: {
            verfassung: "Verfassung", gesetz: "Gesetz",
            verordnung: "Verordnung", reglement: "Reglement",
            beschluss: "Beschluss", konkordat: "Konkordat",
            treaty: "Staatsvertrag", other: "Andere"
        },
        no_results: "Keine Ergebnisse.",
        search_unavailable: "Suchindex nicht verfügbar.",
        search_error: "Fehler bei der Suche.",
        hits: "Treffer",
        page_of: "Seite {page} von {total}"
    };
    var i18nEl = document.getElementById("search-i18n");
    if (i18nEl) {
        try {
            var parsed = JSON.parse(i18nEl.textContent);
            if (parsed.level_labels) i18n.level_labels = parsed.level_labels;
            if (parsed.doc_type_labels) i18n.doc_type_labels = parsed.doc_type_labels;
            if (parsed.no_results) i18n.no_results = parsed.no_results;
            if (parsed.search_unavailable) i18n.search_unavailable = parsed.search_unavailable;
            if (parsed.search_error) i18n.search_error = parsed.search_error;
            if (parsed.hits) i18n.hits = parsed.hits;
            if (parsed.page_of) i18n.page_of = parsed.page_of;
        } catch (e) {
            // Keep German defaults
        }
    }

    // Build windowed page list: [1, 0, 4, 5, 6, 7, 8, 0, 30]
    // 0 = ellipsis placeholder
    function buildPageWindow(current, total) {
        if (total <= 7) {
            var all = [];
            for (var i = 1; i <= total; i++) all.push(i);
            return all;
        }
        var pages = [];
        var windowStart = Math.max(2, current - 2);
        var windowEnd = Math.min(total - 1, current + 2);
        // Expand window if near edges
        if (windowStart <= 3) windowStart = 2;
        if (windowEnd >= total - 2) windowEnd = total - 1;

        pages.push(1);
        if (windowStart > 2) pages.push(0); // ellipsis
        for (var j = windowStart; j <= windowEnd; j++) pages.push(j);
        if (windowEnd < total - 1) pages.push(0); // ellipsis
        pages.push(total);
        return pages;
    }

    function escapeHtml(s) {
        var d = document.createElement("div");
        d.textContent = s;
        return d.innerHTML;
    }

    function showSearch() {
        browseDiv.classList.add("hidden");
        resultsDiv.classList.add("visible");
        if (filtersBar) filtersBar.classList.add("visible");
        if (typeFiltersBar) typeFiltersBar.classList.add("visible");
    }

    function showBrowse() {
        browseDiv.classList.remove("hidden");
        resultsDiv.classList.remove("visible");
        resultsDiv.innerHTML = "";
        if (filtersBar) filtersBar.classList.remove("visible");
        if (typeFiltersBar) typeFiltersBar.classList.remove("visible");
        resetChips();
        currentOffset = 0;
    }

    function resetChips() {
        activeLevels = [];
        activeDocTypes = [];
        for (var i = 0; i < chips.length; i++) {
            chips[i].classList.toggle("active", chips[i].dataset.level === "");
        }
        if (typeFiltersBar) typeFiltersBar.innerHTML = "";
    }

    function updateChipCounts(facets) {
        for (var i = 0; i < chips.length; i++) {
            var countEl = chips[i].querySelector(".chip-count");
            if (!countEl) continue;
            var lvl = chips[i].dataset.level;
            if (lvl === "") {
                // "Alle" chip: sum all
                countEl.textContent = "(" + (facets.ch + facets.vs + facets.mun) + ")";
            } else if (facets[lvl] !== undefined) {
                countEl.textContent = "(" + facets[lvl] + ")";
            }
        }
    }

    function renderDocTypeChips(docTypeFacets) {
        if (!typeFiltersBar) return;
        var html = "";
        var keys = Object.keys(docTypeFacets);
        if (keys.length <= 1) {
            typeFiltersBar.innerHTML = "";
            return;
        }

        for (var i = 0; i < keys.length; i++) {
            var dt = keys[i];
            var count = docTypeFacets[dt];
            var label = i18n.doc_type_labels[dt] || dt;
            var active = activeDocTypes.indexOf(dt) !== -1 ? " active" : "";
            html += '<button class="filter-chip type-chip' + active + '" data-doctype="' + dt + '">';
            html += escapeHtml(label) + ' <span class="chip-count">(' + count + ")</span></button>";
        }
        typeFiltersBar.innerHTML = html;

        // Bind click handlers
        var typeChips = typeFiltersBar.querySelectorAll(".type-chip");
        for (var j = 0; j < typeChips.length; j++) {
            typeChips[j].addEventListener("click", (function (chip) {
                return function () {
                    var dt = chip.dataset.doctype;
                    var idx = activeDocTypes.indexOf(dt);
                    if (idx !== -1) {
                        activeDocTypes.splice(idx, 1);
                    } else {
                        activeDocTypes.push(dt);
                    }
                    currentOffset = 0;
                    doSearch();
                };
            })(typeChips[j]));
        }
    }

    function renderResults(data) {
        updateChipCounts(data.facets);
        renderDocTypeChips(data.doc_type_facets || {});

        if (data.total === 0) {
            resultsDiv.innerHTML = '<p class="search-meta">' + escapeHtml(i18n.no_results) + '</p>';
            return;
        }

        var page = Math.floor(currentOffset / PAGE_SIZE) + 1;
        var totalPages = Math.ceil(data.total / PAGE_SIZE);

        var html = '<p class="search-meta">' + data.total + " " + escapeHtml(i18n.hits) + " (" + data.query_ms + " ms)";
        if (totalPages > 1) {
            html += " — " + i18n.page_of.replace("{page}", page).replace("{total}", totalPages);
        }
        html += "</p>";

        html += '<ul class="search-result-list">';
        for (var i = 0; i < data.hits.length; i++) {
            var h = data.hits[i];
            var levelClass = "level-tag-" + h.level;
            var levelLabel = i18n.level_labels[h.level] || h.level;
            var snippet = h.snippet_body || h.snippet_title || "";
            var typeLabel = i18n.doc_type_labels[h.doc_type] || "";

            html += '<li class="search-result-item">';
            html += '<a href="' + escapeHtml(ROOT + h.eli_path) + '">';
            html += '<div class="result-header">';
            html += '<span class="level-tag ' + levelClass + '">' + escapeHtml(levelLabel) + "</span>";
            if (typeLabel) {
                html += '<span class="doc-type-tag">' + escapeHtml(typeLabel) + "</span>";
            }
            html += '<span class="result-title">' + escapeHtml(h.title) + "</span>";
            if (h.abbreviation) {
                html += '<span class="result-abbr">(' + escapeHtml(h.abbreviation) + ")</span>";
            }
            html += "</div>";
            if (snippet) {
                // snippet_body from tantivy contains <b> highlights — safe (tantivy escapes content)
                html += '<div class="result-snippet">' + snippet + "</div>";
            }
            html += "</a></li>";
        }
        html += "</ul>";

        // Pagination nav — windowed page numbers
        if (totalPages > 1) {
            html += '<nav class="search-pagination">';

            // Previous arrow
            if (page > 1) {
                html += '<button class="pagination-btn" data-page="' + (page - 1) + '">&lsaquo;</button>';
            }

            // Build page number list with ellipsis gaps
            var pages = buildPageWindow(page, totalPages);
            for (var pi = 0; pi < pages.length; pi++) {
                var p = pages[pi];
                if (p === 0) {
                    html += '<span class="pagination-ellipsis">&hellip;</span>';
                } else if (p === page) {
                    html += '<span class="pagination-btn active">' + p + "</span>";
                } else {
                    html += '<button class="pagination-btn" data-page="' + p + '">' + p + "</button>";
                }
            }

            // Next arrow
            if (page < totalPages) {
                html += '<button class="pagination-btn" data-page="' + (page + 1) + '">&rsaquo;</button>';
            }

            html += "</nav>";
        }

        resultsDiv.innerHTML = html;
    }

    function renderError(msg) {
        resultsDiv.innerHTML = '<p class="search-error">' + escapeHtml(msg) + "</p>";
    }

    function doSearch() {
        var q = input.value.trim();
        if (q.length < MIN_CHARS) {
            showBrowse();
            return;
        }

        showSearch();

        // Cancel in-flight request
        if (controller) controller.abort();
        controller = new AbortController();

        var url = ROOT + "/search?q=" + encodeURIComponent(q) + "&limit=" + PAGE_SIZE;
        if (activeLevels.length) url += "&level=" + activeLevels.join(",");
        if (activeDocTypes.length) url += "&doc_type=" + activeDocTypes.join(",");
        if (currentOffset > 0) url += "&offset=" + currentOffset;

        fetch(url, { signal: controller.signal })
            .then(function (res) {
                if (res.status === 503) {
                    renderError(i18n.search_unavailable);
                    return null;
                }
                if (!res.ok) {
                    renderError(i18n.search_error);
                    return null;
                }
                return res.json();
            })
            .then(function (data) {
                if (data) renderResults(data);
            })
            .catch(function (err) {
                if (err.name !== "AbortError") {
                    renderError(i18n.search_error);
                }
            });
    }

    // Debounced input handler — resets to page 1
    input.addEventListener("input", function () {
        currentOffset = 0;
        clearTimeout(timer);
        timer = setTimeout(doSearch, DEBOUNCE_MS);
    });

    // Escape key clears search
    input.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
            input.value = "";
            showBrowse();
        }
    });

    function updateLevelChipStyles() {
        var alleActive = activeLevels.length === 0;
        for (var j = 0; j < chips.length; j++) {
            var lvl = chips[j].dataset.level;
            if (lvl === "") {
                chips[j].classList.toggle("active", alleActive);
            } else {
                chips[j].classList.toggle("active", activeLevels.indexOf(lvl) !== -1);
            }
        }
    }

    // Filter chip clicks — reset to page 1
    for (var i = 0; i < chips.length; i++) {
        chips[i].addEventListener("click", (function (chip) {
            return function () {
                var lvl = chip.dataset.level;
                if (lvl === "") {
                    // "Alle" chip — clear all level filters
                    activeLevels = [];
                } else {
                    var idx = activeLevels.indexOf(lvl);
                    if (idx !== -1) {
                        activeLevels.splice(idx, 1);
                    } else {
                        activeLevels.push(lvl);
                    }
                }
                updateLevelChipStyles();
                currentOffset = 0;
                doSearch();
            };
        })(chips[i]));
    }

    // Pagination button clicks (event delegation on resultsDiv)
    resultsDiv.addEventListener("click", function (e) {
        var btn = e.target.closest("button.pagination-btn");
        if (!btn) return;

        var targetPage = parseInt(btn.dataset.page, 10);
        if (isNaN(targetPage) || targetPage < 1) return;

        currentOffset = (targetPage - 1) * PAGE_SIZE;
        doSearch();
        input.scrollIntoView({ behavior: "smooth" });
    });
})();
