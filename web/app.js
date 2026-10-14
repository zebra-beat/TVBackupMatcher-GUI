let currentState = null;
let pollTimer = null;
let lastRenderedVersion = -1;
let currentFilter = "all";

document.addEventListener("DOMContentLoaded", () => {
  checkSystemStatus();
  loadUserSettings();
  pollState();
  initDirAutocomplete();
  loadDatabaseStats();
});

async function checkSystemStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    const container = document.getElementById("sys-status");
    container.innerHTML = "";

    const tools = ["ffmpeg", "ffprobe", "tesseract", "dovi_convert"];
    tools.forEach(tool => {
      const info = data[tool];
      const pill = document.createElement("span");
      const isDovi = (tool === "dovi_convert");
      const avail = Boolean(info && info.available);
      pill.className = `status-pill ${avail ? "online" : (isDovi ? "checking" : "offline")}`;
      pill.textContent = isDovi
        ? `dovi_convert: ${avail ? "Ready" : "Optional"}`
        : `${tool}: ${avail ? "Ready" : "Missing"}`;
      container.appendChild(pill);
    });

    if (!data.ready) {
      document.getElementById("btn-scan").disabled = true;
      document.getElementById("btn-scan").textContent = "Missing Required Tools (Install via brew)";
    }
  } catch (err) {
    console.error("Failed to check status:", err);
  }
}

async function loadUserSettings() {
  try {
    const res = await fetch("/api/settings");
    const s = await res.json();
    if (!s) return;

    if (s.metadata_source) document.getElementById("metadata-source").value = s.metadata_source;
    if (s.tmdb_api_key) document.getElementById("tmdb-key").value = s.tmdb_api_key;
    if (s.frame_windows) document.getElementById("frame-windows").value = s.frame_windows;
    if (s.export_cards_dir) document.getElementById("export-cards-dir").value = s.export_cards_dir;
    if (s.episodes_file) document.getElementById("episodes-file").value = s.episodes_file;
    if (s.min_score) document.getElementById("min-score").value = s.min_score;
    if (s.review_threshold) document.getElementById("review-threshold").value = s.review_threshold;
    if (s.workers) document.getElementById("workers").value = s.workers;

    // Sonarr & Radarr tags
    if (s.sonarr_tags_enabled !== undefined && document.getElementById("sonarr-enabled")) {
      document.getElementById("sonarr-enabled").checked = s.sonarr_tags_enabled;
    }
    if (s.sonarr_style && document.getElementById("sonarr-style")) {
      document.getElementById("sonarr-style").value = s.sonarr_style;
    }
    if (s.sonarr_source && document.getElementById("sonarr-source")) {
      document.getElementById("sonarr-source").value = s.sonarr_source;
    }
    if (s.sonarr_include_resolution !== undefined && document.getElementById("tag-inc-res")) {
      document.getElementById("tag-inc-res").checked = s.sonarr_include_resolution;
    }
    if (s.sonarr_include_codec !== undefined && document.getElementById("tag-inc-codec")) {
      document.getElementById("tag-inc-codec").checked = s.sonarr_include_codec;
    }
    if (s.sonarr_include_hdr !== undefined && document.getElementById("tag-inc-hdr")) {
      document.getElementById("tag-inc-hdr").checked = s.sonarr_include_hdr;
    }
    if (s.sonarr_include_audio !== undefined && document.getElementById("tag-inc-audio")) {
      document.getElementById("tag-inc-audio").checked = s.sonarr_include_audio;
    }
    if (s.sonarr_dv_profile_tag && document.getElementById("tag-dv-prof")) {
      document.getElementById("tag-dv-prof").value = s.sonarr_dv_profile_tag;
    }

    // Dolby Vision settings
    if (s.dovi_auto_convert_p7 !== undefined && document.getElementById("dovi-auto-convert")) {
      document.getElementById("dovi-auto-convert").checked = s.dovi_auto_convert_p7;
    }
    if (s.dovi_convert_mode && document.getElementById("dovi-mode")) {
      document.getElementById("dovi-mode").value = s.dovi_convert_mode;
    }

    // Check dovi_convert binary status
    try {
      const doviRes = await fetch("/api/dovi/status");
      const doviData = await doviRes.json();
      const hintBox = document.getElementById("dovi-install-hint");
      if (hintBox) {
        if (!doviData.available) {
          hintBox.style.display = "block";
          hintBox.textContent = "ℹ️ dovi_convert not detected on this system.\nTo convert Dolby Vision Profile 7 to 8.1:\nbrew tap cryptochrome/dovi_convert && brew install dovi_convert";
        } else {
          hintBox.style.display = "none";
        }
      }
    } catch (_) {}

    // Populate recent directories
    if (s.recent_dirs && s.recent_dirs.length > 0) {
      const dirInput = document.getElementById("series-dir");
      if (!dirInput.value) {
        dirInput.value = s.recent_dirs[0];
      }
      renderRecentChips(s.recent_dirs);
    }
  } catch (err) {
    console.error("Failed to load user settings:", err);
  }
}

async function saveSonarrOptions() {
  const enabled = document.getElementById("sonarr-enabled").checked;
  const subWrap = document.getElementById("tag-sub-options");
  if (subWrap) subWrap.style.opacity = enabled ? "1" : "0.5";

  const payload = {
    enabled: enabled,
    style: document.getElementById("sonarr-style").value,
    source: document.getElementById("sonarr-source").value,
    include_resolution: document.getElementById("tag-inc-res").checked,
    include_codec: document.getElementById("tag-inc-codec").checked,
    include_hdr: document.getElementById("tag-inc-hdr").checked,
    include_audio: document.getElementById("tag-inc-audio").checked,
    dv_profile_tag: document.getElementById("tag-dv-prof").value,
  };
  try {
    await fetch("/api/sonarr/options", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    // Save to user settings as well
    fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sonarr_tags_enabled: payload.enabled,
        sonarr_style: payload.style,
        sonarr_source: payload.source,
        sonarr_include_resolution: payload.include_resolution,
        sonarr_include_codec: payload.include_codec,
        sonarr_include_hdr: payload.include_hdr,
        sonarr_include_audio: payload.include_audio,
        sonarr_dv_profile_tag: payload.dv_profile_tag,
      })
    });
    lastRenderedVersion = -1;
    pollState();
  } catch (err) {
    console.error("Failed to save Sonarr options:", err);
  }
}

async function saveDoviOptions() {
  const payload = {
    mode: document.getElementById("dovi-mode").value,
    backup: document.getElementById("dovi-backup").checked,
  };
  try {
    await fetch("/api/dovi/options", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dovi_convert_mode: payload.mode })
    });
  } catch (err) {
    console.error("Failed to save Dovi options:", err);
  }
}

async function saveDoviAutoConvert() {
  const autoConvert = document.getElementById("dovi-auto-convert").checked;
  try {
    await fetch("/api/dovi/auto_convert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto_convert: autoConvert })
    });
    fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dovi_auto_convert_p7: autoConvert })
    });
    lastRenderedVersion = -1;
    pollState();
  } catch (err) {
    console.error("Failed to set Dovi auto convert:", err);
  }
}

async function toggleDoviConvert(filePath, convert) {
  try {
    await fetch("/api/dovi/set_convert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_path: filePath, convert: convert })
    });
    lastRenderedVersion = -1;
    pollState();
  } catch (err) {
    console.error("Failed to toggle Dovi convert:", err);
  }
}

async function setAllDoviConvert(convert) {
  try {
    await fetch("/api/dovi/set_all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ convert: convert })
    });
    lastRenderedVersion = -1;
    pollState();
  } catch (err) {
    console.error("Failed to set all Dovi convert:", err);
  }
}

function renderRecentChips(dirs) {
  const container = document.getElementById("recent-chips");
  const list = document.getElementById("recent-chips-list");
  list.innerHTML = "";
  if (!dirs || dirs.length === 0) {
    container.style.display = "none";
    return;
  }
  dirs.forEach(path => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip";
    const parts = path.split(/[/\\]/).filter(Boolean);
    btn.textContent = parts[parts.length - 1] || path;
    btn.title = path;
    btn.onclick = () => setDir(path);
    list.appendChild(btn);
  });
  container.style.display = "flex";
}

function setDir(path) {
  document.getElementById("series-dir").value = path;
}

function toggleAdvanced() {
  const panel = document.getElementById("adv-panel");
  const arrow = document.getElementById("adv-arrow");
  if (panel.style.display === "none") {
    panel.style.display = "block";
    arrow.textContent = "▼";
  } else {
    panel.style.display = "none";
    arrow.textContent = "▶";
  }
}

