"use strict";
const $ = (id) => document.getElementById(id);
const fields = {
  source_id: "Issue ID *",
  arrival_date: "Reported date *",
  closure_date: "Closure date",
  component: "Component",
  severity: "Severity / priority",
  status: "Status",
  found_in_version: "Found-in version",
};
const state = {
  upload: null,
  inspection: null,
  profile: null,
  revision: 0,
  loadedName: "",
  run: null,
  resolutions: {},
  offset: 0,
  total: 0,
  user: null,
  step: 1,
};
const colors = [
  "#127e76",
  "#ce7538",
  "#7156a6",
  "#3d7fbd",
  "#94a72d",
  "#ac4d7d",
  "#345363",
  "#bb9b37",
  "#507b4b",
];
const lines = (value) =>
  value
    .split("\n")
    .map((v) => v.trim())
    .filter(Boolean);
function node(tag, text, className) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (className) n.className = className;
  return n;
}
function clear(id) {
  const n = typeof id === "string" ? $(id) : id;
  n.replaceChildren();
  return n;
}
function show(id, visible = true) {
  $(id).classList.toggle("hidden", !visible);
}
function message(text, good = false) {
  $("notice").textContent = text;
  $("notice").className = "notice" + (good ? " good" : "");
}
function option(value, text) {
  const o = node("option", text);
  o.value = value;
  return o;
}
async function api(path, data, options = {}) {
  const init = { ...options };
  if (data !== undefined) {
    init.method = "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(data);
  }
  const r = await fetch(path, init);
  const payload = await r.json();
  if (!r.ok) {
    if (r.status === 401) {
      show("loginScreen");
      show("workspace", false);
    }
    throw new Error(payload.error || payload.detail || "Request failed");
  }
  return payload;
}
async function task(action) {
  show("busy");
  show("notice", false);
  try {
    return await action();
  } catch (error) {
    message(error.message);
  } finally {
    show("busy", false);
  }
}
function bind(id, action) {
  $(id).addEventListener("click", () => task(action));
}
function step(n) {
  if (n > 1 && !state.inspection) {
    message("Choose and inspect a source file first.");
    return;
  }
  if (n === 4 && !state.run) {
    message("Prepare the data to see record decisions and exports.");
    return;
  }
  state.step = n;
  show("workflow");
  show("historySection", false);
  $("breadcrumb").textContent = "New preparation";
  document
    .querySelectorAll(".step")
    .forEach((el) => el.classList.toggle("hidden", el.id !== "step" + n));
  document
    .querySelectorAll("[data-step]")
    .forEach((el) =>
      el.classList.toggle("selected", Number(el.dataset.step) === n),
    );
  $("newPreparation").classList.add("active");
  $("historyButton").classList.remove("active");
}
document
  .querySelectorAll("[data-step],[data-go]")
  .forEach((el) =>
    el.addEventListener("click", () =>
      step(Number(el.dataset.step || el.dataset.go)),
    ),
  );
