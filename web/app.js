let currentState = null;
let pollTimer = null;
let lastRenderedVersion = -1;
let currentFilter = "all";

document.addEventListener("DOMContentLoaded", () => {
  checkSystemStatus();
  loadUserSettings();
  pollState();
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
    const parts = path.split("/").filter(Boolean);
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

function renderCards(matches, availableEpisodes, reviewThreshold) {
  const grid = document.getElementById("results-grid");
  grid.innerHTML = "";
  const filter = document.getElementById("match-filter").value;

  matches.forEach(item => {
    // Filter logic
    if (filter === "ready" && item.needs_review) return;
    if (filter === "review" && !item.needs_review) return;
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
      thumbWrap.innerHTML = `<div style="height:100%;display:flex;align-items:center;justify-content:center;color:#6b7280;font-size:12px;">No Title Card Captured</div>`;
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

    // Media Info Badges (Resolution, Codec, HDR/DV, Audio)
    if (item.media_info) {
      const mb = document.createElement("div");
      mb.className = "media-badges";
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
    scorePill.textContent = item.is_overridden ? (item.status === "skipped" ? "Excluded" : "Manual") : `${Math.round(item.score * 100)}%`;
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

    // Override & Confirm Row
    const overrideRow = document.createElement("div");
    overrideRow.className = "override-row";

    const select = document.createElement("select");
    select.className = "override-select";

    const defaultOpt = document.createElement("option");
    defaultOpt.value = "";
    defaultOpt.textContent = "-- Reassign Episode --";
    select.appendChild(defaultOpt);

    const skipOpt = document.createElement("option");
    skipOpt.value = "__skip__";
    skipOpt.textContent = "-- Exclude / Skip File --";
    if (item.status === "skipped") skipOpt.selected = true;
    select.appendChild(skipOpt);

    (availableEpisodes || []).forEach(ep => {
      const opt = document.createElement("option");
      opt.value = ep.code;
      opt.textContent = `${ep.code} - ${ep.title}`;
      if (ep.code === item.episode_code && item.status !== "skipped") {
        opt.selected = true;
      }
      select.appendChild(opt);
    });

    select.onchange = (e) => applyOverride(item.file_path, e.target.value);

    const actionFlex = document.createElement("div");
    actionFlex.style.display = "flex";
    actionFlex.style.gap = "8px";
    actionFlex.style.alignItems = "center";
    actionFlex.appendChild(select);

    if (item.needs_review) {
      const confirmBtn = document.createElement("button");
      confirmBtn.className = "btn-confirm";
      confirmBtn.textContent = "✓ Confirm";
      confirmBtn.title = "Approve this match for reorganization";
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

    content.appendChild(toolsRow);

    // Collapsible Frame Drawer
    const drawer = document.createElement("div");
    drawer.className = "frame-drawer";
    drawer.style.display = "none";
    btnDrawer.onclick = () => {
      drawer.style.display = drawer.style.display === "none" ? "flex" : "none";
    };

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