async function startScan() {
  const seriesDir = document.getElementById("series-dir").value.trim();
  if (!seriesDir) {
    alert("Please enter or select a series directory path.");
    return;
  }

  const payload = {
    series_dir: seriesDir,
    series_name: document.getElementById("series-name").value.trim(),
    tvdb_id: document.getElementById("tvdb-id").value.trim(),
    metadata_source: document.getElementById("metadata-source").value,
    tmdb_api_key: document.getElementById("tmdb-key").value.trim(),
    frame_windows: document.getElementById("frame-windows").value.trim() || "64-75",
    export_cards_dir: document.getElementById("export-cards-dir").value.trim(),
    episodes_file: document.getElementById("episodes-file").value.trim(),
    min_score: parseFloat(document.getElementById("min-score").value) || 0.35,
    review_threshold: parseFloat(document.getElementById("review-threshold").value) || 0.60,
    workers: parseInt(document.getElementById("workers").value, 10) || 3
  };

  document.getElementById("btn-scan").disabled = true;
  document.getElementById("btn-cancel").style.display = "inline-block";
  document.getElementById("progress-section").style.display = "block";
  document.getElementById("results-section").style.display = "none";
  document.getElementById("action-bar").style.display = "none";

  try {
    const res = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const result = await res.json();
    if (!result.success) {
      alert("Error: " + result.error);
      document.getElementById("btn-scan").disabled = false;
      document.getElementById("btn-cancel").style.display = "none";
      document.getElementById("progress-section").style.display = "none";
      return;
    }
    lastRenderedVersion = -1;
    startPolling();
  } catch (err) {
    alert("Failed to start scan: " + err.message);
    document.getElementById("btn-scan").disabled = false;
    document.getElementById("btn-cancel").style.display = "none";
    document.getElementById("progress-section").style.display = "none";
  }
}

