(function() {
    "use strict";

    var metaEl = document.getElementById("lex-meta");
    if (!metaEl) return;
    var META = JSON.parse(metaEl.textContent);

    var body = document.getElementById("law-body");
    var toolbar = document.getElementById("cite-toolbar");
    var toast = document.getElementById("cite-toast");
    if (!body || !toolbar) return;

    // Current citation scope: articles intersecting the text selection.
    // Stored so toolbar buttons can use it even if focus shifts to the button.
    var scopeArticles = [];

    // All articles in the law body (queried once)
    var allArticles = body.querySelectorAll(".akn-article[data-eid]");

    // --- eId → human-readable reference ---
    //
    // Canonical extraction from the eId string. Avoids parsing header text
    // which on federal laws contains inline footnotes.
    //
    //   art_1           → Art. 1
    //   art_3_a         → Art. 3a
    //   art_36_a        → Art. 36a

    function parseEid(eid) {
        if (!eid) return "";
        var m = eid.match(/^art_(\d+)(?:_([a-z]+))?/);
        if (!m) return "";
        return "Art. " + m[1] + (m[2] || "");
    }

    // --- Selection → scope ---

    function updateScope() {
        var sel = window.getSelection();
        if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
            clearScope();
            return;
        }

        // Ignore selections entirely outside the law body
        var range = sel.getRangeAt(0);
        if (!body.contains(range.commonAncestorContainer)) {
            clearScope();
            return;
        }

        // Find all articles that intersect the selection
        var hit = [];
        for (var i = 0; i < allArticles.length; i++) {
            if (sel.containsNode(allArticles[i], true)) {
                hit.push(allArticles[i]);
            }
        }

        if (hit.length === 0) {
            clearScope();
            return;
        }

        // Update scope highlight
        setScope(hit);

        // Position toolbar above the selection
        var rect = range.getBoundingClientRect();
        showToolbar(rect);
    }

    function setScope(articles) {
        // Remove old highlights
        for (var i = 0; i < allArticles.length; i++) {
            allArticles[i].classList.remove("cite-scope");
        }
        // Apply new highlights
        for (var j = 0; j < articles.length; j++) {
            articles[j].classList.add("cite-scope");
        }
        scopeArticles = articles;
    }

    function clearScope() {
        if (scopeArticles.length === 0) return;
        for (var i = 0; i < allArticles.length; i++) {
            allArticles[i].classList.remove("cite-scope");
        }
        scopeArticles = [];
        toolbar.classList.remove("visible");
    }

    // --- Toolbar positioning ---

    function showToolbar(selectionRect) {
        toolbar.classList.add("visible");

        // Need a frame for the toolbar to have dimensions
        requestAnimationFrame(function() {
            var tbRect = toolbar.getBoundingClientRect();
            var top = selectionRect.top + window.scrollY - tbRect.height - 8;
            var left = selectionRect.left + window.scrollX
                       + (selectionRect.width / 2) - (tbRect.width / 2);

            // If no room above, place below
            if (top < window.scrollY + 8) {
                top = selectionRect.bottom + window.scrollY + 8;
            }
            // Clamp horizontally
            if (left < 8) left = 8;
            var maxLeft = document.documentElement.clientWidth - tbRect.width - 8;
            if (left > maxLeft) left = maxLeft;

            toolbar.style.top = top + "px";
            toolbar.style.left = left + "px";
        });
    }

    // --- Event listeners ---

    document.addEventListener("selectionchange", updateScope);

    // Prevent toolbar clicks from collapsing the selection
    toolbar.addEventListener("mousedown", function(e) {
        e.preventDefault();
    });

    // --- Reference helpers ---

    function refLabel(article) {
        return parseEid(article.getAttribute("data-eid") || "");
    }

    function lawName() {
        return META.abbreviation || META.title;
    }

    function srLabel() {
        if (META.level === "ch") return "SR " + META.sr_number;
        return META.sr_number;
    }

    function uniqueRefs(articles) {
        var refs = [];
        for (var i = 0; i < articles.length; i++) {
            var r = refLabel(articles[i]);
            if (r && refs.indexOf(r) === -1) refs.push(r);
        }
        return refs;
    }

    // --- Format functions ---

    function formatText(articles) {
        var parts = [];
        for (var i = 0; i < articles.length; i++) {
            parts.push((articles[i].innerText || articles[i].textContent).trim());
        }
        return parts.join("\n\n");
    }

    function formatCitation(articles) {
        var refs = uniqueRefs(articles);
        var refStr = refs.join(", ");
        var name = lawName();
        var sr = srLabel();
        return refStr ? refStr + " " + name + " (" + sr + ")" : name + " (" + sr + ")";
    }

    function formatUrl(articles) {
        var base = window.location.origin + (window.__LEX_ROOT_PATH || "") + META.eli_path;
        if (articles.length > 0) {
            var eid = articles[0].getAttribute("data-eid");
            if (eid) base += "#" + eid;
        }
        return base;
    }

    function formatAkn(articles) {
        var lines = [];
        for (var i = 0; i < articles.length; i++) {
            var eid = articles[i].getAttribute("data-eid") || "";
            var label = refLabel(articles[i]);
            var name = lawName();
            var display = label ? label + " " + name : name;
            lines.push('<ref href="' + META.eli_path + "#" + eid + '">' + display + "</ref>");
        }
        return lines.join("\n");
    }

    function formatBibtex(articles) {
        var refs = uniqueRefs(articles);
        var key = META.abbreviation || META.sr_number.replace(/[.\s]/g, "_");
        var note = refs.join(", ");
        var lines = [
            "@legislation{" + key + ",",
            "  title = {" + META.title + "},",
            "  number = {" + srLabel() + "},"
        ];
        if (META.date_document) {
            lines.push("  date = {" + META.date_document + "},");
        }
        lines.push("  url = {" + formatUrl(articles) + "},");
        if (note) {
            lines.push("  note = {" + note + "},");
        }
        lines.push("}");
        return lines.join("\n");
    }

    // --- Clipboard + toast ---

    var toastTimer = null;

    function showToast(msg) {
        if (!toast) return;
        toast.textContent = msg;
        toast.classList.add("show");
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = setTimeout(function() {
            toast.classList.remove("show");
        }, 1500);
    }

    // --- Toolbar buttons ---

    var formatters = {
        text: formatText,
        citation: formatCitation,
        url: formatUrl,
        akn: formatAkn,
        bibtex: formatBibtex
    };

    toolbar.addEventListener("click", function(e) {
        var btn = e.target.closest("button[data-format]");
        if (!btn) return;

        var fn = formatters[btn.getAttribute("data-format")];
        if (!fn || scopeArticles.length === 0) return;

        var text = fn(scopeArticles);
        navigator.clipboard.writeText(text).then(
            function() { showToast("Kopiert"); },
            function() { showToast("Fehler"); }
        );
    });

})();
