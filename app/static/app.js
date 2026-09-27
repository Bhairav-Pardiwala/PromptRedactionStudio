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
    sessionId: null,        // the text tab's session; the document tab keeps its own
    textMapping: {},
    mode: "text",           // "text" | "doc" -- which tab is showing
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
    // The document tab's findings come from its last redaction, not from the prompt.
    if (state.mode === "doc") { return; }
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
      state.textMapping = data.mapping;
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

      var tdAction = document.createElement("td");
      var unredact = document.createElement("button");
      unredact.type = "button";
      unredact.className = "btn btn-ghost btn-tiny";
      unredact.textContent = "↺";
      unredact.title = "Keep this value — stop redacting it";
      unredact.setAttribute("aria-label", "Stop redacting " + token);
      unredact.addEventListener("click", function () { unredactToken(mapping[token]); });
      tdAction.appendChild(unredact);

      tr.appendChild(tdToken);
      tr.appendChild(tdValue);
      tr.appendChild(tdAction);
      tbody.appendChild(tr);
    });
    $("mapping-unredact-hint").hidden = tokens.length === 0;
  }

  /* Stop redacting one value: allow-list it, drop it from any deny-list mark, and redact
     again. The allow-list is Presidio's own filter over every recognizer, so no model,
     mark or document-wide propagation brings it back; deleting it from the Allow-list
     box does. Tokens for every other value keep their numbers. */
  function unredactToken(original) {
    var box = $("allow-list");
    var lines = box.value.split("\n").map(function (line) { return line.trim(); }).filter(Boolean);
    if (lines.indexOf(original) === -1) {
      lines.push(original);
      box.value = lines.join("\n");
    }

    var lower = original.toLowerCase();
    var unmarked = false;
    state.recognizers.forEach(function (recognizer) {
      if (recognizer.kind !== "deny_list" || !recognizer.deny_list) { return; }
      var kept = recognizer.deny_list.filter(function (term) { return term.toLowerCase() !== lower; });
      if (kept.length !== recognizer.deny_list.length) {
        recognizer.deny_list = kept;
        unmarked = true;
      }
    });
    if (unmarked) { renderRecognizers(); }

    toast("“" + original + "” will no longer be redacted");
    if (state.mode === "doc") { redactAll(); }
    else if ($("prompt").value.trim()) { redact(); }
  }

  /* --- restore -------------------------------------------------------------- */

  function restore() {
    var docMode = state.mode === "doc";
    var sessionId = currentSessionId();
    var text = $("reply").value.trim() || (docMode ? "" : $("redacted").value);
    if (!text) { toast("Paste the model's reply first.", true); return; }
    if (!sessionId) { toast(docMode ? "Redact a document first." : "Redact a prompt first.", true); return; }

    $("btn-restore").disabled = true;
    api("/api/restore", { text: text, session_id: sessionId }).then(function (data) {
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
  /* Add terms to the deny-list recognizer for `label`, creating it if needed. Shared by
     the right-click menu and the document tab's suggestions. Returns how many were new. */
  function addDenyTerms(terms, label) {
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

    var added = 0;
    terms.forEach(function (term) {
      var lower = term.toLowerCase();
      var already = (existing.deny_list || []).some(function (marked) {
        return marked.toLowerCase() === lower;    // the matching is case-insensitive too
      });
      if (!already) { existing.deny_list.push(term); added++; }
    });
    if (added) { renderRecognizers(); }
    return added;
  }

  function markTerm(term, label) {
    var entity = slugifyEntity(label);
    if (!addDenyTerms([term], label)) { toast("Already marked as " + entity); return; }

    if (!recognizersRevealed) {
      recognizersRevealed = true;
      reveal($("recognizer-list"));   // show the user where the term landed, once
    }
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

  /* --- dictation (on-device speech) ----------------------------------------- */
  /* Speech is only ever recognised on-device. The ordinary Web Speech API in Chrome and
     Edge streams audio to Google or Microsoft, which would ship spoken PII off the machine
     before it is redacted -- so a browser that cannot recognise locally gets a disabled
     button, never a cloud fallback.

     Chrome has renamed the on-device API once already, so both generations are detected:
     available()/install() with processLocally, and availableOnDevice()/installOnDevice()
     with mode = "ondevice-only". Only final results are written into the prompt; interim
     text is shown beside it, so the debounced analyze() never chases words that are still
     changing. Transcripts are prompt content and are never logged. */

  var dictation = { recognition: null, listening: false, lang: navigator.language || "en-US" };

  function onDeviceApi(SR) {
    if (typeof SR.available === "function") {
      return {
        check: function (lang) { return SR.available({ langs: [lang], processLocally: true }); },
        install: function (lang) { return SR.install({ langs: [lang], processLocally: true }); },
        configure: function (rec) { rec.processLocally = true; }
      };
    }
    if (typeof SR.availableOnDevice === "function") {
      return {
        check: function (lang) { return SR.availableOnDevice(lang); },
        install: function (lang) { return SR.installOnDevice(lang); },
        configure: function (rec) { rec.mode = "ondevice-only"; }
      };
    }
    return null;
  }

  function setDictateButton(listening, busyText) {
    var btn = $("btn-dictate");
    dictation.listening = listening;
    btn.classList.toggle("is-listening", listening);
    btn.setAttribute("aria-pressed", listening ? "true" : "false");
    btn.textContent = busyText || (listening ? "Stop" : "Dictate");
    if (!listening) { $("dictate-interim").hidden = true; }
  }

  function disableDictation(reason) {
    var btn = $("btn-dictate");
    btn.disabled = true;
    btn.title = reason;
  }

  function insertDictated(text) {
    var prompt = $("prompt");
    var start = prompt.selectionStart;
    var end = prompt.selectionEnd;
    text = text.trim();
    if (!text) { return; }
    var before = prompt.value.charAt(start - 1);
    if (start > 0 && before && !/\s/.test(before)) { text = " " + text; }
    prompt.setRangeText(text, start, end, "end");
    onPromptChanged();
  }

  function startDictation(SR, device) {
    var rec = new SR();
    device.configure(rec);
    rec.lang = dictation.lang;
    rec.continuous = true;
    rec.interimResults = true;

    rec.onresult = function (event) {
      var interim = "";
      for (var i = event.resultIndex; i < event.results.length; i++) {
        var result = event.results[i];
        if (result.isFinal) { insertDictated(result[0].transcript); }
        else { interim += result[0].transcript; }
      }
      var box = $("dictate-interim");
      box.textContent = interim ? "Hearing: " + interim : "";
      box.hidden = !interim;
    };
    rec.onerror = function (event) {
      if (event.error === "not-allowed") {
        toast("Microphone permission denied", true);
      } else if (event.error === "language-not-supported" || event.error === "service-not-allowed") {
        toast("On-device speech recognition is unavailable for " + dictation.lang, true);
      } else if (event.error !== "no-speech" && event.error !== "aborted") {
        toast("Dictation stopped: " + event.error, true);
      }
    };
    rec.onend = function () {
      dictation.recognition = null;
      setDictateButton(false);
    };

    dictation.recognition = rec;
    $("prompt").focus();
    rec.start();
    setDictateButton(true);
  }

  function toggleDictation(SR, device) {
    if (dictation.recognition) { dictation.recognition.stop(); return; }
    var btn = $("btn-dictate");
    btn.disabled = true;
    Promise.resolve(device.check(dictation.lang)).then(function (availability) {
      // The earliest builds answered with a plain boolean rather than a status string.
      if (availability === "available" || availability === true) { return true; }
      if (availability === "downloadable" || availability === "downloading") {
        setDictateButton(false, "Downloading model…");
        return device.install(dictation.lang);
      }
      return false;
    }).then(function (ready) {
      btn.disabled = false;
      setDictateButton(false);
      if (!ready) {
        toast("On-device speech recognition is unavailable for " + dictation.lang, true);
        return;
      }
      startDictation(SR, device);
    }).catch(function () {
      btn.disabled = false;
      setDictateButton(false);
      toast("Could not start on-device speech recognition", true);
    });
  }

  function initDictation() {
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { return; }   // no speech API at all: keep the button hidden
    var btn = $("btn-dictate");
    btn.hidden = false;
    var device = onDeviceApi(SR);
    if (!device) {
      disableDictation("On-device speech recognition isn't available in this browser; " +
        "dictation is off so audio never leaves your machine.");
      return;
    }
    btn.title = "Dictate into the prompt, recognised on this device";
    btn.addEventListener("click", function () { toggleDictation(SR, device); });
    document.addEventListener("visibilitychange", function () {
      if (document.hidden && dictation.recognition) { dictation.recognition.stop(); }
    });
  }

  /* --- documents ------------------------------------------------------------ */
  /* Files are read into memory, sent as base64 with the same options a prompt gets, and
     the redacted copies come back as downloads. Every file in a batch joins one server
     session, so one person keeps one token across all of them, and a reply about any of
     the files restores against the whole batch. Files are sent one at a time, in order,
     which keeps the session's token numbering deterministic. Nothing about the files is
     kept in browser storage; sessionStorage holds only which tab was open. */

  var TAB_STORAGE = "prs.tab";
  var docState = {
    files: [],            // {id, file, base64, status, result, error}
    sessionId: null,      // the batch's session, set by the first file redacted
    busy: false,
    nextId: 1,
    merged: { findings: [], mapping: {} }
  };

  function currentSessionId() {
    return state.mode === "doc" ? docState.sessionId : state.sessionId;
  }

  function formatBytes(bytes) {
    if (bytes < 1024) { return bytes + " B"; }
    if (bytes < 1024 * 1024) { return Math.round(bytes / 1024) + " KB"; }
    return (bytes / (1024 * 1024)).toFixed(1).replace(/\.0$/, "") + " MB";
  }

  function extensionOf(name) {
    var dot = name.lastIndexOf(".");
    return dot === -1 ? "" : name.slice(dot).toLowerCase();
  }

  function plural(count, word) {
    return count + " " + word + (count === 1 ? "" : "s");
  }

  function setMode(mode) {
    state.mode = mode;
    var isDoc = mode === "doc";
    $("tab-text").setAttribute("aria-selected", String(!isDoc));
    $("tab-doc").setAttribute("aria-selected", String(isDoc));
    $("pane-text").hidden = isDoc;
    $("pane-doc").hidden = !isDoc;
    $("doc-restore").hidden = !isDoc;
    $("btn-restore").disabled = !currentSessionId();
    $("reply").value = "";
    $("restored").value = "";
    $("restore-summary").textContent = "";
    try { window.sessionStorage.setItem(TAB_STORAGE, mode); } catch (err) { /* not remembered */ }

    if (isDoc) {
      state.findings = docState.merged.findings;
      renderFindings(state.findings);
      renderMapping(docState.merged.mapping);
    } else {
      renderMapping(state.textMapping);
      analyze();
    }
  }

  function readAsBase64(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () {
        var url = String(reader.result);
        resolve(url.slice(url.indexOf(",") + 1));   // strip "data:<type>;base64,"
      };
      reader.onerror = function () { reject(new Error("Could not read " + file.name + ".")); };
      reader.readAsDataURL(file);
    });
  }

  /* Refuse what the server would refuse, before reading a single byte. */
  function checkFile(file) {
    var types = state.config.document_types || [];
    if (types.indexOf(extensionOf(file.name)) === -1) {
      return file.name + ": unsupported file type. Supported: " + types.join(" ");
    }
    if (file.size > state.config.max_document_bytes) {
      return file.name + " is too large. The limit is " + formatBytes(state.config.max_document_bytes) + ".";
    }
    return null;
  }

  /* --- the batch -------------------------------------------------------------- */

  function addDocuments(fileList) {
    var incoming = Array.prototype.slice.call(fileList || []);
    if (!incoming.length) { return; }

    var accepted = [];
    incoming.forEach(function (file) {
      var problem = checkFile(file);
      var duplicate = docState.files.some(function (entry) {
        return entry.file.name === file.name && entry.file.size === file.size;
      });
      if (problem) { toast(problem, true); }
      else if (duplicate) { toast(file.name + " is already in this batch.", true); }
      else { accepted.push(file); }
    });
    $("doc-file").value = "";
    if (!accepted.length) { return; }

    Promise.all(accepted.map(function (file) {
      return readAsBase64(file).then(function (base64) {
        return { id: docState.nextId++, file: file, base64: base64, status: "waiting",
                 result: null, error: null };
      });
    })).then(function (entries) {
      docState.files = docState.files.concat(entries);
      renderBatch();
      pump();
    }).catch(function (err) { toast(err.message, true); });
  }

  /* Redact waiting files one at a time, each joining the batch's session. Files added
     while this runs are picked up by the same loop. */
  function pump() {
    if (docState.busy) { return; }
    var next = docState.files.filter(function (entry) { return entry.status === "waiting"; })[0];
    if (!next) { finishBatch(); return; }

    docState.busy = true;
    next.status = "redacting";
    renderBatch();

    var payload = basePayload();
    delete payload.text;                 // the prompt has nothing to do with this request
    payload.filename = next.file.name;
    payload.content_base64 = next.base64;
    payload.session_id = docState.sessionId;

    api("/api/documents/redact", payload).then(function (data) {
      docState.sessionId = data.session_id;
      next.result = data;
      next.status = "done";
      next.error = null;
    }).catch(function (err) {
      if (docState.sessionId && /expired/.test(err.message)) {
        // The batch's mapping is gone, so the tokens already handed out mean nothing any
        // more. Start a fresh session and redact every file again, in order.
        docState.sessionId = null;
        docState.files.forEach(function (entry) { entry.status = "waiting"; entry.result = null; });
        toast("The batch had expired. Redacting every file again.", true);
        return;
      }
      next.status = "error";
      next.error = err.message;
      toast(next.file.name + ": " + err.message, true);
    }).finally(function () {
      docState.busy = false;
      renderBatch();
      pump();
    });
  }

  /* Re-run every file, e.g. after options change or suggestions are marked. The session
     is kept, so every token already handed out stays exactly as it was. */
  function redactAll() {
    if (!docState.files.length) { toast("Add a file first.", true); return; }
    docState.files.forEach(function (entry) { entry.status = "waiting"; });
    renderBatch();
    pump();
  }

  function finishBatch() {
    var done = docState.files.filter(function (entry) { return entry.result; });
    var findings = [];
    var mapping = {};
    var items = 0;
    done.forEach(function (entry) {
      findings = findings.concat(entry.result.findings);
      Object.keys(entry.result.mapping).forEach(function (token) {
        mapping[token] = entry.result.mapping[token];
      });
      items += entry.result.items.length;
    });
    docState.merged = { findings: findings, mapping: mapping };

    if (state.mode === "doc") {
      state.findings = findings;
      renderFindings(findings);
      renderMapping(mapping);
    }
    $("btn-restore").disabled = !currentSessionId();

    var errors = docState.files.length - done.length;
    status($("doc-status"),
      !docState.files.length ? "No files"
        : errors ? plural(errors, "error")
        : items ? items + " redacted" : "No PII found",
      !docState.files.length ? "muted" : errors ? "err" : items ? "ok" : "muted");

    renderBatchSummary(done, items, mapping);
    renderSuggestions(mergeSuggestions(done));
    $("doc-result").hidden = !done.length;
  }

  function renderBatchSummary(done, items, mapping) {
    var warnings = [];
    done.forEach(function (entry) {
      entry.result.warnings.forEach(function (text) { warnings.push(entry.file.name + ": " + text); });
    });
    var tokens = Object.keys(mapping).length;
    $("doc-summary").textContent = done.length
      ? "✓ " + plural(done.length, "file") + " · " + plural(items, "item") + " redacted · "
        + plural(tokens, "token") + (warnings.length ? " · " + plural(warnings.length, "warning") : "")
      : "";
    $("btn-doc-download-all").disabled = !done.length;

    var box = $("doc-warnings");
    box.innerHTML = "";
    warnings.forEach(function (text) {
      var p = document.createElement("p");
      p.className = "hint warn";
      p.textContent = "⚠ " + text;
      box.appendChild(p);
    });
  }

  var STATUS_LABELS = { waiting: "Waiting", redacting: "Redacting…", error: "Error" };

  function renderBatch() {
    var list = $("doc-files");
    list.innerHTML = "";
    docState.files.forEach(function (entry) {
      // DOM nodes and textContent only: file names come from the user's disk.
      var row = document.createElement("li");
      row.className = "file-row";

      var icon = document.createElement("span");
      icon.className = "file-row-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "📄";

      var name = document.createElement("span");
      name.className = "file-row-name";
      name.textContent = entry.file.name;
      name.title = entry.error || entry.file.name;

      var size = document.createElement("span");
      size.className = "file-row-size";
      size.textContent = formatBytes(entry.file.size);

      var pill = document.createElement("span");
      var count = entry.result ? entry.result.items.length : 0;
      if (entry.status === "done") {
        status(pill, count ? count + " redacted" : "No PII", count ? "ok" : "muted");
      } else {
        status(pill, STATUS_LABELS[entry.status],
          entry.status === "error" ? "err" : entry.status === "redacting" ? "busy" : "muted");
      }

      var download = document.createElement("button");
      download.type = "button";
      download.className = "btn btn-tiny";
      download.textContent = "⬇ Download";
      download.disabled = !entry.result;
      download.addEventListener("click", function () {
        var data = entry.result;
        downloadBlob(new Blob([base64ToBytes(data.content_base64)], { type: data.media_type }), data.filename);
      });

      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-ghost btn-tiny";
      remove.setAttribute("aria-label", "Remove " + entry.file.name + " from the batch");
      remove.textContent = "×";
      remove.disabled = entry.status === "redacting";
      remove.addEventListener("click", function () { removeDocument(entry.id); });

      [icon, name, size, pill, download, remove].forEach(function (node) { row.appendChild(node); });
      list.appendChild(row);
    });

    var hasFiles = docState.files.length > 0;
    $("doc-batch").hidden = !hasFiles;
    $("doc-drop").classList.toggle("dropzone-small", hasFiles);
    $("doc-drop-main").innerHTML = hasFiles
      ? "Add more files: drop them here or <u>browse</u>"
      : "Drop files or <u>browse</u>";
    if (docState.busy) { status($("doc-status"), "Redacting…", "busy"); }
  }

  function removeDocument(id) {
    // Only the file leaves the batch; its tokens stay in the session, so a reply that
    // mentions them still restores, and their numbers are never handed out again.
    docState.files = docState.files.filter(function (entry) { return entry.id !== id; });
    renderBatch();
    finishBatch();
  }

  var newBatchArmed = null;
  function newBatch() {
    // Two clicks rather than confirm(): a browser dialog would block the page.
    var button = $("btn-doc-new");
    if (!newBatchArmed) {
      button.textContent = "Click again to clear";
      newBatchArmed = setTimeout(function () {
        newBatchArmed = null;
        button.textContent = "New batch";
      }, 3000);
      return;
    }
    clearTimeout(newBatchArmed);
    newBatchArmed = null;
    button.textContent = "New batch";

    var old = docState.sessionId;
    docState.files = [];
    docState.sessionId = null;
    renderBatch();
    finishBatch();
    $("btn-restore").disabled = true;
    if (old) {
      api("/api/sessions/" + encodeURIComponent(old), null, "DELETE").catch(function () {
        /* it expires on its own within the hour */
      });
    }
    toast("New batch started");
  }

  /* --- suggestions: words the model left, for the user to redact ------------- */
  /* The server lists what is still in each redacted file, minus articles, auxiliaries,
     prepositions, conjunctions and pronouns; the lists are merged across the batch.
     Ticking words (or typing a phrase) marks them exactly as the right-click menu does --
     a deny-list recognizer per label -- and redacts every file again. */

  var SUGGESTION_LIMIT = 300;

  function mergeSuggestions(done) {
    var byKey = {};
    done.forEach(function (entry) {
      entry.result.suggestions.forEach(function (suggestion) {
        var key = suggestion.term.toLowerCase();
        if (byKey[key]) { byKey[key].count += suggestion.count; }
        else { byKey[key] = { term: suggestion.term, count: suggestion.count, key: key }; }
      });
    });
    // Same order as app/suggestions.py: likely names and IDs, then frequency, then A-Z.
    var merged = Object.keys(byKey).map(function (key) { return byKey[key]; });
    merged.sort(function (a, b) {
      var likelyA = /^[A-Z]|\d/.test(a.term) ? 0 : 1;
      var likelyB = /^[A-Z]|\d/.test(b.term) ? 0 : 1;
      return likelyA - likelyB || b.count - a.count || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0);
    });
    return { suggestions: merged.slice(0, SUGGESTION_LIMIT), suggestions_total: merged.length };
  }

  function renderSuggestions(data) {
    var box = $("doc-suggestions");
    box.innerHTML = "";
    var shown = data.suggestions.length;
    var total = data.suggestions_total;
    $("suggest-counter").textContent = total
      ? "(" + (shown < total ? shown + " of " + total : total) + " words)"
      : "";
    $("suggest-filter").value = "";

    if (!shown) {
      var empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "No words left to review.";
      box.appendChild(empty);
    }
    data.suggestions.forEach(function (suggestion) {
      // DOM nodes and textContent only: these are words from the user's files.
      var chip = document.createElement("label");
      chip.className = "chip";
      chip.dataset.term = suggestion.term.toLowerCase();
      var tick = document.createElement("input");
      tick.type = "checkbox";
      tick.value = suggestion.term;
      tick.addEventListener("change", function () {
        chip.classList.toggle("is-checked", tick.checked);
        updateSuggestButton();
      });
      var word = document.createElement("span");
      word.textContent = suggestion.term;
      chip.appendChild(tick);
      chip.appendChild(word);
      if (suggestion.count > 1) {
        var count = document.createElement("span");
        count.className = "chip-count";
        count.textContent = "×" + suggestion.count;
        chip.appendChild(count);
      }
      box.appendChild(chip);
    });

    var select = $("suggest-entity");
    var previous = select.value;
    select.innerHTML = "";
    var entities = markableEntities();
    entities.forEach(function (entity) {
      var option = document.createElement("option");
      option.value = entity;
      option.textContent = "as " + entity;
      select.appendChild(option);
    });
    select.value = entities.indexOf(previous) !== -1 ? previous
      : entities.indexOf("PERSON") !== -1 ? "PERSON" : entities[0];
    updateSuggestButton();
  }

  function selectedSuggestions() {
    var terms = Array.prototype.map.call(
      $("doc-suggestions").querySelectorAll("input:checked"),
      function (input) { return input.value; });
    var custom = $("suggest-custom").value.trim();
    if (custom) { terms.push(custom); }
    return terms;
  }

  function updateSuggestButton() {
    var count = selectedSuggestions().length;
    var button = $("btn-suggest-redact");
    button.disabled = count === 0;
    button.textContent = count > 1 ? "Redact " + count + " selected" : "Redact selected";
  }

  function filterSuggestions() {
    var needle = $("suggest-filter").value.trim().toLowerCase();
    $("doc-suggestions").querySelectorAll(".chip").forEach(function (chip) {
      chip.hidden = needle !== "" && chip.dataset.term.indexOf(needle) === -1;
    });
  }

  function redactSuggestions() {
    var terms = selectedSuggestions();
    if (!terms.length) { return; }
    var label = $("suggest-entity").value;
    var added = addDenyTerms(terms, label);
    $("suggest-custom").value = "";
    if (!added) { toast("Already marked as " + slugifyEntity(label)); return; }
    toast("Added " + plural(added, "term") + " as " + slugifyEntity(label) + " — redacting every file again");
    redactAll();
  }

  /* --- downloads -------------------------------------------------------------- */

  function base64ToBytes(base64) {
    var binary = atob(base64);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) { bytes[i] = binary.charCodeAt(i); }
    return bytes;
  }

  function downloadBlob(blob, filename) {
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Revoked once the browser has started the download, so the file does not linger.
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  /* A minimal zip writer: entries stored uncompressed, which every unzip tool reads.
     Office files are zips already, so compressing again would gain almost nothing, and
     this keeps the page free of a zip library and the server free of the files. */
  var CRC_TABLE = null;

  function crc32(bytes) {
    if (!CRC_TABLE) {
      CRC_TABLE = new Uint32Array(256);
      for (var n = 0; n < 256; n++) {
        var c = n;
        for (var k = 0; k < 8; k++) { c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; }
        CRC_TABLE[n] = c >>> 0;
      }
    }
    var crc = 0xFFFFFFFF;
    for (var i = 0; i < bytes.length; i++) { crc = CRC_TABLE[(crc ^ bytes[i]) & 0xFF] ^ (crc >>> 8); }
    return (crc ^ 0xFFFFFFFF) >>> 0;
  }

  function uniqueNames(names) {
    var seen = {};
    return names.map(function (name) {
      var key = name.toLowerCase();
      if (!seen[key]) { seen[key] = 1; return name; }
      seen[key] += 1;
      var dot = name.lastIndexOf(".");
      var stem = dot > 0 ? name.slice(0, dot) : name;
      var ext = dot > 0 ? name.slice(dot) : "";
      return stem + " (" + seen[key] + ")" + ext;
    });
  }

  function zipStore(entries) {
    var encoder = new TextEncoder();
    var now = new Date();
    var time = (now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1);
    var date = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
    var files = [];
    var central = [];
    var offset = 0;
    var centralSize = 0;

    entries.forEach(function (entry) {
      var name = encoder.encode(entry.name);
      var crc = crc32(entry.bytes);
      var size = entry.bytes.length;

      var local = new DataView(new ArrayBuffer(30));
      local.setUint32(0, 0x04034b50, true);
      local.setUint16(4, 20, true);          // version needed
      local.setUint16(6, 0x0800, true);      // names are UTF-8
      local.setUint16(8, 0, true);           // stored
      local.setUint16(10, time, true);
      local.setUint16(12, date, true);
      local.setUint32(14, crc, true);
      local.setUint32(18, size, true);
      local.setUint32(22, size, true);
      local.setUint16(26, name.length, true);
      local.setUint16(28, 0, true);
      files.push(local.buffer, name, entry.bytes);

      var header = new DataView(new ArrayBuffer(46));
      header.setUint32(0, 0x02014b50, true);
      header.setUint16(4, 20, true);
      header.setUint16(6, 20, true);
      header.setUint16(8, 0x0800, true);
      header.setUint16(10, 0, true);
      header.setUint16(12, time, true);
      header.setUint16(14, date, true);
      header.setUint32(16, crc, true);
      header.setUint32(20, size, true);
      header.setUint32(24, size, true);
      header.setUint16(28, name.length, true);
      header.setUint32(42, offset, true);    // everything else in the header stays zero
      central.push(header.buffer, name);

      offset += 30 + name.length + size;
      centralSize += 46 + name.length;
    });

    var end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true);
    end.setUint16(8, entries.length, true);
    end.setUint16(10, entries.length, true);
    end.setUint32(12, centralSize, true);
    end.setUint32(16, offset, true);

    return new Blob(files.concat(central, [end.buffer]), { type: "application/zip" });
  }

  function downloadAll() {
    var done = docState.files.filter(function (entry) { return entry.result; });
    if (!done.length) { return; }
    if (done.length === 1) {
      var only = done[0].result;
      downloadBlob(new Blob([base64ToBytes(only.content_base64)], { type: only.media_type }), only.filename);
      return;
    }
    var names = uniqueNames(done.map(function (entry) { return entry.result.filename; }));
    downloadBlob(zipStore(done.map(function (entry, i) {
      return { name: names[i], bytes: base64ToBytes(entry.result.content_base64) };
    })), "redacted-documents.zip");
  }

  /* --- restore files ---------------------------------------------------------- */

  function restoreDocuments(fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length) { return; }
    if (!docState.sessionId) { toast("Redact a document first.", true); return; }
    for (var i = 0; i < files.length; i++) {
      var problem = checkFile(files[i]);
      if (problem) { toast(problem, true); $("doc-restore-file").value = ""; return; }
    }

    var restored = [];
    var tokens = 0;
    var chain = Promise.resolve();
    files.forEach(function (file) {
      chain = chain.then(function () {
        return readAsBase64(file);
      }).then(function (base64) {
        return api("/api/documents/restore", {
          filename: file.name,
          content_base64: base64,
          session_id: docState.sessionId
        });
      }).then(function (data) {
        restored.push(data);
        tokens += data.tokens_restored;
      });
    });

    chain.then(function () {
      if (restored.length === 1) {
        var only = restored[0];
        downloadBlob(new Blob([base64ToBytes(only.content_base64)], { type: only.media_type }), only.filename);
      } else {
        var names = uniqueNames(restored.map(function (data) { return data.filename; }));
        downloadBlob(zipStore(restored.map(function (data, i) {
          return { name: names[i], bytes: base64ToBytes(data.content_base64) };
        })), "restored-documents.zip");
      }
      $("restore-summary").textContent = plural(restored.length, "file") + " restored · "
        + plural(tokens, "token") + " put back";
      toast(restored.length === 1 ? "Restored copy downloaded" : "Restored copies downloaded as a zip");
    }).catch(function (err) {
      toast(err.message, true);
    }).finally(function () {
      $("doc-restore-file").value = "";
    });
  }

  /* A drop zone is a <label> around a hidden file input, so a click browses. This adds
     keyboard access and drag-and-drop. */
  function bindDropZone(zone, input, onFiles) {
    zone.tabIndex = 0;
    zone.setAttribute("role", "button");
    zone.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); input.click(); }
    });
    input.addEventListener("change", function () { onFiles(input.files); });
    zone.addEventListener("dragover", function (event) {
      event.preventDefault();
      zone.classList.add("is-dragover");
    });
    zone.addEventListener("dragleave", function () { zone.classList.remove("is-dragover"); });
    zone.addEventListener("drop", function (event) {
      event.preventDefault();
      zone.classList.remove("is-dragover");
      onFiles(event.dataTransfer.files);
    });
  }

  function initDocuments(config) {
    $("tab-text").addEventListener("click", function () { setMode("text"); });
    $("tab-doc").addEventListener("click", function () { setMode("doc"); });

    // The page is static and always current, but the server may be an older process
    // (started before document support, without --reload). Say so, rather than showing
    // "up to NaN MB" and refusing every file as unsupported.
    if (!config.document_types || !config.max_document_bytes) {
      $("doc-drop").hidden = true;
      $("doc-status").textContent = "Unavailable";
      var note = document.createElement("p");
      note.className = "hint warn";
      note.textContent = "This server does not support documents yet. It is probably still "
        + "running an older version: restart it and reload this page.";
      $("doc-drop").parentNode.insertBefore(note, $("doc-drop"));
      return;
    }

    var types = config.document_types;
    $("doc-types").textContent = types.join(" ") + " · up to " + formatBytes(config.max_document_bytes) + " each";
    $("doc-file").accept = types.join(",");
    $("doc-restore-file").accept = types.concat([".txt"]).join(",");

    bindDropZone($("doc-drop"), $("doc-file"), addDocuments);
    bindDropZone($("doc-restore-drop"), $("doc-restore-file"), restoreDocuments);

    // A file dropped just beside a drop zone would otherwise replace the whole page. Only
    // files: dragging text into the prompt must keep working.
    ["dragover", "drop"].forEach(function (name) {
      window.addEventListener(name, function (event) {
        var types = event.dataTransfer && event.dataTransfer.types;
        if (types && Array.prototype.indexOf.call(types, "Files") !== -1) { event.preventDefault(); }
      });
    });

    $("suggest-filter").addEventListener("input", filterSuggestions);
    $("suggest-custom").addEventListener("input", updateSuggestButton);
    $("suggest-custom").addEventListener("keydown", function (event) {
      if (event.key === "Enter") { event.preventDefault(); redactSuggestions(); }
    });
    $("btn-suggest-redact").addEventListener("click", redactSuggestions);
    $("btn-doc-again").addEventListener("click", redactAll);
    $("btn-doc-download-all").addEventListener("click", downloadAll);
    $("btn-doc-new").addEventListener("click", newBatch);

    var saved = null;
    try { saved = window.sessionStorage.getItem(TAB_STORAGE); } catch (err) { /* default tab */ }
    if (saved === "doc") { setMode("doc"); }
  }

  /* --- wiring --------------------------------------------------------------- */

  function onPromptChanged() {
    renderHighlights(state.findings);   // repaint immediately so text stays legible
    scheduleAnalyze();
  }

  function bindEvents() {
    var prompt = $("prompt");
    prompt.addEventListener("input", onPromptChanged);
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
      // Both tabs' sessions: the text prompt's and the document batch's. The batch goes
      // too -- a file added after its mapping is gone would restart at <PERSON_1> and
      // no longer match the files already redacted.
      function forgetLocally() {
        state.sessionId = null;
        state.textMapping = {};
        docState.sessionId = null;
        docState.files = [];
        renderBatch();
        finishBatch();
        $("btn-restore").disabled = true;
        renderMapping({});
      }

      var ids = [state.sessionId, docState.sessionId].filter(Boolean);
      if (!ids.length) {
        forgetLocally();
        toast("Nothing stored to clear");
        return;
      }

      Promise.all(ids.map(function (id) {
        return api("/api/sessions/" + encodeURIComponent(id), null, "DELETE");
      })).then(function (responses) {
        var cleared = responses.reduce(function (sum, data) { return sum + data.cleared; }, 0);
        forgetLocally();
        toast("Cleared " + cleared + " stored mapping(s)");
      }).catch(function (err) { toast(err.message, true); });
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
    initDictation();
    initDocuments(config);
    $("threshold-value").value = state.threshold.toFixed(2);
  }).catch(function (err) {
    status($("engine-status"), "Failed to load", "err");
    toast("Could not load configuration: " + err.message, true);
  });
})();