async function cancelScan() {
  try {
    await fetch("/api/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });
    document.getElementById("btn-cancel").textContent = "Cancelling...";
    document.getElementById("btn-cancel").disabled = true;
  } catch (err) {
    console.error("Cancel failed:", err);
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollState, 1000);
}

async function pollState() {
  try {
    const res = await fetch("/api/state");
    const data = await res.json();
    currentState = data;

    updateUI(data);

    if (data.state === "review_ready" || data.state === "completed" || data.state === "error" || data.state === "idle") {
      if (data.state !== "processing" && data.state !== "scanning") {
        if (pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
        document.getElementById("btn-scan").disabled = false;
        document.getElementById("btn-cancel").style.display = "none";
        document.getElementById("btn-cancel").textContent = "Cancel";
        document.getElementById("btn-cancel").disabled = false;
      }
    }
  } catch (err) {
    console.error("Poll error:", err);
  }
}

function updateUI(data) {
  // Update Undo buttons
  const undoHeader = document.getElementById("undo-header-container");
  const undoBar = document.getElementById("btn-undo-bar");
  if (data.undo) {
    undoHeader.style.display = "block";
    undoBar.style.display = "inline-block";
  } else {
    undoHeader.style.display = "none";
    undoBar.style.display = "none";
  }

  // Update Progress
  const progressSec = document.getElementById("progress-section");
  const fill = document.getElementById("progress-fill");
  const pct = document.getElementById("progress-pct");
  const msg = document.getElementById("status-msg");

  if (data.state === "scanning" || data.state === "processing" || data.state === "renaming") {
    progressSec.style.display = "block";
    const percentInt = Math.round(data.progress * 100);
    fill.style.width = percentInt + "%";
    pct.textContent = percentInt + "%";
    msg.textContent = data.status_message;
  } else if (data.state === "review_ready" || data.state === "completed") {
    progressSec.style.display = "block";
    fill.style.width = "100%";
    pct.textContent = "100%";
    msg.textContent = data.status_message;
  } else if (data.state === "error") {
    progressSec.style.display = "block";
    pct.textContent = "Failed";
    msg.textContent = data.error_message || data.status_message;
  }

  // Update Metadata source badge
  const metaBadge = document.getElementById("metadata-badge");
  if (data.metadata_description) {
    metaBadge.textContent = data.metadata_description;
    metaBadge.style.display = "inline-block";
  } else {
    metaBadge.style.display = "none";
  }

  // Update Results
  if (data.matches && data.matches.length > 0) {
    document.getElementById("results-section").style.display = "block";
    document.getElementById("action-bar").style.display = "flex";

    const counts = data.counts || {};
    const willRename = counts.will_rename || 0;
    const needsReview = counts.needs_review || 0;
    const total = data.total_files || data.matches.length;

    document.getElementById("results-summary").textContent =
      `Examined ${total} files: ${willRename} ready to rename, ${needsReview} require review.`;

    document.getElementById("action-stat").textContent =
      `${willRename} files ready to reorganize for '${data.series_name || "Series"}'`;

    document.getElementById("unreviewed-count").textContent = needsReview;
    document.getElementById("unreviewed-wrap").style.display = needsReview > 0 ? "inline-flex" : "none";

    // Dolby Vision Profile 7 banner
    const doviBanner = document.getElementById("dovi-banner");
    if (doviBanner) {
      if (counts.dv_p7_total && counts.dv_p7_total > 0) {
        doviBanner.style.display = "flex";
        doviBanner.innerHTML = `
          <div class="dovi-banner-text">
            <span style="font-size:18px;">⚡</span>
            <span><strong>Dolby Vision Profile 7 Detected:</strong> Found <strong>${counts.dv_p7_total}</strong> UHD Blu-ray file(s). <strong>${counts.dv_p7_convert || 0}</strong> queued for Apple TV / Infuse Profile 8.1 conversion.</span>
          </div>
          <div class="dovi-banner-actions">
            <button type="button" class="btn btn-secondary btn-sm" onclick="setAllDoviConvert(true)">⚡ Convert All to Profile 8.1</button>
            <button type="button" class="btn btn-secondary btn-sm" onclick="setAllDoviConvert(false)">Keep Profile 7</button>
          </div>
        `;
      } else {
        doviBanner.style.display = "none";
      }
    }

    // Only re-render cards if data version changed
    if (data.version !== lastRenderedVersion) {
      renderCards(data.matches, data.available_episodes, data.review_threshold || 0.60);
      lastRenderedVersion = data.version;
    }
  }
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function createSearchableEpisodeDropdown(item, availableEpisodes, onSelect) {
  const container = document.createElement("div");
  container.className = "combobox-container";

  const inputWrapper = document.createElement("div");
  inputWrapper.className = "combobox-input-wrapper";

  const searchIcon = document.createElement("span");
  searchIcon.className = "combobox-icon";
  searchIcon.textContent = "🔍";

  const input = document.createElement("input");
  input.type = "text";
  input.className = "combobox-input";
  input.autocomplete = "off";
  input.spellcheck = false;

  let currentTitle = "";
  if (item.status === "skipped") {
    currentTitle = "-- Exclude / Skip File --";
  } else if (item.episode_code) {
    const matchedEp = (availableEpisodes || []).find(e => e.code === item.episode_code);
    currentTitle = matchedEp ? `${matchedEp.code} - ${matchedEp.title}` : `${item.episode_code} - ${item.episode_title}`;
  }
  input.value = currentTitle;
  input.placeholder = "Search or select episode...";
  input.title = currentTitle || "Search or select episode";

  const toggleBtn = document.createElement("button");
  toggleBtn.type = "button";
  toggleBtn.className = "combobox-toggle-btn";
  toggleBtn.innerHTML = "▾";
  toggleBtn.tabIndex = -1;

  inputWrapper.appendChild(searchIcon);
  inputWrapper.appendChild(input);
  inputWrapper.appendChild(toggleBtn);
  container.appendChild(inputWrapper);

  const menu = document.createElement("div");
  menu.className = "combobox-menu";
  menu.style.display = "none";

  const actionsWrap = document.createElement("div");
  actionsWrap.className = "combobox-actions";

  const optSkip = document.createElement("div");
  optSkip.className = "combobox-item combobox-item-action text-danger";
  optSkip.innerHTML = `<span class="combobox-badge-action">🚫</span> Exclude / Skip File`;
  optSkip.onmousedown = (e) => {
    e.preventDefault();
    selectOption("__skip__", "-- Exclude / Skip File --");
  };
  actionsWrap.appendChild(optSkip);

  const optClear = document.createElement("div");
  optClear.className = "combobox-item combobox-item-action";
  optClear.innerHTML = `<span class="combobox-badge-action">🔄</span> Unassign Episode`;
  optClear.onmousedown = (e) => {
    e.preventDefault();
    selectOption("", "");
  };
  actionsWrap.appendChild(optClear);
  menu.appendChild(actionsWrap);

  const listContainer = document.createElement("div");
  listContainer.className = "combobox-list";
  menu.appendChild(listContainer);

  const emptyMsg = document.createElement("div");
  emptyMsg.className = "combobox-empty";
  emptyMsg.textContent = "No matching episodes found";
  emptyMsg.style.display = "none";
  menu.appendChild(emptyMsg);

  container.appendChild(menu);

  let activeIndex = -1;

  function renderList(query = "") {
    listContainer.innerHTML = "";
    const q = query.trim().toLowerCase();
    const visibleItems = [];

    (availableEpisodes || []).forEach(ep => {
      const codeMatch = ep.code.toLowerCase().includes(q);
      const titleMatch = ep.title.toLowerCase().includes(q);
      if (!q || codeMatch || titleMatch) {
        visibleItems.push(ep);
        const itemEl = document.createElement("div");
        const isCurrent = ep.code === item.episode_code && item.status !== "skipped";
        itemEl.className = `combobox-item ${isCurrent ? "is-selected" : ""}`;
        itemEl.innerHTML = `
          <span class="combobox-code">${escapeHtml(ep.code)}</span>
          <span class="combobox-title">${escapeHtml(ep.title)}</span>
        `;
        itemEl.onmousedown = (e) => {
          e.preventDefault();
          selectOption(ep.code, `${ep.code} - ${ep.title}`);
        };
        listContainer.appendChild(itemEl);
      }
    });

    if (visibleItems.length === 0) {
      emptyMsg.style.display = "block";
    } else {
      emptyMsg.style.display = "none";
    }
  }

  function selectOption(val, displayText) {
    input.value = displayText;
    input.title = displayText;
    currentTitle = displayText;
    closeMenu();
    if (val !== item.episode_code || (val === "__skip__" && item.status !== "skipped")) {
      onSelect(val);
    }
  }

  function openMenu() {
    document.querySelectorAll(".combobox-menu").forEach(m => {
      if (m !== menu) m.style.display = "none";
    });
    document.querySelectorAll(".result-card").forEach(c => c.classList.remove("dropdown-open"));

    renderList(input.value === currentTitle ? "" : input.value);
    menu.style.display = "flex";
    const cardEl = container.closest(".result-card");
    if (cardEl) cardEl.classList.add("dropdown-open");
  }

  function closeMenu() {
    menu.style.display = "none";
    const cardEl = container.closest(".result-card");
    if (cardEl) cardEl.classList.remove("dropdown-open");
    activeIndex = -1;
  }

  input.onfocus = () => {
    input.select();
    openMenu();
  };

  input.onclick = (e) => {
    e.stopPropagation();
    if (menu.style.display === "none") {
      openMenu();
    }
  };

  input.oninput = () => {
    if (menu.style.display === "none") {
      openMenu();
    }
    renderList(input.value);
  };

  toggleBtn.onclick = (e) => {
    e.stopPropagation();
    if (menu.style.display === "none") {
      input.focus();
    } else {
      closeMenu();
    }
  };

  input.onblur = () => {
    setTimeout(() => {
      closeMenu();
      input.value = currentTitle;
    }, 180);
  };

  input.onkeydown = (e) => {
    if (menu.style.display === "none") {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        openMenu();
        e.preventDefault();
        return;
      }
    }
    const items = listContainer.querySelectorAll(".combobox-item");
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (items.length > 0) {
        activeIndex = (activeIndex + 1) % items.length;
        highlightItem(items, activeIndex);
      }
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (items.length > 0) {
        activeIndex = (activeIndex - 1 + items.length) % items.length;
        highlightItem(items, activeIndex);
      }
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (activeIndex >= 0 && items[activeIndex]) {
        items[activeIndex].dispatchEvent(new MouseEvent("mousedown"));
      } else if (items.length > 0) {
        items[0].dispatchEvent(new MouseEvent("mousedown"));
      }
    } else if (e.key === "Escape") {
      closeMenu();
      input.value = currentTitle;
    }
  };

  function highlightItem(items, idx) {
    items.forEach((it, i) => {
      it.classList.toggle("is-active", i === idx);
      if (i === idx) {
        it.scrollIntoView({ block: "nearest" });
      }
    });
  }

  return container;
}

function renderCards(matches, availableEpisodes, reviewThreshold) {
  const grid = document.getElementById("results-grid");
  grid.innerHTML = "";
  const filter = document.getElementById("match-filter").value;

  matches.forEach(item => {
    // Filter logic
    if (filter === "ready" && item.needs_review) return;
    if (filter === "review" && !item.needs_review) return;
    if (filter === "raw_disc" && !item.is_raw_disc) return;
    if (filter === "overridden" && !item.is_overridden) return;
    if (filter === "skipped" && item.status !== "skipped") return;

    const card = document.createElement("div");
    card.className = "result-card";
    if (item.needs_review) {
      card.style.borderColor = "rgba(245, 158, 11, 0.4)";
    }

    // Thumbnail section
    const thumbWrap = document.createElement("div");
    thumbWrap.className = "card-thumb-wrap";

    if (item.has_thumbnail) {
      const img = document.createElement("img");
      img.src = `/api/thumbnail?path=${encodeURIComponent(item.image_path)}`;
      img.alt = item.episode_title;
      img.onclick = () => openModal(
        item.file_path,
        item.image_path,
        `${item.episode_code || ""} ${item.episode_title}`,
        `File: ${item.file_name} • t = ${item.timestamp}s`,
        item.ocr_sample,
        item.available_frames,
        item.active_frame_index
      );
      thumbWrap.appendChild(img);

      if (item.has_credits) {
        const credBadge = document.createElement("span");
        credBadge.className = "credit-badge";
        credBadge.textContent = "⚠️ Staff Credits";
        credBadge.title = "Director, Writer, or Staff credits detected in this frame";
        thumbWrap.appendChild(credBadge);
      }

      if (item.available_frames && item.available_frames.length > 1) {
        const nav = document.createElement("div");
        nav.className = "thumb-nav";

        const prevBtn = document.createElement("button");
        prevBtn.type = "button";
        prevBtn.className = "thumb-nav-btn";
        prevBtn.innerHTML = "◀";
        prevBtn.title = "Previous captured frame";
        prevBtn.onclick = (e) => { e.stopPropagation(); cycleFrame(item.file_path, -1); };

        const counter = document.createElement("span");
        counter.className = "thumb-counter";
        counter.textContent = `t = ${item.timestamp}s (${(item.active_frame_index >= 0 ? item.active_frame_index : 0) + 1}/${item.available_frames.length})`;

        const nextBtn = document.createElement("button");
        nextBtn.type = "button";
        nextBtn.className = "thumb-nav-btn";
        nextBtn.innerHTML = "▶";
        nextBtn.title = "Next captured frame";
        nextBtn.onclick = (e) => { e.stopPropagation(); cycleFrame(item.file_path, 1); };

        nav.appendChild(prevBtn);
        nav.appendChild(counter);
        nav.appendChild(nextBtn);
        thumbWrap.appendChild(nav);
      } else {
        const tag = document.createElement("span");
        tag.className = "thumb-tag";
        tag.textContent = `t = ${item.timestamp}s`;
        thumbWrap.appendChild(tag);
      }
    } else {
      thumbWrap.innerHTML = `
        <div style="height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;color:#6b7280;font-size:12px;cursor:pointer;">
          <span>No Title Card Captured</span>
          <span style="color:#60a5fa;font-size:11px;font-weight:600;">🎬 Click to Scrub Footage</span>
        </div>`;
      thumbWrap.onclick = () => openScrubber(item.file_path, item.timestamp || 50.0);
    }
    card.appendChild(thumbWrap);

    // Content section
    const content = document.createElement("div");
    content.className = "card-content";

    // Original file name
    const origFn = document.createElement("div");
    origFn.className = "orig-filename";
    origFn.title = item.file_name;
    origFn.textContent = item.file_name;
    content.appendChild(origFn);

    // Media Info Badges (Resolution, Codec, HDR/DV, Audio, Raw Disc Rip, DB Match)
    const mb = document.createElement("div");
    mb.className = "media-badges";
    if (item.is_raw_disc) {
      const p = document.createElement("span");
      p.className = "media-pill raw-disc";
      p.textContent = `📀 Raw Disc (${item.raw_disc_tag || "rip"})`;
      p.title = "Raw optical disc rip pattern detected (e.g. MakeMKV _t00)";
      mb.appendChild(p);
    }
    if (item.db_matched) {
      const p = document.createElement("span");
      p.className = "media-pill db-match";
      p.textContent = "💾 DB Matched";
      p.title = "Recognized from previously confirmed title card database";
      mb.appendChild(p);
    }
    if (item.media_info) {
      if (item.media_info.resolution) {
        const p = document.createElement("span");
        p.className = "media-pill res";
        p.textContent = item.media_info.resolution;
        mb.appendChild(p);
      }
      if (item.media_info.video_codec) {
        const p = document.createElement("span");
        p.className = "media-pill codec";
        p.textContent = item.media_info.video_codec;
        mb.appendChild(p);
      }
      if (item.media_info.is_dovi || item.has_dv_p7) {
        const p = document.createElement("span");
        p.className = `media-pill dovi ${item.has_dv_p7 ? "dovi-p7" : ""}`;
        p.textContent = item.convert_dv_p7 ? "DV Profile 8.1" : (item.media_info.dv_profile_name || "Dolby Vision");
        mb.appendChild(p);
      } else if (item.media_info.hdr_format && item.media_info.hdr_format !== "SDR") {
        const p = document.createElement("span");
        p.className = "media-pill hdr";
        p.textContent = item.media_info.hdr_format;
        mb.appendChild(p);
      }
      if (item.media_info.audio_title) {
        const p = document.createElement("span");
        p.className = "media-pill audio";
        p.textContent = item.media_info.audio_title;
        mb.appendChild(p);
      }
    }
    if (mb.children.length > 0) {
      content.appendChild(mb);
    }

    // Match Header Row
    const matchRow = document.createElement("div");
    matchRow.className = "match-row";

    const epBadge = document.createElement("span");
    epBadge.className = "ep-badge";
    epBadge.textContent = item.episode_code ? `${item.episode_code}: ${item.episode_title}` : (item.status === "skipped" ? "Skipped (Excluded)" : "Unmatched");
    matchRow.appendChild(epBadge);

    const scorePill = document.createElement("span");
    let scoreClass = "low";
    if (item.score >= 0.8) scoreClass = "high";
    else if (item.score >= 0.5) scoreClass = "mid";
    scorePill.className = `score-pill ${scoreClass}`;
    if (item.status === "skipped") {
      scorePill.textContent = "Excluded";
    } else if (item.is_overridden || item.confirmation_type === "manual") {
      scorePill.textContent = "✓ Manual";
    } else {
      scorePill.textContent = `Auto ${Math.round(item.score * 100)}%`;
    }
    matchRow.appendChild(scorePill);

    content.appendChild(matchRow);

    // Dolby Vision Profile 7 -> 8.1 conversion toggle
    if (item.has_dv_p7) {
      const doviWrap = document.createElement("label");
      doviWrap.className = "dovi-toggle-wrap";
      doviWrap.title = "Convert Dolby Vision Profile 7 (Blu-ray MEL/FEL) to Profile 8.1 for Apple TV / Infuse playback compatibility";
      const doviChk = document.createElement("input");
      doviChk.type = "checkbox";
      doviChk.checked = Boolean(item.convert_dv_p7);
      doviChk.onchange = (e) => toggleDoviConvert(item.file_path, e.target.checked);

      const doviLabel = document.createElement("span");
      doviLabel.className = `dovi-convert-badge ${item.convert_dv_p7 ? "converted" : ""}`;
      doviLabel.textContent = item.convert_dv_p7
        ? "✓ Will convert DV Profile 7 → 8.1 (Universal / Apple TV)"
        : "⚡ Convert DV Profile 7 → 8.1 (Universal / Apple TV)";

      doviWrap.appendChild(doviChk);
      doviWrap.appendChild(doviLabel);
      content.appendChild(doviWrap);
    }

    // Target Path Preview
    if (item.target_relpath) {
      const targetDiv = document.createElement("div");
      targetDiv.className = "target-path";
      targetDiv.title = `Proposed target: ${item.target_relpath}`;
      targetDiv.textContent = `→ ${item.target_relpath}`;
      content.appendChild(targetDiv);
    }

    // Warning alert (if duration mismatch or review needed)
    if (item.warning) {
      const warnBox = document.createElement("div");
      warnBox.className = "card-warning";
      warnBox.textContent = `⚠️ ${item.warning}`;
      content.appendChild(warnBox);
    }

    // Override & Confirm Row (Searchable Episode Combobox)
    const overrideRow = document.createElement("div");
    overrideRow.className = "override-row";

    const combobox = createSearchableEpisodeDropdown(item, availableEpisodes, (val) => {
      applyOverride(item.file_path, val);
    });

    const actionFlex = document.createElement("div");
    actionFlex.style.display = "flex";
    actionFlex.style.gap = "8px";
    actionFlex.style.alignItems = "center";
    actionFlex.appendChild(combobox);

    if (item.needs_review) {
      const confirmBtn = document.createElement("button");
      confirmBtn.className = "btn-confirm";
      confirmBtn.textContent = "✓ Confirm";
      confirmBtn.title = "Approve this match and log title card timestamp to database";
      confirmBtn.onclick = () => confirmMatch(item.file_path);
      actionFlex.appendChild(confirmBtn);
    }

    overrideRow.appendChild(actionFlex);
    content.appendChild(overrideRow);

    // Card Tools: Rescan File & Frame Drawer Toggle
    const toolsRow = document.createElement("div");
    toolsRow.className = "card-tools";

    const btnRescan = document.createElement("button");
    btnRescan.type = "button";
    btnRescan.className = "btn-card-tool";
    btnRescan.innerHTML = "<span>🔄</span> Rescan File";
    btnRescan.title = "Re-analyze candidate frames for this file only";
    btnRescan.onclick = () => rescanSingleFile(item.file_path);
    toolsRow.appendChild(btnRescan);

    const btnDrawer = document.createElement("button");
    btnDrawer.type = "button";
    btnDrawer.className = "btn-card-tool";
    btnDrawer.innerHTML = `<span>⏱️</span> Frames (${item.frame_count || (item.available_frames ? item.available_frames.length : 1)})`;
    btnDrawer.title = "Pick another captured frame or extract at custom timestamp";
    toolsRow.appendChild(btnDrawer);

    const btnScrub = document.createElement("button");
    btnScrub.type = "button";
    btnScrub.className = "btn-card-tool";
    btnScrub.innerHTML = `<span>🎬</span> Scrub Footage`;
    btnScrub.title = "Open interactive video scrubber and keyframe filmstrip to find title card manually";
    btnScrub.onclick = () => openScrubber(item.file_path, item.timestamp || 50.0);
    toolsRow.appendChild(btnScrub);

    content.appendChild(toolsRow);

    // Collapsible Frame Drawer
    const drawer = document.createElement("div");
    drawer.className = "frame-drawer";
    drawer.style.display = "none";
    btnDrawer.onclick = () => {
      drawer.style.display = drawer.style.display === "none" ? "flex" : "none";
    };

    // Quick scrubber launcher inside drawer
    const drawerScrubRow = document.createElement("div");
    drawerScrubRow.className = "frame-drawer-row";
    drawerScrubRow.innerHTML = `
      <button type="button" class="btn btn-secondary btn-sm" style="width: 100%;">
        🎬 Open Interactive Video Scrubber &amp; Filmstrip
      </button>
    `;
    drawerScrubRow.querySelector("button").onclick = () => openScrubber(item.file_path, item.timestamp || 50.0);
    drawer.appendChild(drawerScrubRow);

    // If multiple frames exist, display chips
    if (item.available_frames && item.available_frames.length > 0) {
      const chipsContainer = document.createElement("div");
      chipsContainer.className = "frame-chips";
      item.available_frames.forEach((f, idx) => {
        const chip = document.createElement("div");
        chip.className = `frame-chip ${f.is_active ? "active" : ""} ${f.has_credits ? "credits" : ""}`;
        chip.title = f.ocr_sample ? `OCR: ${f.ocr_sample}` : `Timestamp: ${f.timestamp}s`;
        chip.innerHTML = `${f.is_active ? "✓ " : ""}t = ${f.timestamp}s ${f.has_credits ? "⚠️" : ""}`;
        chip.onclick = () => setActiveFrame(item.file_path, f.timestamp);
        chipsContainer.appendChild(chip);
      });
      drawer.appendChild(chipsContainer);
    }

    // Custom capture input row
    const captureRow = document.createElement("div");
    captureRow.className = "frame-drawer-row";
    captureRow.innerHTML = `
      <label>Capture Time:</label>
      <input type="text" placeholder="e.g. 74.5 or 01:14" id="time-input-${indexSafe(item.file_path)}">
      <button type="button" class="btn btn-primary btn-sm">Capture</button>
    `;
    const captureBtn = captureRow.querySelector("button");
    const captureInput = captureRow.querySelector("input");
    captureBtn.onclick = () => captureCustomFrame(item.file_path, captureInput.value.trim());
    drawer.appendChild(captureRow);

    // Custom rescan window input row
    const rescanRow = document.createElement("div");
    rescanRow.className = "frame-drawer-row";
    rescanRow.innerHTML = `
      <label>Window (s):</label>
      <input type="text" value="${document.getElementById('frame-windows')?.value || '64-75'}" id="rescan-win-${indexSafe(item.file_path)}">
      <button type="button" class="btn btn-secondary btn-sm">Rescan</button>
    `;
    const customRescanBtn = rescanRow.querySelector("button");
    const customRescanInput = rescanRow.querySelector("input");
    customRescanBtn.onclick = () => rescanSingleFile(item.file_path, customRescanInput.value.trim());
    drawer.appendChild(rescanRow);

    content.appendChild(drawer);

    card.appendChild(content);
    grid.appendChild(card);
  });
}

function indexSafe(str) {
  return (str || "").replace(/[^a-zA-Z0-9]/g, "_");
}

async function confirmMatch(filePath) {
  try {
    const res = await fetch("/api/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_path: filePath })
    });
    const data = await res.json();
    if (data.success) {
      pollState();
      loadDatabaseStats();
    }
  } catch (err) {
    alert("Confirm failed: " + err.message);
  }
}

