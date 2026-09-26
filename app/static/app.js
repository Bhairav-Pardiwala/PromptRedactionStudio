/* Prompt Redaction Studio -- frontend logic.
 *
 * State lives in `state`; every control writes into it and calls scheduleAnalyze() or
 * redact(). The API is the source of truth for which engines, entities and operators
 * exist, so the options panel is rendered from /api/config rather than hardcoded.
 */

(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  var state = {
    config: null,
    engine: null,
    threshold: 0.35,
    defaultOperator: { type: "placeholder", params: {} },
    perEntity: {},          // entity type -> {type, params}
    entities: null,         // Set of enabled entity types, or null for "all"
    recognizers: [],
    sessionId: null,
    apiKey: null,           // only ever set when the instance reports auth_required
    findings: [],
    sort: { key: "score", dir: -1 },
    seq: 0                  // guards against out-of-order analyze responses
  };

  /* --- entity colours ------------------------------------------------------ */
  /* A fixed hue per entity type, derived from its name so colours stay stable
     across reloads and new entity types get one automatically. */

  function hueFor(name) {
    var hash = 0;
    for (var i = 0; i < name.length; i++) {
      hash = (hash * 31 + name.charCodeAt(i)) % 360;
    }
    return hash;
  }

  function entityStyle(name) {
    var hue = hueFor(name);
    var dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    return dark
      ? "--ent-bg: hsl(" + hue + " 55% 26%); --ent-line: hsl(" + hue + " 60% 52%); --ent-ink: hsl(" + hue + " 70% 82%)"
      : "--ent-bg: hsl(" + hue + " 85% 88%); --ent-line: hsl(" + hue + " 60% 55%); --ent-ink: hsl(" + hue + " 55% 28%)";
  }

  /* --- small helpers ------------------------------------------------------- */

  function escapeHtml(text) {
    return text.replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  var toastTimer = null;
  function toast(message, isError) {
    var el = $("toast");
    el.textContent = message;
    el.className = "toast" + (isError ? " error" : "");
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.hidden = true; }, isError ? 6000 : 2600);
  }

  function status(el, text, kind) {
    el.textContent = text;
    el.className = "pill pill-" + (kind || "muted");
  }

  /* --- instance key --------------------------------------------------------- */
  /* Only in play when the instance sets REDACTION_API_KEY. It lives in sessionStorage
     so it is gone when the tab closes, and every access is guarded because a browser
     with site data blocked throws on the property itself rather than returning null. */

  var KEY_STORAGE = "prs.apiKey";

  function readStoredKey() {
    try { return window.sessionStorage.getItem(KEY_STORAGE) || null; }
    catch (err) { return null; }
  }

  function storeKey(value) {
    try {
      if (value) { window.sessionStorage.setItem(KEY_STORAGE, value); }
      else { window.sessionStorage.removeItem(KEY_STORAGE); }
    } catch (err) { /* the key still works for this page; it just will not persist */ }
  }

  function showAuthLock(message) {
    var lock = $("auth-lock");
    lock.hidden = false;
    lock.className = "auth-lock" + (message ? " is-error" : "");
    if (message) { toast(message, true); }
    $("auth-key").focus();
  }

  function saveKeyFromInput() {
    var input = $("auth-key");
    var value = input.value.trim();
    if (!value) { showAuthLock("Enter the key for this instance."); return; }

    state.apiKey = value;
    storeKey(value);
    input.value = "";
    $("auth-lock").hidden = true;
    $("auth-lock").className = "auth-lock";
    toast("Key saved for this tab");
    // Prove it immediately rather than leaving the user to discover it at the next action.
    analyze();
  }

  function api(path, body, method) {
    var headers = { "Content-Type": "application/json" };
    if (state.apiKey) { headers["X-Redaction-Key"] = state.apiKey; }

    return fetch(path, {
      method: method || (body ? "POST" : "GET"),
      headers: headers,
      body: body ? JSON.stringify(body) : undefined
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) {
          if (response.status === 401) {
            // Wrong key, or none yet. Ask for it rather than reporting a bare 401.
            showAuthLock("This instance needs a key. Enter it to continue.");
          }
          throw new Error(data && data.detail ? data.detail : "Request failed (" + response.status + ")");
        }
        return data;
      }).catch(function (err) {
        if (err instanceof SyntaxError) { throw new Error("Server returned an invalid response."); }
        throw err;
      });
    });
  }

  /* --- request payload ----------------------------------------------------- */

  function allowListTerms() {
    return $("allow-list").value.split("\n")
      .map(function (line) { return line.trim(); })
      .filter(Boolean);
  }

  function basePayload() {
    var perEntity = {};
    Object.keys(state.perEntity).forEach(function (entity) {
      var spec = state.perEntity[entity];
      if (spec && spec.type) { perEntity[entity] = spec; }
    });

    return {
      text: $("prompt").value,
      engine: state.engine,
      language: "en",
      entities: state.entities ? Array.from(state.entities) : null,
      score_threshold: state.threshold,
      allow_list: allowListTerms(),
      allow_list_match: $("allow-list-fuzzy").checked ? "fuzzy" : "exact",
      custom_recognizers: state.recognizers.filter(function (r) { return r.name; }),
      detect_organization: $("detect-org").checked,
      return_explanations: $("explanations").checked,
      default_operator: state.defaultOperator,
      per_entity_operators: perEntity
    };
  }

  /* --- analyze (live, as you type) ----------------------------------------- */

  var analyzeTimer = null;
  function scheduleAnalyze() {
    clearTimeout(analyzeTimer);
    analyzeTimer = setTimeout(analyze, 400);
  }

  function analyze() {
    var text = $("prompt").value;
    if (!text.trim()) {
      state.findings = [];
      renderHighlights([]);
      renderFindings([]);
      status($("detect-status"), "Ready", "muted");
      return;
    }

    var ticket = ++state.seq;
    status($("detect-status"), "Analyzing…", "busy");

    api("/api/analyze", basePayload()).then(function (data) {
      if (ticket !== state.seq) { return; }   // a newer request already landed
      state.findings = data.findings;
      renderHighlights(data.findings);
      renderFindings(data.findings);
      status($("detect-status"),
        data.count === 0 ? "No PII found" : data.count + " found",
        data.count === 0 ? "muted" : "ok");
    }).catch(function (err) {
      if (ticket !== state.seq) { return; }
      status($("detect-status"), "Error", "err");
      toast(err.message, true);
    });
  }

  /* --- highlight underlay --------------------------------------------------- */

  /* Overlapping spans are resolved by keeping the higher score, mirroring how
     Presidio itself dedupes before anonymizing. */
  function dedupe(findings) {
    var sorted = findings.slice().sort(function (a, b) {
      return a.start - b.start || b.score - a.score || (b.end - b.start) - (a.end - a.start);
    });
    var kept = [];
    var cursor = -1;
    sorted.forEach(function (finding) {
      if (finding.start >= cursor) { kept.push(finding); cursor = finding.end; }
    });
    return kept;
  }

  function renderHighlights(findings) {
    var text = $("prompt").value;
    var layer = $("highlights");
    var spans = dedupe(findings);
    var html = "";
    var cursor = 0;

    spans.forEach(function (span) {
      html += escapeHtml(text.slice(cursor, span.start));
      html += '<mark style="' + entityStyle(span.entity_type) + '">'
           +  escapeHtml(text.slice(span.start, span.end))
           +  "</mark>";
      cursor = span.end;
    });
    // Trailing newline keeps the underlay's height in step with the textarea.
    html += escapeHtml(text.slice(cursor)) + "\n";

    layer.innerHTML = html;
    layer.scrollTop = $("prompt").scrollTop;
  }

  /* --- findings table ------------------------------------------------------- */

  function renderFindings(findings) {
    var table = $("findings-table");
    var empty = $("findings-empty");
    var tbody = table.querySelector("tbody");
    $("findings-counter").textContent = findings.length ? "(" + findings.length + ")" : "";

    if (!findings.length) {
      table.hidden = true;
      empty.hidden = false;
      return;
    }
    table.hidden = false;
    empty.hidden = true;

    var key = state.sort.key;
    var dir = state.sort.dir;
    var rows = findings.slice().sort(function (a, b) {
      var av = a[key], bv = b[key];
      if (typeof av === "string") { return av.localeCompare(bv) * dir; }
      return (av - bv) * dir;
    });

    tbody.innerHTML = "";
    rows.forEach(function (finding) {
      var tr = document.createElement("tr");

      var tdEntity = document.createElement("td");
      var tag = document.createElement("span");
      tag.className = "tag";
      tag.setAttribute("style", entityStyle(finding.entity_type));
      tag.textContent = finding.entity_type;
      tdEntity.appendChild(tag);

      var tdText = document.createElement("td");
      tdText.className = "value mono";
      tdText.textContent = finding.text;

      var tdScore = document.createElement("td");
      tdScore.className = "mono";
      tdScore.textContent = finding.score.toFixed(2);
      var bar = document.createElement("span");
      bar.className = "score-bar";
      bar.style.width = Math.round(finding.score * 100) + "%";
      tdScore.appendChild(bar);

      tr.appendChild(tdEntity);
      tr.appendChild(tdText);
      tr.appendChild(tdScore);
      tr.title = buildTooltip(finding);
      tbody.appendChild(tr);
    });
  }

  function buildTooltip(finding) {
    var lines = [finding.entity_type + "  •  score " + finding.score.toFixed(2)];
    if (finding.recognizer) { lines.push("Recognizer: " + finding.recognizer); }
    var ex = finding.explanation;
    if (ex) {
      if (ex.pattern_name) { lines.push("Pattern: " + ex.pattern_name); }
      if (ex.pattern) { lines.push("Regex: " + ex.pattern); }
      if (ex.original_score != null && ex.original_score !== finding.score) {
        lines.push("Original score: " + Number(ex.original_score).toFixed(2));
      }
      if (ex.score_context_improvement) {
        lines.push("Context boost: +" + Number(ex.score_context_improvement).toFixed(2)
          + (ex.supportive_context_word ? " (\"" + ex.supportive_context_word + "\")" : ""));
      }
      if (ex.validation_result != null) { lines.push("Checksum validated: " + ex.validation_result); }
      if (ex.textual_explanation) { lines.push(ex.textual_explanation); }
    }
    return lines.join("\n");
  }

  /* --- redact --------------------------------------------------------------- */

  function redact() {
    var text = $("prompt").value;
    if (!text.trim()) { toast("Type or paste a prompt first.", true); return; }

    status($("detect-status"), "Redacting…", "busy");
    $("btn-redact").disabled = true;

    api("/api/redact", basePayload()).then(function (data) {
      state.sessionId = data.session_id;
      state.findings = data.findings;
      $("redacted").value = data.redacted_text;
      $("btn-copy").disabled = !data.redacted_text;
      $("btn-restore").disabled = !data.session_id;

      renderHighlights(data.findings);
      renderFindings(data.findings);
      renderMapping(data.mapping);

      var count = data.findings.length;
      var reversibleCount = Object.keys(data.mapping).length;
      $("redact-summary").textContent = count
        ? count + " entit" + (count === 1 ? "y" : "ies") + " redacted • "
          + reversibleCount + " reversible token" + (reversibleCount === 1 ? "" : "s")
        : "Nothing detected — the prompt was left unchanged.";

      status($("detect-status"), count ? count + " redacted" : "No PII found", count ? "ok" : "muted");
      toast(count ? "Redacted " + count + " entities" : "No PII detected");
    }).catch(function (err) {
      status($("detect-status"), "Error", "err");
      toast(err.message, true);
    }).finally(function () {
      $("btn-redact").disabled = false;
    });
  }

  function renderMapping(mapping) {
    var tokens = Object.keys(mapping);
    var table = $("mapping-table");
    var tbody = table.querySelector("tbody");

    $("mapping-empty").hidden = tokens.length > 0;
    table.hidden = tokens.length === 0;
    $("mapping-warning").hidden = tokens.length === 0;

    tbody.innerHTML = "";
    tokens.forEach(function (token) {
      var tr = document.createElement("tr");
      var tdToken = document.createElement("td");
      tdToken.className = "mono";
      var entity = token.slice(1, token.lastIndexOf("_"));
      var tag = document.createElement("span");
      tag.className = "tag";
      tag.setAttribute("style", entityStyle(entity));
      tag.textContent = token;
      tdToken.appendChild(tag);

      var tdValue = document.createElement("td");
      tdValue.className = "value mono";
      tdValue.textContent = mapping[token];

      tr.appendChild(tdToken);
      tr.appendChild(tdValue);
      tbody.appendChild(tr);
    });
  }

  /* --- restore -------------------------------------------------------------- */

  function restore() {
    var text = $("reply").value.trim() || $("redacted").value;
    if (!text) { toast("Paste the model's reply first.", true); return; }
    if (!state.sessionId) { toast("Redact a prompt first.", true); return; }

    $("btn-restore").disabled = true;
    api("/api/restore", { text: text, session_id: state.sessionId }).then(function (data) {
      $("restored").value = data.restored_text;
      var summary = data.tokens_restored + " of " + data.tokens_total + " tokens restored";
      if (data.not_found.length) {
        summary += " • not present in the text: " + data.not_found.join(", ");
      }
      $("restore-summary").textContent = summary;
      toast("Restored " + data.tokens_restored + " tokens");
    }).catch(function (err) {
      toast(err.message, true);
    }).finally(function () {
      $("btn-restore").disabled = false;
    });
  }

  /* --- options panel rendering --------------------------------------------- */

  function renderEngines(config) {
    var select = $("engine");
    select.innerHTML = "";
    Object.keys(config.engines).forEach(function (key) {
      var info = config.engines[key];
      var option = document.createElement("option");
      option.value = key;
      option.textContent = info.label + (info.available ? "" : "  (not installed)");
      option.disabled = !info.available;
      select.appendChild(option);
    });

    var preferred = config.engines[config.default_engine] && config.engines[config.default_engine].available
      ? config.default_engine
      : Object.keys(config.engines).filter(function (k) { return config.engines[k].available; })[0];

    if (!preferred) {
      status($("engine-status"), "No model installed", "err");
      $("engine-hint").textContent = "Run setup.ps1 to download the spaCy models.";
      return;
    }
    select.value = preferred;
    state.engine = preferred;
    updateEngineHint();
  }

  function updateEngineHint() {
    var info = state.config.engines[state.engine];
    if (!info) { return; }
    $("engine-hint").textContent = info.available
      ? info.description
      : info.description + "  Install: " + info.install_hint;
    status($("engine-status"), info.label, info.available ? "ok" : "err");
  }

  function operatorByName(name) {
    return state.config.operators.filter(function (op) { return op.name === name; })[0];
  }

  function renderOperatorSelect(select, selected) {
    select.innerHTML = "";
    state.config.operators.forEach(function (op) {
      var option = document.createElement("option");
      option.value = op.name;
      option.textContent = op.label;
      select.appendChild(option);
    });
    select.value = selected;
  }

  /* Renders the parameter inputs for whichever operator is selected. */
  function renderOperatorParams(container, opName, params, onChange) {
    var spec = operatorByName(opName);
    container.innerHTML = "";
    if (!spec || !spec.params.length) { return; }

    spec.params.forEach(function (param) {
      var wrapper = document.createElement("div");

      if (param.type === "bool") {
        wrapper.className = "checkbox";
        var checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = params[param.name] != null ? !!params[param.name] : !!param.default;
        checkbox.addEventListener("change", function () {
          params[param.name] = checkbox.checked;
          onChange();
        });
        var text = document.createElement("span");
        text.textContent = param.label;
        wrapper.appendChild(checkbox);
        wrapper.appendChild(text);
        container.appendChild(wrapper);
        return;
      }

      var label = document.createElement("label");
      label.textContent = param.label;
      wrapper.appendChild(label);

      var input;
      if (param.type === "choice") {
        input = document.createElement("select");
        param.choices.forEach(function (choice) {
          var option = document.createElement("option");
          option.value = choice;
          option.textContent = choice;
          input.appendChild(option);
        });
        input.value = params[param.name] || param.default;
      } else {
        input = document.createElement("input");
        input.type = param.type === "int" ? "number" : "text";
        input.value = params[param.name] != null ? params[param.name] : (param.default || "");
        if (param.placeholder) { input.placeholder = param.placeholder; }
      }

      input.addEventListener("input", function () {
        params[param.name] = param.type === "int" ? Number(input.value) : input.value;
        onChange();
      });
      input.addEventListener("change", function () {
        params[param.name] = param.type === "int" ? Number(input.value) : input.value;
        onChange();
      });

      wrapper.appendChild(input);
      container.appendChild(wrapper);
    });
  }

  function refreshDefaultOperator() {
    var name = $("default-operator").value;
    if (state.defaultOperator.type !== name) {
      state.defaultOperator = { type: name, params: {} };
    }
    var spec = operatorByName(name);
    $("default-operator-hint").textContent = spec
      ? spec.description + (spec.reversible ? "" : "  This cannot be reversed.")
      : "";
    renderOperatorParams($("default-operator-params"), name, state.defaultOperator.params, function () {});
  }

  function renderEntityGroups(config) {
    var host = $("entity-groups");
    host.innerHTML = "";
    // First load starts from Presidio's own default entity set rather than all 78
    // types, so the findings list is useful instead of noisy.
    var defaults = new Set(config.default_entities || config.all_entities || []);

    config.entity_groups.forEach(function (group) {
      var section = document.createElement("div");
      section.className = "entity-group";
      var heading = document.createElement("h3");
      heading.textContent = group.name;
      heading.title = group.description;
      section.appendChild(heading);

      group.entities.forEach(function (entity) {
        var row = document.createElement("div");
        row.className = "entity-row";

        var label = document.createElement("label");
        var checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = entity;
        checkbox.checked = state.entities ? state.entities.has(entity) : defaults.has(entity);
        checkbox.className = "entity-toggle";
        checkbox.addEventListener("change", function () {
          syncEntitiesFromCheckboxes();
          scheduleAnalyze();
        });
        var name = document.createElement("span");
        name.textContent = entity;
        name.title = entity;
        label.appendChild(checkbox);
        label.appendChild(name);

        // Per-entity operator override. "Default" means "inherit the global choice".
        var override = document.createElement("select");
        var inherit = document.createElement("option");
        inherit.value = "";
        inherit.textContent = "default";
        override.appendChild(inherit);
        config.operators.forEach(function (op) {
          var option = document.createElement("option");
          option.value = op.name;
          option.textContent = op.label;
          override.appendChild(option);
        });
        override.title = "Anonymization for " + entity;
        override.addEventListener("change", function () {
          if (override.value) {
            state.perEntity[entity] = { type: override.value, params: defaultParamsFor(override.value) };
            override.classList.add("overridden");
          } else {
            delete state.perEntity[entity];
            override.classList.remove("overridden");
          }
        });

        row.appendChild(label);
        row.appendChild(override);
        section.appendChild(row);
      });

      host.appendChild(section);
    });

    syncEntitiesFromCheckboxes();
  }

  function defaultParamsFor(opName) {
    var spec = operatorByName(opName);
    var params = {};
    if (spec) {
      spec.params.forEach(function (param) {
        if (param.default !== undefined && param.default !== "") { params[param.name] = param.default; }
      });
    }
    return params;
  }

  function syncEntitiesFromCheckboxes() {
    var boxes = Array.from(document.querySelectorAll(".entity-toggle"));
    var checked = boxes.filter(function (box) { return box.checked; });
    state.entities = checked.length === boxes.length ? null : new Set(checked.map(function (b) { return b.value; }));
    $("entity-counter").textContent = "(" + checked.length + "/" + boxes.length + ")";
  }

  function setAllEntities(on) {
    document.querySelectorAll(".entity-toggle").forEach(function (box) { box.checked = on; });
    syncEntitiesFromCheckboxes();
    scheduleAnalyze();
  }

  /* --- custom recognizers --------------------------------------------------- */

  function renderRecognizers() {
    var host = $("recognizer-list");
    host.innerHTML = "";
    $("recognizer-counter").textContent = state.recognizers.length
      ? "(" + state.recognizers.length + ")" : "";

    state.recognizers.forEach(function (recognizer, index) {
      var card = document.createElement("div");
      card.className = "recognizer";

      var head = document.createElement("div");
      head.className = "recognizer-head";
      var title = document.createElement("strong");
      title.textContent = "Recognizer " + (index + 1);
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-tiny btn-danger";
      remove.textContent = "Remove";
      remove.addEventListener("click", function () {
        state.recognizers.splice(index, 1);
        renderRecognizers();
        scheduleAnalyze();
      });
      head.appendChild(title);
      head.appendChild(remove);
      card.appendChild(head);

      var nameInput = document.createElement("input");
      nameInput.type = "text";
      nameInput.placeholder = "Entity name, e.g. Project code";
      nameInput.value = recognizer.name;
      nameInput.addEventListener("input", function () {
        recognizer.name = nameInput.value;
        // A card created by right-click pins `entity` so the term keeps the label it was
        // marked with. Renaming the card by hand hands that back to the name, which is how
        // a hand-built recognizer has always behaved.
        delete recognizer.entity;
        scheduleAnalyze();
      });
      card.appendChild(nameInput);

      var kindSelect = document.createElement("select");
      [["regex", "Regex pattern"], ["deny_list", "Deny-list of words"]].forEach(function (pair) {
        var option = document.createElement("option");
        option.value = pair[0];
        option.textContent = pair[1];
        kindSelect.appendChild(option);
      });
      kindSelect.value = recognizer.kind;
      kindSelect.addEventListener("change", function () {
        recognizer.kind = kindSelect.value;
        renderRecognizers();
        scheduleAnalyze();
      });
      card.appendChild(kindSelect);

      var error = document.createElement("p");
      error.className = "error";

      if (recognizer.kind === "regex") {
        var patternInput = document.createElement("input");
        patternInput.type = "text";
        patternInput.placeholder = "ACME-\\d{5}";
        patternInput.value = recognizer.pattern || "";
        patternInput.addEventListener("input", function () {
          recognizer.pattern = patternInput.value;
          try {
            new RegExp(patternInput.value);
            patternInput.classList.remove("invalid");
            error.textContent = "";
            scheduleAnalyze();
          } catch (err) {
            patternInput.classList.add("invalid");
            error.textContent = "Invalid regex: " + err.message;
          }
        });
        card.appendChild(patternInput);
      } else {
        var denyInput = document.createElement("textarea");
        denyInput.rows = 2;
        denyInput.placeholder = "One term per line";
        denyInput.value = (recognizer.deny_list || []).join("\n");
        denyInput.addEventListener("input", function () {
          recognizer.deny_list = denyInput.value.split("\n")
            .map(function (line) { return line.trim(); })
            .filter(Boolean);
          scheduleAnalyze();
        });
        card.appendChild(denyInput);
      }

      var scoreInput = document.createElement("input");
      scoreInput.type = "number";
      scoreInput.min = "0.05";
      scoreInput.max = "1";
      scoreInput.step = "0.05";
      scoreInput.value = recognizer.score;
      scoreInput.title = "Confidence score for matches";
      scoreInput.addEventListener("input", function () {
        recognizer.score = Number(scoreInput.value);
        scheduleAnalyze();
      });
      card.appendChild(scoreInput);
      card.appendChild(error);

      host.appendChild(card);
    });
  }

  /* --- mark a selection for redaction -------------------------------------- */
  /* Right-clicking a selection in the prompt offers to redact it. The mechanism is the
     deny-list custom recognizer that already exists, so a marked term needs no special
     casing anywhere downstream -- and because redaction.analyze() folds custom entity
     types back into the requested list, a term marked as PERSON is still found even when
     PERSON itself is unchecked. */

  var MAX_MARK_CHARS = 200;
  var MARK_MENU_ENTITIES = 6;   // past this the menu is longer than it is useful

  var pendingSelection = null;  // snapshot taken at right-click; see openMarkMenu
  var menuOpen = false;
  var recognizersRevealed = false;

  /* Mirrors _slugify_entity in recognizers.py, so state.recognizers[].entity is exactly
     the type the server will report back -- which keeps the menu's colour, the findings
     tag and the token all in agreement from the first render. */
  function slugifyEntity(name) {
    var slug = name.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toUpperCase();
    return slug || "CUSTOM";
  }

  /* Presidio compiles a deny-list to (?:^|(?<=\W))(terms)(?:(?=\W)|$), so a term that
     starts or ends mid-word can never match. Refusing up front beats accepting a mark
     that then silently fails to redact anything. */
  function isWholeWord(text, start, end) {
    var before = start > 0 ? text.charAt(start - 1) : "";
    var after = end < text.length ? text.charAt(end) : "";
    return (!before || /\W/.test(before)) && (!after || /\W/.test(after));
  }

  /* Right-clicking something Presidio already found should act on the whole finding, not
     on the fragment the pointer happened to land in -- otherwise "Never redact" over a
     detected email would allow-list half of it. So the span is grown outward to cover
     every finding it touches, repeatedly, because findings overlap: a click on the URL
     that Presidio sees inside an address pulls in the wider EMAIL_ADDRESS with it.

     Findings can be a beat behind the text, since analyze() is debounced. A finding whose
     recorded text no longer sits at its offsets is stale, and ignored rather than trusted. */
  function expandToFindings(text, start, end) {
    var span = { start: start, end: end };

    for (var pass = 0; pass < 5; pass++) {
      var grew = false;
      state.findings.forEach(function (finding) {
        if (text.slice(finding.start, finding.end) !== finding.text) { return; }

        // A collapsed caret counts as inside a finding it merely touches; a real selection
        // has to genuinely overlap one.
        var touches = span.start === span.end
          ? finding.start <= span.start && span.start <= finding.end
          : finding.start < span.end && span.start < finding.end;
        if (!touches) { return; }

        if (finding.start < span.start) { span.start = finding.start; grew = true; }
        if (finding.end > span.end) { span.end = finding.end; grew = true; }
      });
      if (!grew) { break; }
    }
    return span;
  }

  /* The snapshot is what every menu action works from. A textarea stops painting its
     selection once focus moves to a menu button, so reading selectionStart later would be
     unreliable -- the captured offsets are not. */
  function captureSelection() {
    var prompt = $("prompt");
    var start = prompt.selectionStart;
    var end = prompt.selectionEnd;
    if (start == null || end == null) { return null; }

    var text = prompt.value;
    // Chrome moves the caret to the click before firing contextmenu, so a collapsed caret
    // is a usable position: right-clicking a highlighted value with nothing selected marks
    // that whole value. A caret outside every finding still means "no selection".
    var span = expandToFindings(text, start, end);
    if (span.start === span.end) { return null; }

    var raw = text.slice(span.start, span.end);
    var trimmed = raw.trim();
    if (!trimmed) { return null; }
    var lead = raw.length - raw.replace(/^\s+/, "").length;

    return {
      text: trimmed,
      start: span.start + lead,
      end: span.start + lead + trimmed.length
    };
  }

  /* The types a person actually reaches for when a detector missed something. This only
     ranks the menu -- what exists still comes from /api/config, and anything outside this
     list is still reachable through "Redact as...". Without a ranking the menu would follow
     default_entities, which is alphabetical, and offer CRYPTO and IBAN_CODE while burying
     PERSON. */
  var MARK_PREFERRED = [
    "PERSON", "ORGANIZATION", "LOCATION", "EMAIL_ADDRESS", "PHONE_NUMBER",
    "DATE_TIME", "URL", "CREDIT_CARD", "US_SSN"
  ];

  /* Offered from every supported type rather than the checked ones, because marking works
     regardless of the checkboxes -- turning PERSON detection off for being noisy is a good
     reason to mark one name by hand, not a reason to hide the label. CUSTOM leads: it is
     the answer when none of the built-in types fit. */
  function markableEntities() {
    var config = state.config || {};
    var supported = config.all_entities || config.default_entities || [];
    var offered = ["CUSTOM"];

    MARK_PREFERRED.concat(supported).forEach(function (entity) {
      if (entity !== "CUSTOM" && supported.indexOf(entity) !== -1
          && offered.indexOf(entity) === -1 && offered.length <= MARK_MENU_ENTITIES) {
        offered.push(entity);
      }
    });
    return offered;
  }

  function reveal(element) {
    var group = element.closest("details");
    if (group) { group.open = true; }
  }

  /* --- the menu itself ------------------------------------------------------ */

  function menuItems() {
    return Array.prototype.slice.call($("mark-menu").querySelectorAll(".ctx-menu-item"));
  }

  /* Focus carries the highlight, so Enter and Space activate an item natively and the
     keyboard path needs no extra key handling. preventScroll matters: focusing an item
     must not scroll the page, because the scroll listener below closes the menu. */
  function setActiveItem(index) {
    var items = menuItems();
    if (!items.length) { return; }
    var wrapped = ((index % items.length) + items.length) % items.length;
    items.forEach(function (item, i) { item.classList.toggle("is-active", i === wrapped); });
    items[wrapped].focus({ preventScroll: true });
  }

  function onDocPointerDown(event) {
    if (!$("mark-menu").contains(event.target)) { closeMarkMenu(false); }
  }

  function onMenuDismiss() { closeMarkMenu(false); }

  function onMenuKeydown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeMarkMenu(true);
      return;
    }
    // The "Redact as..." row swaps in a text input; leave its own key handling alone.
    if (document.activeElement && document.activeElement.tagName === "INPUT") { return; }

    var items = menuItems();
    if (!items.length) { return; }
    var current = items.indexOf(document.activeElement);

    if (event.key === "ArrowDown") { event.preventDefault(); setActiveItem(current + 1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); setActiveItem(current < 0 ? -1 : current - 1); }
    else if (event.key === "Home") { event.preventDefault(); setActiveItem(0); }
    else if (event.key === "End") { event.preventDefault(); setActiveItem(items.length - 1); }
  }

  function closeMarkMenu(restoreSelection) {
    if (!menuOpen) { return; }
    menuOpen = false;

    var menu = $("mark-menu");
    menu.hidden = true;
    menu.innerHTML = "";

    document.removeEventListener("pointerdown", onDocPointerDown, true);
    document.removeEventListener("keydown", onMenuKeydown, true);
    document.removeEventListener("scroll", onMenuDismiss, true);
    window.removeEventListener("resize", onMenuDismiss);
    window.removeEventListener("blur", onMenuDismiss);

    // Escape and a completed action put the user back where they were; clicking away does
    // not, because they have chosen to be somewhere else.
    if (restoreSelection && pendingSelection) {
      var prompt = $("prompt");
      prompt.focus();
      prompt.setSelectionRange(pendingSelection.start, pendingSelection.end);
    }
  }

  function menuSeparator() {
    var separator = document.createElement("div");
    separator.className = "ctx-menu-sep";
    return separator;
  }

  function menuButton(label) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "ctx-menu-item";
    button.setAttribute("role", "menuitem");
    var text = document.createElement("span");
    text.textContent = label;
    button.appendChild(text);
    return button;
  }

  function markMenuItem(entity, selection) {
    var button = menuButton("Redact as");
    var tag = document.createElement("span");
    tag.className = "tag";
    tag.setAttribute("style", entityStyle(entity));
    tag.textContent = entity;
    button.appendChild(tag);
    button.addEventListener("click", function () {
      closeMarkMenu(true);
      markTerm(selection.text, entity);
    });
    return button;
  }

  /* A native prompt() would block the page, so the label is typed inline instead. */
  function showLabelInput(selection) {
    var menu = $("mark-menu");
    menu.innerHTML = "";

    var head = document.createElement("div");
    head.className = "ctx-menu-head";
    head.textContent = "Label for “" + selection.text + "”";
    head.title = selection.text;
    menu.appendChild(head);

    var row = document.createElement("div");
    row.className = "ctx-menu-label";

    var input = document.createElement("input");
    input.type = "text";
    input.placeholder = "e.g. Project code";
    input.spellcheck = false;

    var apply = document.createElement("button");
    apply.type = "button";
    apply.className = "btn btn-tiny btn-primary";
    apply.textContent = "Mark";

    function commit() {
      var label = input.value.trim();
      if (!label) { input.focus(); return; }
      closeMarkMenu(true);
      markTerm(selection.text, label);
    }

    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") { event.preventDefault(); commit(); }
    });
    apply.addEventListener("click", commit);

    row.appendChild(input);
    row.appendChild(apply);
    menu.appendChild(row);
    input.focus({ preventScroll: true });
  }

  function buildMarkMenu(selection) {
    var menu = $("mark-menu");
    menu.innerHTML = "";

    var head = document.createElement("div");
    head.className = "ctx-menu-head";
    head.textContent = "“" + selection.text + "”";
    head.title = selection.text;
    menu.appendChild(head);
    menu.appendChild(menuSeparator());

    markableEntities().forEach(function (entity) {
      menu.appendChild(markMenuItem(entity, selection));
    });

    var custom = menuButton("Redact as…");
    custom.addEventListener("click", function () { showLabelInput(selection); });
    menu.appendChild(custom);

    menu.appendChild(menuSeparator());

    var allow = menuButton("Never redact (allow-list)");
    allow.addEventListener("click", function () {
      closeMarkMenu(true);
      addToAllowList(selection.text);
    });
    menu.appendChild(allow);

    return menu;
  }

  function positionMenu(menu, x, y) {
    // Unhide at the origin first: the menu has to be laid out before it can be measured.
    menu.style.left = "0px";
    menu.style.top = "0px";
    menu.hidden = false;

    var width = menu.offsetWidth;
    var height = menu.offsetHeight;
    var left = Math.max(6, Math.min(x + 2, window.innerWidth - width - 6));
    var top = y + 2;
    if (top + height > window.innerHeight - 6) { top = Math.max(6, y - height - 2); }

    menu.style.left = left + "px";
    menu.style.top = top + "px";
  }

  function openMarkMenu(event) {
    var prompt = $("prompt");
    var selection = captureSelection();
    // Nothing selected: leave the browser's own menu alone so copy and paste still work.
    if (!selection) { return; }

    event.preventDefault();

    if (selection.text.length > MAX_MARK_CHARS) {
      toast("That selection is too long to mark — pick a word or short phrase.", true);
      return;
    }
    if (!isWholeWord(prompt.value, selection.start, selection.end)) {
      toast("Select whole words — a deny-list only matches on word boundaries.", true);
      return;
    }

    pendingSelection = selection;
    // Paint the span the menu is about to act on. It is usually what the user selected,
    // but a click inside a finding will have grown outward to the whole value, and that
    // should be visible before they pick a label rather than a surprise afterwards.
    prompt.setSelectionRange(selection.start, selection.end);

    var menu = buildMarkMenu(selection);

    // Shift+F10 and the Windows menu key report no usable coordinates; anchor to the editor.
    var x = event.clientX;
    var y = event.clientY;
    if ((!x && !y) || x < 0 || y < 0) {
      var box = prompt.getBoundingClientRect();
      x = box.left + 12;
      y = box.top + 12;
    }
    positionMenu(menu, x, y);

    menuOpen = true;
    setActiveItem(0);

    document.addEventListener("pointerdown", onDocPointerDown, true);
    document.addEventListener("keydown", onMenuKeydown, true);
    document.addEventListener("scroll", onMenuDismiss, true);
    window.addEventListener("resize", onMenuDismiss);
    window.addEventListener("blur", onMenuDismiss);
  }

  /* --- the two actions ------------------------------------------------------ */

  /* One auto-managed deny-list recognizer per label, so marking three names as PERSON
     produces one card holding three terms rather than three cards. */
  function markTerm(term, label) {
    var entity = slugifyEntity(label);
    var existing = null;
    state.recognizers.forEach(function (recognizer) {
      if (recognizer.kind === "deny_list" && recognizer.entity === entity) { existing = recognizer; }
    });

    if (!existing) {
      // score 1 clears any threshold the slider can produce, and wins Presidio's conflict
      // resolution against a lower-scoring detection over the same span.
      existing = { name: entity, entity: entity, kind: "deny_list", deny_list: [], score: 1 };
      state.recognizers.push(existing);
    }

    var lower = term.toLowerCase();
    var already = (existing.deny_list || []).some(function (marked) {
      return marked.toLowerCase() === lower;    // the matching is case-insensitive too
    });
    if (already) { toast("Already marked as " + entity); return; }

    existing.deny_list.push(term);
    if (!recognizersRevealed) {
      recognizersRevealed = true;
      reveal($("recognizer-list"));   // show the user where the term landed, once
    }
    renderRecognizers();
    analyze();
    toast("Marked “" + term + "” as " + entity);
  }

  function addToAllowList(term) {
    var box = $("allow-list");
    var lines = box.value.split("\n").map(function (line) { return line.trim(); });
    if (lines.indexOf(term) !== -1) { toast("Already in the allow-list"); return; }

    var body = box.value.replace(/\s+$/, "");
    box.value = body ? body + "\n" + term : term;
    reveal(box);
    analyze();
    toast("“" + term + "” will never be redacted");
  }

  /* --- wiring --------------------------------------------------------------- */

  function bindEvents() {
    var prompt = $("prompt");
    prompt.addEventListener("input", function () {
      renderHighlights(state.findings);   // repaint immediately so text stays legible
      scheduleAnalyze();
    });
    prompt.addEventListener("contextmenu", openMarkMenu);
    prompt.addEventListener("scroll", function () {
      $("highlights").scrollTop = prompt.scrollTop;
      $("highlights").scrollLeft = prompt.scrollLeft;
    });

    $("engine").addEventListener("change", function () {
      state.engine = $("engine").value;
      updateEngineHint();
      analyze();
    });

    $("threshold").addEventListener("input", function () {
      state.threshold = Number($("threshold").value);
      $("threshold-value").value = state.threshold.toFixed(2);
      scheduleAnalyze();
    });

    $("default-operator").addEventListener("change", refreshDefaultOperator);

    $("allow-list").addEventListener("input", scheduleAnalyze);
    $("allow-list-fuzzy").addEventListener("change", scheduleAnalyze);
    $("detect-org").addEventListener("change", analyze);
    $("explanations").addEventListener("change", scheduleAnalyze);

    $("btn-redact").addEventListener("click", redact);
    $("btn-restore").addEventListener("click", restore);

    $("btn-copy").addEventListener("click", function () {
      var text = $("redacted").value;
      if (!text) { return; }
      navigator.clipboard.writeText(text).then(function () {
        toast("Redacted prompt copied");
      }).catch(function () {
        $("redacted").select();
        document.execCommand("copy");
        toast("Redacted prompt copied");
      });
    });

    $("btn-sample").addEventListener("click", function () {
      $("prompt").value = state.config.sample_prompt;
      analyze();
    });

    $("btn-add-recognizer").addEventListener("click", function () {
      state.recognizers.push({ name: "", kind: "regex", pattern: "", deny_list: [], score: 0.7 });
      renderRecognizers();
    });

    $("btn-clear-sessions").addEventListener("click", function () {
      // Drops this page's own mapping, not the whole instance's. Clearing every
      // client's mappings is an operator action and needs the admin key.
      function forgetLocally() {
        state.sessionId = null;
        $("btn-restore").disabled = true;
        renderMapping({});
      }

      if (!state.sessionId) {
        forgetLocally();
        toast("Nothing stored to clear");
        return;
      }

      api("/api/sessions/" + encodeURIComponent(state.sessionId), null, "DELETE")
        .then(function (data) {
          forgetLocally();
          toast("Cleared " + data.cleared + " stored mapping(s)");
        })
        .catch(function (err) { toast(err.message, true); });
    });

    $("btn-auth-save").addEventListener("click", saveKeyFromInput);
    $("auth-key").addEventListener("keydown", function (event) {
      if (event.key === "Enter") { saveKeyFromInput(); }
    });

    document.querySelectorAll("[data-select-all]").forEach(function (button) {
      button.addEventListener("click", function () {
        setAllEntities(button.dataset.selectAll === "1");
      });
    });

    $("btn-defaults").addEventListener("click", function () {
      var defaults = state.config.default_entities || [];
      document.querySelectorAll(".entity-toggle").forEach(function (box) {
        box.checked = defaults.indexOf(box.value) !== -1;
      });
      syncEntitiesFromCheckboxes();
      scheduleAnalyze();
    });

    document.querySelectorAll("#findings-table th[data-sort]").forEach(function (th) {
      th.addEventListener("click", function () {
        var key = th.dataset.sort;
        state.sort = { key: key, dir: state.sort.key === key ? -state.sort.dir : -1 };
        renderFindings(state.findings);
      });
    });
  }

  /* --- boot ----------------------------------------------------------------- */

  state.apiKey = readStoredKey();

  api("/api/config").then(function (config) {
    state.config = config;
    // /api/config is open precisely so this can happen: the page has to load before it
    // can find out a key is required. A local install reports false and shows nothing.
    if (config.auth_required && !state.apiKey) {
      showAuthLock(null);
    }
    renderEngines(config);
    renderOperatorSelect($("default-operator"), config.default_operator);
    refreshDefaultOperator();
    renderEntityGroups(config);
    renderRecognizers();
    bindEvents();
    $("threshold-value").value = state.threshold.toFixed(2);
  }).catch(function (err) {
    status($("engine-status"), "Failed to load", "err");
    toast("Could not load configuration: " + err.message, true);
  });
})();
