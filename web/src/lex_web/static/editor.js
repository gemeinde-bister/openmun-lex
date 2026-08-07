/**
 * lex editor boot — wires the shared openmun-editor to the law view.
 *
 * Schema, ProseMirror vendor bundles and initEditor() live in the
 * openmun-editor package, mounted at /static/editor/. Imports are
 * relative so the app works behind reverse-proxy prefixes.
 */

import { initEditor } from "./editor/core/editor.js";
import { aknSchema } from "./editor/core/schema-akn.js";

function boot() {
    const container = document.getElementById("law-body");
    if (!container) return;

    const editable = container.dataset.editable === "true";
    if (!editable) return;

    const eliPath = container.dataset.eli;
    if (!eliPath) return;

    initEditor(container, {
        schema: aknSchema,
        autosaveKey: `lex:autosave:${eliPath}`,
    });
}

// Wait for DOM
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
} else {
    boot();
}