async function applyOverride(filePath, epCode) {
  if (!epCode) return;
  try {
    const res = await fetch("/api/override", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_path: filePath, episode_code: epCode })
    });
    const data = await res.json();
    if (data.success) {
      if (data.displaced) {
        console.log(`Reassigned episode. Displaced from ${data.displaced}`);
      }
      pollState();
    } else {
      alert("Override error: " + data.error);
    }
  } catch (err) {
    alert("Failed to override assignment: " + err.message);
  }
}

function filterResults() {
  if (currentState && currentState.matches) {
    renderCards(currentState.matches, currentState.available_episodes, currentState.review_threshold || 0.60);
  }
}

async function confirmApply() {
  const exportCardsDir = document.getElementById("export-cards-dir").value.trim();
  const includeUnreviewed = document.getElementById("include-unreviewed").checked;

  const counts = (currentState && currentState.counts) || {};
  const toRename = includeUnreviewed ? (currentState.matches.length - (counts.skipped || 0)) : (counts.will_rename || 0);

  if (toRename === 0) {
    alert("No files are ready to rename. Please review and confirm matches or check 'Include unreviewed matches'.");
    return;
  }

  const confirmed = confirm(
    `Are you sure you want to reorganize ${toRename} file(s)?\n\n` +
    `- Files will be safely staged into Season folders with standard Plex/Jellyfin naming.\n` +
    `- Collision prevention ensures zero risk of accidental overwrites.\n` +
    (exportCardsDir ? `- Title cards will be exported to: ${exportCardsDir}\n` : "") +
    `- Every action is fully logged and can be completely reverted with 'Undo Last Rename'.`
  );
  if (!confirmed) return;

  const btnApply = document.getElementById("btn-apply");
  btnApply.disabled = true;
  btnApply.textContent = "Reorganizing Files...";

  try {
    const res = await fetch("/api/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        export_cards_dir: exportCardsDir,
        include_unreviewed: includeUnreviewed
      })
    });
    const result = await res.json();
    if (result.success) {
      let msg = `Success! Successfully reorganized ${result.renamed_count} file(s).`;
      if (result.skipped_count) msg += ` (${result.skipped_count} left untouched pending review).`;
      alert(msg);
      lastRenderedVersion = -1;
      pollState();
    } else {
      let errStr = "Error: " + result.error;
      if (result.problems && result.problems.length > 0) {
        errStr += "\n\nProblems:\n" + result.problems.slice(0, 6).join("\n");
      }
      alert(errStr);
    }
  } catch (err) {
    alert("Apply failed: " + err.message);
  } finally {
    btnApply.disabled = false;
    btnApply.textContent = "Apply Safe Staged Rename";
  }
}