function sourceOptions() {
  return {
    sheet: $("sheet").value || null,
    header_row: Number($("headerRow").value),
    encoding: $("encoding").value,
    delimiter: $("delimiter").value,
  };
}
function metrics(id, items) {
  const box = clear(id);
  items.forEach(([label, value, hint, decision]) => {
    const el = node(
      decision !== undefined ? "button" : "div",
      undefined,
      "metric",
    );
    el.append(
      node("span", label),
      node("strong", Number(value).toLocaleString()),
      node("small", hint),
    );
    if (decision !== undefined)
      el.onclick = () =>
        task(async () => {
          $("decisionFilter").value = decision;
          state.offset = 0;
          await loadRecords();
          $("recordTable").scrollIntoView({ behavior: "smooth" });
        });
    box.append(el);
  });
}
function table(headers, rows) {
  const t = node("table"),
    head = node("thead"),
    tr = node("tr"),
    body = node("tbody");
  headers.forEach((h) => tr.append(node("th", h)));
  head.append(tr);
  rows.forEach((values) => {
    const r = node("tr");
    values.forEach((v) => {
      const cell = node("td");
      cell.append(
        v instanceof Node ? v : document.createTextNode(String(v ?? "")),
      );
      r.append(cell);
    });
    body.append(r);
  });
  t.append(head, body);
  return t;
}
async function upload(file) {
  if (!file) return;
  if (file.size > 25 * 1024 * 1024)
    throw new Error("Choose a file up to 25 MiB.");
  const result = await api("/api/uploads", undefined, {
    method: "POST",
    headers: {
      "Content-Type": "application/octet-stream",
      "X-Filename": encodeURIComponent(file.name),
    },
    body: file,
  });
  state.upload = result.upload_id;
  state.run = null;
  state.resolutions = {};
  state.profile = null;
  state.revision = 0;
  state.loadedName = "";
  $("fileLabel").textContent = file.name;
  clear("sheet");
  $("headerRow").value = 1;
  $("encoding").value = "utf-8-sig";
  $("delimiter").value = "auto";
  show("sourceControls");
  await inspect();
  step(1);
}
async function inspect() {
  const options = sourceOptions();
  const d = await api("/api/inspect", { upload_id: state.upload, options });
  state.inspection = d;
  clear("sheet");
  (d.sheets.length ? d.sheets : [""]).forEach((s) =>
    $("sheet").append(option(s, s || "CSV · no worksheets")),
  );
  $("sheet").value = d.sheet || "";
  if (!state.profile) state.profile = structuredClone(d.suggested_profile);
  state.profile.source_options = sourceOptions();
  metrics("sourceStats", [
    ["Source rows", d.report.rows_read, "Nonempty records"],
    ["Columns", d.columns.length, "Available for mapping"],
    ["Worksheets", d.sheets.length || 1, "Selected source"],
    [
      "Header row",
      d.header_row,
      d.valid_headers ? "Valid column names" : "Choose unique column names",
    ],
  ]);
  const max = Math.max(0, ...d.preview.map((r) => r.values.length));
  clear("sourcePreview").append(
    table(
      ["Row", ...Array.from({ length: max }, (_, i) => String(i + 1))],
      d.preview.map((r) => [r.row, ...r.values]),
    ),
  );
  $("sourceHint").textContent = d.duplicate_headers?.length
    ? "Repeated headers preserved with column numbers: " +
      d.duplicate_headers.join(", ") +
      ". Confirm each date column in the mapping step."
    : d.valid_headers
      ? "Source inspected. Review the mappings next."
      : "Select the row containing the column headers.";
  $("toMapping").disabled = !d.valid_headers;
  show("inspection");
  renderProfile();
}
function renderProfile() {
  const p = state.profile,
    d = state.inspection;
  if (!p || !d) return;
  $("profileName").value = p.profile_id || "customer";
  $("dateFormat").value = p.date_format || "iso";
  $("blankValues").value = (
    p.normalization?.blank_values || ["", "none", "NO_FW_Version"]
  )
    .filter(Boolean)
    .join("\n");
  $("profileRevision").textContent = state.revision
    ? `Loaded revision ${state.revision}. Saving creates a new version.`
    : "New profile · the exact rules are also retained with each run.";
  const box = clear("mappings");
  Object.entries(fields).forEach(([key, label]) => {
    const row = node("div", undefined, "mapping-row");
    const l = node("label", label);
    l.htmlFor = "map-" + key;
    const select = node("select");
    select.id = "map-" + key;
    select.dataset.map = key;
    select.append(option("", "Not supplied"));
    d.columns.forEach((c) => select.append(option(c, c)));
    select.value = p.source_columns?.[key] || "";
    const sample = node("small");
    const update = () => {
      p.source_columns[key] = select.value;
      const f = d.report.fields[select.value];
      sample.textContent = f
        ? `${f.empty || 0} missing · ${f.unique || 0} unique`
        : "";
    };
    select.onchange = () => {
      update();
      resetConfirmations();
      if (key === "status" || key === "found_in_version") renderRules();
    };
    update();
    row.append(l, node("span", "→"), select, sample);
    box.append(row);
  });
  $("duplicateMode").value = p.inclusion_rules?.duplicate_policy || "review";
  $("versionMode").value =
    p.inclusion_rules?.version_policy?.mode || "review_required";
  $("acceptedVersions").value = (
    p.inclusion_rules?.version_policy?.accepted_values || []
  ).join("\n");
  document
    .querySelectorAll("[data-confirm]")
    .forEach(
      (el) => (el.checked = Boolean(p.confirmations?.[el.dataset.confirm])),
    );
  clear("milestones");
  Object.entries(p.release_dates || {}).forEach(([release, date]) =>
    addMilestone(release, date),
  );
  renderRules();
}
function resetConfirmations() {
  document
    .querySelectorAll("[data-confirm]")
    .forEach((el) => (el.checked = false));
}
function valuesFor(key) {
  const column = $("map-" + key)?.value;
  return state.inspection?.report.fields[column]?.values || [];
}
function renderRules() {
  const p = state.profile,
    rules = p.inclusion_rules || {},
    vp = rules.version_policy || {};
  const box = clear("statusOptions"),
    statuses = valuesFor("status");
  if (!statuses.length)
    box.append(
      node("p", "No status field mapped; all records are considered.", "hint"),
    );
  statuses.forEach(({ value, count }) => {
    const label = node("label");
    const input = node("input");
    input.type = "checkbox";
    input.dataset.status = value;
    input.checked =
      !rules.statuses?.length ||
      rules.statuses.some((v) => v.toLowerCase() === value.toLowerCase());
    label.append(
      input,
      document.createTextNode(`${value || "(blank)"} · ${count}`),
    );
    box.append(label);
  });
  const rows = clear("versionRows"),
    values = valuesFor("found_in_version");
  const aliases = vp.aliases || {},
    excluded = vp.excluded_values || [];
  values.forEach(({ value, count }) => {
    const tr = node("tr");
    tr.dataset.version = value;
    const raw = node("td", value || "(blank)"),
      num = node("td", count),
      cell = node("td"),
      exc = node("td");
    const input = node("input");
    input.setAttribute("aria-label", `Release for ${value || "blank version"}`);
    input.value =
      aliases[value] ||
      Object.entries(aliases).find(
        ([k]) => k.toLowerCase() === value.toLowerCase(),
      )?.[1] ||
      "";
    input.placeholder = "Needs a release mapping";
    input.dataset.alias = value;
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.dataset.exclude = value;
    checkbox.checked = excluded.some(
      (v) => v.toLowerCase() === value.toLowerCase(),
    );
    checkbox.setAttribute("aria-label", `Exclude ${value || "blank version"}`);
    input.disabled = checkbox.checked;
    checkbox.onchange = () => {
      input.disabled = checkbox.checked;
    };
    cell.append(input);
    exc.append(checkbox);
    tr.append(raw, num, cell, exc);
    rows.append(tr);
  });
  const info =
    state.inspection?.report.fields[$("map-found_in_version")?.value];
  $("versionHint").textContent = info?.values_truncated
    ? "Showing the 1,000 most frequent values. Additional values remain in review until corrected."
    : `${values.length} source version labels. Suggestions need your confirmation; deprecated labels are left unmapped.`;
  toggleVersionMode();
}
function toggleVersionMode() {
  show("allowListWrap", $("versionMode").value === "allow_list");
}
function addMilestone(release = "", date = "") {
  const row = node("div", undefined, "milestone-row"),
    r = node("input"),
    d = node("input"),
    b = node("button", "Remove", "secondary");
  r.placeholder = "Release name";
  r.value = release;
  r.setAttribute("aria-label", "Milestone release");
  r.dataset.milestone = "release";
  d.type = "date";
  d.value = date;
  d.setAttribute("aria-label", "Release date");
  d.dataset.milestone = "date";
  b.onclick = () => row.remove();
  row.append(r, d, b);
  $("milestones").append(row);
}
function collect() {
  const p = structuredClone(state.profile);
  p.schema_version = 2;
  p.profile_id = $("profileName").value.trim();
  p.source_options = sourceOptions();
  p.source_columns = {};
  document
    .querySelectorAll("[data-map]")
    .forEach((el) => (p.source_columns[el.dataset.map] = el.value));
  p.date_format = $("dateFormat").value;
  p.normalization = { blank_values: ["", ...lines($("blankValues").value)] };
  const statuses = [...document.querySelectorAll("[data-status]:checked")].map(
    (el) => el.dataset.status,
  );
  if (document.querySelectorAll("[data-status]").length && !statuses.length)
    throw new Error("Select at least one status to include.");
  const vp = p.inclusion_rules?.version_policy || {};
  const aliases = { ...(vp.aliases || {}) },
    excluded = new Set(vp.excluded_values || []);
  document.querySelectorAll("[data-alias]").forEach((el) => {
    delete aliases[el.dataset.alias];
    if (el.value.trim()) aliases[el.dataset.alias] = el.value.trim();
  });
  document.querySelectorAll("[data-exclude]").forEach((el) => {
    if (el.checked) excluded.add(el.dataset.exclude);
    else excluded.delete(el.dataset.exclude);
  });
  p.inclusion_rules = {
    statuses,
    duplicate_policy: $("duplicateMode").value,
    version_policy: {
      mode: $("versionMode").value,
      aliases,
      excluded_values: [...excluded],
      accepted_values: lines($("acceptedVersions").value),
    },
  };
  p.confirmations = {};
  document
    .querySelectorAll("[data-confirm]")
    .forEach((el) => (p.confirmations[el.dataset.confirm] = el.checked));
  p.release_dates = {};
  document.querySelectorAll(".milestone-row").forEach((row) => {
    const r = row.querySelector('[data-milestone="release"]').value.trim(),
      d = row.querySelector('[data-milestone="date"]').value;
    if (r && d) p.release_dates[r] = d;
  });
  return p;
}
async function loadProfiles() {
  const d = await api("/api/profiles");
  const selected = $("profileSelect").value;
  clear("profileSelect").append(option("", "Suggested mappings"));
  d.profiles.forEach((p) =>
    $("profileSelect").append(option(p.id, `${p.id} · v${p.revision}`)),
  );
  $("profileSelect").value = selected;
}
async function prepare() {
  const profile = collect();
  const d = await api("/api/prepare", {
    upload_id: state.upload,
    profile,
    resolutions: state.resolutions,
    parent_id: state.run?.id || null,
  });
  state.profile = profile;
  state.run = d;
  state.offset = 0;
  await renderRun();
  step(4);
}
async function renderRun() {
  const r = state.run,
    v = r.validation;
  $("runCaption").textContent =
    `${r.filename} · ${new Date(r.created * 1000).toLocaleString()} · prepared by ${r.actor}`;
  $("approvalBadge").textContent = v.approved
    ? "Ready for STAR"
    : "Review required";
  $("approvalBadge").className = "badge " + (v.approved ? "good" : "warn");
  metrics("runStats", [
    ["Source rows", v.rows_read, "All input records", ""],
    ["Included", v.rows_included, "Validated for this cohort", "included"],
    [
      "Needs review",
      v.rows_review_required,
      "Resolve before exporting",
      "review_required",
    ],
    ["Excluded", v.rows_excluded, "Reasons retained in the log", "excluded"],
  ]);
  $("validationMessages").textContent = v.approved
    ? "Validation passed. Review the totals, then download a STAR file for each release."
    : `${v.pending_rules.length ? "Confirm: " + v.pending_rules.join(", ") + ". " : ""}${v.rows_review_required} records need review. ${v.warnings.join(". ")} Approved STAR downloads become available when every review is resolved and at least one record is included.`;
  $("validationMessages").className = "notice" + (v.approved ? " good" : "");
  renderChart();
  clear("releaseFilter").append(option("", "All releases"));
  const releases = new Set([
    ...r.release_totals.map((t) => t.release),
    ...Object.values(r.profile.inclusion_rules?.version_policy?.aliases || {}),
  ]);
  [...releases]
    .sort()
    .forEach((name) => $("releaseFilter").append(option(name, name)));
  $("decisionFilter").value = "";
  $("recordSearch").value = "";
  updateCorrections();
  await loadRecords();
  $("exportHint").textContent = v.approved
    ? "Upload each release CSV to its matching release in STAR. The bundle includes the source, profile, prepared data, timeline and decision log."
    : "Candidate files are for review only and must not be imported into STAR.";
  const downloads = clear("downloads"),
    all = clear("allArtifacts");
  r.artifacts.forEach((a) => {
    const link = node("a", a.name);
    link.href = a.url;
    link.download = a.name;
    all.append(link);
    if (
      a.name === "preparation-bundle.zip" ||
      /^release-\d+-star.csv$/.test(a.name)
    )
      downloads.append(link.cloneNode(true));
  });
  const manifest = r.artifacts.find((a) => a.name === "release-manifest.json");
  if (manifest && v.approved) {
    const data = await (await fetch(manifest.url)).json();
    downloads.querySelectorAll("a").forEach((a) => {
      const entry = data.find((m) => m.file === a.download);
      if (entry) a.textContent = `${entry.release} · ${entry.rows} defects ↓`;
    });
  }
  $("runMetadata").textContent =
    `Run ${r.id} · Input SHA-256: ${r.metadata.input_sha256} · Profile SHA-256: ${r.metadata.profile_sha256}`;
  $("rerun").disabled = state.user.role === "viewer";
}
async function loadRecords() {
  if (!state.run) return;
  const params = new URLSearchParams({
    decision: $("decisionFilter").value,
    release: $("releaseFilter").value,
    query: $("recordSearch").value,
    offset: state.offset,
    limit: 50,
  });
  const runId = state.run.id;
  const data = await api(`/api/runs/${runId}/records?${params}`);
  if (state.run.id !== runId) return;
  state.total = data.total;
  const rows = data.rows.map((r) => {
    const badge = node(
      "span",
      r.decision.replaceAll("_", " "),
      "badge " +
        (r.decision === "included"
          ? "good"
          : r.decision === "review_required"
            ? "warn"
            : ""),
    );
    const reason = node("span", r.reason);
    const b = node("button", "Resolve", "secondary");
    b.disabled = state.user.role === "viewer";
    b.onclick = () => editRecord(r);
    return [
      r.source_row,
      r.source_id,
      r.release || "Unmapped",
      r.arrival_date,
      badge,
      reason,
      b,
    ];
  });
  clear("recordTable").append(
    table(
      [
        "Row",
        "Issue ID",
        "Release",
        "Reported",
        "Decision",
        "Reason",
        "Action",
      ],
      rows,
    ),
  );
  if (!rows.length)
    $("recordTable").append(
      node("div", "No records match these filters.", "empty"),
    );
  $("pageInfo").textContent =
    `${data.total ? state.offset + 1 : 0}–${Math.min(state.offset + 50, data.total)} of ${data.total.toLocaleString()} records`;
  $("prevPage").disabled = state.offset === 0;
  $("nextPage").disabled = state.offset + 50 >= data.total;
}
function editRecord(row) {
  const prior = state.resolutions[String(row.source_row)];
  $("editTitle").textContent = `Resolve source row ${row.source_row}`;
  $("editForm").dataset.row = row.source_row;
  $("resolutionAction").value = prior?.action || "edit";
  $("resolutionReason").value = prior?.reason || "";
  const box = clear("editFields");
  Object.entries({ ...fields, release: "Normalized release" }).forEach(
    ([key, label]) => {
      const wrap = node("div"),
        l = node("label", label.replace(" *", "")),
        input = node("input");
      input.id = "edit-" + key;
      l.htmlFor = input.id;
      input.dataset.edit = key;
      input.value = prior?.values?.[key] ?? row[key] ?? "";
      wrap.append(l, input);
      box.append(wrap);
    },
  );
  $("editDialog").showModal();
}
function updateCorrections() {
  $("pendingCorrections").textContent =
    `${Object.keys(state.resolutions).length} recorded corrections`;
}
function svgNode(tag, attrs = {}, text) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, String(v)));
  if (text !== undefined) el.textContent = text;
  return el;
}
function renderChart() {
  const box = clear("chart"),
    legend = clear("releaseLegend"),
    r = state.run;
  if (!r.series.length) {
    box.append(
      node(
        "div",
        "The chart will appear once records pass validation. Confirm the rules and resolve flagged records to populate it.",
        "empty",
      ),
    );
    $("downloadChart").disabled = true;
    return;
  }
  $("downloadChart").disabled = false;
  const width = 960,
    height = 350,
    left = 66,
    right = 24,
    top = 44,
    bottom = 48,
    plotW = width - left - right,
    plotH = height - top - bottom;
  const dates = r.series.map((p) => Date.parse(p.date)),
    milestones = Object.entries(r.profile.release_dates || {});
  const min = Math.min(...dates, ...milestones.map(([, d]) => Date.parse(d))),
    max = Math.max(
      ...dates,
      ...milestones.map(([, d]) => Date.parse(d)),
      min + 86400000,
    );
  const ymax = Math.max(
    5,
    Math.ceil(Math.max(...r.series.map((p) => p.cumulative_defects)) / 5) * 5,
  );
  const x = (v) => left + ((Date.parse(v) - min) / (max - min)) * plotW,
    y = (v) => top + plotH - (v / ymax) * plotH;
  const svg = svgNode("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": "Cumulative included defects by reported date and release",
    xmlns: "http://www.w3.org/2000/svg",
  });
  svg.append(svgNode("rect", { width, height, fill: "white" }));
  for (let i = 0; i <= 5; i++) {
    const value = (ymax * i) / 5,
      yy = y(value);
    svg.append(
      svgNode("line", {
        x1: left,
        y1: yy,
        x2: width - right,
        y2: yy,
        stroke: "#e6edef",
      }),
      svgNode(
        "text",
        {
          x: left - 12,
          y: yy + 4,
          "text-anchor": "end",
          fill: "#71838b",
          "font-size": 11,
          "font-family": "system-ui",
        },
        Math.round(value).toLocaleString(),
      ),
    );
    const time = min + ((max - min) * i) / 5;
    svg.append(
      svgNode(
        "text",
        {
          x: left + (plotW * i) / 5,
          y: height - 20,
          "text-anchor": i === 0 ? "start" : i === 5 ? "end" : "middle",
          fill: "#71838b",
          "font-size": 11,
          "font-family": "system-ui",
        },
        new Date(time).toISOString().slice(0, 10),
      ),
    );
  }
  svg.append(
    svgNode(
      "text",
      {
        x: left,
        y: 18,
        fill: "#627780",
        "font-size": 11,
        "font-family": "system-ui",
      },
      "Cumulative defects",
    ),
  );
  milestones.forEach(([release, date], i) => {
    if (Date.parse(date) < min) return;
    const xx = x(date);
    svg.append(
      svgNode("line", {
        x1: xx,
        x2: xx,
        y1: top,
        y2: top + plotH,
        stroke: "#9aaeb5",
        "stroke-dasharray": "5 5",
      }),
    );
    const label = svgNode(
      "text",
      {
        x: Math.min(xx, width - 100),
        y: top - 10 - (i % 2) * 14,
        fill: "#647983",
        "font-size": 10,
        "font-family": "system-ui",
      },
      `${release} · ${date}`,
    );
    svg.append(label);
  });
  r.release_totals.forEach((total, index) => {
    const points = r.series.filter((p) => p.release === total.release),
      color = colors[index % colors.length];
    let d = `M ${x(points[0].date)} ${y(0)}`;
    points.forEach(
      (p) => (d += ` H ${x(p.date)} V ${y(p.cumulative_defects)}`),
    );
    const path = svgNode("path", {
      d,
      fill: "none",
      stroke: color,
      "stroke-width": 2.5,
    });
    path.append(
      svgNode("title", {}, `${total.release}: ${total.count} defects`),
    );
    svg.append(path);
    const b = node("button"),
      dot = node("i");
    dot.style.backgroundColor = color;
    b.append(dot, document.createTextNode(`${total.release} · ${total.count}`));
    b.onclick = () =>
      task(async () => {
        $("releaseFilter").value = total.release;
        $("decisionFilter").value = "included";
        state.offset = 0;
        await loadRecords();
        $("recordTable").scrollIntoView({ behavior: "smooth" });
      });
    legend.append(b);
  });
  box.append(svg);
}
async function history() {
  show("workflow", false);
  show("historySection");
  $("breadcrumb").textContent = "Preparation history";
  $("newPreparation").classList.remove("active");
  $("historyButton").classList.add("active");
  const d = await api("/api/runs");
  const rows = d.runs.map((r) => {
    const b = node("button", "Open run", "secondary");
    b.onclick = () => task(() => openRun(r.id));
    return [
      r.filename,
      r.profile_id,
      new Date(r.created * 1000).toLocaleString(),
      r.actor,
      node(
        "span",
        r.approved ? "Ready for STAR" : "Needs review",
        "badge " + (r.approved ? "good" : "warn"),
      ),
      b,
    ];
  });
  clear("historyTable").append(
    table(["Source", "Profile", "Prepared", "By", "Result", ""], rows),
  );
  if (!rows.length)
    $("historyTable").append(
      node(
        "div",
        "No runs yet. Start with a customer export or the synthetic example.",
        "empty",
      ),
    );
}
async function openRun(id) {
  const r = await api("/api/runs/" + id);
  state.run = r;
  state.upload = r.upload_id;
  state.profile = structuredClone(r.profile);
  state.resolutions = structuredClone(r.resolutions);
  state.offset = 0;
  state.revision = 0;
  state.loadedName = "";
  if (state.user.role !== "viewer") {
    const d = await api("/api/inspect", {
      upload_id: r.upload_id,
      options: r.profile.source_options || {},
    });
    state.inspection = d;
    clear("sheet");
    (d.sheets.length ? d.sheets : [""]).forEach((s) =>
      $("sheet").append(option(s, s || "CSV")),
    );
    $("sheet").value = d.sheet || "";
    $("headerRow").value = d.header_row;
    $("encoding").value = r.profile.source_options?.encoding || "utf-8-sig";
    $("delimiter").value = r.profile.source_options?.delimiter || "auto";
    $("fileLabel").textContent = r.filename;
    renderProfile();
  } else state.inspection = {};
  await renderRun();
  step(4);
}
async function init() {
  const session = await api("/api/session");
  if (!session.user) {
    show("loginScreen");
    return;
  }
  state.user = session.user;
  show("loginScreen", false);
  show("workspace");
  $("identity").textContent = `${session.user.name} · ${session.user.role}`;
  $("modeBadge").textContent = session.shared
    ? "Shared internal workspace"
    : "Local workspace";
  show("logout", session.shared);
  await loadProfiles();
  if (state.user.role === "viewer") {
    $("newPreparation").disabled = true;
    $("demoButton").disabled = true;
    document
      .querySelectorAll("[data-step],[data-go]")
      .forEach((el) => (el.disabled = true));
    await history();
  }
}
$("sourceFile").onchange = (e) => task(() => upload(e.target.files[0]));
const drop = $("dropzone");
drop.ondragover = (e) => {
  e.preventDefault();
  drop.classList.add("drag");
};
drop.ondragleave = () => drop.classList.remove("drag");
drop.ondrop = (e) => {
  e.preventDefault();
  drop.classList.remove("drag");
  task(() => upload(e.dataTransfer.files[0]));
};
bind("inspectAgain", async () => {
  state.profile = null;
  state.run = null;
  state.resolutions = {};
  state.revision = 0;
  await inspect();
});
bind("toMapping", () => step(2));
bind("toRules", () => {
  if (!$("map-source_id").value || !$("map-arrival_date").value)
    throw new Error("Map the issue ID and reported date before continuing.");
  state.profile = collect();
  renderRules();
  step(3);
});
$("profileSelect").onchange = () =>
  task(async () => {
    const id = $("profileSelect").value;
    if (!id) {
      state.profile = structuredClone(state.inspection.suggested_profile);
      state.revision = 0;
      state.loadedName = "";
    } else {
      const d = await api("/api/profiles/" + encodeURIComponent(id));
      state.profile = d.profile;
      state.revision = d.revision;
      state.loadedName = id;
    }
    state.profile.source_options = sourceOptions();
    renderProfile();
  });
