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

  /* --- wiring --------------------------------------------------------------- */

  function bindEvents() {
    var prompt = $("prompt");
    prompt.addEventListener("input", function () {
      renderHighlights(state.findings);   // repaint immediately so text stays legible
      scheduleAnalyze();
    });
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