async function undoLastRename() {
  const seriesDir = document.getElementById("series-dir").value.trim();
  if (!seriesDir) return;

  const confirmed = confirm(
    `Are you sure you want to revert all files in this series back to their original names and locations?\n\n` +
    `Folder: ${seriesDir}`
  );
  if (!confirmed) return;

  try {
    const res = await fetch("/api/undo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ series_dir: seriesDir })
    });
    const result = await res.json();
    if (result.success) {
      alert(`Undo complete! Successfully restored ${result.restored_count} file(s) to their original names.`);
      lastRenderedVersion = -1;
      pollState();
    } else {
      alert("Undo failed: " + result.error);
    }
  } catch (err) {
    alert("Undo request failed: " + err.message);
  }
}

function resetForm() {
  if (confirm("Reset current scan and results?")) {
    window.location.reload();
  }
}

// ------------------------------------------------------------- Frame & Rescan API

async function setActiveFrame(filePath, timestamp) {
  try {
    const res = await fetch("/api/set_frame", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_path: filePath, timestamp: timestamp })
    });
    const data = await res.json();
    if (data.success) {
      pollState();
    } else {
      alert("Failed to switch frame: " + data.error);
    }
  } catch (err) {
    alert("Error switching frame: " + err.message);
  }
}

async function captureCustomFrame(filePath, timestampVal) {
  if (!timestampVal) {
    alert("Please enter a timestamp (seconds like 74.5 or MM:SS like 01:14).");
    return;
  }
  try {
    const res = await fetch("/api/capture_frame", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_path: filePath, timestamp: timestampVal })
    });
    const data = await res.json();
    if (data.success) {
      pollState();
    } else {
      alert("Capture error: " + data.error);
    }
  } catch (err) {
    alert("Error capturing frame: " + err.message);
  }
}

async function rescanSingleFile(filePath, customWindow = "") {
  try {
    const res = await fetch("/api/rescan_file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_path: filePath,
        frame_windows: customWindow || document.getElementById("frame-windows")?.value || "64-75"
      })
    });
    const data = await res.json();
    if (data.success) {
      pollState();
    } else {
      alert("Rescan failed: " + data.error);
    }
  } catch (err) {
    alert("Error rescanning file: " + err.message);
  }
}

function cycleFrame(filePath, delta) {
  const item = currentState?.matches?.find(m => m.file_path === filePath);
  if (!item || !item.available_frames || item.available_frames.length <= 1) return;
  const currentIdx = item.active_frame_index >= 0 ? item.active_frame_index : 0;
  let nextIdx = (currentIdx + delta) % item.available_frames.length;
  if (nextIdx < 0) nextIdx = item.available_frames.length - 1;
  const targetFrame = item.available_frames[nextIdx];
  if (targetFrame) {
    setActiveFrame(filePath, targetFrame.timestamp);
  }
}

// ------------------------------------------------------------- Modal Handlers

let modalState = { filePath: "", frames: [], activeIdx: 0, title: "", details: "" };

function openModal(filePath, imgSrc, title, details, ocrSample = "", frames = [], activeIdx = 0) {
  modalState = {
    filePath: filePath || "",
    frames: frames || [],
    activeIdx: activeIdx >= 0 ? activeIdx : 0,
    title: title || "",
    details: details || ""
  };
  const modal = document.getElementById("image-modal");
  const img = document.getElementById("modal-img");
  img.src = `/api/thumbnail?path=${encodeURIComponent(imgSrc)}`;
  document.getElementById("modal-title").textContent = title;
  document.getElementById("modal-details").textContent = details;

  const ocrBox = document.getElementById("modal-ocr-box");
  if (ocrSample) {
    ocrBox.textContent = "OCR Text: " + ocrSample;
    ocrBox.style.display = "block";
  } else {
    ocrBox.style.display = "none";
  }

  const thumbNav = document.getElementById("modal-thumb-nav");
  if (frames && frames.length > 1) {
    thumbNav.style.display = "flex";
    document.getElementById("modal-counter").textContent = `Frame ${modalState.activeIdx + 1} of ${frames.length}`;
  } else {
    thumbNav.style.display = "none";
  }

  modal.style.display = "flex";
}

function modalCycleFrame(delta) {
  if (!modalState.frames || modalState.frames.length <= 1) return;
  let nextIdx = (modalState.activeIdx + delta) % modalState.frames.length;
  if (nextIdx < 0) nextIdx = modalState.frames.length - 1;
  modalState.activeIdx = nextIdx;
  const frame = modalState.frames[nextIdx];

  document.getElementById("modal-img").src = `/api/thumbnail?path=${encodeURIComponent(frame.image_path)}`;
  document.getElementById("modal-counter").textContent = `Frame ${nextIdx + 1} of ${modalState.frames.length} (t = ${frame.timestamp}s)`;

  const ocrBox = document.getElementById("modal-ocr-box");
  if (frame.ocr_sample) {
    ocrBox.textContent = "OCR Text: " + frame.ocr_sample + (frame.has_credits ? " [⚠️ Staff Credits Detected]" : "");
    ocrBox.style.display = "block";
  } else {
    ocrBox.style.display = "none";
  }

  if (modalState.filePath) {
    setActiveFrame(modalState.filePath, frame.timestamp);
  }
}

async function modalCaptureCustom() {
  const val = document.getElementById("modal-time-input").value.trim();
  if (!val || !modalState.filePath) return;
  await captureCustomFrame(modalState.filePath, val);
  document.getElementById("modal-time-input").value = "";
  closeModal();
}

async function modalRescanFile() {
  const win = document.getElementById("modal-rescan-win").value.trim();
  if (!modalState.filePath) return;
  await rescanSingleFile(modalState.filePath, win);
  document.getElementById("modal-rescan-win").value = "";
  closeModal();
}

function closeModal() {
  document.getElementById("image-modal").style.display = "none";
}

// ------------------------------------------------------------- Interactive Video Scrubber & Filmstrip

let scrubberState = {
  filePath: "",
  startTime: 50.0,
  duration: 20.0,
  videoDuration: 0.0,
  currentClipTime: 50.0,
  keyframes: [],
  chapters: [],
};

function formatTimecode(seconds) {
  if (isNaN(seconds) || seconds < 0) return "00:00.0";
  const total = Math.max(0, Number(seconds));
  const m = Math.floor(total / 60);
  const s = (total % 60).toFixed(1);
  const mm = m < 10 ? "0" + m : "" + m;
  const ss = (total % 60) < 10 ? "0" + s : "" + s;
  return `${mm}:${ss}`;
}

async function openScrubber(filePath, initialStartTime = 50.0) {
  if (!filePath) return;
  closeModal();
  scrubberState.filePath = filePath;
  const fileName = filePath.split(/[/\\]/).pop() || filePath;

  let start = typeof initialStartTime === "number" ? initialStartTime : parseFloat(initialStartTime);
  if (isNaN(start) || start <= 0) start = 50.0;
  scrubberState.startTime = start;
  scrubberState.duration = 20.0;
  scrubberState.currentClipTime = start;

  document.getElementById("scrub-file-title").textContent = `🎬 Manual Title Card Scrubber: ${fileName}`;
  document.getElementById("scrub-file-subtitle").textContent = filePath;
  document.getElementById("scrub-start-input").value = formatTimecode(start);
  document.getElementById("scrub-duration-select").value = "20";

  const modal = document.getElementById("scrubber-modal");
  modal.style.display = "flex";

  await loadScrubberPreview();
}

function modalOpenScrubber() {
  if (modalState && modalState.filePath) {
    const currentFrame = modalState.frames && modalState.frames[modalState.activeIdx];
    const initialT = currentFrame ? currentFrame.timestamp : 50.0;
    openScrubber(modalState.filePath, initialT);
  }
}

function closeScrubberModal() {
  const video = document.getElementById("scrub-video");
  if (video) {
    video.pause();
    video.src = "";
  }
  document.getElementById("scrubber-modal").style.display = "none";
}

