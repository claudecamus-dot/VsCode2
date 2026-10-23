"""Harnais partagé : charge le <script> inline COMPLET d'un écran sous node.

Pourquoi : le JS d'un template est UNE IIFE. Un bloc ajouté après le `})();`
final tourne dans la portée globale, où `transcriptSoFar`, `MISSION_ID`, etc.
n'existent pas -> ReferenceError silencieux. Une comparaison de positions de
texte ne le voit pas ; seule l'exécution du script entier le voit.

Le « global » du bac à sable ne résout QUE les noms d'hôte d'une liste blanche
(`_HOTE`) : tout autre identifiant libre lève un vrai ReferenceError, comme dans
le navigateur. Contrairement au bac de `test_tranches_et_reprise_execution.py`
(Proxy `has: true`, qui rend la portée invisible), celui-ci la fait respecter.

Aucun accès base.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
REC_DRAFT = RACINE / "app" / "static" / "rec_draft.js"

_HOTE = [
    "document", "window", "navigator", "console", "JSON", "Math", "Promise", "Array",
    "String", "Object", "Date", "Error", "TypeError", "setTimeout", "clearTimeout",
    "setInterval", "clearInterval", "parseInt", "parseFloat", "isNaN", "Number",
    "Boolean", "RegExp", "encodeURIComponent", "decodeURIComponent", "undefined",
    "recFetch", "confirm", "alert", "URL", "Blob", "FormData", "MediaRecorder",
    "AudioContext", "Set", "Map", "Uint8Array", "Float32Array", "crypto", "location",
    "history", "requestAnimationFrame", "cancelAnimationFrame", "performance",
    "AbortController", "Infinity", "NaN", "Symbol", "WeakMap", "Event", "CustomEvent",
    "fetch", "File", "localStorage", "Intl", "encodeURI", "btoa", "atob",
    "TextEncoder", "TextDecoder", "queueMicrotask", "getComputedStyle", "matchMedia",
    "webkitAudioContext", "Notification", "screen", "i2dAudioSource", "RETRY_PLAN",
    "recRetry", "recAudioSource", "recTabs", "MutationObserver", "recDraft",
]

_PRELUDE = r"""
const fs = require('fs'), vm = require('vm');
const HOTE = new Set(%(hote)s);
const store = {};
const elements = {};
const doublure = new Proxy(function () {}, {
  get: function (t, p) {
    if (p === Symbol.toPrimitive || p === 'toString' || p === 'valueOf') return function () { return ''; };
    if (p === 'length') return 0;
    return doublure;
  },
  set: function () { return true; }, apply: function () { return doublure; },
  construct: function () { return doublure; }, has: function () { return true; }
});
function nouvelElement(id) {
  const base = { id: id, textContent: '', hidden: true, value: '', disabled: false,
                 _on: {}, className: '', children: [] };
  base.addEventListener = function (ev, fn) { base._on[ev] = fn; };
  base.classList = { toggle: function () {}, add: function () {}, remove: function () {}, contains: function () { return false; } };
  base.querySelector = function () { return null; };
  base.appendChild = function () { return doublure; };
  return new Proxy(base, { get: function (t, p) { return (p in t) ? t[p] : doublure; },
                           set: function (t, p, v) { t[p] = v; return true; } });
}
function el(id) { if (!elements[id]) elements[id] = nouvelElement(id); return elements[id]; }
const g = {
  document: { getElementById: function (id) { return el(id); },
              querySelector: function () { return doublure; },
              querySelectorAll: function () { return []; },
              createElement: function () { return nouvelElement('cree'); },
              addEventListener: function () {}, body: doublure, documentElement: doublure },
  window: { localStorage: {
      getItem: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
      setItem: function (k, v) { store[k] = String(v); },
      removeItem: function (k) { delete store[k]; } },
    addEventListener: function () {}, location: doublure },
  module: undefined, console: console, JSON: JSON, Math: Math, Promise: Promise,
  Array: Array, String: String, Object: Object, Date: Date, Error: Error, TypeError: TypeError,
  Number: Number, Boolean: Boolean, RegExp: RegExp, parseInt: parseInt, parseFloat: parseFloat,
  isNaN: isNaN, Infinity: Infinity, NaN: NaN, Symbol: Symbol, Set: Set, Map: Map,
  encodeURIComponent: encodeURIComponent, decodeURIComponent: decodeURIComponent,
  FormData: function () { this.append = function () {}; },
  setTimeout: function () { return 1; }, clearTimeout: function () {},
  setInterval: function () { return 1; }, clearInterval: function () {},
  confirm: function () { return true; }, alert: function () {},
  recFetch: function () { return Promise.resolve({ ok: true, json: function () { return Promise.resolve({}); } }); }
};
g.window.MediaRecorder = doublure;
const bac = new Proxy(g, {
  has: function (t, p) { return typeof p === 'symbol' || HOTE.has(p) || (p in t); },
  get: function (t, p) { if (p in t) return t[p]; return HOTE.has(p) ? doublure : undefined; },
  set: function (t, p, v) { t[p] = v; return true; }
});
vm.createContext(bac);
function jouer(code) { return vm.runInContext(code, bac); }
let erreur = null;
%(corps)s
"""


def script_inline(ecran: Path) -> str:
    """Le dernier <script> inline de l'écran, variables Jinja neutralisées."""
    html = ecran.read_text(encoding="utf-8")
    debut, fin = html.rindex("<script>"), html.rindex("</script>")
    js = html[debut + len("<script>"):fin]
    js = re.sub(r"\{\{\s*mission\.id\s*\}\}", "19", js)
    return re.sub(r"\{\{[^}]*\|\s*tojson\s*\}\}", '""', js)


def jouer_ecran(ecran: Path, avant: str = "", apres: str = "") -> dict:
    """Charge `rec_draft.js`, joue `avant`, exécute le script ENTIER de l'écran
    (erreur capturée dans `erreur`), puis `apres`. Le scénario imprime un JSON
    via `console.log(JSON.stringify(...))`."""
    if shutil.which("node") is None:
        pytest.fail("node est requis : ces tests sont la preuve par exécution de l'ordre du JS.")
    corps = (
        "jouer(%s);\n%s\ntry { jouer(%s); } catch (e) { erreur = e.name + ': ' + e.message; }\n%s\n"
        % (json.dumps(REC_DRAFT.read_text(encoding="utf-8")), avant,
           json.dumps(script_inline(ecran)), apres)
    )
    script = _PRELUDE % {"hote": json.dumps(_HOTE), "corps": corps}
    fd, chemin = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        res = subprocess.run(["node", chemin], capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
    finally:
        os.unlink(chemin)
    assert res.returncode == 0, res.stderr[:3000]
    return json.loads(res.stdout.strip().splitlines()[-1])