$("versionMode").onchange = toggleVersionMode;
$("dateFormat").onchange = resetConfirmations;
$("versionSearch").oninput = () => {
  const query = $("versionSearch").value.toLowerCase();
  document
    .querySelectorAll("#versionRows tr")
    .forEach((row) =>
      row.classList.toggle(
        "hidden",
        !row.dataset.version.toLowerCase().includes(query),
      ),
    );
};
bind("suggestVersions", () => {
  document.querySelectorAll("[data-alias]").forEach((el) => {
    if (
      el.value ||
      el.disabled ||
      /deprecated|do not use/i.test(el.dataset.alias)
    )
      return;
    const match = el.dataset.alias.match(/\bV?(\d+\.\d+)\b/i);
    if (match)
      el.value =
        "V" + match[1] + (/\bOPR\b/i.test(el.dataset.alias) ? " OPR" : "");
  });
  document.querySelector('[data-confirm="versions"]').checked = false;
  message(
    "Suggested release names are ready to review. Confirm the grouping before preparing.",
    true,
  );
});
bind("addMilestone", () => addMilestone());
bind("saveProfile", async () => {
  const profile = collect();
  const result = await api("/api/profiles", {
    profile,
    base_revision: profile.profile_id === state.loadedName ? state.revision : 0,
  });
  state.profile = profile;
  state.loadedName = profile.profile_id;
  state.revision = result.revision;
  $("profileRevision").textContent = `Saved revision ${result.revision}.`;
  await loadProfiles();
  message(`Saved ${profile.profile_id}, revision ${result.revision}.`, true);
});
bind("prepare", prepare);
bind("rerun", prepare);
bind("historyButton", history);
bind("refreshHistory", history);
bind("newPreparation", () => {
  state.run = null;
  state.upload = null;
  state.inspection = null;
  state.profile = null;
  state.resolutions = {};
  $("sourceFile").value = "";
  $("fileLabel").textContent =
    "Supports .csv and .xlsx, including multi-sheet workbooks";
  show("sourceControls", false);
  show("inspection", false);
  step(1);
});
["decisionFilter", "releaseFilter"].forEach(
  (id) =>
    ($(id).onchange = () =>
      task(async () => {
        state.offset = 0;
        await loadRecords();
      })),
);
let searchTimer;
$("recordSearch").oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(
    () =>
      task(async () => {
        state.offset = 0;
        await loadRecords();
      }),
    250,
  );
};
bind("prevPage", async () => {
  state.offset = Math.max(0, state.offset - 50);
  await loadRecords();
});
bind("nextPage", async () => {
  state.offset += 50;
  await loadRecords();
});
$("closeDialog").onclick = () => $("editDialog").close();
$("editForm").onsubmit = (e) => {
  e.preventDefault();
  const values = {};
  document
    .querySelectorAll("[data-edit]")
    .forEach((el) => (values[el.dataset.edit] = el.value));
  state.resolutions[$("editForm").dataset.row] = {
    action: $("resolutionAction").value,
    values,
    reason: $("resolutionReason").value.trim(),
  };
  updateCorrections();
  $("editDialog").close();
  message(
    "Correction staged. Apply corrections to validate and create a new run.",
    true,
  );
};
bind("downloadChart", () => {
  const svg = $("chart").querySelector("svg");
  if (!svg) return;
  const url = URL.createObjectURL(
    new Blob([new XMLSerializer().serializeToString(svg)], {
      type: "image/svg+xml",
    }),
  );
  const link = node("a");
  link.href = url;
  link.download = "defects-by-release.svg";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$("loginForm").onsubmit = (e) => {
  e.preventDefault();
  task(async () => {
    try {
      await api("/api/login", {
        username: $("username").value,
        password: $("password").value,
      });
      $("password").value = "";
      await init();
    } catch (error) {
      $("loginError").textContent = error.message;
    }
  });
};
bind("logout", async () => {
  await api("/api/logout", {});
  location.reload();
});
bind("demoButton", () =>
  upload(
    new File(
      [
        "Issue ID,Priority,Created,Resolved,Component,Status,Version\nDEMO-001,High,2025-01-03,2025-02-01,Core,Closed,V1.0 build 1\nDEMO-002,Medium,2025-01-15,,UI,Open,V1.0 build 2\nDEMO-002,Medium,2025-01-15,,UI,Open,V1.0 build 2\nDEMO-003,Low,2025-02-08,2025-03-02,Core,Closed,V1.1 build 1\nDEMO-004,High,2025-03-05,,API,Open,V1.1 build 2\nDEMO-005,Low,,,UI,Open,Legacy unknown\n",
      ],
      "synthetic-demo.csv",
      { type: "text/csv" },
    ),
  ),
);
task(init);