async function refreshScrubberPreview() {
  const startVal = document.getElementById("scrub-start-input").value.trim();
  const durVal = parseFloat(document.getElementById("scrub-duration-select").value) || 20.0;

  scrubberState.startTime = startVal;
  scrubberState.duration = durVal;
  await loadScrubberPreview();
}

async function loadScrubberPreview() {
  const loading = document.getElementById("scrub-loading");
  const loadingText = document.getElementById("scrub-loading-text");
  const video = document.getElementById("scrub-video");
  const filmstrip = document.getElementById("scrub-filmstrip");
  const chaptersBar = document.getElementById("scrub-chapters-bar");

  loading.style.display = "flex";
  loadingText.textContent = `Generating fast ${scrubberState.duration}s scrubable preview clip...`;
  video.pause();
  filmstrip.innerHTML = "";
  chaptersBar.style.display = "none";
  chaptersBar.innerHTML = "";

  try {
    const res = await fetch("/api/scrub_preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_path: scrubberState.filePath,
        start_time: scrubberState.startTime,
        duration: scrubberState.duration
      })
    });
    const data = await res.json();
    if (!data.success) {
      alert("Error generating scrub preview: " + (data.error || "Unknown error"));
      loading.style.display = "none";
      return;
    }

    scrubberState.startTime = data.start_time;
    scrubberState.duration = data.duration;
    scrubberState.videoDuration = data.video_duration || 0;
    scrubberState.keyframes = data.keyframes || [];
    scrubberState.chapters = data.chapters || [];
    scrubberState.currentClipTime = data.start_time;

    // Render Chapters Bar if available
    if (data.chapters && data.chapters.length > 0) {
      chaptersBar.innerHTML = '<span style="font-size:11px;color:var(--text-secondary);font-weight:600;">Chapters:</span>';
      data.chapters.slice(0, 8).forEach(c => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "chapter-chip";
        chip.textContent = `${c.title} (${formatTimecode(c.start)})`;
        chip.onclick = () => {
          document.getElementById("scrub-start-input").value = formatTimecode(c.start);
          refreshScrubberPreview();
        };
        chaptersBar.appendChild(chip);
      });
      chaptersBar.style.display = "flex";
    }

    // Set Video Source
    if (data.has_video && data.video_url) {
      video.src = data.video_url;
      video.load();
    } else {
      video.src = "";
    }

    // Render Keyframes Filmstrip
    renderFilmstrip(data.keyframes);

    updateTimecodeDisplay(scrubberState.startTime);

  } catch (err) {
    alert("Failed to load scrub preview: " + err.message);
  } finally {
    loading.style.display = "none";
  }
}

function renderFilmstrip(keyframes) {
  const strip = document.getElementById("scrub-filmstrip");
  strip.innerHTML = "";
  if (!keyframes || keyframes.length === 0) {
    strip.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:8px;">No filmstrip frames available.</div>';
    return;
  }

  keyframes.forEach((kf, idx) => {
    const card = document.createElement("div");
    card.className = "filmstrip-card";
    card.title = `Click to jump to t = ${kf.timestamp}s. Double-click to set as Title Card.`;

    const img = document.createElement("img");
    img.src = kf.thumbnail_url;
    img.alt = `Keyframe ${idx + 1}`;
    card.appendChild(img);

    const footer = document.createElement("div");
    footer.className = "filmstrip-footer";

    const timeSpan = document.createElement("span");
    timeSpan.className = "filmstrip-time";
    timeSpan.textContent = formatTimecode(kf.timestamp);
    footer.appendChild(timeSpan);

    const useBtn = document.createElement("button");
    useBtn.type = "button";
    useBtn.className = "filmstrip-btn";
    useBtn.textContent = "Use";
    useBtn.onclick = (e) => {
      e.stopPropagation();
      captureExactTime(scrubberState.filePath, kf.timestamp);
    };
    footer.appendChild(useBtn);

    card.appendChild(footer);

    // Single click: jump video
    card.onclick = () => {
      const video = document.getElementById("scrub-video");
      const relTime = Math.max(0, kf.timestamp - scrubberState.startTime);
      if (video && !isNaN(video.duration)) {
        video.currentTime = Math.min(relTime, video.duration);
        video.pause();
      }
      updateTimecodeDisplay(kf.timestamp);
      document.querySelectorAll(".filmstrip-card").forEach(c => c.classList.remove("active-frame"));
      card.classList.add("active-frame");
    };

    // Double click: apply directly
    card.ondblclick = () => {
      captureExactTime(scrubberState.filePath, kf.timestamp);
    };

    strip.appendChild(card);
  });
}

function onScrubTimeUpdate() {
  const video = document.getElementById("scrub-video");
  if (!video) return;
  const absTime = scrubberState.startTime + (video.currentTime || 0);
  updateTimecodeDisplay(absTime);
}

function updateTimecodeDisplay(absTime) {
  scrubberState.currentClipTime = absTime;
  const formatted = formatTimecode(absTime);
  const text = `${formatted} (${absTime.toFixed(1)}s)`;
  const timecodeEl = document.getElementById("scrub-current-timecode");
  const applyTimeEl = document.getElementById("scrub-apply-time");
  if (timecodeEl) timecodeEl.textContent = text;
  if (applyTimeEl) applyTimeEl.textContent = formatted;
}

function stepScrubVideo(deltaSeconds) {
  const video = document.getElementById("scrub-video");
  if (!video) return;
  video.pause();
  const playBtn = document.getElementById("btn-play-pause");
  if (playBtn) playBtn.textContent = "⏯ Play";
  const duration = video.duration || scrubberState.duration || 20.0;
  const newRelTime = Math.max(0, Math.min((video.currentTime || 0) + deltaSeconds, duration));
  video.currentTime = newRelTime;
  const absTime = scrubberState.startTime + newRelTime;
  updateTimecodeDisplay(absTime);
}

function toggleScrubPlayPause() {
  const video = document.getElementById("scrub-video");
  const btn = document.getElementById("btn-play-pause");
  if (!video) return;
  if (video.paused) {
    video.play();
    btn.textContent = "⏸ Pause";
  } else {
    video.pause();
    btn.textContent = "⏯ Play";
  }
}

async function applyCurrentScrubFrame() {
  const video = document.getElementById("scrub-video");
  const relTime = (video && !isNaN(video.currentTime)) ? video.currentTime : 0;
  const absTime = scrubberState.startTime + relTime;
  await captureExactTime(scrubberState.filePath, absTime);
}

async function captureExactTime(filePath, timestamp) {
  if (!filePath) return;
  const t = typeof timestamp === "number" ? timestamp.toFixed(2) : timestamp;
  const btn = document.getElementById("btn-set-title-card");
  if (btn) {
    btn.disabled = true;
    btn.textContent = `Capturing t = ${t}s...`;
  }
  try {
    await captureCustomFrame(filePath, t);
    closeScrubberModal();
  } finally {
    if (btn) {
      btn.disabled = false;
      const formatted = formatTimecode(Number(timestamp));
      btn.innerHTML = `🎯 Set as Title Card (<span id="scrub-apply-time">${formatted}</span>)`;
    }
  }
}

// ------------------------------------------------------------- Dynamic Folder Browser & Autocomplete

let dirBrowserState = {
  currentPath: "",
  selectedPath: "",
  parentPath: null,
  breadcrumbs: [],
  shortcuts: [],
  directories: [],
  videoFiles: [],
  videoCount: 0,
  filterQuery: "",
};

async function openDirBrowser(initialPath) {
  const modal = document.getElementById("dir-browser-modal");
  if (!modal) return;
  modal.style.display = "flex";

  const target = initialPath || document.getElementById("series-dir").value.trim() || "";
  const filterInp = document.getElementById("dir-filter-input");
  if (filterInp) filterInp.value = "";
  dirBrowserState.filterQuery = "";
  await loadBrowseDirectory(target);
}

function closeDirBrowser() {
  const modal = document.getElementById("dir-browser-modal");
  if (modal) modal.style.display = "none";
}

async function loadBrowseDirectory(targetPath) {
  const listEl = document.getElementById("dir-list");
  if (listEl) {
    listEl.innerHTML = `<div class="dir-empty-state"><div class="spinner" style="margin: 0 auto 8px;"></div>Loading folders...</div>`;
  }
  try {
    const res = await fetch(`/api/browse_dir?path=${encodeURIComponent(targetPath || "")}`);
    const data = await res.json();
    if (!data.success && !data.current_path) {
      if (listEl) listEl.innerHTML = `<div class="dir-empty-state">Error: ${escapeHtml(data.error || "Could not read directory")}</div>`;
      return;
    }

    dirBrowserState.currentPath = data.current_path || "";
    dirBrowserState.selectedPath = data.current_path || "";
    dirBrowserState.parentPath = data.parent_path || null;
    dirBrowserState.breadcrumbs = data.breadcrumbs || [];
    dirBrowserState.shortcuts = data.shortcuts || [];
    dirBrowserState.directories = data.directories || [];
    dirBrowserState.videoFiles = data.video_files || [];
    dirBrowserState.videoCount = data.video_count || 0;

    renderDirShortcuts();
    renderDirBreadcrumbs();
    renderDirStatus();
    renderDirList();
    renderDirVideos();
    updateDirSelectedDisplay();
  } catch (err) {
    console.error("Failed to browse directory:", err);
    if (listEl) listEl.innerHTML = `<div class="dir-empty-state">Failed to connect to server</div>`;
  }
}

