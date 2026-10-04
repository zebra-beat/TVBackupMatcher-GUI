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

    const tools = ["ffmpeg", "ffprobe", "tesseract"];
    tools.forEach(tool => {
      const info = data[tool];
      const pill = document.createElement("span");
      pill.className = `status-pill ${info && info.available ? "online" : "offline"}`;
      pill.textContent = `${tool}: ${info && info.available ? "Ready" : "Missing"}`;
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
      img.onclick = () => openModal(item.image_path, `${item.episode_code || ""} ${item.episode_title}`, `Timestamp: t = ${item.timestamp}s`);
      thumbWrap.appendChild(img);

      const tag = document.createElement("span");
      tag.className = "thumb-tag";
      tag.textContent = `t = ${item.timestamp}s`;
      thumbWrap.appendChild(tag);
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

    card.appendChild(content);
    grid.appendChild(card);
  });
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

function openModal(imgSrc, title, details) {
  const modal = document.getElementById("image-modal");
  const img = document.getElementById("modal-img");
  img.src = `/api/thumbnail?path=${encodeURIComponent(imgSrc)}`;
  document.getElementById("modal-title").textContent = title;
  document.getElementById("modal-details").textContent = details;
  modal.style.display = "flex";
}

function closeModal() {
  document.getElementById("image-modal").style.display = "none";
}