function navDirUp() {
  if (dirBrowserState.parentPath) {
    loadBrowseDirectory(dirBrowserState.parentPath);
  }
}

function renderDirShortcuts() {
  const cont = document.getElementById("dir-shortcuts-list");
  if (!cont) return;
  cont.innerHTML = "";
  dirBrowserState.shortcuts.forEach(sc => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "dir-shortcut-chip";
    chip.innerHTML = `<span>${sc.icon || "📁"}</span> <span>${escapeHtml(sc.name)}</span>`;
    chip.title = sc.path;
    chip.onclick = () => loadBrowseDirectory(sc.path);
    cont.appendChild(chip);
  });
}

function renderDirBreadcrumbs() {
  const bc = document.getElementById("dir-breadcrumbs");
  const upBtn = document.getElementById("btn-dir-up");
  if (upBtn) upBtn.disabled = !dirBrowserState.parentPath;
  if (!bc) return;
  bc.innerHTML = "";

  const crumbs = dirBrowserState.breadcrumbs;
  crumbs.forEach((crumb, idx) => {
    const isLast = (idx === crumbs.length - 1);
    const span = document.createElement("span");
    span.className = `dir-crumb ${isLast ? "active" : ""}`;
    span.textContent = crumb.name;
    span.title = crumb.path;
    span.onclick = () => loadBrowseDirectory(crumb.path);
    bc.appendChild(span);

    if (!isLast) {
      const sep = document.createElement("span");
      sep.className = "dir-crumb-sep";
      sep.textContent = "/";
      bc.appendChild(sep);
    }
  });
  bc.scrollLeft = bc.scrollWidth;
}

function renderDirStatus() {
  const banner = document.getElementById("dir-status-banner");
  if (!banner) return;
  const vCount = dirBrowserState.videoCount;
  const dCount = dirBrowserState.directories.length;

  if (vCount > 0) {
    banner.className = "dir-status-banner has-videos";
    banner.style.display = "flex";
    banner.innerHTML = `<span>🎬</span> <strong>${vCount} video file${vCount === 1 ? "" : "s"} detected directly in this folder!</strong> (${dCount} subfolder${dCount === 1 ? "" : "s"})`;
  } else if (dCount > 0) {
    banner.className = "dir-status-banner no-videos";
    banner.style.display = "flex";
    banner.innerHTML = `<span>📁</span> <span>No video files directly in this folder. Click any of the <strong>${dCount}</strong> subfolders below to drill down.</span>`;
  } else {
    banner.className = "dir-status-banner no-videos";
    banner.style.display = "flex";
    banner.innerHTML = `<span>ℹ️</span> <span>Folder is empty or contains no media files.</span>`;
  }
}

function renderDirList() {
  const listEl = document.getElementById("dir-list");
  const countEl = document.getElementById("dir-item-count");
  if (!listEl) return;
  listEl.innerHTML = "";

  const q = (dirBrowserState.filterQuery || "").toLowerCase().trim();
  const filtered = dirBrowserState.directories.filter(d => !q || d.name.toLowerCase().includes(q));

  if (countEl) {
    countEl.textContent = `${filtered.length} of ${dirBrowserState.directories.length} folder${dirBrowserState.directories.length === 1 ? "" : "s"}`;
  }

  if (filtered.length === 0) {
    listEl.innerHTML = `<div class="dir-empty-state">${q ? 'No matching folders found for "' + escapeHtml(q) + '"' : 'No subfolders found.'}</div>`;
    return;
  }

  filtered.forEach(d => {
    const item = document.createElement("div");
    item.className = `dir-item ${dirBrowserState.selectedPath === d.path ? "selected" : ""}`;

    const left = document.createElement("div");
    left.className = "dir-item-left";
    left.innerHTML = `<span class="dir-item-icon">📁</span><span class="dir-item-name" title="${escapeHtml(d.name)}">${escapeHtml(d.name)}</span>`;

    const right = document.createElement("div");
    right.className = "dir-item-right";
    if (d.video_count > 0) {
      const vBadge = document.createElement("span");
      vBadge.className = "dir-badge-video";
      vBadge.textContent = `🎬 ${d.video_count} video${d.video_count === 1 ? "" : "s"}`;
      right.appendChild(vBadge);
    }
    const chevron = document.createElement("span");
    chevron.className = "dir-item-chevron";
    chevron.textContent = "›";
    right.appendChild(chevron);

    item.appendChild(left);
    item.appendChild(right);

    item.onclick = () => {
      dirBrowserState.selectedPath = d.path;
      document.querySelectorAll(".dir-item").forEach(el => el.classList.remove("selected"));
      item.classList.add("selected");
      updateDirSelectedDisplay();
    };

    item.ondblclick = () => {
      loadBrowseDirectory(d.path);
    };

    listEl.appendChild(item);
  });
}

function onFilterDirList() {
  const inp = document.getElementById("dir-filter-input");
  dirBrowserState.filterQuery = inp ? inp.value : "";
  renderDirList();
}

function renderDirVideos() {
  const wrap = document.getElementById("dir-videos-preview");
  const tags = document.getElementById("dir-videos-tags");
  if (!wrap || !tags) return;
  tags.innerHTML = "";
  if (dirBrowserState.videoFiles && dirBrowserState.videoFiles.length > 0) {
    wrap.style.display = "block";
    dirBrowserState.videoFiles.forEach(vf => {
      const pill = document.createElement("span");
      pill.className = "dir-video-pill";
      pill.textContent = vf;
      tags.appendChild(pill);
    });
    if (dirBrowserState.videoCount > dirBrowserState.videoFiles.length) {
      const more = document.createElement("span");
      more.className = "dir-video-pill";
      more.style.opacity = "0.7";
      more.textContent = `+${dirBrowserState.videoCount - dirBrowserState.videoFiles.length} more...`;
      tags.appendChild(more);
    }
  } else {
    wrap.style.display = "none";
  }
}

function updateDirSelectedDisplay() {
  const disp = document.getElementById("dir-current-display");
  if (disp) {
    disp.textContent = dirBrowserState.selectedPath || dirBrowserState.currentPath || "/";
  }
}

function confirmDirSelection() {
  const chosen = dirBrowserState.selectedPath || dirBrowserState.currentPath;
  if (!chosen) return;
  document.getElementById("series-dir").value = chosen;
  closeDirBrowser();

  const seriesNameInput = document.getElementById("series-name");
  if (seriesNameInput && !seriesNameInput.value.trim()) {
    const base = chosen.split(/[/\\]/).filter(Boolean).pop();
    if (base) {
      seriesNameInput.placeholder = `Auto-detected: ${base}`;
    }
  }
}

async function pickNativeFolder() {
  const currentVal = document.getElementById("series-dir").value.trim();
  try {
    const res = await fetch("/api/choose_folder_native", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initial_dir: currentVal }),
    });
    const data = await res.json();
    if (data.success && data.path) {
      document.getElementById("series-dir").value = data.path;
      closeDirBrowser();
      const seriesNameInput = document.getElementById("series-name");
      if (seriesNameInput && !seriesNameInput.value.trim()) {
        const base = data.path.split(/[/\\]/).filter(Boolean).pop();
        if (base) {
          seriesNameInput.placeholder = `Auto-detected: ${base}`;
        }
      }
    } else if (data.error && data.error !== "Cancelled") {
      alert(`Native Folder Picker: ${data.error}`);
    }
  } catch (err) {
    console.error("Native folder chooser error:", err);
  }
}

function initDirAutocomplete() {
  const input = document.getElementById("series-dir");
  const dropdown = document.getElementById("dir-autocomplete");
  if (!input || !dropdown) return;

  let debounceTimer = null;
  let activeIndex = -1;

  async function updateAutocomplete() {
    const val = input.value.trim();
    if (!val || val.length < 2) {
      dropdown.style.display = "none";
      dropdown.innerHTML = "";
      return;
    }

    try {
      const res = await fetch(`/api/browse_dir?path=${encodeURIComponent(val)}`);
      const data = await res.json();
      if (!data.success || !data.directories || data.directories.length === 0) {
        dropdown.style.display = "none";
        return;
      }

      dropdown.innerHTML = "";
      activeIndex = -1;

      const term = val.split(/[/\\]/).pop().toLowerCase();
      const matches = data.directories.filter(d => !term || d.name.toLowerCase().includes(term));
      const listToRender = (matches.length > 0 ? matches : data.directories).slice(0, 8);

      listToRender.forEach((d) => {
        const item = document.createElement("div");
        item.className = "dir-auto-item";
        item.innerHTML = `
          <div class="dir-auto-item-name">
            <span>📁</span>
            <span>${escapeHtml(d.name)}</span>
          </div>
          ${d.video_count > 0 ? `<span class="dir-auto-badge">🎬 ${d.video_count}</span>` : ""}
        `;
        item.onclick = (e) => {
          e.stopPropagation();
          input.value = d.path;
          dropdown.style.display = "none";
          // If folder has subfolders, immediately query them so the user can continue drilling down
          if (d.has_subdirs) {
            updateAutocomplete();
          }
        };
        dropdown.appendChild(item);
      });

      const footer = document.createElement("div");
      footer.className = "dir-auto-footer";
      footer.innerHTML = `<span>📁</span> <span>Open Full Folder Browser...</span>`;
      footer.onclick = (e) => {
        e.stopPropagation();
        dropdown.style.display = "none";
        openDirBrowser(val);
      };
      dropdown.appendChild(footer);

      dropdown.style.display = "flex";
    } catch (_) {
      dropdown.style.display = "none";
    }
  }

  input.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(updateAutocomplete, 250);
  });

  input.addEventListener("keydown", (e) => {
    if (dropdown.style.display === "none") return;
    const items = dropdown.querySelectorAll(".dir-auto-item");
    if (e.key === "ArrowDown") {
      e.preventDefault();
      activeIndex = (activeIndex + 1) % items.length;
      items.forEach((it, i) => it.classList.toggle("active", i === activeIndex));
      if (items[activeIndex]) items[activeIndex].scrollIntoView({ block: "nearest" });
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      activeIndex = (activeIndex - 1 + items.length) % items.length;
      items.forEach((it, i) => it.classList.toggle("active", i === activeIndex));
      if (items[activeIndex]) items[activeIndex].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" && activeIndex >= 0 && items[activeIndex]) {
      e.preventDefault();
      items[activeIndex].click();
    } else if (e.key === "Escape") {
      dropdown.style.display = "none";
    }
  });

  document.addEventListener("click", (e) => {
    if (!input.contains(e.target) && !dropdown.contains(e.target)) {
      dropdown.style.display = "none";
    }
  });
}

// ------------------------------------------------------------- Database & Community Sync

let cachedDbRecords = [];

async function loadDatabaseStats() {
  try {
    const res = await fetch("/api/database/stats");
    if (!res.ok) return;
    const stats = await res.json();
    const countEl = document.getElementById("nav-db-count");
    if (countEl) {
      countEl.textContent = stats.total_records || 0;
    }
    const statTotal = document.getElementById("db-stat-total");
    const statManual = document.getElementById("db-stat-manual");
    const statAuto = document.getElementById("db-stat-auto");
    const statSeries = document.getElementById("db-stat-series");
    if (statTotal) statTotal.textContent = stats.total_records || 0;
    if (statManual) statManual.textContent = stats.manual_confirmations || 0;
    if (statAuto) statAuto.textContent = stats.auto_scans || 0;
    if (statSeries) statSeries.textContent = stats.series_count || 0;
  } catch (err) {
    console.debug("Failed to load database stats:", err);
  }
}

async function openDatabaseSyncModal() {
  const modal = document.getElementById("db-sync-modal");
  if (!modal) return;
  modal.style.display = "flex";
  setDbSyncStatus("", "");
  await loadDatabaseStats();
  await loadDatabaseRecords();
}

function closeDatabaseSyncModal() {
  const modal = document.getElementById("db-sync-modal");
  if (modal) modal.style.display = "none";
}

async function loadDatabaseRecords() {
  const tbody = document.getElementById("db-records-tbody");
  if (tbody) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 20px;">Loading database records...</td></tr>';
  }
  try {
    const res = await fetch("/api/database_records");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    cachedDbRecords = Array.isArray(data.records) ? data.records : [];
    filterDatabaseTable();
  } catch (err) {
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--color-danger); padding: 20px;">Failed to load records: ${escapeHtml(err.message)}</td></tr>`;
    }
  }
}

function setDbSyncStatus(msg, type = "info") {
  const statusEl = document.getElementById("db-sync-status");
  if (!statusEl) return;
  if (!msg) {
    statusEl.style.display = "none";
    statusEl.textContent = "";
    statusEl.className = "db-sync-status";
    return;
  }
  statusEl.className = `db-sync-status ${type}`;
  statusEl.textContent = msg;
  statusEl.style.display = "block";
}

function filterDatabaseTable() {
  const query = (document.getElementById("db-search-input")?.value || "").trim().toLowerCase();
  if (!query) {
    renderDatabaseTable(cachedDbRecords);
    return;
  }
  const filtered = cachedDbRecords.filter(r => {
    return (
      (r.series_name && r.series_name.toLowerCase().includes(query)) ||
      (r.episode_code && r.episode_code.toLowerCase().includes(query)) ||
      (r.episode_title && r.episode_title.toLowerCase().includes(query)) ||
      (r.file_name && r.file_name.toLowerCase().includes(query))
    );
  });
  renderDatabaseTable(filtered);
}

function renderDatabaseTable(records) {
  const tbody = document.getElementById("db-records-tbody");
  if (!tbody) return;
  tbody.innerHTML = "";

  if (!records || records.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 24px;">No matching title card records found.</td></tr>';
    return;
  }

  const fragment = document.createDocumentFragment();
  records.forEach(r => {
    const tr = document.createElement("tr");

    const tdSeries = document.createElement("td");
    tdSeries.textContent = r.series_name || "-";
    tr.appendChild(tdSeries);

    const tdEp = document.createElement("td");
    tdEp.style.fontWeight = "600";
    tdEp.textContent = r.episode_code || "-";
    tr.appendChild(tdEp);

    const tdTitle = document.createElement("td");
    tdTitle.textContent = r.episode_title || "-";
    tr.appendChild(tdTitle);

    const tdTime = document.createElement("td");
    const tVal = (typeof r.title_card_time === "number") ? r.title_card_time : parseFloat(r.title_card_time);
    tdTime.textContent = !isNaN(tVal) ? formatTimecode(tVal) : (r.title_card_time || "-");
    tr.appendChild(tdTime);

    const tdType = document.createElement("td");
    const isManual = (r.confirmation_type === "manual");
    tdType.innerHTML = `<span class="db-type-badge ${isManual ? "manual" : "auto"}">${isManual ? "🎯 Manual" : "⚡ Auto"}</span>`;
    tr.appendChild(tdType);

    const tdScore = document.createElement("td");
    const scoreVal = parseFloat(r.match_score);
    tdScore.textContent = !isNaN(scoreVal) ? `${Math.round(scoreVal * 100)}%` : "-";
    tr.appendChild(tdScore);

    fragment.appendChild(tr);
  });

  tbody.appendChild(fragment);
}

async function pullCommunityDatabase() {
  const btn = document.getElementById("btn-pull-community");
  if (btn) btn.disabled = true;
  setDbSyncStatus("⬇️ Fetching latest community title cards from GitHub repository...", "info");

  try {
    const res = await fetch("/api/database/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });
    const data = await res.json();
    if (data.success) {
      setDbSyncStatus(
        `✅ Community database synced: ${data.imported || 0} records imported (${data.upgraded || 0} upgraded to manual confirmations). Total loaded: ${data.records_read || 0}.`,
        "success"
      );
      await loadDatabaseStats();
      await loadDatabaseRecords();
    } else {
      setDbSyncStatus(`❌ Failed to pull community database: ${data.error || "Unknown error"}`, "error");
    }
  } catch (err) {
    setDbSyncStatus(`❌ Network error while pulling community database: ${err.message}`, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function syncDatabaseToRepo() {
  const btn = document.getElementById("btn-sync-repo");
  if (btn) btn.disabled = true;
  setDbSyncStatus("⬆️ Exporting database and syncing to git repository...", "info");

  try {
    const res = await fetch("/api/database/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ push: true })
    });
    const data = await res.json();
    if (data.success) {
      const pushNote = data.pushed ? "Changes pushed to remote git repository." : "Saved & committed to local git repository.";
      setDbSyncStatus(`✅ ${data.message} ${pushNote} (${data.records_count} records formatted in data/community_database.json)`, "success");
      await loadDatabaseStats();
      await loadDatabaseRecords();
    } else {
      setDbSyncStatus(`⚠️ Sync notification: ${data.message || data.error || "Could not complete git sync."}`, "info");
      await loadDatabaseStats();
    }
  } catch (err) {
    setDbSyncStatus(`❌ Sync error: ${err.message}`, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function exportDatabaseJson() {
  const btn = document.getElementById("btn-export-json");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch("/api/database/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });
    const data = await res.json();
    if (data.success && data.data) {
      const blob = new Blob([JSON.stringify(data.data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "community_database.json";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setDbSyncStatus(`✅ Exported ${data.records_count} title card records to community_database.json!`, "success");
    } else {
      setDbSyncStatus(`❌ Export failed: ${data.error || "Unknown error"}`, "error");
    }
  } catch (err) {
    setDbSyncStatus(`❌ Export error: ${err.message}`, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function quitApplication() {
  if (!confirm("Are you sure you want to quit TVBackupMatcher?\nAll background processes will be terminated cleanly.")) {
    return;
  }

  document.body.innerHTML = `
    <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; background: #0f172a; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; text-align: center; padding: 20px;">
      <div style="font-size: 54px; margin-bottom: 16px;">👋</div>
      <h1 style="font-size: 26px; font-weight: 700; margin-bottom: 8px;">TVBackupMatcher Has Shut Down</h1>
      <p style="font-size: 15px; color: #94a3b8; max-width: 480px; margin-bottom: 24px;">All background server processes have terminated. You can safely close this window.</p>
    </div>
  `;

  try {
    await fetch("/api/shutdown", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });
  } catch (_) {
    // Server shutdown might drop connection before responding, which is expected
  }

  try {
    window.close();
  } catch (_) {}
}

