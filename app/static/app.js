const state = {
  workspaceId: document.documentElement.dataset.workspaceId || "default",
  workflow: null,
  busy: false,
  history: [],
  currentPage: "workbench",
  opportunityResult: null,
  opportunityHistory: [],
  opportunityHistoryLoaded: false,
  opportunityHistoryOpen: true,
  opportunityCompareOpen: false,
  pendingOpportunityHandoff: null,
  opportunityGenerationBusy: false,
  engineStatus: "checking",
  pendingAdoptionSync: null,
  healthInfo: null,
  usage: null,
  betaUsage: null,
  bugReports: null,
  authStatus: null,
  authContext: null,
  members: [],
  prompts: [],
  activePromptKey: null,
  promptVersions: [],
  promptTestCases: [],
  promptTestRuns: [],
  workspaceAIProfile: null,
  activeAssetPreview: null,
  assetPacingProfile: window.localStorage.getItem("tackyflow:asset-pacing-profile:v1") || "auto",
  workbenchReturnPage: null,
};

const form = document.querySelector("#workflowForm");
const submitButton = document.querySelector("#submitButton");
const messageBar = document.querySelector("#messageBar");
const decisionPanel = document.querySelector("#decisionPanel");
const resultPanel = document.querySelector("#resultPanel");
const executionLogPanel = document.querySelector("#executionLogPanel");
const historyPanel = document.querySelector("#historyPanel");
const liveProgressPanel = document.querySelector("#liveProgressPanel");
let activePreviewTab = "script";
let scriptViewMode = "full";
let scriptEditMode = false;
let rhythmViewMode = "timeline";
let rhythmEditMode = false;
let rhythmDraftCues = [];
let assetCueEditWorkflowId = null;
let assetCueDrafts = [];
let assetFocusedCue = null;
const assetTimelinePlayheads = {};
let wordCountTouched = false;
let opportunityRequestId = 0;
let opportunityProgressTimer = null;
let opportunityProgressStartedAt = 0;
const opportunityItemProgressTimers = new Map();
let healthPollTimer = null;
let draftSaveTimer = null;
let draftReferenceFiles = [];
let draftDirty = false;
let draftRestoring = false;
let liveProgressStartedAt = 0;
let liveProgressTimer = null;
let liveProgressPollTimer = null;
let liveProgressPollPending = false;
let liveProgressHideTimer = null;
let passwordResetAccessToken = "";
let bugCapturedScreenshot = null;
let bugScreenshotEditorState = null;
let workspaceScriptSnippetsDraft = [];

const workbenchDraftStoragePrefix = "tackyflow:workbench-draft:v2";
const rememberedLoginEmailKey = "tackyflow:remembered-login-email:v1";
function adoptionRetryStorageKey() { return `tackyflow:adoption-retry:v1:${state.workspaceId}`; }
const workbenchDraftFieldIds = [
  "topic", "goal", "outputType", "targetWordCount", "brandVoice", "constraints",
  "targetAudience", "brandName", "contentLanguage", "contentRegion",
  "brandBrief", "referenceFileKind", "referenceTextType", "referenceFocus", "referenceText", "referenceUrls", "referenceBoundary",
];

const stageOrder = ["requirements", "ai_creation", "production_package", "approval_publish"];
const agentOrder = ["reference_analyst", "research", "verification", "writer", "editorial_critic", "production_planner", "visual_director", "youtube_reference_verifier", "production_quality", "publishing_preflight"];
const stageAgentOrder = {
  ai_creation: ["research", "verification", "writer", "editorial_critic"],
  production_package: ["production_planner", "visual_director", "youtube_reference_verifier", "production_quality", "publishing_preflight"],
};
const agentExpectedSeconds = {
  research: 90, verification: 75, writer: 90, editorial_critic: 90,
  production_planner: 150, visual_director: 90, youtube_reference_verifier: 120,
  production_quality: 60, publishing_preflight: 60,
};
const wordCountSuggestions = {
  short_video: 500,
  long_video: 1500,
  short_text_post: 350,
  thread: 800,
  long_text_article: 1800,
  carousel_slides: 700,
};
const assetPacingPresets = {
  short: {
    label: "短影音",
    duration: "30–60 秒",
    refresh: "前 6 秒每 2–3 秒；之後每 3–5 秒",
    shot: "單段 B-roll 2–4 秒",
    shotSeconds: [2, 4],
    note: "開場先呈現結果、問題或最有說服力的證據。",
  },
  review: {
    label: "開箱／評測",
    duration: "60–180 秒",
    refresh: "每 4–7 秒提供一次有意義的畫面變化",
    shot: "單段 B-roll 3–5 秒",
    shotSeconds: [3, 5],
    note: "優先使用產品實拍、操作過程與前後比較。",
  },
  long: {
    label: "教學／長影片",
    duration: "3 分鐘以上",
    refresh: "每 5–10 秒提供一次有意義的畫面變化",
    shot: "B-roll 3–6 秒；操作畫面可 5–12 秒",
    shotSeconds: [3, 6],
    note: "以理解為優先，不為換畫面而打斷完整操作。",
  },
};

const pageLabels = {
  dashboard: "工作總覽",
  opportunities: "內容機會",
  plan: "內容計畫",
  assets: "素材中心",
  publishing: "發布中心",
  performance: "成效回顧",
  settings: "系統設定",
  workbench: "AI 內容工作台",
};

document.querySelectorAll("[data-page]").forEach((button) => {
  button.addEventListener("click", () => showPage(button.dataset.page));
});
document.querySelectorAll("[data-page-link]").forEach((button) => {
  button.addEventListener("click", () => showPage(button.dataset.pageLink));
});
document.querySelectorAll(".page-start-task").forEach((button) => {
  button.addEventListener("click", () => startNewTask("", { clearDraft: true }));
});
document.querySelector("#opportunityForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await generateOpportunities();
});
document.querySelectorAll(".opportunity-platform-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    window.setTimeout(() => chip.classList.toggle("selected", chip.querySelector("input").checked), 0);
  });
});
document.querySelector("#opportunityHistoryToggle").addEventListener("click", () => toggleOpportunityHistory(true));
document.querySelector("#opportunityHistoryClose").addEventListener("click", () => toggleOpportunityHistory(false));
document.querySelector("#opportunityHistoryFilter").addEventListener("input", renderOpportunityHistory);
document.querySelector("#opportunityMoreButton").addEventListener("click", () => generateOpportunities({ moreAngles: true }));
document.querySelector("#opportunityCompareButton").addEventListener("click", () => {
  state.opportunityCompareOpen = !state.opportunityCompareOpen;
  renderOpportunityComparison();
});
document.querySelector("#engineReconnectButton").addEventListener("click", async () => {
  const healthy = await checkHealth(true);
  if (healthy && state.currentPage === "opportunities") {
    await loadOpportunityHistory(true);
    if (state.opportunityResult?.id) await loadOpportunityGeneration(state.opportunityResult.id, { updateUrl: false });
  }
});
document.querySelector("#assetSearch").addEventListener("input", renderAssets);
document.querySelector("#assetTypeFilter").addEventListener("change", renderAssets);
document.querySelector("#assetStatusFilter").addEventListener("change", renderAssets);
document.querySelector("#assetPacingProfile").value = state.assetPacingProfile;
document.querySelector("#assetPacingProfile").addEventListener("change", (event) => {
  state.assetPacingProfile = event.target.value;
  window.localStorage.setItem("tackyflow:asset-pacing-profile:v1", state.assetPacingProfile);
  renderAssets();
});
document.querySelectorAll("[data-close-asset-preview]").forEach((button) => button.addEventListener("click", closeAssetPreview));
document.querySelector("#assetPreviewDownload").addEventListener("click", () => downloadAsset(state.activeAssetPreview, "txt"));
document.querySelector("#assetPreviewExport").addEventListener("click", () => downloadAsset(state.activeAssetPreview, "json"));
document.querySelector("#assetPreviewOpenWorkflow").addEventListener("click", () => {
  const workflowId = state.activeAssetPreview?.workflow?.id;
  closeAssetPreview();
  if (workflowId) openHistoryWorkflow(workflowId);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !document.querySelector("#assetPreviewModal").classList.contains("hidden")) closeAssetPreview();
  if (event.key === "Escape" && !document.querySelector("#bugScreenshotEditor").classList.contains("hidden")) closeBugScreenshotEditor(false);
  else if (event.key === "Escape" && !document.querySelector("#bugReportModal").classList.contains("hidden")) closeBugReport();
});
document.querySelector("#publishingStatusFilter").addEventListener("change", renderPublishing);
document.querySelector("#mobileNavToggle").addEventListener("click", () => toggleMobileNav());
document.querySelector("#mobileNavBackdrop").addEventListener("click", () => toggleMobileNav(false));
document.querySelector("#notificationButton").addEventListener("click", () => {
  const panel = document.querySelector("#notificationPanel");
  const open = panel.classList.toggle("hidden") === false;
  document.querySelector("#notificationButton").setAttribute("aria-expanded", String(open));
  renderNotifications();
});

document.querySelector("#targetWordCount").addEventListener("input", () => {
  wordCountTouched = true;
  updateEstimatedDuration();
});
document.querySelector("#outputType").addEventListener("change", (event) => {
  if (!wordCountTouched) {
    document.querySelector("#targetWordCount").value = wordCountSuggestions[event.target.value] || 500;
  }
  updateEstimatedDuration();
});
document.querySelector("#referenceFiles").addEventListener("change", async (event) => {
  const files = [...event.target.files].slice(0, 10);
  const kind = document.querySelector("#referenceFileKind").value;
  draftReferenceFiles = [];
  document.querySelector("#referenceFileList").textContent = files.length ? `正在擷取 ${files.length} 個檔案的文字…` : "尚未選擇檔案";
  renderReferenceFileReview();
  for (const file of files) {
    try {
      if (file.size > 8 * 1024 * 1024) throw new Error("單一檔案不可超過 8 MB。");
      const extracted = await api("/api/v1/documents/extract", {
        method: "POST",
        body: JSON.stringify({ filename: file.name, content_base64: await fileToBase64(file) }),
      });
      draftReferenceFiles.push({
        name: file.name,
        content: extracted.text,
        size: file.size,
        lastModified: file.lastModified,
        kind,
        approved: false,
        fileType: extracted.file_type,
        characterCount: extracted.character_count,
        originalCharacterCount: extracted.original_character_count,
        unitLabel: extracted.unit_label,
        unitCount: extracted.unit_count,
        warnings: extracted.warnings || [],
      });
    } catch (error) {
      draftReferenceFiles.push({ name: file.name, size: file.size, lastModified: file.lastModified, kind, approved: false, error: error.message || "文件解析失敗。" });
    }
    renderReferenceFileReview();
  }
  const succeeded = draftReferenceFiles.filter((file) => !file.error).length;
  document.querySelector("#referenceFileList").textContent = files.length
    ? `已完成 ${succeeded}/${files.length} 個檔案：請在下方確認擷取文字`
    : "尚未選擇檔案";
  markWorkbenchDraftDirty();
});

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || "").split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error("瀏覽器無法讀取這個檔案。"));
    reader.readAsDataURL(file);
  });
}

function renderReferenceFileReview() {
  const container = document.querySelector("#referenceFileReview");
  if (!draftReferenceFiles.length) {
    container.innerHTML = "";
    return;
  }
  const kindOptions = [
    ["brand_brief", "品牌 Brief／規範"], ["product_facts", "產品與事實資料"],
    ["owned_content", "自家過往內容"], ["creator_reference", "外部創作者參考"], ["general", "一般補充資料"],
  ];
  container.innerHTML = draftReferenceFiles.map((file, index) => {
    if (file.error) return `<article class="reference-file-review-card error"><header><div><strong>${escapeHtml(file.name)}</strong><span>解析失敗</span></div><button type="button" data-reference-file-remove="${index}">移除</button></header><p>${escapeHtml(file.error)}</p></article>`;
    const meta = `${escapeHtml(file.fileType || "文件")} · ${file.unitCount || 0} ${escapeHtml(file.unitLabel || "單位")} · ${Number(file.characterCount || 0).toLocaleString()} 字`;
    const warnings = (file.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
    return `<article class="reference-file-review-card ${file.approved ? "approved" : ""}">
      <header><div><strong>${escapeHtml(file.name)}</strong><span>${meta}</span></div><button type="button" data-reference-file-remove="${index}">移除</button></header>
      ${warnings ? `<ul class="reference-file-warnings">${warnings}</ul>` : ""}
      <label class="reference-file-kind"><span>文件用途</span><select data-reference-file-kind="${index}">${kindOptions.map(([value, label]) => `<option value="${value}" ${file.kind === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
      <label class="reference-file-text"><span>擷取文字（可直接修改）</span><textarea data-reference-file-content="${index}" maxlength="50000">${escapeHtml(file.content || "")}</textarea></label>
      <label class="reference-file-approval"><input type="checkbox" data-reference-file-approve="${index}" ${file.approved ? "checked" : ""} /><span><strong>確認文字正確，納入 AI 參考</strong><small>未勾選的文件不會送進工作流。</small></span></label>
    </article>`;
  }).join("");
  container.querySelectorAll("[data-reference-file-remove]").forEach((button) => button.addEventListener("click", () => {
    draftReferenceFiles.splice(Number(button.dataset.referenceFileRemove), 1);
    renderReferenceFileReview();
    document.querySelector("#referenceFileList").textContent = draftReferenceFiles.length ? `尚有 ${draftReferenceFiles.length} 個文件待確認` : "尚未選擇檔案";
    markWorkbenchDraftDirty();
  }));
  container.querySelectorAll("[data-reference-file-kind]").forEach((select) => select.addEventListener("change", () => {
    draftReferenceFiles[Number(select.dataset.referenceFileKind)].kind = select.value;
    markWorkbenchDraftDirty();
  }));
  container.querySelectorAll("[data-reference-file-content]").forEach((textarea) => textarea.addEventListener("input", () => {
    const file = draftReferenceFiles[Number(textarea.dataset.referenceFileContent)];
    file.content = textarea.value.slice(0, 50000);
    file.characterCount = file.content.length;
    file.approved = false;
    textarea.closest(".reference-file-review-card")?.classList.remove("approved");
    const checkbox = textarea.closest(".reference-file-review-card")?.querySelector("[data-reference-file-approve]");
    if (checkbox) checkbox.checked = false;
    markWorkbenchDraftDirty();
  }));
  container.querySelectorAll("[data-reference-file-approve]").forEach((checkbox) => checkbox.addEventListener("change", () => {
    draftReferenceFiles[Number(checkbox.dataset.referenceFileApprove)].approved = checkbox.checked;
    checkbox.closest(".reference-file-review-card")?.classList.toggle("approved", checkbox.checked);
    markWorkbenchDraftDirty();
  }));
}

document.querySelectorAll(".platform-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    window.setTimeout(() => chip.classList.toggle("selected", chip.querySelector("input").checked), 0);
  });
});

document.querySelector("#newWorkflowButton").addEventListener("click", () => startNewTask("", { clearDraft: true }));
document.querySelector("#workbenchReturnButton").addEventListener("click", () => {
  const returnPage = state.workbenchReturnPage;
  if (!returnPage || !pageLabels[returnPage]) return;
  state.workbenchReturnPage = null;
  syncWorkbenchReturnButton();
  showPage(returnPage);
});
document.querySelector("#historyButton").addEventListener("click", async () => {
  await loadHistory(true);
  historyPanel.classList.remove("hidden");
  historyPanel.scrollIntoView({ behavior: "smooth", block: "start" });
});
document.querySelector("#closeHistoryButton").addEventListener("click", () => historyPanel.classList.add("hidden"));
document.querySelector("#approveButton").addEventListener("click", () => submitDecision(true));
document.querySelector("#rejectButton").addEventListener("click", () => submitDecision(false));
document.querySelector("#requestChangesButton").addEventListener("click", () => submitDecision(false, "request_changes"));
document.querySelector("#scrollToDecisionButton").addEventListener("click", () => {
  if (state.workflow?.status === "completed") {
    showPage("publishing");
    return;
  }
  if (decisionPanel.classList.contains("hidden")) {
    showMessage("目前沒有等待處理的核准決策；請等待 AI 階段完成。", false);
    return;
  }
  decisionPanel.scrollIntoView({ behavior: "smooth", block: "center" });
});
document.querySelectorAll("[data-feedback-rating]").forEach((button) => {
  button.addEventListener("click", () => submitArtifactFeedback(button.dataset.feedbackRating));
});
document.querySelector("#authForm").addEventListener("submit", submitAuthForm);
document.querySelector("#forgotPasswordButton").addEventListener("click", () => showAuthMode("request-reset"));
document.querySelector("#passwordResetRequestForm").addEventListener("submit", submitPasswordResetRequest);
document.querySelector("#passwordResetConfirmForm").addEventListener("submit", submitPasswordResetConfirm);
document.querySelectorAll("[data-return-to-login]").forEach((button) => button.addEventListener("click", () => showAuthMode("login")));
document.querySelector("#showSignupButton").addEventListener("click", () => showAuthMode("signup"));
document.querySelector("#signupForm").addEventListener("submit", submitSignupForm);
document.querySelector("#resendVerificationButton").addEventListener("click", resendVerificationEmail);
document.querySelector("#createInviteCodeForm").addEventListener("submit", createInviteCode);
document.querySelector("#logoutButton").addEventListener("click", logout);
document.querySelector("#workspaceSwitcher").addEventListener("change", (event) => switchWorkspace(event.target.value));
document.querySelector("#createWorkspaceForm").addEventListener("submit", createWorkspace);
document.querySelector("#addMemberForm").addEventListener("submit", addWorkspaceMember);
document.querySelector("#changePasswordForm").addEventListener("submit", changePassword);
document.querySelector("#workspaceAIProfileForm").addEventListener("submit", saveWorkspaceAIProfile);
document.querySelector("#addWorkspaceScriptSnippet").addEventListener("click", addWorkspaceScriptSnippet);
document.querySelector("#workspaceScriptSnippetInput").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  addWorkspaceScriptSnippet();
});
document.querySelector("#refreshBetaUsage").addEventListener("click", () => loadBetaUsage(true));
document.querySelector("#betaUsageDays").addEventListener("change", () => loadBetaUsage(true));
document.querySelector("#bugReportStatusFilter").addEventListener("change", () => loadBugReports(true));
document.querySelector("#bugReportButton").addEventListener("click", openBugReport);
document.querySelectorAll("[data-close-bug-report]").forEach((button) => button.addEventListener("click", closeBugReport));
document.querySelector("#bugReportForm").addEventListener("submit", submitBugReport);
document.querySelector("#bugReportScreenshot").addEventListener("change", previewBugScreenshot);
document.querySelector("#captureBugScreenshot").addEventListener("click", captureCurrentScreenForBugReport);
document.querySelector("#removeBugScreenshot").addEventListener("click", clearBugScreenshot);
document.querySelectorAll("[data-bug-editor-tool]").forEach((button) => button.addEventListener("click", () => setBugEditorTool(button.dataset.bugEditorTool)));
document.querySelectorAll("[data-bug-editor-emoji]").forEach((button) => button.addEventListener("click", () => selectBugEditorEmoji(button.dataset.bugEditorEmoji)));
document.querySelector("#bugEditorUndo").addEventListener("click", undoBugEditorAction);
document.querySelector("#bugEditorCancel").addEventListener("click", () => closeBugScreenshotEditor(false));
document.querySelector("#bugEditorConfirm").addEventListener("click", confirmBugScreenshotEditor);
document.querySelector("#bugEditorDownload").addEventListener("click", downloadBugEditorImage);
document.querySelector("#bugEditorCanvas").addEventListener("pointerdown", beginBugEditorAction);
document.querySelector("#bugEditorCanvas").addEventListener("pointermove", moveBugEditorAction);
document.querySelector("#bugEditorCanvas").addEventListener("pointerup", finishBugEditorAction);
document.querySelector("#bugEditorCanvas").addEventListener("pointercancel", finishBugEditorAction);
document.querySelector("#executionLogToggle").addEventListener("click", () => {
  setExecutionLogCollapsed(!executionLogPanel.classList.contains("collapsed"));
});

function setExecutionLogCollapsed(collapsed) {
  executionLogPanel.classList.toggle("collapsed", collapsed);
  document.querySelector("#executionLogToggle").setAttribute("aria-expanded", String(!collapsed));
}
document.querySelectorAll(".preview-tab").forEach((tab) => {
  tab.addEventListener("click", () => activatePreviewTab(tab));
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = [...document.querySelectorAll(".preview-tab")];
    const current = tabs.indexOf(tab);
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    activatePreviewTab(tabs[next]);
    tabs[next].focus();
  });
});

function activatePreviewTab(tab) {
  activePreviewTab = tab.dataset.tab;
  document.querySelectorAll(".preview-tab").forEach((item) => {
    const active = item === tab;
    item.classList.toggle("active", active);
    item.setAttribute("aria-selected", String(active));
    item.tabIndex = active ? 0 : -1;
  });
  renderPreview(activePreviewTab);
}

function toggleMobileNav(force) {
  const open = typeof force === "boolean" ? force : !document.body.classList.contains("mobile-nav-open");
  document.body.classList.toggle("mobile-nav-open", open);
  document.querySelector("#mobileNavToggle").setAttribute("aria-expanded", String(open));
  document.querySelector("#mobileNavToggle").setAttribute("aria-label", open ? "關閉導覽" : "開啟導覽");
}

form.addEventListener("input", () => markWorkbenchDraftDirty());
form.addEventListener("change", () => markWorkbenchDraftDirty());
document.querySelector("#referenceSection").addEventListener("toggle", () => markWorkbenchDraftDirty());

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy) return;

  const platforms = [...document.querySelectorAll('.platform-chip input:checked')].map((input) => input.value);
  if (!platforms.length) {
    showMessage("請至少選擇一個發布平台。", true);
    return;
  }

  const pendingHandoff = state.pendingOpportunityHandoff ? { ...state.pendingOpportunityHandoff } : null;
  const submittedDraftKey = getWorkbenchDraftKey(pendingHandoff);
  let pendingAdoptionRecord = null;
  let createdWorkflowId = null;
  setBusy(true, "正在建立任務…");
  resetAgentStates();
  setExecutionLogCollapsed(true);
  try {
    const referenceMaterials = await collectReferenceMaterials();
    const payload = {
      topic: document.querySelector("#topic").value.trim(),
      goal: document.querySelector("#goal").value,
      platforms,
      output_type: document.querySelector("#outputType").value,
      target_word_count: Number(document.querySelector("#targetWordCount").value),
      brand_voice: document.querySelector("#brandVoice").value.trim() || "品牌預設語氣",
      constraints: document.querySelector("#constraints").value.split("\n").map((item) => item.trim()).filter(Boolean),
      reference_materials: referenceMaterials,
      reference_boundary: document.querySelector("#referenceBoundary").value.trim(),
      target_audience: document.querySelector("#targetAudience").value.trim(),
      brand_name: document.querySelector("#brandName").value.trim(),
      language: document.querySelector("#contentLanguage").value,
      region: document.querySelector("#contentRegion").value.trim() || "台灣",
      ...(state.pendingOpportunityHandoff?.workspaceId ? { workspace_id: state.pendingOpportunityHandoff.workspaceId } : {}),
      ...(state.pendingOpportunityHandoff?.sourceOpportunityId ? { source_opportunity_id: state.pendingOpportunityHandoff.sourceOpportunityId } : {}),
      ...(state.pendingOpportunityHandoff?.sourceGenerationId ? { source_generation_id: state.pendingOpportunityHandoff.sourceGenerationId } : {}),
    };
    state.workflow = await api("/api/v1/workflows", { method: "POST", body: JSON.stringify(payload) });
    createdWorkflowId = state.workflow.id;
    clearWorkbenchDraft(submittedDraftKey);
    draftDirty = false;
    draftReferenceFiles = [];
    state.pendingOpportunityHandoff = null;
    updatePageUrl("workbench", null, true, null, state.workflow.id);
    if (pendingHandoff?.sourceGenerationId && pendingHandoff?.sourceOpportunityId) {
      try {
        await syncAdoptedOpportunity(pendingHandoff, state.workflow.id);
        clearPendingAdoptionSync(state.workflow.id);
      } catch (error) {
        pendingAdoptionRecord = savePendingAdoptionSync(pendingHandoff, state.workflow.id);
      }
    }
    showMessage("任務已建立，AI 團隊正在自動執行研究、撰寫與製作規劃。", false);
    setStage("ai_creation");
    await delay(650);
    await runWorkflow();
    if (pendingAdoptionRecord) showAdoptionSyncWarning(pendingAdoptionRecord);
  } catch (error) {
    if (pendingAdoptionRecord) {
      showAdoptionSyncWarning(
        pendingAdoptionRecord,
        `任務 ${createdWorkflowId} 已保存，但後續執行暫時中斷：${error.message || "請稍後從工作紀錄繼續。"}`,
      );
    } else {
      showMessage(
        createdWorkflowId
          ? `任務 ${createdWorkflowId} 已保存，但後續執行暫時中斷：${error.message || "請稍後從工作紀錄繼續。"}`
          : error.message || "建立任務失敗，請稍後重試。",
        true,
      );
    }
  } finally {
    setBusy(false);
  }
});

async function syncAdoptedOpportunity(handoff, workflowId) {
  const url = `/api/v1/opportunities/${encodeURIComponent(handoff.sourceGenerationId)}/items/${encodeURIComponent(handoff.sourceOpportunityId)}`;
  const update = { status: "adopted", feedback_note: "工作流建立成功後同步採用狀態", adopted_workflow_id: workflowId };
  const updatedGeneration = await api(url, { method: "PATCH", body: JSON.stringify(update) });
  if (state.opportunityResult?.id === updatedGeneration.id) state.opportunityResult = updatedGeneration;
  return updatedGeneration;
}

async function runWorkflow(options = {}) {
  if (!state.workflow) return;
  beginLiveWorkflowProgress(state.workflow, options.retryFailed ? "正在重試失敗階段" : "正在啟動 AI 工作流");
  try {
    if (state.workflow.status === "failed" && options.retryFailed) {
      state.workflow = await api(`/api/v1/workflows/${state.workflow.id}/advance`, { method: "POST" });
      renderWorkflow(state.workflow);
    }
    while (!["waiting_for_human", "completed", "cancelled", "failed"].includes(state.workflow.status)) {
      state.workflow = await api(`/api/v1/workflows/${state.workflow.id}/advance`, { method: "POST" });
      renderWorkflow(state.workflow);
      await loadHistory();
      if (state.workflow.status !== "running") break;
      await delay(450);
    }
  } catch (error) {
    updateLiveWorkflowProgress(state.workflow, { failed: true, detail: error.message || "執行暫時中斷，任務進度已保存。" });
    throw error;
  } finally {
    endLiveWorkflowProgress(state.workflow);
  }
}

async function submitDecision(approved, action = approved ? "approve" : "reject") {
  if (!state.workflow || state.busy) return;
  const decisionReason = state.workflow.human_request?.reason;
  const finalApproval = decisionReason === "final_approval";
  const productionException = !finalApproval && state.workflow.stage === "production_package";
  const feedback = document.querySelector("#decisionFeedback").value.trim();
  if (action === "request_changes" && !feedback) {
    showMessage("請先填寫要修改的內容，AI 才能依意見重做。", true);
    document.querySelector("#decisionFeedback").focus();
    return;
  }
  setBusy(true, approved ? "正在套用你的決定…" : "正在退回任務…");
  setDecisionButtonsBusy(true, approved, finalApproval);
  if (approved && !finalApproval) beginLiveWorkflowProgress(state.workflow, "核准完成，正在啟動下一階段");
  if (approved) {
    showMessage(
      finalApproval
        ? "正在完成最終核准，完成後即可前往發布中心安排發布。"
        : productionException
          ? "已送出品質例外核准，正在繼續執行剩餘的製作包 QA 與發布前檢查。"
          : "已送出風險核准，正在啟動製作規劃代理；通常需要 30–90 秒，請保持此頁開啟。",
      false,
    );
  }
  try {
    state.workflow = await api(`/api/v1/workflows/${state.workflow.id}/decisions`, {
      method: "POST",
      body: JSON.stringify({ approved, action, note: feedback || (approved ? "由內容負責人核准" : "由內容負責人退回") }),
    });
    decisionPanel.classList.add("hidden");
    document.querySelector("#decisionFeedback").value = "";
    if (state.workflow.status === "draft" && action === "request_changes") {
      showMessage("修改意見已記錄，正在建立下一版內容。", false);
      await runWorkflow();
    } else if (state.workflow.status === "running") {
      showMessage(productionException ? "品質例外已記錄，正在完成剩餘 QA 與發布前檢查。" : "風險核准已記錄，製作規劃代理正在建立分鏡、視覺與發布素材；通常需要 30–90 秒。", false);
      setStage(state.workflow.stage);
      await delay(500);
      await runWorkflow();
    } else {
      renderWorkflow(state.workflow);
    }
    await loadHistory();
  } catch (error) {
    if (approved && !finalApproval) updateLiveWorkflowProgress(state.workflow, { failed: true, detail: error.message || "執行暫時中斷，任務進度已保存。" });
    showMessage(error.message || "無法送出決定，請稍後重試。", true);
  } finally {
    if (approved && !finalApproval) endLiveWorkflowProgress(state.workflow);
    setBusy(false);
    setDecisionButtonsBusy(false, false, state.workflow?.human_request?.reason === "final_approval");
  }
}

function setDecisionButtonsBusy(busy, approved, finalApproval) {
  ["#approveButton", "#rejectButton", "#requestChangesButton"].forEach((selector) => {
    document.querySelector(selector).disabled = busy;
  });
  document.querySelector("#approveButton").textContent = busy && approved
    ? (finalApproval ? "正在完成最終核准…" : "正在啟動製作規劃…")
    : (finalApproval ? "最終核准內容" : "核准風險並繼續");
}

function renderWorkflow(workflow) {
  resetAgentStates();
  setStage(workflow.stage);
  updateResultAction(workflow);
  const completedAgentNames = new Set(workflow.agent_results.map((result) => result.agent));
  workflow.agent_results.forEach((result) => setAgentState(result.agent, "completed"));
  if (workflow.status === "failed") {
    const failedLog = [...(workflow.execution_log || [])].reverse().find((entry) => entry.event_type === "agent.failed" && entry.agent);
    if (failedLog?.agent) setAgentState(failedLog.agent, "failed");
  }
  setAgentState("reference_analyst", workflow.artifacts.reference_analysis ? "completed" : "skipped");
  if (workflow.artifacts.script && !workflow.agent_results.some((result) => result.agent === "writer")) {
    setAgentState("writer", "completed");
    completedAgentNames.add("writer");
  }
  if (["production_package", "approval_publish"].includes(workflow.stage)) {
    stageAgentOrder.ai_creation.forEach((agent) => {
      if (!completedAgentNames.has(agent)) setAgentState(agent, "skipped");
    });
  }
  if (workflow.stage === "approval_publish") {
    stageAgentOrder.production_package.forEach((agent) => {
      if (!completedAgentNames.has(agent)) setAgentState(agent, "skipped");
    });
  }
  renderExecutionLog(workflow.execution_log || []);

  if (workflow.artifacts.script) {
    resultPanel.classList.remove("hidden");
    document.querySelector("#previewTitle").textContent = workflow.artifacts.script.title;
    document.querySelector("#previewSummary").textContent = workflow.artifacts.script.summary;
    document.querySelector("#qualityScore").textContent = workflow.artifacts.script.quality_score == null
      ? "本機結構檢查"
      : `品質評分 ${workflow.artifacts.script.quality_score} / 100`;
    document.querySelector("#wordCountBadge").textContent = `實際 ${workflow.artifacts.script.word_count} 字 / 目標 ${workflow.artifacts.script.target_word_count} 字`;
    renderPreview(activePreviewTab);
  }

  if (workflow.status === "waiting_for_human" && workflow.human_request) {
    const finalApproval = workflow.human_request.reason === "final_approval";
    decisionPanel.classList.remove("hidden");
    document.querySelector("#decisionQuestion").textContent = workflow.human_request.question;
    document.querySelector("#decisionStepBadge").textContent = finalApproval ? "第 2 次核准｜最終發布確認" : "第 1 次核准｜允許繼續製作";
    document.querySelector("#decisionSuggestion").textContent = finalApproval
      ? "請先查看下方成果預覽；確認腳本、來源與製作素材後，點擊「最終核准內容」。"
      : workflow.stage === "production_package"
        ? `${workflow.human_request.suggested_action || "確認品質例外後即可繼續。"} 核准後會執行剩餘 QA 與發布前檢查。`
        : `${workflow.human_request.suggested_action || "確認風險後即可繼續。"} 核准後會執行製作規劃，通常需要 30–90 秒。`;
    document.querySelector("#requestChangesButton").classList.toggle("hidden", !finalApproval);
    setDecisionButtonsBusy(false, false, finalApproval);
    document.querySelector("#resultStatus").textContent = finalApproval ? "等待重新核准" : "等待風險核准";
    showMessage(finalApproval ? "製作素材包已完成。請預覽成果後進行第 2 次、也是最後一次人工核准。" : "AI 偵測到例外；這是第 1 次核准，通過後才會繼續製作素材。", false);
  }

  if (workflow.status === "completed") {
    decisionPanel.classList.add("hidden");
    resultPanel.classList.remove("hidden");
    document.querySelector("#resultStatus").textContent = "已核准";
    showMessage("全部流程已完成；內容目前為「等待安排發布」。", false, {
      label: "前往發布中心",
      onClick: () => showPage("publishing"),
    });
  }

  if (workflow.status === "cancelled") {
    decisionPanel.classList.add("hidden");
    showMessage("任務已退回。你可以調整需求後建立新任務。", true);
  }

  if (workflow.status === "failed") {
    decisionPanel.classList.add("hidden");
    showWorkflowFailedMessage(workflow);
  }
}

function updateResultAction(workflow) {
  const button = document.querySelector("#scrollToDecisionButton");
  if (workflow.status === "completed") {
    button.classList.remove("hidden");
    button.textContent = "前往發布中心 →";
    return;
  }
  if (workflow.status === "waiting_for_human") {
    button.classList.remove("hidden");
    button.textContent = "前往核准決策 ↑";
    return;
  }
  button.classList.add("hidden");
}

let applicationStarted = false;

async function publicAuthApi(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(readableApiDetail(data?.detail ?? data, response.status));
    error.status = response.status;
    error.code = data?.detail?.code || "";
    throw error;
  }
  return data;
}

function showAuthScreen() {
  const bootstrap = Boolean(state.authStatus?.bootstrap_available);
  document.querySelector("#authScreen").classList.remove("hidden");
  document.querySelector("#authNameField").classList.toggle("hidden", !bootstrap);
  document.querySelector("#authDisplayName").required = bootstrap;
  document.querySelector("#authTitle").textContent = bootstrap ? "建立第一位管理員" : "登入 TackyFlow";
  document.querySelector("#authDescription").textContent = bootstrap
    ? "目前尚未建立帳號。這個一次性步驟會建立預設工作區與擁有者。"
    : "登入後才能存取你所屬工作區的內容。";
  document.querySelector("#authSubmitButton").textContent = bootstrap ? "建立管理員並登入" : "登入";
  document.querySelector("#authPassword").autocomplete = bootstrap ? "new-password" : "current-password";
  document.querySelector("#forgotPasswordButton").classList.toggle("hidden", bootstrap);
  document.querySelector("#showSignupButton").classList.toggle("hidden", !state.authStatus?.signup_available);
  document.querySelector("#resendVerificationButton").classList.add("hidden");
  restoreRememberedLoginEmail();
  const verified = consumeSignupVerificationCallback();
  const recovery = consumePasswordRecoveryCallback();
  if (recovery.token) {
    passwordResetAccessToken = recovery.token;
    showAuthMode("confirm-reset");
    return;
  }
  const inviteCode = consumeInviteLinkParameter();
  if (inviteCode && state.authStatus?.signup_available) {
    showAuthMode("signup");
    document.querySelector("#signupInviteCode").value = inviteCode;
    document.querySelector("#signupDisplayName").focus();
    return;
  }
  showAuthMode("login");
  const message = document.querySelector("#authMessage");
  if (verified) {
    message.classList.add("success");
    message.textContent = "Email 驗證完成，請登入開始使用。";
  }
  if (recovery.error) message.textContent = recovery.error;
  document.querySelector("#authEmail").focus();
}

function consumeInviteLinkParameter() {
  const url = new URL(window.location.href);
  const code = url.searchParams.get("invite") || "";
  if (!code) return "";
  url.searchParams.delete("invite");
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}`);
  return code;
}

function consumeSignupVerificationCallback() {
  // Supabase redirects back with ?auth=verified plus a #type=signup session fragment.
  // The session is not used: TackyFlow signs in with its own cookie after login.
  const url = new URL(window.location.href);
  const params = new URLSearchParams(url.hash.slice(1));
  const verified = url.searchParams.get("auth") === "verified" || params.get("type") === "signup";
  if (!verified || params.get("error")) return false;
  url.searchParams.delete("auth");
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}`);
  return true;
}

function consumePasswordRecoveryCallback() {
  if (!window.location.hash) return { token: "", error: "" };
  const params = new URLSearchParams(window.location.hash.slice(1));
  if (params.get("type") !== "recovery" && !params.get("error")) return { token: "", error: "" };
  const token = params.get("access_token") || "";
  const error = params.get("error_description")
    ? decodeURIComponent(params.get("error_description").replace(/\+/g, " "))
    : token ? "" : "重設連結無效或已過期，請重新申請。";
  window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}`);
  return { token, error };
}

function showAuthMode(mode) {
  const bootstrap = Boolean(state.authStatus?.bootstrap_available);
  document.querySelector("#authMessage").classList.remove("success");
  document.querySelector("#authForm").classList.toggle("hidden", mode !== "login");
  document.querySelector("#signupForm").classList.toggle("hidden", mode !== "signup");
  document.querySelector("#passwordResetRequestForm").classList.toggle("hidden", mode !== "request-reset");
  document.querySelector("#passwordResetConfirmForm").classList.toggle("hidden", mode !== "confirm-reset");
  if (mode === "signup") {
    document.querySelector("#authTitle").textContent = "建立 TackyFlow 帳號";
    document.querySelector("#authDescription").textContent = "輸入邀請碼建立帳號。你會得到自己專屬的工作區，資料只有你和你邀請的成員看得到。";
    document.querySelector("#signupMessage").textContent = "";
    document.querySelector("#signupMessage").classList.remove("success");
    document.querySelector("#signupEmail").value ||= document.querySelector("#authEmail").value.trim();
    document.querySelector("#signupInviteCode").focus();
    return;
  }
  if (mode === "request-reset") {
    document.querySelector("#authTitle").textContent = "重設密碼";
    document.querySelector("#authDescription").textContent = "輸入帳號電子郵件，我們會寄出一次性的安全重設連結。";
    document.querySelector("#passwordResetEmail").value = document.querySelector("#authEmail").value.trim();
    document.querySelector("#passwordResetRequestMessage").textContent = "";
    document.querySelector("#passwordResetRequestMessage").classList.remove("success");
    document.querySelector("#passwordResetEmail").focus();
    return;
  }
  if (mode === "confirm-reset") {
    document.querySelector("#authTitle").textContent = "設定新密碼";
    document.querySelector("#authDescription").textContent = "請設定至少 12 個字元的新密碼。完成後可立即返回登入。";
    document.querySelector("#passwordResetConfirmMessage").textContent = "";
    document.querySelector("#passwordResetNewPassword").focus();
    return;
  }
  passwordResetAccessToken = "";
  document.querySelector("#authTitle").textContent = bootstrap ? "建立第一位管理員" : "登入 TackyFlow";
  document.querySelector("#authDescription").textContent = bootstrap
    ? "目前尚未建立帳號。這個一次性步驟會建立預設工作區與擁有者。"
    : "登入後才能存取你所屬工作區的內容。";
  document.querySelector("#authEmail").focus();
}

function restoreRememberedLoginEmail() {
  try {
    const email = window.localStorage.getItem(rememberedLoginEmailKey) || "";
    document.querySelector("#rememberCredentials").checked = Boolean(email);
    if (email && !document.querySelector("#authEmail").value) document.querySelector("#authEmail").value = email;
  } catch {
    document.querySelector("#rememberCredentials").checked = false;
  }
}

function persistRememberedLoginEmail(email) {
  try {
    if (document.querySelector("#rememberCredentials").checked) window.localStorage.setItem(rememberedLoginEmailKey, email);
    else window.localStorage.removeItem(rememberedLoginEmailKey);
  } catch {}
}

async function offerBrowserCredentialStorage(email, password, displayName) {
  if (!document.querySelector("#rememberCredentials").checked) return;
  if (!window.PasswordCredential || !navigator.credentials?.store) return;
  try {
    await navigator.credentials.store(new PasswordCredential({ id: email, password, name: displayName || email }));
  } catch {
    // The browser may disable credential storage or let another password manager handle the form.
  }
}

function hideAuthScreen() {
  document.querySelector("#authScreen").classList.add("hidden");
  document.querySelector("#authMessage").textContent = "";
  document.querySelector("#authForm").reset();
}

function applyAuthContext(context) {
  state.authContext = context;
  state.workspaceId = context.workspace.id;
  document.documentElement.dataset.workspaceId = state.workspaceId;
  document.querySelector("#currentUserName").textContent = context.user.display_name;
  document.querySelector("#currentUserRole").textContent = `${context.user.email} · ${roleLabel(context.workspace.role)}`;
  document.querySelector(".avatar").textContent = context.user.display_name.slice(0, 2).toUpperCase();
  document.querySelector("#logoutButton").classList.remove("hidden");
  const switcher = document.querySelector("#workspaceSwitcher");
  switcher.innerHTML = context.workspaces.map((workspace) => `<option value="${escapeHtml(workspace.id)}">${escapeHtml(workspace.name)}</option>`).join("");
  switcher.value = context.workspace.id;
  switcher.classList.toggle("hidden", context.workspaces.length < 2);
  document.querySelector("#accountSecurityPanel").classList.remove("hidden");
  document.querySelector("#promptCenterPanel").classList.add("hidden");
  document.querySelector("#inviteCodePanel").classList.add("hidden");
  document.querySelector("#workspaceAIProfilePanel").classList.toggle("hidden", !["owner", "admin"].includes(context.workspace.role));
  document.querySelector("#bugReportButton").classList.remove("hidden");
}

async function initializeAuthentication() {
  try {
    state.authStatus = await publicAuthApi("/api/v1/auth/status");
    if (!state.authStatus.authentication_required) {
      document.querySelector("#workspaceSwitcher").classList.add("hidden");
      document.querySelector("#bugReportButton").classList.remove("hidden");
      return true;
    }
    try {
      const context = await publicAuthApi("/api/v1/auth/me");
      applyAuthContext(context);
      hideAuthScreen();
      return true;
    } catch {
      showAuthScreen();
      return false;
    }
  } catch (error) {
    state.authStatus = { authentication_required: true, bootstrap_available: false };
    showAuthScreen();
    document.querySelector("#authMessage").textContent = error.message || "無法讀取登入狀態。";
    return false;
  }
}

async function submitAuthForm(event) {
  event.preventDefault();
  const button = document.querySelector("#authSubmitButton");
  const message = document.querySelector("#authMessage");
  const bootstrap = Boolean(state.authStatus?.bootstrap_available);
  button.disabled = true;
  message.textContent = bootstrap ? "正在建立安全工作區…" : "正在登入…";
  try {
    const email = document.querySelector("#authEmail").value.trim();
    const password = document.querySelector("#authPassword").value;
    const payload = {
      email,
      password,
      ...(bootstrap ? { display_name: document.querySelector("#authDisplayName").value.trim() } : {}),
    };
    const context = await publicAuthApi(bootstrap ? "/api/v1/auth/bootstrap" : "/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.authStatus.bootstrap_available = false;
    persistRememberedLoginEmail(email);
    await offerBrowserCredentialStorage(email, password, context.user?.display_name);
    applyAuthContext(context);
    hideAuthScreen();
    await startAuthenticatedApplication();
  } catch (error) {
    message.textContent = error.message || "登入失敗，請再試一次。";
    document.querySelector("#resendVerificationButton").classList.toggle("hidden", error.code !== "email_not_verified");
  } finally {
    button.disabled = false;
  }
}

async function submitSignupForm(event) {
  event.preventDefault();
  const button = document.querySelector("#signupSubmitButton");
  const message = document.querySelector("#signupMessage");
  const email = document.querySelector("#signupEmail").value.trim();
  const password = document.querySelector("#signupPassword").value;
  message.classList.remove("success");
  if (password !== document.querySelector("#signupPasswordConfirm").value) {
    message.textContent = "兩次輸入的密碼不一致。";
    return;
  }
  button.disabled = true;
  message.textContent = "正在建立帳號…";
  try {
    const result = await publicAuthApi("/api/v1/auth/signup", {
      method: "POST",
      body: JSON.stringify({
        invite_code: document.querySelector("#signupInviteCode").value.trim(),
        display_name: document.querySelector("#signupDisplayName").value.trim(),
        email,
        password,
      }),
    });
    document.querySelector("#signupForm").reset();
    if (result.verification_required) {
      showAuthMode("login");
      document.querySelector("#authEmail").value = email;
      const loginMessage = document.querySelector("#authMessage");
      loginMessage.classList.add("success");
      loginMessage.textContent = `驗證信已寄到 ${email}。請點擊信中的連結完成驗證，再回來登入。`;
      document.querySelector("#resendVerificationButton").classList.remove("hidden");
      return;
    }
    applyAuthContext(result);
    hideAuthScreen();
    await startAuthenticatedApplication();
  } catch (error) {
    message.textContent = error.message || "無法建立帳號，請再試一次。";
  } finally {
    button.disabled = false;
  }
}

async function resendVerificationEmail() {
  const message = document.querySelector("#authMessage");
  const email = document.querySelector("#authEmail").value.trim();
  if (!email) {
    message.classList.remove("success");
    message.textContent = "請先輸入註冊時使用的電子郵件。";
    return;
  }
  try {
    const result = await publicAuthApi("/api/v1/auth/signup/resend", { method: "POST", body: JSON.stringify({ email }) });
    message.classList.add("success");
    message.textContent = result.message || "已重新寄出驗證信。";
  } catch (error) {
    message.classList.remove("success");
    message.textContent = error.message || "目前無法寄送驗證信，請稍後再試。";
  }
}

async function submitPasswordResetRequest(event) {
  event.preventDefault();
  const button = document.querySelector("#passwordResetRequestButton");
  const message = document.querySelector("#passwordResetRequestMessage");
  button.disabled = true;
  message.classList.remove("success");
  message.textContent = "正在寄送安全連結…";
  try {
    const result = await publicAuthApi("/api/v1/auth/password-reset/request", {
      method: "POST",
      body: JSON.stringify({ email: document.querySelector("#passwordResetEmail").value.trim() }),
    });
    message.classList.add("success");
    message.textContent = result.message || "若帳號存在，系統已寄出密碼重設連結。";
  } catch (error) {
    message.textContent = error.message || "目前無法寄送重設連結，請稍後再試。";
  } finally {
    button.disabled = false;
  }
}

async function submitPasswordResetConfirm(event) {
  event.preventDefault();
  const button = document.querySelector("#passwordResetConfirmButton");
  const message = document.querySelector("#passwordResetConfirmMessage");
  const newPassword = document.querySelector("#passwordResetNewPassword").value;
  const confirmation = document.querySelector("#passwordResetConfirmPassword").value;
  if (newPassword !== confirmation) {
    message.textContent = "兩次輸入的新密碼不一致。";
    return;
  }
  if (!passwordResetAccessToken) {
    message.textContent = "重設連結無效或已過期，請重新申請。";
    return;
  }
  button.disabled = true;
  message.textContent = "正在安全更新密碼…";
  try {
    await publicAuthApi("/api/v1/auth/password-reset/confirm", {
      method: "POST",
      body: JSON.stringify({ access_token: passwordResetAccessToken, new_password: newPassword }),
    });
    document.querySelector("#passwordResetConfirmForm").reset();
    passwordResetAccessToken = "";
    showAuthMode("login");
    const loginMessage = document.querySelector("#authMessage");
    loginMessage.classList.add("success");
    loginMessage.textContent = "密碼已更新，請使用新密碼登入。";
  } catch (error) {
    message.textContent = error.message || "無法更新密碼，請重新申請重設連結。";
  } finally {
    button.disabled = false;
  }
}

async function logout() {
  try {
    await api("/api/v1/auth/logout", { method: "POST" });
  } catch {}
  state.authContext = null;
  state.history = [];
  state.workflow = null;
  applicationStarted = false;
  state.authStatus = await publicAuthApi("/api/v1/auth/status").catch(() => ({ authentication_required: true, bootstrap_available: false }));
  document.querySelector("#bugReportButton").classList.add("hidden");
  showAuthScreen();
}

async function switchWorkspace(workspaceId) {
  if (!workspaceId || workspaceId === state.workspaceId) return;
  try {
    const context = await api("/api/v1/auth/switch-workspace", {
      method: "POST",
      body: JSON.stringify({ workspace_id: workspaceId }),
    });
    applyAuthContext(context);
    state.workflow = null;
    state.history = [];
    state.opportunityResult = null;
    state.opportunityHistory = [];
    state.opportunityHistoryLoaded = false;
    state.workspaceAIProfile = null;
    resetInterface();
    await Promise.allSettled([loadHistory(), loadUsage(), loadWorkspaceAIProfile()]);
    if (state.currentPage === "settings") {
      renderSettings();
      await Promise.allSettled([loadWorkspaceMembers(), loadWorkspaceAIProfile(), loadPromptCenter(), loadInviteCodes(), loadBetaUsage(), loadBugReports()]);
    }
    showMessage(`已切換到「${context.workspace.name}」。`, false);
  } catch (error) {
    document.querySelector("#workspaceSwitcher").value = state.workspaceId;
    showMessage(error.message || "無法切換工作區。", true);
  }
}

async function createWorkspace(event) {
  event.preventDefault();
  const submittedForm = event.currentTarget;
  const name = document.querySelector("#newWorkspaceName").value.trim();
  try {
    const workspace = await api("/api/v1/workspaces", { method: "POST", body: JSON.stringify({ name }) });
    const context = await api("/api/v1/auth/me");
    applyAuthContext(context);
    submittedForm.reset();
    showMembershipMessage(`已建立「${workspace.name}」，可從左下角切換。`);
  } catch (error) {
    showMembershipMessage(error.message || "無法建立工作區。", true);
  }
}

async function addWorkspaceMember(event) {
  event.preventDefault();
  const submittedForm = event.currentTarget;
  try {
    await api(`/api/v1/workspaces/${encodeURIComponent(state.workspaceId)}/members`, {
      method: "POST",
      body: JSON.stringify({
        display_name: document.querySelector("#memberDisplayName").value.trim(),
        email: document.querySelector("#memberEmail").value.trim(),
        password: document.querySelector("#memberPassword").value,
        role: document.querySelector("#memberRole").value,
      }),
    });
    submittedForm.reset();
    showMembershipMessage("成員已建立；請用安全管道提供臨時密碼。", false);
    await loadWorkspaceMembers();
  } catch (error) {
    showMembershipMessage(error.message || "無法新增成員。", true);
  }
}

async function changePassword(event) {
  event.preventDefault();
  const submittedForm = event.currentTarget;
  const bar = document.querySelector("#passwordMessage");
  try {
    await api("/api/v1/auth/change-password", {
      method: "POST",
      body: JSON.stringify({
        current_password: document.querySelector("#currentPassword").value,
        new_password: document.querySelector("#newPassword").value,
      }),
    });
    submittedForm.reset();
    bar.textContent = "密碼已更新，其他裝置的 Session 已撤銷。";
    bar.classList.remove("error");
    bar.classList.add("show");
  } catch (error) {
    bar.textContent = error.message || "無法更新密碼。";
    bar.classList.add("show", "error");
  }
}

async function loadWorkspaceMembers() {
  const panel = document.querySelector("#workspaceAdminPanel");
  const role = state.authContext?.workspace?.role;
  const canManage = ["owner", "admin"].includes(role);
  panel.classList.toggle("hidden", !canManage);
  if (!canManage) return;
  try {
    state.members = await api(`/api/v1/workspaces/${encodeURIComponent(state.workspaceId)}/members`);
    renderWorkspaceMembers();
  } catch (error) {
    showMembershipMessage(error.message || "無法讀取成員。", true);
  }
}

function profileLines(value) {
  return String(value || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

async function loadWorkspaceAIProfile() {
  const panel = document.querySelector("#workspaceAIProfilePanel");
  const canManage = ["owner", "admin"].includes(state.authContext?.workspace?.role);
  panel.classList.toggle("hidden", !canManage);
  try {
    const profile = await api("/api/v1/workspace-ai-profile");
    state.workspaceAIProfile = profile;
    if (!canManage) return;
    document.querySelector("#workspaceBrandName").value = profile.brand_name || "";
    document.querySelector("#workspaceBrandPositioning").value = profile.brand_positioning || "";
    document.querySelector("#workspaceTargetAudience").value = profile.target_audience || "";
    document.querySelector("#workspaceTone").value = profile.tone || "";
    document.querySelector("#workspacePreferredVocabulary").value = (profile.preferred_vocabulary || []).join("\n");
    document.querySelector("#workspaceForbiddenPhrases").value = (profile.forbidden_phrases || []).join("\n");
    document.querySelector("#workspaceDefaultCTA").value = profile.default_cta || "";
    workspaceScriptSnippetsDraft = [...(profile.script_snippets || [])];
    renderWorkspaceScriptSnippets();
    document.querySelector("#workspaceVisualStyle").value = profile.visual_style || "";
    document.querySelector("#workspaceContentPrinciples").value = (profile.content_principles || []).join("\n");
    document.querySelector("#workspaceAIProfileMeta").textContent = profile.updated_at
      ? `最後更新：${formatTimestamp(profile.updated_at)} · 新任務會自動套用`
      : "尚未儲存品牌設定；目前只使用平台核心 Prompt。";
  } catch (error) {
    showWorkspaceAIProfileMessage(error.message || "無法讀取 Workspace 品牌 AI 設定。", true);
  }
}

async function saveWorkspaceAIProfile(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const profile = await api("/api/v1/workspace-ai-profile", {
      method: "PUT",
      body: JSON.stringify({
        brand_name: document.querySelector("#workspaceBrandName").value,
        brand_positioning: document.querySelector("#workspaceBrandPositioning").value,
        target_audience: document.querySelector("#workspaceTargetAudience").value,
        tone: document.querySelector("#workspaceTone").value,
        preferred_vocabulary: profileLines(document.querySelector("#workspacePreferredVocabulary").value),
        forbidden_phrases: profileLines(document.querySelector("#workspaceForbiddenPhrases").value),
        default_cta: document.querySelector("#workspaceDefaultCTA").value,
        script_snippets: workspaceScriptSnippetsDraft,
        visual_style: document.querySelector("#workspaceVisualStyle").value,
        content_principles: profileLines(document.querySelector("#workspaceContentPrinciples").value),
      }),
    });
    state.workspaceAIProfile = profile;
    workspaceScriptSnippetsDraft = [...(profile.script_snippets || [])];
    renderWorkspaceScriptSnippets();
    document.querySelector("#workspaceAIProfileMeta").textContent = `最後更新：${formatTimestamp(profile.updated_at)} · 新任務會自動套用`;
    showWorkspaceAIProfileMessage("Workspace 品牌設定已儲存；之後的新 AI 任務會疊加在平台核心 Prompt 上。", false);
  } catch (error) {
    showWorkspaceAIProfileMessage(error.message || "無法儲存 Workspace 品牌 AI 設定。", true);
  } finally {
    button.disabled = false;
  }
}

function showWorkspaceAIProfileMessage(message, isError = false) {
  const bar = document.querySelector("#workspaceAIProfileMessage");
  bar.textContent = message;
  bar.classList.toggle("error", Boolean(isError));
  bar.classList.add("show");
}

function addWorkspaceScriptSnippet() {
  const input = document.querySelector("#workspaceScriptSnippetInput");
  const message = document.querySelector("#workspaceScriptSnippetMessage");
  const value = input.value.trim();
  if (!value) {
    message.textContent = "請先輸入一則常用語。";
    input.focus();
    return;
  }
  if (workspaceScriptSnippetsDraft.length >= 50) {
    message.textContent = "已達 50 則上限；請先刪除不需要的常用語。";
    return;
  }
  if (workspaceScriptSnippetsDraft.includes(value)) {
    message.textContent = "這則常用語已經在清單中。";
    return;
  }
  workspaceScriptSnippetsDraft.push(value);
  input.value = "";
  renderWorkspaceScriptSnippets();
  message.textContent = "已新增至清單；請按下方「儲存 Workspace 設定」完成保存。";
  input.focus();
}

function renderWorkspaceScriptSnippets() {
  const list = document.querySelector("#workspaceScriptSnippetList");
  document.querySelector("#workspaceScriptSnippetCount").textContent = `${workspaceScriptSnippetsDraft.length} / 50`;
  if (!workspaceScriptSnippetsDraft.length) {
    list.innerHTML = '<div class="workspace-snippet-empty">尚未新增常用語。請在上方輸入第一則，再點擊「新增常用語」。</div>';
    return;
  }
  list.innerHTML = workspaceScriptSnippetsDraft.map((snippet, index) => `<article class="workspace-snippet-row">
    <span>${index + 1}</span>
    <input data-workspace-snippet-index="${index}" maxlength="200" value="${escapeHtml(snippet)}" aria-label="第 ${index + 1} 則常用語" />
    <div>
      <button type="button" data-workspace-snippet-up="${index}" ${index === 0 ? "disabled" : ""} title="向上移動" aria-label="向上移動第 ${index + 1} 則">↑</button>
      <button type="button" data-workspace-snippet-down="${index}" ${index === workspaceScriptSnippetsDraft.length - 1 ? "disabled" : ""} title="向下移動" aria-label="向下移動第 ${index + 1} 則">↓</button>
      <button type="button" class="remove" data-workspace-snippet-remove="${index}" title="刪除" aria-label="刪除第 ${index + 1} 則">×</button>
    </div>
  </article>`).join("");
  list.querySelectorAll("[data-workspace-snippet-index]").forEach((input) => input.addEventListener("input", () => {
    workspaceScriptSnippetsDraft[Number(input.dataset.workspaceSnippetIndex)] = input.value.slice(0, 200);
    document.querySelector("#workspaceScriptSnippetMessage").textContent = "內容已修改；請按下方「儲存 Workspace 設定」完成保存。";
  }));
  list.querySelectorAll("[data-workspace-snippet-remove]").forEach((button) => button.addEventListener("click", () => {
    workspaceScriptSnippetsDraft.splice(Number(button.dataset.workspaceSnippetRemove), 1);
    renderWorkspaceScriptSnippets();
    document.querySelector("#workspaceScriptSnippetMessage").textContent = "已從清單移除；請儲存 Workspace 設定。";
  }));
  const move = (index, offset) => {
    const target = index + offset;
    if (target < 0 || target >= workspaceScriptSnippetsDraft.length) return;
    [workspaceScriptSnippetsDraft[index], workspaceScriptSnippetsDraft[target]] = [workspaceScriptSnippetsDraft[target], workspaceScriptSnippetsDraft[index]];
    renderWorkspaceScriptSnippets();
    document.querySelector("#workspaceScriptSnippetMessage").textContent = "順序已調整；快捷鍵編號會依新順序更新，請儲存 Workspace 設定。";
  };
  list.querySelectorAll("[data-workspace-snippet-up]").forEach((button) => button.addEventListener("click", () => move(Number(button.dataset.workspaceSnippetUp), -1)));
  list.querySelectorAll("[data-workspace-snippet-down]").forEach((button) => button.addEventListener("click", () => move(Number(button.dataset.workspaceSnippetDown), 1)));
}

function renderWorkspaceMembers() {
  const isOwner = state.authContext?.workspace?.role === "owner";
  const currentUserId = state.authContext?.user?.id;
  document.querySelector("#memberList").innerHTML = state.members.map((member) => `
    <article class="member-row">
      <div><strong>${escapeHtml(member.display_name)}</strong><small>${escapeHtml(member.email)}</small></div>
      ${member.role === "owner" || !isOwner ? `<span>${escapeHtml(roleLabel(member.role))}</span>` : `<select data-member-role="${escapeHtml(member.id)}"><option value="member" ${member.role === "member" ? "selected" : ""}>成員</option><option value="admin" ${member.role === "admin" ? "selected" : ""}>管理員</option></select>`}
      ${member.role === "owner" || member.id === currentUserId ? "" : `<button class="member-remove" type="button" data-remove-member="${escapeHtml(member.id)}">移除</button>`}
    </article>`).join("");
  document.querySelectorAll("[data-member-role]").forEach((select) => select.addEventListener("change", () => updateMemberRole(select.dataset.memberRole, select.value)));
  document.querySelectorAll("[data-remove-member]").forEach((button) => button.addEventListener("click", () => removeWorkspaceMember(button.dataset.removeMember)));
}

async function updateMemberRole(userId, role) {
  try {
    await api(`/api/v1/workspaces/${encodeURIComponent(state.workspaceId)}/members/${encodeURIComponent(userId)}`, { method: "PATCH", body: JSON.stringify({ role }) });
    showMembershipMessage("成員角色已更新。", false);
    await loadWorkspaceMembers();
  } catch (error) {
    showMembershipMessage(error.message || "無法更新角色。", true);
    await loadWorkspaceMembers();
  }
}

async function removeWorkspaceMember(userId) {
  if (!window.confirm("確定要移除這位成員嗎？該成員在此工作區的登入 Session 會立即失效。")) return;
  try {
    await api(`/api/v1/workspaces/${encodeURIComponent(state.workspaceId)}/members/${encodeURIComponent(userId)}`, { method: "DELETE" });
    showMembershipMessage("成員已從工作區移除。", false);
    await loadWorkspaceMembers();
  } catch (error) {
    showMembershipMessage(error.message || "無法移除成員。", true);
  }
}

function showMembershipMessage(message, isError = false) {
  const bar = document.querySelector("#membershipMessage");
  bar.textContent = message;
  bar.classList.toggle("error", Boolean(isError));
  bar.classList.add("show");
}

function roleLabel(role) {
  return ({ owner: "擁有者", admin: "管理員", member: "成員" })[role] || role;
}

function showPage(pageName, options = {}) {
  const page = pageLabels[pageName] ? pageName : "workbench";
  state.currentPage = page;
  document.querySelectorAll(".app-page").forEach((section) => {
    section.classList.toggle("hidden", section.id !== `${page}Page`);
  });
  document.querySelectorAll("[data-page]").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === page);
  });
  document.querySelector(".breadcrumbs strong").textContent = pageLabels[page];
  toggleMobileNav(false);
  if (options.updateUrl !== false) updatePageUrl(page, options.generationId, options.replaceUrl, options.itemId, options.workflowId);
  if (page === "dashboard") renderDashboard();
  if (page === "opportunities") initializeOpportunityPage(options.generationId);
  if (page === "plan") renderPlan();
  if (page === "assets") renderAssets();
  if (page === "publishing") renderPublishing();
  if (page === "performance") renderPerformance();
  if (page === "settings") {
    renderSettings();
    loadWorkspaceMembers();
    loadWorkspaceAIProfile();
    loadPromptCenter();
    loadInviteCodes();
    loadBetaUsage();
    loadBugReports();
  }
  if (!options.preserveScroll) window.scrollTo({ top: 0, behavior: options.updateUrl === false ? "auto" : "smooth" });
  if (options.updateUrl !== false) trackEvent("page.viewed", { page });
}

function startNewTask(source = "", options = {}) {
  if (draftDirty && !options.clearDraft) saveWorkbenchDraft();
  if (options.clearDraft) {
    clearWorkbenchDraft();
    clearWorkbenchDraft(getWorkbenchDraftKey(null));
  }
  showPage("workbench", { updateUrl: options.updateUrl !== false });
  state.workbenchReturnPage = null;
  syncWorkbenchReturnButton();
  resetInterface();
  if (!source) {
    if (!options.clearDraft && restoreWorkbenchDraft()) {
      showMessage("已還原尚未建立的內容草稿。", false);
    }
    return;
  }
  if (typeof source === "string") {
    document.querySelector("#topic").value = source;
    draftDirty = true;
    saveWorkbenchDraft();
    return;
  }

  const { item, generation } = source;
  const request = generation?.request || {};
  const brief = item?.brief || {};
  state.pendingOpportunityHandoff = {
    sourceOpportunityId: item?.id || null,
    sourceGenerationId: generation?.id || null,
    sourceRevision: Number(generation?.revision ?? generation?.revision_count ?? 0),
    workspaceId: request.workspace_id || "default",
  };
  document.querySelector("#topic").value = item?.topic || request.topic || generation?.seed || "";
  document.querySelector("#goal").value = normalizeGoal(request.goal);
  const outputType = normalizeOutputType(request.preferred_formats?.[0] || request.preferred_format || item?.recommended_formats?.[0] || brief.recommended_formats?.[0]);
  document.querySelector("#outputType").value = outputType;
  document.querySelector("#targetWordCount").value = String(wordCountSuggestions[outputType] || 500);
  document.querySelector("#brandVoice").value = request.brand_voice || "專業、清楚，但保有自然的對話感";
  document.querySelector("#targetAudience").value = brief.target_audience || request.audience || request.target_audience || "";
  document.querySelector("#brandName").value = request.brand_name || "";
  document.querySelector("#contentLanguage").value = languageLabel(request.language || "繁體中文");
  document.querySelector("#contentRegion").value = request.region || "台灣";
  const handoffBrandBrief = request.brand_brief || request.reference_materials?.find((material) => material.kind === "brand_brief" && material.content)?.content || "";
  const handoffReference = request.reference_materials?.find((material) => material.kind !== "brand_brief" && material.content);
  document.querySelector("#brandBrief").value = handoffBrandBrief;

  const handoffConstraints = [
    ...normalizeTextList(request.constraints),
    brief.target_audience ? `目標受眾：${brief.target_audience}` : request.audience ? `目標受眾：${request.audience}` : request.target_audience ? `目標受眾：${request.target_audience}` : "",
    brief.angle ? `內容角度：${brief.angle}` : "",
    brief.hook ? `開場 Hook：${brief.hook}` : "",
    brief.key_points?.length ? `關鍵重點：${brief.key_points.join("；")}` : "",
    brief.cta ? `CTA：${brief.cta}` : "",
    brief.estimated_effort ? `預估製作：${brief.estimated_effort}` : "",
    normalizeTextList(brief.production_notes).length ? `製作備註：${normalizeTextList(brief.production_notes).join("；")}` : "",
    brief.evidence_needed?.length ? `製作前須確認：${brief.evidence_needed.join("；")}` : "",
  ].filter(Boolean);
  document.querySelector("#constraints").value = handoffConstraints.join("\n");
  document.querySelector("#referenceText").value = request.reference_text || handoffReference?.content || "";
  document.querySelector("#referenceTextType").value = handoffReference?.kind || "creator_reference";
  document.querySelector("#referenceFocus").value = handoffReference?.focus || "開場、敘事節奏與 CTA 結構";
  document.querySelector("#referenceUrls").value = normalizeTextList(request.reference_urls).join("\n");
  document.querySelector("#referenceBoundary").value = `本任務源自內容機會紀錄 ${generation?.id || ""}；參考資料只用於事實與高層次風格，不得直接仿寫。`;
  document.querySelector("#referenceSection").toggleAttribute("open", Boolean(handoffBrandBrief || request.reference_text || request.reference_materials?.length || request.reference_urls?.length));

  const requestedPlatforms = normalizePlatforms(request.platforms?.length ? request.platforms : item?.recommended_platforms || brief.recommended_platforms || ["youtube"]);
  document.querySelectorAll(".platform-chip").forEach((chip) => {
    const selected = requestedPlatforms.includes(chip.querySelector("input").value);
    chip.querySelector("input").checked = selected;
    chip.classList.toggle("selected", selected);
  });
  updateEstimatedDuration();
  if (options.updateUrl !== false) updatePageUrl("workbench", generation?.id, true, item?.id);
  const restored = restoreWorkbenchDraft();
  showMessage(
    restored
      ? "已還原你先前修改過的選題草稿；任務建立成功後才會標記為已採用。"
      : "已帶入選題、受眾、內容目標、平台、Brief 與參考邊界；目前只是草稿，任務建立成功後才會標記為已採用。",
    false,
  );
}

function syncWorkbenchReturnButton() {
  const button = document.querySelector("#workbenchReturnButton");
  const returnPage = state.workbenchReturnPage;
  const visible = Boolean(returnPage && pageLabels[returnPage] && returnPage !== "workbench");
  button.classList.toggle("hidden", !visible);
  if (visible) {
    button.textContent = `← 返回${pageLabels[returnPage]}`;
    button.setAttribute("aria-label", `返回${pageLabels[returnPage]}`);
  }
}

function updatePageUrl(page, generationId = null, replace = false, itemId = null, workflowId = null) {
  const url = new URL(window.location.href);
  url.search = "";
  url.searchParams.set("page", page);
  if (page === "opportunities" && generationId) url.searchParams.set("generation", generationId);
  if (page === "workbench" && workflowId) url.searchParams.set("workflow", workflowId);
  if (page === "workbench" && !workflowId && generationId && itemId) {
    url.searchParams.set("generation", generationId);
    url.searchParams.set("item", itemId);
  }
  window.history[replace ? "replaceState" : "pushState"]({ page, generationId, itemId, workflowId }, "", url);
}

function routeFromLocation() {
  const params = new URLSearchParams(window.location.search);
  let page = params.get("page");
  if (!page && params.has("opportunities")) page = "opportunities";
  if (!page && params.has("history")) page = "workbench";
  if (!page && params.has("manage")) page = "plan";
  if (!page && params.has("library")) page = "assets";
  return {
    page: pageLabels[page] ? page : "workbench",
    generationId: params.get("generation"),
    itemId: params.get("item"),
    workflowId: params.get("workflow"),
  };
}

async function restoreOpportunityHandoff(generationId, itemId) {
  if (!generationId || !itemId) return;
  try {
    const generation = state.opportunityResult?.id === generationId
      ? state.opportunityResult
      : await api(`/api/v1/opportunities/${encodeURIComponent(generationId)}`);
    const item = generation.opportunities?.find((candidate) => candidate.id === itemId);
    if (!item) throw new Error("這筆探索紀錄中找不到指定題目。");
    state.opportunityResult = generation;
    startNewTask({ item, generation }, { updateUrl: false });
  } catch (error) {
    showMessage(error.message || "無法還原這筆內容機會草稿。", true);
  }
}

async function restoreRouteState(route) {
  if (route.page !== "workbench") return;
  if (route.workflowId) {
    await openHistoryWorkflow(route.workflowId, { updateUrl: false, scroll: false });
    return;
  }
  if (route.generationId && route.itemId) {
    await restoreOpportunityHandoff(route.generationId, route.itemId);
    return;
  }
  if (restoreWorkbenchDraft()) showMessage("已還原尚未建立的內容草稿。", false);
}

function renderDashboard() {
  const workflows = state.history;
  const waiting = workflows.filter((item) => item.status === "waiting_for_human");
  const completed = workflows.filter((item) => item.status === "completed");
  const active = workflows.filter((item) => ["draft", "running"].includes(item.status));
  const referenceCount = workflows.reduce((sum, item) => sum + (item.input.reference_materials?.length || 0), 0);
  document.querySelector("#dashboardMetrics").innerHTML = [
    ["所有任務", workflows.length, "PostgreSQL 永久保存"],
    ["執行中", active.length, "AI Pipeline 處理中"],
    ["等待核准", waiting.length, "需要內容負責人決定", "attention"],
    ["已完成", completed.length, `累計使用 ${referenceCount} 份參考資料`, "success"],
  ].map(([label, value, note, tone = ""]) => `<article class="overview-metric ${tone}"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`).join("");

  renderOverviewList("#dashboardAttentionList", waiting, "目前沒有待核准任務");
  renderOverviewList("#dashboardRecentList", workflows.slice(0, 5), "建立第一個內容任務後會顯示在這裡");
}

function renderPerformance() {
  const total = state.history.length;
  const completed = state.history.filter((item) => item.status === "completed").length;
  const waiting = state.history.filter((item) => item.status === "waiting_for_human").length;
  const failed = state.history.filter((item) => item.status === "failed").length;
  const completionRate = total ? Math.round((completed / total) * 100) : 0;
  document.querySelector("#performanceMetrics").innerHTML = [
    ["流程完成率", `${completionRate}%`, `${completed} / ${total} 個任務`],
    ["等待人工", waiting, "風險例外或最終核准", "attention"],
    ["執行失敗", failed, "可從紀錄中重試", failed ? "attention" : "success"],
    ["外部平台成效", "尚無資料", "尚未串接社群平台"],
  ].map(([label, value, note, tone = ""]) => `<article class="overview-metric ${tone}"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`).join("");
}

function renderSettings() {
  if (!state.healthInfo && !state.settingsHealthLoading) {
    state.settingsHealthLoading = true;
    checkHealth().finally(() => { state.settingsHealthLoading = false; });
  }
  const health = state.healthInfo || {};
  const quota = state.usage?.generation;
  const authSetting = health.auth_provider === "supabase"
    ? ["Supabase Auth", "登入憑證由 Supabase Auth 驗證；Session、Workspace 與 Membership 由 PostgreSQL 保存。"]
    : health.auth_mode === "session"
    ? ["Session 登入", "由伺服器 Session 與 Membership 驗證工作區權限。"]
    : health.auth_mode === "trusted_proxy"
      ? ["受信任代理", "由上游平台提供使用者與工作區邊界。"]
      : ["本機模式", "僅適用自動化測試；正式環境不可使用。"];
  const settings = [
    ["執行環境", health.environment || "讀取中", `Python 服務：${state.engineStatus === "healthy" ? "正常" : "未連線"}`],
    ["身分驗證", authSetting[0], authSetting[1]],
    ["內容機會引擎", health.status === "ok" ? "AI 研究已啟用" : "讀取中", "研究來源、內容角度與品質覆核由伺服器端安全執行。"],
    ["內容生成服務", health.content_provider === "openai" ? "AI 生成已啟用" : "本機規則模式", "服務憑證與模型設定只保存在伺服器端。"],
    ["工作台代理", health.workflow_agent_provider === "openai" ? "AI 代理已啟用" : "本機規則模式", "研究、條件式驗證、品質主編與製作規劃共用安全的伺服器端設定。"],
    ["今日生成額度", quota ? `${quota.used} / ${quota.limit}` : "讀取中", quota ? `今日剩餘 ${quota.remaining} 次；額度依工作區隔離。` : "正在讀取工作區額度。"],
    ["請求限制", health.requests_per_minute ? `每分鐘 ${health.requests_per_minute} 次` : "讀取中", "MVP 單機限制；多機部署時需改用共用 Redis 或 API Gateway。"],
    ["資料保存", health.workflow_storage === "postgresql" ? "PostgreSQL + Supabase Storage" : "SQLite 本機資料庫", health.workflow_storage === "postgresql" ? "工作流、內容機會、額度、回饋與 Prompt 版本儲存在 PostgreSQL；生成媒體儲存在私有 Storage。" : "僅用於本機測試環境。"],
    ["外部發布", "尚未連接", "發布中心只保存排程，不會直接呼叫社群平台。"],
    ["AI 信心值", "來源透明", "本機規則不再顯示成模型信心百分比。"],
  ];
  document.querySelector("#settingsContent").innerHTML = settings.map(([label, value, note]) => `<article class="settings-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><p>${escapeHtml(note)}</p></article>`).join("");
}

async function loadPromptCenter() {
  const panel = document.querySelector("#promptCenterPanel");
  try {
    state.prompts = await api("/api/v1/prompts");
    panel.classList.remove("hidden");
    if (!state.prompts.some((item) => item.key === state.activePromptKey)) state.activePromptKey = state.prompts[0]?.key || null;
    renderPromptCenter();
  } catch (error) {
    if (error.status === 403) {
      panel.classList.add("hidden");
      state.prompts = [];
      state.activePromptKey = null;
      document.querySelector("#promptCatalog").replaceChildren();
      document.querySelector("#promptEditor").replaceChildren();
      return;
    }
    panel.classList.remove("hidden");
    showPromptCenterMessage(error.message || "無法讀取 Prompt 中心。", true);
  }
}

function renderPromptCenter() {
  const catalog = document.querySelector("#promptCatalog");
  catalog.innerHTML = state.prompts.map((item) => {
    const active = item.active_version?.version;
    return `<button type="button" class="prompt-catalog-item ${item.key === state.activePromptKey ? "active" : ""}" data-prompt-key="${escapeHtml(item.key)}"><strong>${escapeHtml(item.name)}</strong><span>${active ? `正式 v${active}` : "使用預設值"}</span></button>`;
  }).join("");
  catalog.querySelectorAll("[data-prompt-key]").forEach((button) => button.addEventListener("click", () => {
    state.activePromptKey = button.dataset.promptKey;
    state.promptVersions = [];
    state.promptTestCases = [];
    state.promptTestRuns = [];
    renderPromptCenter();
  }));
  const item = state.prompts.find((prompt) => prompt.key === state.activePromptKey);
  const editor = document.querySelector("#promptEditor");
  if (!item) {
    editor.innerHTML = '<div class="prompt-empty">目前沒有可管理的 Prompt。</div>';
    return;
  }
  const editable = item.latest_version?.instructions || item.active_version?.instructions || item.default;
  editor.innerHTML = `
    <div class="prompt-editor-heading"><div><span class="section-kicker">${escapeHtml(item.key)}</span><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(item.description)}</p></div><span class="prompt-version-badge">${item.active_version ? `正式 v${item.active_version.version}` : "系統預設"}</span></div>
    <div class="locked-prompt-rules"><strong>🔒 鎖定規則</strong><p>${escapeHtml(item.locked)}</p><small>這一層不能從介面修改，Owner 指示若與它衝突仍以鎖定規則為準。</small></div>
    <form id="promptVersionForm" class="prompt-version-form">
      <label><span>Owner 業務指示</span><textarea id="promptInstructions" maxlength="12000" required>${escapeHtml(editable)}</textarea></label>
      <label><span>版本說明</span><input id="promptChangeNote" maxlength="500" placeholder="例如：強化開箱評測的實測證據與口語節奏" /></label>
      <div class="prompt-editor-actions"><small>儲存後會建立草稿版本，不會直接影響正式流程。</small><div class="prompt-editor-buttons"><button id="loadPromptTemplate" class="text-button" type="button">載入精細模板</button><button class="secondary-button" type="submit">建立新版本</button></div></div>
    </form>
    <div class="prompt-version-header"><h4>版本紀錄</h4><button id="refreshPromptVersions" class="text-button" type="button">重新整理</button></div>
    <div id="promptVersionList" class="prompt-version-list"><div class="prompt-empty">正在讀取版本…</div></div>
    <section class="prompt-test-bench" aria-label="Prompt 測試台">
      <div class="prompt-test-heading"><div><span class="section-kicker">FIXED DATA · A/B EVALUATION</span><h4>Prompt 測試台</h4><p>固定同一份輸入，同時執行兩個版本；AI 分數與人工偏好分開記錄。</p></div><span class="owner-only-badge">Owner only</span></div>
      <form id="promptTestCaseForm" class="prompt-test-case-form">
        <h5>建立固定測試資料</h5>
        <label><span>案例名稱</span><input id="promptTestCaseName" maxlength="120" placeholder="例如：DJI Osmo 360 開箱評測" required /></label>
        <label><span>固定輸入</span><textarea id="promptTestInput" maxlength="12000" placeholder="貼上每次 A/B 測試都使用的 Brief、逐字稿或結構化輸入。" required></textarea></label>
        <label><span>驗收規準</span><textarea id="promptTestRubric" maxlength="4000" placeholder="例如：不可虛構實測；每段需有時間、畫面目的與 B-roll。" required></textarea></label>
        <button class="secondary-button" type="submit">保存固定案例</button>
      </form>
      <form id="promptTestRunForm" class="prompt-test-run-form">
        <h5>執行 v1 / v2 比較</h5>
        <label><span>固定案例</span><select id="promptTestCaseSelect" required></select></label>
        <label><span>版本 A</span><select id="promptVersionA" required></select></label>
        <label><span>版本 B</span><select id="promptVersionB" required></select></label>
        <button id="runPromptTestButton" class="primary-button" type="submit">同時執行 A / B 測試</button>
        <small>會呼叫兩次生成與一次匿名 AI 評審；成本由 Token 與環境單價估算。</small>
      </form>
      <div id="promptTestMessage" class="message-bar" role="status" aria-live="polite"></div>
      <div id="promptTestResults" class="prompt-test-results"><div class="prompt-empty">尚無測試紀錄。</div></div>
    </section>`;
  editor.querySelector("#loadPromptTemplate").addEventListener("click", () => {
    editor.querySelector("#promptInstructions").value = item.default;
    editor.querySelector("#promptChangeNote").value = "套用系統精細模板，待 A/B 測試後決定是否啟用";
    showPromptCenterMessage("已載入此代理的系統精細模板；請檢查內容並建立新版本，不會自動啟用。", false);
  });
  editor.querySelector("#promptVersionForm").addEventListener("submit", createPromptVersion);
  editor.querySelector("#refreshPromptVersions").addEventListener("click", () => loadPromptVersions(item.key));
  editor.querySelector("#promptTestCaseForm").addEventListener("submit", createPromptTestCase);
  editor.querySelector("#promptTestRunForm").addEventListener("submit", runPromptComparison);
  loadPromptVersions(item.key);
}

async function loadPromptVersions(promptKey) {
  if (!state.prompts.length || promptKey !== state.activePromptKey) return;
  const container = document.querySelector("#promptVersionList");
  try {
    const versions = await api(`/api/v1/prompts/${encodeURIComponent(promptKey)}/versions`);
    if (promptKey !== state.activePromptKey) return;
    state.promptVersions = versions;
    container.innerHTML = versions.length ? versions.map((version) => `<article class="prompt-version-row"><div><strong>v${version.version}${version.activated_at ? " · 正式使用中" : " · 草稿"}</strong><span>${escapeHtml(version.change_note || "未填版本說明")}</span><small>${formatTimestamp(version.created_at)}</small></div>${version.activated_at ? '<span class="active-version-mark">已啟用</span>' : `<button class="text-button" type="button" data-activate-version="${version.version}">設為正式版本</button>`}</article>`).join("") : '<div class="prompt-empty">尚未建立自訂版本，目前使用系統預設指示。</div>';
    container.querySelectorAll("[data-activate-version]").forEach((button) => button.addEventListener("click", () => activatePromptVersion(promptKey, Number(button.dataset.activateVersion))));
    renderPromptTestSelectors();
    await loadPromptTests(promptKey);
  } catch (error) {
    container.innerHTML = `<div class="prompt-empty error-text">${escapeHtml(error.message || "無法讀取版本。")}</div>`;
  }
}

function renderPromptTestSelectors() {
  const caseSelect = document.querySelector("#promptTestCaseSelect");
  const versionA = document.querySelector("#promptVersionA");
  const versionB = document.querySelector("#promptVersionB");
  const runButton = document.querySelector("#runPromptTestButton");
  if (!caseSelect || !versionA || !versionB || !runButton) return;
  caseSelect.innerHTML = state.promptTestCases.length
    ? state.promptTestCases.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("")
    : '<option value="">請先建立固定案例</option>';
  const versionOptions = state.promptVersions.map((item) => `<option value="${item.version}">v${item.version}${item.activated_at ? " · 正式" : " · 草稿"}</option>`).join("");
  versionA.innerHTML = versionOptions || '<option value="">尚無版本</option>';
  versionB.innerHTML = versionOptions || '<option value="">尚無版本</option>';
  if (state.promptVersions.length > 1) versionB.value = String(state.promptVersions[1].version);
  runButton.disabled = state.promptVersions.length < 2 || !state.promptTestCases.length;
  runButton.title = state.promptVersions.length < 2 ? "至少需要兩個 Prompt 版本" : !state.promptTestCases.length ? "請先建立固定案例" : "";
}

function promptTestCost(result) {
  const tokens = result?.usage?.total_tokens || 0;
  return result?.estimated_cost_usd == null
    ? `${tokens.toLocaleString()} tokens · 尚未設定單價`
    : `$${Number(result.estimated_cost_usd).toFixed(6)} · ${tokens.toLocaleString()} tokens`;
}

function renderPromptTestResults() {
  const container = document.querySelector("#promptTestResults");
  if (!container) return;
  if (!state.promptTestRuns.length) {
    container.innerHTML = '<div class="prompt-empty">尚無測試紀錄。</div>';
    return;
  }
  const caseNames = new Map(state.promptTestCases.map((item) => [item.id, item.name]));
  container.innerHTML = state.promptTestRuns.map((run) => {
    const resultA = run.result.result_a;
    const resultB = run.result.result_b;
    const judge = run.result.judgment;
    const human = run.preference ? ({ a: `偏好 v${run.version_a}`, b: `偏好 v${run.version_b}`, tie: "人工判定平手" })[run.preference] : "尚未評選";
    return `<article class="prompt-test-result" data-prompt-test-run="${escapeHtml(run.id)}">
      <div class="prompt-test-result-header"><div><strong>${escapeHtml(caseNames.get(run.case_id) || "固定案例")}</strong><small>${formatTimestamp(run.created_at)} · AI 自動評估</small></div><span class="prompt-test-winner">${judge.winner === "tie" ? "AI：平手" : `AI：v${judge.winner === "a" ? run.version_a : run.version_b} 較佳`}</span></div>
      <p>${escapeHtml(judge.summary)}</p>
      <div class="prompt-test-comparison">
        <section><h5>版本 A · v${run.version_a}</h5><div class="prompt-test-metrics"><b>${judge.score_a} 分</b><span>${(resultA.latency_ms / 1000).toFixed(1)} 秒</span><span>${escapeHtml(promptTestCost(resultA))}</span></div><details><summary>查看輸出</summary><pre>${escapeHtml(resultA.output)}</pre></details></section>
        <section><h5>版本 B · v${run.version_b}</h5><div class="prompt-test-metrics"><b>${judge.score_b} 分</b><span>${(resultB.latency_ms / 1000).toFixed(1)} 秒</span><span>${escapeHtml(promptTestCost(resultB))}</span></div><details><summary>查看輸出</summary><pre>${escapeHtml(resultB.output)}</pre></details></section>
      </div>
      <div class="prompt-human-preference"><strong>人工偏好：${escapeHtml(human)}</strong><select data-test-preference><option value="">請選擇</option><option value="a" ${run.preference === "a" ? "selected" : ""}>版本 A · v${run.version_a}</option><option value="b" ${run.preference === "b" ? "selected" : ""}>版本 B · v${run.version_b}</option><option value="tie" ${run.preference === "tie" ? "selected" : ""}>平手</option></select><input data-test-preference-note maxlength="1000" value="${escapeHtml(run.preference_note || "")}" placeholder="偏好原因（選填）" /><button type="button" class="secondary-button" data-save-test-preference>保存人工偏好</button></div>
    </article>`;
  }).join("");
  container.querySelectorAll("[data-save-test-preference]").forEach((button) => button.addEventListener("click", () => savePromptTestPreference(button.closest("[data-prompt-test-run]"))));
}

async function loadPromptTests(promptKey) {
  if (promptKey !== state.activePromptKey) return;
  try {
    const [cases, runs] = await Promise.all([
      api(`/api/v1/prompts/${encodeURIComponent(promptKey)}/test-cases`),
      api(`/api/v1/prompts/${encodeURIComponent(promptKey)}/test-runs`),
    ]);
    if (promptKey !== state.activePromptKey) return;
    state.promptTestCases = cases;
    state.promptTestRuns = runs;
    renderPromptTestSelectors();
    renderPromptTestResults();
  } catch (error) {
    showPromptTestMessage(error.message || "無法讀取 Prompt 測試紀錄。", true);
  }
}

async function createPromptTestCase(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await api(`/api/v1/prompts/${encodeURIComponent(state.activePromptKey)}/test-cases`, { method: "POST", body: JSON.stringify({ name: document.querySelector("#promptTestCaseName").value, input_text: document.querySelector("#promptTestInput").value, rubric: document.querySelector("#promptTestRubric").value }) });
    event.currentTarget.reset();
    showPromptTestMessage("固定測試案例已保存。", false);
    await loadPromptTests(state.activePromptKey);
  } catch (error) {
    showPromptTestMessage(error.message || "無法保存固定案例。", true);
  } finally {
    button.disabled = false;
  }
}

async function runPromptComparison(event) {
  event.preventDefault();
  const button = document.querySelector("#runPromptTestButton");
  const versionA = Number(document.querySelector("#promptVersionA").value);
  const versionB = Number(document.querySelector("#promptVersionB").value);
  if (versionA === versionB) {
    showPromptTestMessage("請選擇兩個不同版本。", true);
    return;
  }
  button.disabled = true;
  button.textContent = "A / B 同時執行中…";
  showPromptTestMessage("正在並行生成兩個版本，完成後會進行匿名品質評估。", false);
  try {
    await api(`/api/v1/prompts/${encodeURIComponent(state.activePromptKey)}/test-runs`, { method: "POST", body: JSON.stringify({ case_id: document.querySelector("#promptTestCaseSelect").value, version_a: versionA, version_b: versionB }) });
    showPromptTestMessage("比較完成；請查看 AI 分數、成本、耗時，並保存你的人工偏好。", false);
    await loadPromptTests(state.activePromptKey);
    await loadUsage();
  } catch (error) {
    showPromptTestMessage(error.message || "Prompt 比較失敗。", true);
  } finally {
    button.textContent = "同時執行 A / B 測試";
    renderPromptTestSelectors();
  }
}

async function savePromptTestPreference(card) {
  const preference = card.querySelector("[data-test-preference]").value;
  if (!preference) {
    showPromptTestMessage("請先選擇版本 A、版本 B 或平手。", true);
    return;
  }
  try {
    await api(`/api/v1/prompts/test-runs/${encodeURIComponent(card.dataset.promptTestRun)}/preference`, { method: "PATCH", body: JSON.stringify({ preference, note: card.querySelector("[data-test-preference-note]").value }) });
    showPromptTestMessage("人工偏好已保存，AI 分數不會覆蓋你的選擇。", false);
    await loadPromptTests(state.activePromptKey);
  } catch (error) {
    showPromptTestMessage(error.message || "無法保存人工偏好。", true);
  }
}

function showPromptTestMessage(message, isError = false) {
  const bar = document.querySelector("#promptTestMessage");
  if (!bar) return;
  bar.textContent = message;
  bar.classList.toggle("error", Boolean(isError));
  bar.classList.add("show");
}

async function createPromptVersion(event) {
  event.preventDefault();
  const promptKey = state.activePromptKey;
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await api(`/api/v1/prompts/${encodeURIComponent(promptKey)}/versions`, { method: "POST", body: JSON.stringify({ instructions: document.querySelector("#promptInstructions").value, change_note: document.querySelector("#promptChangeNote").value }) });
    showPromptCenterMessage("已建立新草稿版本；確認後再設為正式版本。", false);
    await loadPromptCenter();
  } catch (error) {
    showPromptCenterMessage(error.message || "無法建立 Prompt 版本。", true);
  } finally {
    button.disabled = false;
  }
}

async function activatePromptVersion(promptKey, version) {
  if (!window.confirm(`確定要把 v${version} 設為正式版本嗎？之後的新任務會使用這份業務指示。`)) return;
  try {
    await api(`/api/v1/prompts/${encodeURIComponent(promptKey)}/versions/${version}/activate`, { method: "POST" });
    showPromptCenterMessage(`已啟用 v${version}；舊版本仍保留，可隨時回復。`, false);
    await loadPromptCenter();
  } catch (error) {
    showPromptCenterMessage(error.message || "無法啟用 Prompt 版本。", true);
  }
}

function showPromptCenterMessage(message, isError = false) {
  const bar = document.querySelector("#promptCenterMessage");
  bar.textContent = message;
  bar.classList.toggle("error", Boolean(isError));
  bar.classList.add("show");
}

async function loadInviteCodes() {
  const panel = document.querySelector("#inviteCodePanel");
  try {
    state.inviteCodes = await api("/api/v1/invite-codes");
    panel.classList.remove("hidden");
    renderInviteCodes();
  } catch (error) {
    // The server only answers platform owners; everyone else never sees the panel.
    panel.classList.add("hidden");
    state.inviteCodes = [];
    if (error.status !== 403) console.warn("invite codes unavailable", error);
  }
}

function inviteCodeStatus(item) {
  if (item.revoked_at) return "已停用";
  if (item.expires_at && new Date(item.expires_at) <= new Date()) return "已過期";
  if (item.used_count >= item.max_uses) return "已用完";
  return "可使用";
}

function renderInviteCodes() {
  const list = document.querySelector("#inviteCodeList");
  const codes = state.inviteCodes || [];
  if (!codes.length) {
    list.innerHTML = '<div class="prompt-empty">尚未建立邀請碼。</div>';
    return;
  }
  list.innerHTML = codes.map((item) => {
    const status = inviteCodeStatus(item);
    const expires = item.expires_at ? `${formatDateTime(item.expires_at)} 到期` : "不過期";
    return `<article class="member-row"><div><strong>${escapeHtml(item.label || "未命名邀請碼")}</strong><small>…${escapeHtml(item.code_hint)} · 已使用 ${Number(item.used_count)} / ${Number(item.max_uses)} · ${escapeHtml(expires)}</small></div><span>${escapeHtml(status)}</span>${status === "可使用" ? `<button class="text-button" type="button" data-revoke-invite="${escapeHtml(item.id)}">停用</button>` : ""}</article>`;
  }).join("");
  list.querySelectorAll("[data-revoke-invite]").forEach((button) => button.addEventListener("click", () => revokeInviteCode(button.dataset.revokeInvite)));
}

function showInviteCodeMessage(message, isError = false) {
  const bar = document.querySelector("#inviteCodeMessage");
  bar.textContent = message;
  bar.classList.toggle("error", Boolean(isError));
  bar.classList.add("show");
}

async function createInviteCode(event) {
  event.preventDefault();
  const expires = document.querySelector("#inviteExpiresInDays").value;
  try {
    const invite = await api("/api/v1/invite-codes", {
      method: "POST",
      body: JSON.stringify({
        label: document.querySelector("#inviteLabel").value.trim(),
        max_uses: Number(document.querySelector("#inviteMaxUses").value || 1),
        expires_in_days: expires ? Number(expires) : null,
      }),
    });
    const link = `${window.location.origin}/?invite=${encodeURIComponent(invite.code)}`;
    const reveal = document.querySelector("#inviteCodeReveal");
    reveal.innerHTML = `<strong>新的邀請碼（只會顯示這一次）</strong><code>${escapeHtml(invite.code)}</code><small>${escapeHtml(link)}</small><div><button class="secondary-button" type="button" data-copy-invite="code">複製邀請碼</button><button class="secondary-button" type="button" data-copy-invite="link">複製邀請連結</button></div>`;
    reveal.classList.remove("hidden");
    reveal.querySelectorAll("[data-copy-invite]").forEach((button) => button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copyInvite === "link" ? link : invite.code);
        showInviteCodeMessage("已複製。");
      } catch {
        showInviteCodeMessage("無法自動複製，請手動選取上方文字。", true);
      }
    }));
    document.querySelector("#createInviteCodeForm").reset();
    await loadInviteCodes();
  } catch (error) {
    showInviteCodeMessage(error.message || "無法建立邀請碼。", true);
  }
}

async function revokeInviteCode(codeId) {
  try {
    await api(`/api/v1/invite-codes/${encodeURIComponent(codeId)}`, { method: "DELETE" });
    showInviteCodeMessage("邀請碼已停用。");
    await loadInviteCodes();
  } catch (error) {
    showInviteCodeMessage(error.message || "無法停用邀請碼。", true);
  }
}

async function loadUsage() {
  try {
    state.usage = await api("/api/v1/usage");
    if (state.currentPage === "settings") renderSettings();
  } catch {
    state.usage = null;
  }
}

async function loadBetaUsage(force = false) {
  const panel = document.querySelector("#betaUsagePanel");
  const content = document.querySelector("#betaUsageContent");
  const message = document.querySelector("#betaUsageMessage");
  if (!force && state.betaUsage) {
    panel.classList.remove("hidden");
    renderBetaUsage();
    return;
  }
  const days = Number(document.querySelector("#betaUsageDays").value || 30);
  if (force) content.innerHTML = '<div class="prompt-empty">正在更新 Beta 使用資料…</div>';
  message.classList.remove("show", "error");
  try {
    state.betaUsage = await api(`/api/v1/admin/beta-usage?days=${days}`);
    panel.classList.remove("hidden");
    renderBetaUsage();
  } catch (error) {
    state.betaUsage = null;
    if (error.status === 403) {
      panel.classList.add("hidden");
      content.replaceChildren();
      return;
    }
    panel.classList.remove("hidden");
    message.textContent = error.message || "無法讀取 Beta 使用資料。";
    message.classList.add("show", "error");
  }
}

function openBugReport() {
  const modal = document.querySelector("#bugReportModal");
  document.querySelector("#bugReportMessage").textContent = "";
  document.querySelector("#bugReportMessage").classList.remove("error");
  modal.classList.remove("hidden");
  document.body.classList.add("modal-open");
  document.querySelector("#bugReportDescription").focus();
}

function closeBugReport() {
  document.querySelector("#bugReportModal").classList.add("hidden");
  document.body.classList.remove("modal-open");
}

function clearBugScreenshot() {
  bugCapturedScreenshot = null;
  document.querySelector("#bugReportScreenshot").value = "";
  const preview = document.querySelector("#bugScreenshotPreview");
  const image = preview.querySelector("img");
  if (image.src.startsWith("blob:")) URL.revokeObjectURL(image.src);
  image.removeAttribute("src");
  preview.classList.add("hidden");
}

function previewBugScreenshot(event) {
  const file = event.target.files?.[0];
  if (!file) return clearBugScreenshot();
  const message = document.querySelector("#bugReportMessage");
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024) {
    clearBugScreenshot();
    message.textContent = "截圖需為 PNG、JPG 或 WebP，且不可超過 5 MB。";
    message.classList.add("error");
    return;
  }
  bugCapturedScreenshot = null;
  const preview = document.querySelector("#bugScreenshotPreview");
  preview.querySelector("img").src = URL.createObjectURL(file);
  preview.classList.remove("hidden");
  message.textContent = "";
  message.classList.remove("error");
}

async function captureCurrentScreenForBugReport() {
  const message = document.querySelector("#bugReportMessage");
  const button = document.querySelector("#captureBugScreenshot");
  const modal = document.querySelector("#bugReportModal");
  if (!navigator.mediaDevices?.getDisplayMedia) {
    message.textContent = "這個瀏覽器不支援直接擷取畫面，請改用上傳截圖。";
    message.classList.add("error");
    return;
  }
  let stream = null;
  let editorOpened = false;
  button.disabled = true;
  button.textContent = "請選擇目前分頁…";
  message.textContent = "請在瀏覽器視窗選擇「目前分頁」或要回報的視窗。";
  message.classList.remove("error");
  try {
    stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false, preferCurrentTab: true });
    modal.classList.add("hidden");
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const video = document.createElement("video");
    video.muted = true;
    video.srcObject = stream;
    await video.play();
    await new Promise((resolve) => window.setTimeout(resolve, 180));
    if (!video.videoWidth || !video.videoHeight) throw new Error("無法取得擷取畫面尺寸。");
    const scale = Math.min(1, 1920 / video.videoWidth);
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
    canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.86));
    if (!blob) throw new Error("無法建立截圖。");
    if (blob.size > 5 * 1024 * 1024) throw new Error("擷取的畫面超過 5 MB，請改用較小的視窗或上傳圖片。");
    await openBugScreenshotEditor(blob);
    editorOpened = true;
    message.textContent = "可使用標註工具指出問題，完成後按右側綠色勾選。";
  } catch (error) {
    if (error?.name === "NotAllowedError") message.textContent = "你已取消畫面分享；可以重新擷取或改用上傳截圖。";
    else message.textContent = error.message || "畫面擷取失敗，請改用上傳截圖。";
    message.classList.add("error");
  } finally {
    stream?.getTracks().forEach((track) => track.stop());
    if (!editorOpened) modal.classList.remove("hidden");
    button.disabled = false;
    button.innerHTML = "<span>▣</span> 擷取目前畫面";
  }
}

async function openBugScreenshotEditor(blob) {
  const objectUrl = URL.createObjectURL(blob);
  const image = new Image();
  image.src = objectUrl;
  await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = () => reject(new Error("無法讀取擷取畫面。")); });
  const canvas = document.querySelector("#bugEditorCanvas");
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  const availableWidth = Math.max(320, window.innerWidth - 36);
  const availableHeight = Math.max(240, window.innerHeight - 120);
  const displayScale = Math.min(1, availableWidth / canvas.width, availableHeight / canvas.height);
  canvas.style.width = `${Math.round(canvas.width * displayScale)}px`;
  canvas.style.height = `${Math.round(canvas.height * displayScale)}px`;
  bugScreenshotEditorState = {
    canvas, ctx: canvas.getContext("2d"), image, objectUrl, tool: "crop", operations: [],
    crop: { x: 0, y: 0, w: canvas.width, h: canvas.height }, start: null, current: null, drawing: false, selectedEmoji: "⚠️",
  };
  document.querySelector("#bugScreenshotEditor").classList.remove("hidden");
  document.querySelector("#bugReportModal").classList.add("hidden");
  setBugEditorTool("crop");
  renderBugScreenshotEditor();
}

function setBugEditorTool(tool) {
  if (!bugScreenshotEditorState) return;
  bugScreenshotEditorState.tool = tool;
  document.querySelectorAll("[data-bug-editor-tool]").forEach((button) => button.classList.toggle("active", button.dataset.bugEditorTool === tool));
  const emojiPicker = document.querySelector("#bugEditorEmojiPicker");
  const emojiTool = document.querySelector("#bugEditorEmojiTool");
  emojiPicker.classList.toggle("hidden", tool !== "emoji");
  emojiTool.setAttribute("aria-expanded", String(tool === "emoji"));
  const hints = {
    crop: "拖曳框選要保留的畫面範圍", rect: "拖曳矩形框出問題位置", ellipse: "拖曳圓形圈出問題位置",
    arrow: "從起點拖曳到要指向的位置", pen: "按住拖曳自由標示", mosaic: "拖曳遮蔽敏感資訊",
    text: "點擊畫面位置並輸入文字", emoji: `目前選擇 ${bugScreenshotEditorState.selectedEmoji}；點擊畫面位置加入表情`,
  };
  document.querySelector("#bugEditorHint").textContent = hints[tool] || "在畫面上標示問題";
}

function selectBugEditorEmoji(emoji) {
  const state = bugScreenshotEditorState;
  if (!state || !emoji) return;
  state.selectedEmoji = emoji;
  state.tool = "emoji";
  document.querySelectorAll("[data-bug-editor-emoji]").forEach((button) => button.classList.toggle("active", button.dataset.bugEditorEmoji === emoji));
  document.querySelector("#bugEditorEmojiPicker").classList.add("hidden");
  document.querySelector("#bugEditorEmojiTool").setAttribute("aria-expanded", "false");
  document.querySelector("#bugEditorHint").textContent = `已選擇 ${emoji}；請點擊畫面中要標示的位置`;
}

function bugEditorPoint(event) {
  const state = bugScreenshotEditorState;
  const bounds = state.canvas.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(state.canvas.width, (event.clientX - bounds.left) * state.canvas.width / bounds.width)),
    y: Math.max(0, Math.min(state.canvas.height, (event.clientY - bounds.top) * state.canvas.height / bounds.height)),
  };
}

function bugEditorStyle() {
  return { color: document.querySelector("#bugEditorColor").value, size: Number(document.querySelector("#bugEditorSize").value || 6) };
}

function beginBugEditorAction(event) {
  const state = bugScreenshotEditorState;
  if (!state) return;
  event.preventDefault();
  const point = bugEditorPoint(event);
  if (state.tool === "text" || state.tool === "emoji") {
    const value = state.tool === "emoji" ? state.selectedEmoji : window.prompt("輸入要標示的文字", "");
    if (value?.trim()) state.operations.push({ tool: state.tool, point, value: value.trim().slice(0, 120), ...bugEditorStyle() });
    renderBugScreenshotEditor();
    return;
  }
  state.canvas.setPointerCapture?.(event.pointerId);
  state.drawing = true;
  state.start = point;
  state.current = state.tool === "pen" ? { tool: "pen", points: [point], ...bugEditorStyle() } : { tool: state.tool, start: point, end: point, ...bugEditorStyle() };
}

function moveBugEditorAction(event) {
  const state = bugScreenshotEditorState;
  if (!state?.drawing) return;
  event.preventDefault();
  const point = bugEditorPoint(event);
  if (state.tool === "pen") state.current.points.push(point);
  else state.current.end = point;
  renderBugScreenshotEditor();
}

function finishBugEditorAction(event) {
  const state = bugScreenshotEditorState;
  if (!state?.drawing) return;
  if (event.type === "pointerup") moveBugEditorAction(event);
  state.drawing = false;
  if (state.tool === "crop") {
    const rect = normalizedBugEditorRect(state.current.start, state.current.end);
    if (rect.w >= 20 && rect.h >= 20) state.crop = rect;
  } else if (state.tool === "pen") {
    if (state.current.points.length > 1) state.operations.push(state.current);
  } else {
    const rect = normalizedBugEditorRect(state.current.start, state.current.end);
    if (rect.w >= 4 || rect.h >= 4) state.operations.push(state.current);
  }
  state.current = null;
  renderBugScreenshotEditor();
}

function normalizedBugEditorRect(start, end) {
  return { x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), w: Math.abs(end.x - start.x), h: Math.abs(end.y - start.y) };
}

function renderBugScreenshotEditor(includeSelection = true) {
  const state = bugScreenshotEditorState;
  if (!state) return;
  const { ctx, canvas } = state;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(state.image, 0, 0, canvas.width, canvas.height);
  [...state.operations, ...(state.current && state.tool !== "crop" ? [state.current] : [])].forEach((operation) => drawBugEditorOperation(ctx, operation));
  if (!includeSelection) return;
  const selection = state.current?.tool === "crop" ? normalizedBugEditorRect(state.current.start, state.current.end) : state.crop;
  ctx.save();
  ctx.fillStyle = "rgba(4,10,20,.5)";
  ctx.beginPath(); ctx.rect(0, 0, canvas.width, canvas.height); ctx.rect(selection.x, selection.y, selection.w, selection.h); ctx.fill("evenodd");
  ctx.strokeStyle = "#10b981"; ctx.lineWidth = Math.max(2, canvas.width / 700); ctx.setLineDash([10, 7]);
  ctx.strokeRect(selection.x, selection.y, selection.w, selection.h);
  ctx.restore();
}

function drawBugEditorOperation(ctx, operation) {
  const color = operation.color || "#ff3b30";
  const size = operation.size || 6;
  ctx.save(); ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = size; ctx.lineCap = "round"; ctx.lineJoin = "round";
  if (operation.tool === "pen") {
    ctx.beginPath(); operation.points.forEach((point, index) => index ? ctx.lineTo(point.x, point.y) : ctx.moveTo(point.x, point.y)); ctx.stroke();
  } else if (["rect", "ellipse", "mosaic", "arrow"].includes(operation.tool)) {
    const rect = normalizedBugEditorRect(operation.start, operation.end);
    if (operation.tool === "rect") ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
    if (operation.tool === "ellipse") { ctx.beginPath(); ctx.ellipse(rect.x + rect.w / 2, rect.y + rect.h / 2, rect.w / 2, rect.h / 2, 0, 0, Math.PI * 2); ctx.stroke(); }
    if (operation.tool === "mosaic" && rect.w > 0 && rect.h > 0) {
      const pixel = Math.max(6, Math.round(size * 2.5));
      const temp = document.createElement("canvas"); temp.width = Math.max(1, Math.ceil(rect.w / pixel)); temp.height = Math.max(1, Math.ceil(rect.h / pixel));
      temp.getContext("2d").drawImage(ctx.canvas, rect.x, rect.y, rect.w, rect.h, 0, 0, temp.width, temp.height);
      ctx.imageSmoothingEnabled = false; ctx.drawImage(temp, 0, 0, temp.width, temp.height, rect.x, rect.y, rect.w, rect.h); ctx.imageSmoothingEnabled = true;
    }
    if (operation.tool === "arrow") {
      const { x: x1, y: y1 } = operation.start; const { x: x2, y: y2 } = operation.end;
      const angle = Math.atan2(y2 - y1, x2 - x1); const head = Math.max(16, size * 4);
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x2, y2); ctx.lineTo(x2 - head * Math.cos(angle - Math.PI / 6), y2 - head * Math.sin(angle - Math.PI / 6)); ctx.lineTo(x2 - head * Math.cos(angle + Math.PI / 6), y2 - head * Math.sin(angle + Math.PI / 6)); ctx.closePath(); ctx.fill();
    }
  } else if (operation.tool === "text" || operation.tool === "emoji") {
    const fontSize = operation.tool === "emoji" ? Math.max(28, size * 7) : Math.max(22, size * 5);
    ctx.font = `${operation.tool === "text" ? "700 " : ""}${fontSize}px "Noto Sans TC", sans-serif`;
    ctx.lineWidth = Math.max(2, size / 2); ctx.strokeStyle = "rgba(255,255,255,.95)";
    if (operation.tool === "text") ctx.strokeText(operation.value, operation.point.x, operation.point.y);
    ctx.fillText(operation.value, operation.point.x, operation.point.y);
  }
  ctx.restore();
}

function undoBugEditorAction() {
  const state = bugScreenshotEditorState;
  if (!state) return;
  if (state.operations.length) state.operations.pop();
  else state.crop = { x: 0, y: 0, w: state.canvas.width, h: state.canvas.height };
  renderBugScreenshotEditor();
}

async function exportBugEditorBlob() {
  const state = bugScreenshotEditorState;
  if (!state) return null;
  renderBugScreenshotEditor(false);
  const output = document.createElement("canvas");
  output.width = Math.max(1, Math.round(state.crop.w)); output.height = Math.max(1, Math.round(state.crop.h));
  output.getContext("2d").drawImage(state.canvas, state.crop.x, state.crop.y, state.crop.w, state.crop.h, 0, 0, output.width, output.height);
  const blob = await new Promise((resolve) => output.toBlob(resolve, "image/jpeg", 0.9));
  renderBugScreenshotEditor(true);
  return blob;
}

async function confirmBugScreenshotEditor() {
  const blob = await exportBugEditorBlob();
  if (!blob) return;
  if (blob.size > 5 * 1024 * 1024) {
    document.querySelector("#bugEditorHint").textContent = "截圖超過 5 MB，請縮小框選範圍後再試。";
    return;
  }
  bugCapturedScreenshot = new File([blob], `tackyflow-bug-${Date.now()}.jpg`, { type: "image/jpeg" });
  document.querySelector("#bugReportScreenshot").value = "";
  const preview = document.querySelector("#bugScreenshotPreview");
  const image = preview.querySelector("img");
  if (image.src.startsWith("blob:")) URL.revokeObjectURL(image.src);
  image.src = URL.createObjectURL(bugCapturedScreenshot);
  preview.classList.remove("hidden");
  closeBugScreenshotEditor(true);
  const message = document.querySelector("#bugReportMessage");
  message.textContent = "已加入標註截圖，可繼續補充說明或直接提交。"; message.classList.remove("error");
}

async function downloadBugEditorImage() {
  const blob = await exportBugEditorBlob();
  if (!blob) return;
  const link = document.createElement("a"); const url = URL.createObjectURL(blob);
  link.href = url; link.download = `tackyflow-markup-${Date.now()}.jpg`; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function closeBugScreenshotEditor(confirmed) {
  const state = bugScreenshotEditorState;
  document.querySelector("#bugScreenshotEditor").classList.add("hidden");
  document.querySelector("#bugEditorEmojiPicker").classList.add("hidden");
  document.querySelector("#bugEditorEmojiTool").setAttribute("aria-expanded", "false");
  document.querySelector("#bugReportModal").classList.remove("hidden");
  if (state?.objectUrl) URL.revokeObjectURL(state.objectUrl);
  bugScreenshotEditorState = null;
  if (!confirmed) {
    const message = document.querySelector("#bugReportMessage");
    message.textContent = "已取消標註，尚未加入截圖。"; message.classList.remove("error");
  }
}

async function submitBugReport(event) {
  event.preventDefault();
  const button = document.querySelector("#submitBugReport");
  const message = document.querySelector("#bugReportMessage");
  const screenshot = bugCapturedScreenshot || document.querySelector("#bugReportScreenshot").files?.[0] || null;
  const payload = {
    category: document.querySelector("#bugReportCategory").value,
    severity: document.querySelector("#bugReportSeverity").value,
    description: document.querySelector("#bugReportDescription").value.trim(),
    steps_to_reproduce: document.querySelector("#bugReportSteps").value.trim(),
    expected_behavior: document.querySelector("#bugReportExpected").value.trim(),
    actual_behavior: document.querySelector("#bugReportActual").value.trim(),
    page: state.currentPage || "",
    workflow_id: state.workflow?.id || null,
    context: {
      route: `${location.pathname}${location.search}`.slice(0, 300),
      viewport: `${window.innerWidth}x${window.innerHeight}`,
      language: navigator.language || "",
      user_agent: navigator.userAgent.slice(0, 300),
      online: navigator.onLine,
    },
    screenshot_base64: screenshot ? await fileToBase64(screenshot) : "",
    screenshot_name: screenshot?.name || "",
    screenshot_mime: screenshot?.type || "",
  };
  if (![payload.description, payload.steps_to_reproduce, payload.expected_behavior, payload.actual_behavior].some(Boolean) && !screenshot) {
    message.textContent = "請至少填寫一段說明，或上傳一張截圖。";
    message.classList.add("error");
    return;
  }
  button.disabled = true;
  message.textContent = "正在安全提交…";
  message.classList.remove("error");
  try {
    await api("/api/v1/bug-reports", { method: "POST", body: JSON.stringify(payload) });
    document.querySelector("#bugReportForm").reset();
    clearBugScreenshot();
    message.textContent = "已送出，謝謝你的回報。平台管理者會在後台查看。";
    state.bugReports = null;
    if (state.currentPage === "settings") loadBugReports(true);
    window.setTimeout(closeBugReport, 1200);
  } catch (error) {
    message.textContent = error.message || "目前無法提交，請稍後再試。";
    message.classList.add("error");
  } finally {
    button.disabled = false;
  }
}

async function loadBugReports(force = false) {
  const container = document.querySelector("#betaBugReports");
  const panel = document.querySelector("#betaUsagePanel");
  if (!force && state.bugReports) return renderBugReports();
  const filter = document.querySelector("#bugReportStatusFilter").value;
  try {
    state.bugReports = await api(`/api/v1/admin/bug-reports${filter ? `?status=${encodeURIComponent(filter)}` : ""}`);
    renderBugReports();
  } catch (error) {
    state.bugReports = null;
    if (error.status === 403) {
      panel.classList.add("hidden");
      return;
    }
    container.innerHTML = `<div class="prompt-empty">${escapeHtml(error.message || "無法讀取問題回報。")}</div>`;
  }
}

function renderBugReports() {
  const container = document.querySelector("#betaBugReports");
  const categoryLabels = { functional: "功能異常", interface: "畫面／操作", performance: "速度過慢", ai_output: "AI 結果", other: "其他" };
  const severityLabels = { low: "輕微", medium: "影響操作", blocking: "無法繼續" };
  const statusLabels = { new: "待處理", reviewing: "處理中", resolved: "已解決", dismissed: "不處理" };
  if (!state.bugReports?.length) {
    container.innerHTML = '<div class="prompt-empty">目前沒有符合條件的問題回報。</div>';
    return;
  }
  container.innerHTML = `<div class="beta-bug-list">${state.bugReports.map((item) => {
    const details = [["問題", item.description], ["重現步驟", item.steps_to_reproduce], ["原本預期", item.expected_behavior], ["實際結果", item.actual_behavior]].filter(([, value]) => value);
    return `<article class="beta-bug-card" data-bug-report-id="${escapeHtml(item.id)}"><header><div><h4>${escapeHtml(categoryLabels[item.category] || item.category)} · ${escapeHtml(item.workspace_name)}</h4><p>${escapeHtml(item.actor_name)}${item.actor_email ? `（${escapeHtml(item.actor_email)}）` : ""} · ${formatFullDateTime(item.created_at)}</p></div><div class="beta-bug-tags"><span class="${escapeHtml(item.severity)}">${escapeHtml(severityLabels[item.severity] || item.severity)}</span><span>${escapeHtml(statusLabels[item.status] || item.status)}</span><span>${escapeHtml(item.page || "未知頁面")}</span></div></header><div class="beta-bug-body"><div class="beta-bug-copy">${details.length ? details.map(([label,value]) => `<p><b>${label}：</b>${escapeHtml(value)}</p>`).join("") : "<p>使用者只提供了截圖。</p>"}<p><b>環境：</b>${escapeHtml(item.context?.viewport || "-")} · ${escapeHtml(item.context?.language || "-")} · 任務 ${escapeHtml(item.workflow_id || "無")}</p></div>${item.has_screenshot ? `<a href="/api/v1/admin/bug-reports/${encodeURIComponent(item.id)}/screenshot" target="_blank" rel="noopener"><img class="beta-bug-screenshot" src="/api/v1/admin/bug-reports/${encodeURIComponent(item.id)}/screenshot" alt="使用者提供的問題截圖" loading="lazy" /></a>` : ""}</div><div class="beta-bug-actions"><select data-bug-status><option value="new" ${item.status === "new" ? "selected" : ""}>待處理</option><option value="reviewing" ${item.status === "reviewing" ? "selected" : ""}>處理中</option><option value="resolved" ${item.status === "resolved" ? "selected" : ""}>已解決</option><option value="dismissed" ${item.status === "dismissed" ? "selected" : ""}>不處理</option></select><input data-bug-owner-note maxlength="2000" value="${escapeHtml(item.owner_note || "")}" placeholder="Super Owner 處理備註（選填）" /><button class="secondary-button" type="button" data-save-bug-report>儲存</button></div></article>`;
  }).join("")}</div>`;
  container.querySelectorAll("[data-save-bug-report]").forEach((button) => button.addEventListener("click", () => saveBugReport(button.closest("[data-bug-report-id]"))));
}

async function saveBugReport(card) {
  const button = card.querySelector("[data-save-bug-report]");
  button.disabled = true; button.textContent = "儲存中…";
  try {
    await api(`/api/v1/admin/bug-reports/${encodeURIComponent(card.dataset.bugReportId)}`, { method: "PATCH", body: JSON.stringify({ status: card.querySelector("[data-bug-status]").value, owner_note: card.querySelector("[data-bug-owner-note]").value.trim() }) });
    button.textContent = "已儲存";
    state.bugReports = null;
    window.setTimeout(() => loadBugReports(true), 500);
  } catch (error) {
    button.textContent = error.message || "儲存失敗";
  } finally { button.disabled = false; }
}

function betaEventLabel(name) {
  return ({
    "auth.bootstrap": "建立平台帳號",
    "auth.signup": "邀請碼註冊",
    "auth.invite_created": "建立邀請碼",
    "auth.login": "登入",
    "auth.logout": "登出",
    "workspace.created": "建立 Workspace",
    "workspace.switched": "切換 Workspace",
    "workspace.member_added": "新增成員",
    "page.viewed": "瀏覽頁面",
    "workflow.opened": "開啟任務",
    "artifact.copied": "複製腳本",
    "artifact.previewed": "預覽素材",
    "artifact.downloaded": "下載素材",
    "publication.copy": "複製發布文案",
    "publication.package_downloaded": "下載發布包",
    "generation.started": "開始 AI 生成",
    "generation.completed": "AI 生成完成",
    "generation.failed": "AI 生成失敗",
    "feedback.submitted": "提交回饋",
    "bug_report.submitted": "回報問題",
  })[name] || name;
}

function renderBetaUsage() {
  const data = state.betaUsage;
  const content = document.querySelector("#betaUsageContent");
  if (!data) return;
  const totals = data.totals || {};
  const metrics = [
    ["活躍測試者", totals.active_members || 0, `共 ${totals.members || 0} 位成員`],
    ["活躍 Workspace", totals.active_workspaces || 0, `共 ${totals.workspaces || 0} 個 Workspace`],
    ["內容任務", totals.workflows || 0, `${data.window_days} 天內建立`],
    ["AI 生成", totals.generation_started || 0, `完成 ${totals.generation_completed || 0} · 失敗 ${totals.generation_failed || 0}`],
    ["AI Token", Number(totals.token_units || 0).toLocaleString(), "完成事件回報的總量"],
    ["使用者回饋", totals.feedback || 0, `累計 ${totals.events || 0} 個使用事件`],
  ];
  const workspaceRows = (data.workspaces || []).map((item) => `
    <tr><td><strong>${escapeHtml(item.workspace_name)}</strong><small>${escapeHtml(item.workspace_id)}</small></td><td>${item.active_member_count} / ${item.member_count}</td><td>${item.workflow_count}</td><td>${item.generation_count}</td><td class="${item.failure_count ? "beta-risk" : ""}">${item.failure_count}</td><td>${item.feedback_count}</td><td>${item.last_active_at ? formatFullDateTime(item.last_active_at) : "尚無活動"}</td></tr>
  `).join("");
  const userRows = (data.users || []).map((item) => `
    <tr><td><strong>${escapeHtml(item.display_name)}</strong><small>${escapeHtml(item.email)}</small></td><td>${escapeHtml(item.workspace_name)}</td><td>${escapeHtml(roleLabel(item.role))}</td><td>${item.event_count}</td><td>${item.page_views}</td><td>${item.asset_actions}</td><td>${item.last_active_at ? formatFullDateTime(item.last_active_at) : "尚無活動"}</td></tr>
  `).join("");
  const recentRows = (data.recent_events || []).map((item) => `
    <li><div><strong>${escapeHtml(betaEventLabel(item.event_name))}</strong><span>${escapeHtml(item.actor_name)} · ${escapeHtml(item.workspace_name)}</span></div><time>${formatFullDateTime(item.occurred_at)}</time></li>
  `).join("");
  const eventChips = (data.events_by_name || []).map((item) => `<span>${escapeHtml(betaEventLabel(item.event_name))}<b>${item.count}</b></span>`).join("");
  content.innerHTML = `
    <div class="beta-usage-metrics">${metrics.map(([label, value, note]) => `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></article>`).join("")}</div>
    <div class="beta-event-chips">${eventChips || '<span>目前尚無事件</span>'}</div>
    <section class="beta-usage-section"><h3>Workspace 使用情況</h3><div class="beta-table-scroll"><table><thead><tr><th>Workspace</th><th>活躍／成員</th><th>任務</th><th>生成</th><th>失敗</th><th>回饋</th><th>最後活動</th></tr></thead><tbody>${workspaceRows || '<tr><td colspan="7">目前沒有 Workspace 資料。</td></tr>'}</tbody></table></div></section>
    <section class="beta-usage-section"><h3>測試者使用情況</h3><div class="beta-table-scroll"><table><thead><tr><th>測試者</th><th>Workspace</th><th>角色</th><th>事件</th><th>頁面</th><th>素材操作</th><th>最後活動</th></tr></thead><tbody>${userRows || '<tr><td colspan="7">目前沒有成員資料。</td></tr>'}</tbody></table></div></section>
    <section class="beta-usage-section"><h3>最近事件</h3><ul class="beta-recent-events">${recentRows || '<li><span>目前尚無使用事件。</span></li>'}</ul></section>
  `;
}

function trackEvent(eventName, metadata = {}, workflowId = null) {
  api("/api/v1/events", {
    method: "POST",
    body: JSON.stringify({ event_name: eventName, workflow_id: workflowId, metadata }),
  }).catch(() => {});
}

async function submitArtifactFeedback(rating) {
  if (!state.workflow?.artifacts?.script) return;
  const feedbackStatus = document.querySelector("#artifactFeedbackStatus");
  const buttons = [...document.querySelectorAll("[data-feedback-rating]")];
  buttons.forEach((button) => { button.disabled = true; });
  feedbackStatus.textContent = "正在儲存回饋…";
  try {
    await api(`/api/v1/workflows/${encodeURIComponent(state.workflow.id)}/feedback`, {
      method: "POST",
      body: JSON.stringify({
        rating,
        artifact_type: "script",
        note: document.querySelector("#artifactFeedbackNote").value.trim(),
      }),
    });
    buttons.forEach((button) => button.classList.toggle("selected", button.dataset.feedbackRating === rating));
    feedbackStatus.textContent = "謝謝，你的回饋已保存，會用於後續改善生成品質。";
    await loadUsage();
  } catch (error) {
    feedbackStatus.textContent = error.message || "回饋暫時無法儲存，請稍後再試。";
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function renderNotifications() {
  const waiting = state.history.filter((item) => item.status === "waiting_for_human");
  const failed = state.history.filter((item) => item.status === "failed");
  const parts = [];
  if (waiting.length) parts.push(`${waiting.length} 個任務等待人工核准`);
  if (failed.length) parts.push(`${failed.length} 個任務需要重試`);
  document.querySelector("#notificationSummary").textContent = parts.length ? `${parts.join("，")}。` : "目前沒有待處理通知。";
  document.querySelector(".notification-dot").classList.toggle("hidden", !parts.length);
}

function renderOverviewList(selector, workflows, emptyMessage) {
  const container = document.querySelector(selector);
  if (!workflows.length) {
    container.innerHTML = `<div class="overview-empty">${escapeHtml(emptyMessage)}</div>`;
    return;
  }
  container.innerHTML = workflows.map((workflow) => `
    <article class="overview-task">
      <div><strong>${escapeHtml(workflow.input.topic)}</strong><small>${escapeHtml(statusLabel(workflow.status))} · ${formatDateTime(workflow.updated_at)}</small></div>
      <button type="button" data-open-workflow="${escapeHtml(workflow.id)}">查看</button>
    </article>`).join("");
  container.querySelectorAll("[data-open-workflow]").forEach((button) => {
    button.addEventListener("click", () => openHistoryWorkflow(button.dataset.openWorkflow));
  });
}

async function generateOpportunities(options = {}) {
  if (state.opportunityGenerationBusy) return;
  const request = collectOpportunityRequest();
  if (options.moreAngles) {
    request.variation = Math.floor(Date.now() / 1000) % 100000;
    request.exclude_topics = collectOpportunityLineageTopics(state.opportunityResult).slice(0, 100);
  }
  const button = document.querySelector("#opportunityGenerateButton");
  const moreButton = document.querySelector("#opportunityMoreButton");
  if (!request.topic) {
    showOpportunityMessage("請先輸入要探索的主題或關鍵字。", true);
    document.querySelector("#opportunitySeed").focus();
    return;
  }
  if (!request.platforms.length) {
    showOpportunityMessage("請至少選擇一個發布平台。", true);
    return;
  }
  const requestId = ++opportunityRequestId;
  state.opportunityGenerationBusy = true;
  button.disabled = true;
  button.textContent = "分析主題中…";
  startOpportunityProgress();
  moreButton.disabled = true;
  if (options.moreAngles) moreButton.textContent = "產生中…";
  document.querySelector("#opportunityContext").classList.add("hidden");
  document.querySelector("#opportunityCompare").classList.add("hidden");
  document.querySelector("#opportunityList").innerHTML = Array.from({ length: Math.min(request.count, 4) }, () => '<article class="opportunity-card opportunity-loading"><span></span><span></span><span></span></article>').join("");
  showOpportunityMessage(`正在分析「${request.topic}」的主題屬性、受眾與既有內容…`);
  try {
    const result = await api("/api/v1/opportunities/generate", { method: "POST", body: JSON.stringify(request) });
    if (requestId !== opportunityRequestId) return;
    state.opportunityResult = result;
    state.opportunityCompareOpen = false;
    completeOpportunityProgress();
    renderOpportunityResults(result);
    updatePageUrl("opportunities", result.id, true);
    await loadOpportunityHistory();
    showOpportunityMessage(`已產生 ${(result.opportunities || []).length} 個不同內容角度。所有條件與結果都已保存。`);
  } catch (error) {
    if (requestId !== opportunityRequestId) return;
    document.querySelector("#opportunityList").innerHTML = `<div class="library-empty opportunity-error"><strong>內容機會產生失敗</strong><p>${escapeHtml(error.message || "目前無法連線本機服務。")}</p><div><button class="secondary-button" type="button" data-opportunity-reconnect>重新連線</button><button class="primary-button" type="button" data-opportunity-retry>重試產生</button></div></div>`;
    document.querySelector("[data-opportunity-reconnect]")?.addEventListener("click", () => checkHealth(true));
    document.querySelector("[data-opportunity-retry]")?.addEventListener("click", () => generateOpportunities());
    failOpportunityProgress(error.message || "分析暫時中斷，請重試。");
    showOpportunityMessage(error.message || "無法產生內容機會。", true);
  } finally {
    if (requestId === opportunityRequestId) {
      state.opportunityGenerationBusy = false;
      button.disabled = false;
      button.textContent = "產生內容機會";
      moreButton.disabled = false;
      moreButton.textContent = "再產生一批";
    }
  }
}

function opportunityProgressSnapshot(elapsedSeconds) {
  if (elapsedSeconds < 3) return { percent: 10 + Math.round(elapsedSeconds * 4), label: "正在整理探索條件", detail: "正在整理主題、受眾、平台與品牌條件。" };
  if (elapsedSeconds < 9) return { percent: 22 + Math.round((elapsedSeconds - 3) * 4), label: "正在查找相關資料", detail: "正在讀取參考資料並搜尋可追溯的內容來源。" };
  if (elapsedSeconds < 17) return { percent: 46 + Math.round((elapsedSeconds - 9) * 3), label: "正在建立內容角度", detail: "正在比較受眾價值、新穎度與製作可行性。" };
  if (elapsedSeconds < 28) return { percent: 70 + Math.round((elapsedSeconds - 17) * 1.2), label: "正在進行品質覆核", detail: "正在檢查題目差異、來源邊界與平台適配。" };
  return { percent: Math.min(94, 83 + Math.round((elapsedSeconds - 28) / 4)), label: "正在完成分析", detail: "正在整理結果與保存本次探索紀錄。" };
}

function renderOpportunityProgress(percent, label, detail, { error = false } = {}) {
  const safePercent = Math.max(0, Math.min(100, Math.round(percent)));
  const panel = document.querySelector("#opportunityProgress");
  panel.classList.remove("hidden");
  panel.classList.toggle("error", error);
  document.querySelector("#opportunityProgressLabel").textContent = label;
  document.querySelector("#opportunityProgressPercent").textContent = `${safePercent}%`;
  document.querySelector("#opportunityProgressBar").style.width = `${safePercent}%`;
  document.querySelector("#opportunityProgressDetail").textContent = detail;
  document.querySelector("#opportunityProviderBadge").textContent = error ? "--" : `${safePercent}%`;
}

function startOpportunityProgress() {
  if (opportunityProgressTimer) window.clearInterval(opportunityProgressTimer);
  opportunityProgressStartedAt = Date.now();
  renderOpportunityProgress(8, "正在準備分析", "正在整理主題、受眾與探索條件。");
  opportunityProgressTimer = window.setInterval(() => {
    const snapshot = opportunityProgressSnapshot((Date.now() - opportunityProgressStartedAt) / 1000);
    renderOpportunityProgress(snapshot.percent, snapshot.label, snapshot.detail);
  }, 500);
}

function completeOpportunityProgress() {
  if (opportunityProgressTimer) window.clearInterval(opportunityProgressTimer);
  opportunityProgressTimer = null;
  renderOpportunityProgress(100, "內容機會分析完成", "研究、內容角度與品質覆核均已完成。結果已保存。");
}

function failOpportunityProgress(message) {
  if (opportunityProgressTimer) window.clearInterval(opportunityProgressTimer);
  opportunityProgressTimer = null;
  renderOpportunityProgress(0, "內容機會分析未完成", message, { error: true });
}

function collectOpportunityLineageTopics(currentGeneration) {
  if (!currentGeneration) return [];
  const runs = [...state.opportunityHistory];
  if (!runs.some((run) => run.id === currentGeneration.id)) runs.push(currentGeneration);
  const byId = new Map(runs.map((run) => [run.id, run]));
  const topics = [];
  const seenTopics = new Set();
  const visited = new Set();
  const queue = [currentGeneration];
  const addTopic = (topic) => {
    const value = String(topic || "").trim();
    const key = value.toLocaleLowerCase("zh-Hant");
    if (!value || seenTopics.has(key)) return;
    seenTopics.add(key);
    topics.push(value);
  };

  while (queue.length) {
    const run = queue.shift();
    if (!run || visited.has(run.id)) continue;
    visited.add(run.id);
    (run.opportunities || []).forEach((item) => addTopic(item.topic));
    normalizeTextList(run.request?.exclude_topics).forEach(addTopic);

    if (run.parent_generation_id && byId.has(run.parent_generation_id)) queue.push(byId.get(run.parent_generation_id));
    const excluded = new Set(normalizeTextList(run.request?.exclude_topics).map((topic) => topic.toLocaleLowerCase("zh-Hant")));
    if (excluded.size) {
      runs.forEach((candidate) => {
        if (visited.has(candidate.id)) return;
        if ((candidate.opportunities || []).some((item) => excluded.has(String(item.topic || "").trim().toLocaleLowerCase("zh-Hant")))) queue.push(candidate);
      });
    }
  }
  return topics;
}

function renderOpportunityResults(result) {
  const opportunities = result.opportunities || [];
  const request = result.request || {};
  populateOpportunityForm(request, result.seed);
  const context = document.querySelector("#opportunityContext");
  document.querySelector("#opportunityProviderBadge").textContent = "100%";
  const generationConfidence = result.data_confidence || highestOpportunityConfidence(opportunities);
  const signals = result.signals || [];
  const sourceCaptures = result.source_captures || [];
  const hasLiveSignals = Boolean(result.has_live_signals || signals.some((signal) => signal.is_live));
  const qualityReview = result.quality_review || {};
  const reviewStatus = qualityReview.status === "reviewed" ? "reviewed" : "fallback";
  const reviewHtml = qualityReview.summary ? `<div class="opportunity-quality-review ${reviewStatus}"><div><strong>${qualityReview.status === "reviewed" ? "內容機會 Reviewer 已覆核" : "內容機會已完成備援檢查"}</strong><span>${escapeHtml(qualityReview.summary)}</span></div><em>信心 ${Math.round(Number(qualityReview.confidence || 0) * 100)}%</em>${normalizeTextList(qualityReview.issues).length ? `<ul>${normalizeTextList(qualityReview.issues).map((issue) => `<li>${escapeHtml(issue)}</li>`).join("")}</ul>` : ""}</div>` : "";
  const signalDetails = signals.length ? `<details class="opportunity-signals"><summary>查看 ${signals.length} 項資料來源與時間</summary><ul>${signals.map((signal) => `<li><div><strong>${escapeHtml(signal.name || "資料訊號")}</strong><em class="${signal.requires_human_review ? "manual" : signal.is_live ? "live" : "manual"}">${signal.requires_human_review ? "需人工確認" : signal.is_live ? "即時" : "已擷取"}</em></div><span>${escapeHtml(signal.acquisition_method || signal.source || "來源未標示")} · ${formatFullDateTime(signal.observed_at)}${signal.completeness == null ? "" : ` · 完整度 ${Math.round(Number(signal.completeness) * 100)}%`}</span><p>${escapeHtml(signal.summary || "沒有補充摘要")}</p>${signal.source_url ? `<small>${escapeHtml(signal.source_url)}</small>` : ""}</li>`).join("")}</ul></details>` : "";
  const captureDetails = sourceCaptures.length ? `<details class="opportunity-signals"><summary>查看 ${sourceCaptures.length} 筆來源擷取紀錄</summary><ul>${sourceCaptures.map((capture) => `<li><div><strong>${escapeHtml(capture.source_url || "外部資料")}</strong><em class="${capture.status === "success" && !capture.requires_human_review ? "live" : "manual"}">${capture.status === "failed" ? "擷取失敗" : capture.requires_human_review ? "需人工確認" : "擷取完成"}</em></div><span>${escapeHtml(capture.acquisition_method || "未標示方式")} · ${formatFullDateTime(capture.fetched_at)} · 完整度 ${Math.round(Number(capture.completeness || 0) * 100)}% · 信心 ${Math.round(Number(capture.confidence || 0) * 100)}%</span><p>${escapeHtml(capture.failure_reason || capture.excerpt || "沒有補充摘要")}</p></li>`).join("")}</ul></details>` : "";
  context.classList.remove("hidden");
  context.innerHTML = `
    <div class="opportunity-context-main"><div><span class="opportunity-mode">${escapeHtml(generationModeLabel(result.generation_mode))}</span><strong>${escapeHtml(result.topic_type_label || "一般主題")}</strong><span class="confidence-pill ${escapeHtml(generationConfidence)}">資料信心：${escapeHtml(confidenceLabel(generationConfidence))}</span></div><p>${escapeHtml(result.context_summary || "依輸入條件與研究資料產生內容角度。")}</p><small>研究、內容角度與品質覆核已完成</small></div>
    <div class="opportunity-disclaimer"><b>${hasLiveSignals ? "資料訊號" : "目前沒有即時市場訊號"}</b><span>${signals.length ? signals.map((signal) => escapeHtml(signal.name || signal.source || "資料來源")).join("、") : "使用輸入條件、既有內容與本機啟發式規則"}</span><small>${hasLiveSignals ? "部分來源為即時資料，請仍檢查來源時間與品質。" : "分數是內容適配度，不代表搜尋量、流量或市場需求。"}</small>${signalDetails}${captureDetails}</div>${reviewHtml}`;
  document.querySelector("#opportunityResultTitle").textContent = `${request.topic || result.seed || "主題"}的探索結果`;
  document.querySelector("#opportunityResultMeta").textContent = `${opportunities.length} 個角度 · ${formatFullDateTime(result.generated_at || result.created_at)} · ${generationStatusLabel(result.status)}`;
  document.querySelector("#opportunityMoreButton").classList.remove("hidden");
  document.querySelector("#opportunityList").innerHTML = opportunities.length ? opportunities.map((item) => {
    const brief = item.brief || {};
    const status = item.status || "new";
    const evidenceNeeded = normalizeTextList(brief.evidence_needed);
    const productionNotes = normalizeTextList(brief.production_notes);
    const formats = item.recommended_formats || brief.recommended_formats || [];
    const platforms = normalizePlatforms(item.recommended_platforms || brief.recommended_platforms || []);
    const formatLabels = [...new Set(formats.map(formatLabel))];
    const platformLabels = [...new Set(platforms.map(platformLabel))];
    return `
    <article class="opportunity-card status-${escapeHtml(status)}" data-opportunity-card="${escapeHtml(item.id)}">
      <div class="opportunity-card-top"><span class="opportunity-type">${escapeHtml(item.type || "內容角度")}</span><span class="opportunity-item-status ${escapeHtml(status)}">${escapeHtml(opportunityItemStatusLabel(status))}</span></div>
      <h3>${escapeHtml(item.topic)}</h3>
      <p>${escapeHtml(item.description)}</p>
      <div class="opportunity-recommendation"><span>${formatLabels.map(escapeHtml).join(" · ") || "形式待規劃"}</span><span>${platformLabels.map(escapeHtml).join(" · ") || "平台待規劃"}</span></div>
      <div class="opportunity-trust-row"><span>啟發式適配：${escapeHtml(fitLevelLabel(item.fit_level))}</span><span class="confidence-pill ${escapeHtml(item.data_confidence || "low")}">資料信心：${escapeHtml(confidenceLabel(item.data_confidence))}</span></div>
      <details class="opportunity-score-details">
        <summary>規則適配分 ${item.score} <span>查看評分與 Brief</span></summary>
        <p>${escapeHtml(item.rationale)}</p>
        ${item.score_breakdown ? `<div class="score-breakdown">${opportunityScoreRow("主題相關性", item.score_breakdown.relevance)}${opportunityScoreRow("內容新穎度", item.score_breakdown.novelty)}${opportunityScoreRow("受眾價值", item.score_breakdown.audience_value)}${opportunityScoreRow("製作可行性", item.score_breakdown.feasibility)}</div>` : ""}
        <div class="opportunity-brief">
          <div><span>目標受眾</span><strong>${escapeHtml(brief.target_audience || request.audience || "待確認")}</strong></div>
          <div><span>內容角度</span><strong>${escapeHtml(brief.angle || item.rationale || "待確認")}</strong></div>
          <div class="wide"><span>開場 Hook</span><strong>${escapeHtml(brief.hook || "製作時產生")}</strong></div>
          <div class="wide"><span>關鍵重點</span><ul>${normalizeTextList(brief.key_points).map((point) => `<li>${escapeHtml(point)}</li>`).join("") || "<li>製作時確認</li>"}</ul></div>
          <div><span>CTA</span><strong>${escapeHtml(brief.cta || "依內容目標調整")}</strong></div>
          <div><span>製作投入</span><strong>${escapeHtml(brief.estimated_effort || "待評估")}</strong></div>
          <div class="wide"><span>必要證據</span><strong>${escapeHtml(evidenceNeeded.join("、") || "目前沒有外部證據；發布前仍應查核重要主張")}</strong></div>
          ${productionNotes.length ? `<div class="wide"><span>製作備註</span><strong>${escapeHtml(productionNotes.join("、"))}</strong></div>` : ""}
        </div>
        <small class="opportunity-version">評分方法：${escapeHtml(scoringMethodLabel(item.scoring_method))} · ${escapeHtml(result.score_version || "版本未提供")}</small>
      </details>
      <div class="opportunity-evidence"><span>${hasLiveSignals ? `${signals.length} 項資料訊號` : "僅本機／人工輸入訊號"}</span><span>${evidenceNeeded.length ? `需補 ${evidenceNeeded.length} 項證據` : "未列特定證據"}</span></div>
      <div class="opportunity-regenerate-panel hidden" data-opportunity-regenerate-panel="${escapeHtml(item.id)}">
        <label><span>希望 AI 怎麼修改？ <em>選填</em></span><textarea maxlength="2000" data-opportunity-regenerate-instruction placeholder="例如：改成新手購買決策角度、減少規格描述，並加入三個實測情境。"></textarea></label>
        <small>留空會直接產生不同題目；填寫後，AI 會把這段要求列為本次單題重做的修改指示。</small>
        <div class="opportunity-item-progress hidden" data-opportunity-item-progress="${escapeHtml(item.id)}" role="status" aria-live="polite">
          <div><strong data-opportunity-item-progress-label>正在準備單題重做</strong><span data-opportunity-item-progress-percent>0%</span></div>
          <div class="opportunity-item-progress-track" aria-hidden="true"><span data-opportunity-item-progress-bar></span></div>
          <small data-opportunity-item-progress-detail>正在整理原題目與人工修改指示。</small>
        </div>
        <div><button class="secondary-button small" type="button" data-opportunity-regenerate-cancel="${escapeHtml(item.id)}">取消</button><button class="primary-button small" type="button" data-opportunity-regenerate-confirm="${escapeHtml(item.id)}">開始重做</button></div>
      </div>
      <footer class="opportunity-actions"><span class="opportunity-score">規則適配分 ${item.score}</span><div><button class="secondary-button small ${status === "saved" ? "active" : ""}" type="button" data-opportunity-status="${status === "saved" ? "new" : "saved"}" data-item-id="${escapeHtml(item.id)}">${status === "saved" ? "★ 取消收藏" : "☆ 收藏"}</button><button class="secondary-button small" type="button" data-opportunity-status="${status === "dismissed" ? "new" : "dismissed"}" data-item-id="${escapeHtml(item.id)}">${status === "dismissed" ? "恢復題目" : "略過"}</button><button class="secondary-button small" type="button" data-opportunity-regenerate="${escapeHtml(item.id)}">單題重做</button><button class="primary-button small" type="button" data-use-opportunity="${escapeHtml(item.id)}">使用這個題目</button></div></footer>
    </article>`;
  }).join("") : '<div class="library-empty"><strong>這筆探索沒有可用題目</strong><p>你可以調整條件後再產生一批。</p></div>';
  document.querySelectorAll("[data-use-opportunity]").forEach((button) => {
    button.addEventListener("click", () => {
      const item = opportunities.find((candidate) => candidate.id === button.dataset.useOpportunity);
      if (item) startNewTask({ item, generation: result });
    });
  });
  document.querySelectorAll("[data-opportunity-status]").forEach((button) => {
    button.addEventListener("click", () => updateOpportunityStatus(button.dataset.itemId, button.dataset.opportunityStatus));
  });
  document.querySelectorAll("[data-opportunity-regenerate]").forEach((button) => {
    button.addEventListener("click", () => toggleOpportunityRegeneratePanel(button.dataset.opportunityRegenerate, button));
  });
  document.querySelectorAll("[data-opportunity-regenerate-cancel]").forEach((button) => {
    button.addEventListener("click", () => toggleOpportunityRegeneratePanel(button.dataset.opportunityRegenerateCancel));
  });
  document.querySelectorAll("[data-opportunity-regenerate-confirm]").forEach((button) => {
    button.addEventListener("click", () => {
      const panel = button.closest("[data-opportunity-regenerate-panel]");
      const instruction = panel?.querySelector("[data-opportunity-regenerate-instruction]")?.value.trim() || "";
      regenerateOpportunity(button.dataset.opportunityRegenerateConfirm, button, instruction);
    });
  });
  renderOpportunityComparison();
}

function collectOpportunityRequest() {
  const referenceText = document.querySelector("#opportunityReferenceText").value.trim();
  const brandBrief = document.querySelector("#opportunityBrandBrief").value.trim();
  const referenceMaterials = [];
  if (brandBrief) referenceMaterials.push({ name: "內容機會品牌 Brief", kind: "brand_brief", content: brandBrief, focus: "品牌定位與不可違反事項" });
  if (referenceText) referenceMaterials.push({ name: "內容機會參考文字", kind: "creator_reference", content: referenceText, focus: "高層次內容角度與結構" });
  return {
    topic: document.querySelector("#opportunitySeed").value.trim(),
    audience: document.querySelector("#opportunityAudience").value.trim(),
    goal: document.querySelector("#opportunityGoal").value,
    platforms: [...document.querySelectorAll(".opportunity-platform-chip input:checked")].map((input) => input.value),
    preferred_formats: [document.querySelector("#opportunityFormat").value],
    region: document.querySelector("#opportunityRegion").value.trim() || "台灣",
    language: languageLabel(document.querySelector("#opportunityLanguage").value),
    brand_name: document.querySelector("#opportunityBrandName").value.trim(),
    brand_voice: document.querySelector("#opportunityBrandVoice").value.trim() || "專業、清楚，但保有自然的對話感",
    brand_brief: brandBrief,
    constraints: normalizeTextList(document.querySelector("#opportunityConstraints").value),
    reference_materials: referenceMaterials,
    reference_urls: normalizeTextList(document.querySelector("#opportunityReferenceUrls").value),
    count: Number(document.querySelector("#opportunityCount").value) || 4,
    workspace_id: state.workspaceId,
  };
}

function populateOpportunityForm(request = {}, fallbackSeed = "") {
  document.querySelector("#opportunitySeed").value = request.topic || fallbackSeed || "";
  document.querySelector("#opportunityAudience").value = request.audience || request.target_audience || "";
  document.querySelector("#opportunityGoal").value = normalizeGoal(request.goal);
  document.querySelector("#opportunityFormat").value = normalizeOutputType(request.preferred_formats?.[0] || request.preferred_format);
  document.querySelector("#opportunityCount").value = String(request.count || 4);
  document.querySelector("#opportunityRegion").value = request.region || "台灣";
  document.querySelector("#opportunityLanguage").value = languageCode(request.language);
  document.querySelector("#opportunityBrandName").value = request.brand_name || "";
  document.querySelector("#opportunityBrandVoice").value = request.brand_voice || "專業、清楚，但保有自然的對話感";
  document.querySelector("#opportunityBrandBrief").value = request.brand_brief || request.reference_materials?.find((item) => item.kind === "brand_brief")?.content || "";
  document.querySelector("#opportunityConstraints").value = normalizeTextList(request.constraints).join("\n");
  document.querySelector("#opportunityReferenceText").value = request.reference_text || request.reference_materials?.find((item) => item.kind !== "brand_brief" && item.content)?.content || "";
  document.querySelector("#opportunityReferenceUrls").value = normalizeTextList(request.reference_urls).join("\n");
  const platforms = normalizePlatforms(request.platforms || ["youtube", "instagram"]);
  document.querySelectorAll(".opportunity-platform-chip").forEach((chip) => {
    const selected = platforms.includes(chip.querySelector("input").value);
    chip.querySelector("input").checked = selected;
    chip.classList.toggle("selected", selected);
  });
}

async function updateOpportunityStatus(itemId, status, feedbackNote = "", options = {}) {
  const generation = state.opportunityResult;
  const item = generation?.opportunities?.find((candidate) => candidate.id === itemId);
  if (!generation || !item) return;
  const card = [...document.querySelectorAll("[data-opportunity-card]")].find((candidate) => candidate.dataset.opportunityCard === itemId);
  const cardButtons = [...(card?.querySelectorAll("button") || [])];
  cardButtons.forEach((button) => { button.disabled = true; });
  try {
    const updatedGeneration = await api(`/api/v1/opportunities/${encodeURIComponent(generation.id)}/items/${encodeURIComponent(itemId)}`, {
      method: "PATCH",
      body: JSON.stringify({ status, feedback_note: feedbackNote || ({ saved: "使用者加入收藏短名單", dismissed: "使用者略過此題目", adopted: "使用者採用此題目", new: "使用者恢復題目狀態" })[status] || "使用者更新題目狀態" }),
    });
    state.opportunityResult = updatedGeneration;
    const updatedItem = updatedGeneration.opportunities?.find((candidate) => candidate.id === itemId);
    if (options.navigate) {
      if (!updatedItem) throw new Error("伺服器回傳內容缺少已採用的題目。");
      startNewTask({ item: updatedItem, generation: updatedGeneration });
      return;
    }
    if (status === "saved" && updatedGeneration.opportunities.filter((candidate) => candidate.status === "saved").length >= 2) {
      state.opportunityCompareOpen = true;
    }
    renderOpportunityResults(updatedGeneration);
    await loadOpportunityHistory();
    showOpportunityMessage(({ saved: "已加入收藏；收藏兩題後會出現固定比較區。", dismissed: "已記錄略過狀態。", new: "已恢復為新題目。" })[status] || "題目狀態已更新。", false);
  } catch (error) {
    cardButtons.forEach((button) => { button.disabled = false; });
    showOpportunityMessage(error.message || "無法更新題目狀態。", true);
  }
}

function toggleOpportunityRegeneratePanel(itemId, opener = null) {
  const target = document.querySelector(`[data-opportunity-regenerate-panel="${CSS.escape(itemId)}"]`);
  if (!target) return;
  const willOpen = target.classList.contains("hidden");
  document.querySelectorAll("[data-opportunity-regenerate-panel]").forEach((panel) => panel.classList.add("hidden"));
  document.querySelectorAll("[data-opportunity-regenerate]").forEach((button) => {
    button.textContent = "單題重做";
    button.setAttribute("aria-expanded", "false");
  });
  if (!willOpen) return;
  target.classList.remove("hidden");
  const openButton = opener || document.querySelector(`[data-opportunity-regenerate="${CSS.escape(itemId)}"]`);
  if (openButton) {
    openButton.textContent = "收合修改";
    openButton.setAttribute("aria-expanded", "true");
  }
  target.querySelector("textarea")?.focus();
}

async function regenerateOpportunity(itemId, button, modificationInstruction = "") {
  const generation = state.opportunityResult;
  if (!generation) return;
  const panel = button.closest("[data-opportunity-regenerate-panel]");
  const panelButtons = [...(panel?.querySelectorAll("button") || [])];
  const instructionField = panel?.querySelector("textarea");
  panelButtons.forEach((item) => { item.disabled = true; });
  if (instructionField) instructionField.disabled = true;
  button.disabled = true;
  startOpportunityItemProgress(itemId, button);
  try {
    const regenerated = await api(`/api/v1/opportunities/${encodeURIComponent(generation.id)}/items/${encodeURIComponent(itemId)}/regenerate`, {
      method: "POST",
      body: JSON.stringify({ modification_instruction: modificationInstruction || null }),
    });
    completeOpportunityItemProgress(itemId, button);
    await delay(350);
    state.opportunityResult = regenerated;
    state.opportunityCompareOpen = false;
    renderOpportunityResults(regenerated);
    updatePageUrl("opportunities", regenerated.id, true);
    await loadOpportunityHistory();
    showOpportunityMessage(modificationInstruction ? "已依人工修改指示建立新版本；原本紀錄仍完整保留。" : "已建立新版本；原本紀錄仍保留，可從探索紀錄切換查看。", false);
  } catch (error) {
    failOpportunityItemProgress(itemId, button, error.message || "單題重做失敗，請保留指示後重試。");
    showOpportunityMessage(error.message || "單題重做失敗。", true);
    panelButtons.forEach((item) => { item.disabled = false; });
    if (instructionField) instructionField.disabled = false;
  }
}

function opportunityItemProgressSnapshot(elapsedSeconds) {
  if (elapsedSeconds < 4) return { percent: 12 + Math.round(elapsedSeconds * 4), label: "正在理解修改指示", detail: "正在比對原題目、受眾與人工修改方向。" };
  if (elapsedSeconds < 11) return { percent: 28 + Math.round((elapsedSeconds - 4) * 5), label: "正在重新研究與改寫", detail: "正在建立符合修改要求的新內容角度。" };
  if (elapsedSeconds < 20) return { percent: 63 + Math.round((elapsedSeconds - 11) * 2.2), label: "正在檢查新題目", detail: "正在檢查差異性、資料邊界與製作可行性。" };
  return { percent: Math.min(94, 83 + Math.round((elapsedSeconds - 20) / 4)), label: "正在建立新版本", detail: "正在完成品質覆核並保存版本紀錄。" };
}

function renderOpportunityItemProgress(itemId, button, snapshot, { error = false } = {}) {
  const panel = document.querySelector(`[data-opportunity-item-progress="${CSS.escape(itemId)}"]`);
  if (!panel) return;
  const percent = Math.max(0, Math.min(100, Math.round(snapshot.percent)));
  panel.classList.remove("hidden");
  panel.classList.toggle("error", error);
  panel.querySelector("[data-opportunity-item-progress-label]").textContent = snapshot.label;
  panel.querySelector("[data-opportunity-item-progress-percent]").textContent = error ? "--" : `${percent}%`;
  panel.querySelector("[data-opportunity-item-progress-bar]").style.width = `${percent}%`;
  panel.querySelector("[data-opportunity-item-progress-detail]").textContent = snapshot.detail;
  button.textContent = error ? "重新嘗試" : percent === 100 ? "完成 100%" : `重做中 ${percent}%`;
}

function startOpportunityItemProgress(itemId, button) {
  const existing = opportunityItemProgressTimers.get(itemId);
  if (existing) window.clearInterval(existing.timer);
  const startedAt = Date.now();
  renderOpportunityItemProgress(itemId, button, { percent: 8, label: "正在準備單題重做", detail: "正在整理原題目與人工修改指示。" });
  const timer = window.setInterval(() => {
    renderOpportunityItemProgress(itemId, button, opportunityItemProgressSnapshot((Date.now() - startedAt) / 1000));
  }, 500);
  opportunityItemProgressTimers.set(itemId, { timer, startedAt });
}

function completeOpportunityItemProgress(itemId, button) {
  const active = opportunityItemProgressTimers.get(itemId);
  if (active) window.clearInterval(active.timer);
  opportunityItemProgressTimers.delete(itemId);
  renderOpportunityItemProgress(itemId, button, { percent: 100, label: "單題重做完成", detail: "新版本已建立並保存，正在更新畫面。" });
}

function failOpportunityItemProgress(itemId, button, message) {
  const active = opportunityItemProgressTimers.get(itemId);
  if (active) window.clearInterval(active.timer);
  opportunityItemProgressTimers.delete(itemId);
  renderOpportunityItemProgress(itemId, button, { percent: 0, label: "單題重做未完成", detail: message }, { error: true });
}

async function loadOpportunityHistory(showErrors = false) {
  try {
    const data = await api(`/api/v1/opportunities?limit=50&workspace_id=${encodeURIComponent(state.workspaceId)}`);
    state.opportunityHistory = Array.isArray(data) ? data : data.items || data.generations || [];
    state.opportunityHistoryLoaded = true;
    renderOpportunityHistory();
    return state.opportunityHistory;
  } catch (error) {
    state.opportunityHistoryLoaded = true;
    document.querySelector("#opportunityHistoryList").innerHTML = `<div class="history-empty">${escapeHtml(error.message || "無法讀取探索紀錄")}</div>`;
    if (showErrors) showOpportunityMessage(error.message || "無法讀取探索紀錄。", true);
    return [];
  }
}

function renderOpportunityHistory() {
  const container = document.querySelector("#opportunityHistoryList");
  const query = document.querySelector("#opportunityHistoryFilter").value.trim().toLocaleLowerCase("zh-Hant");
  const runs = state.opportunityHistory.filter((run) => {
    const searchable = [run.seed, run.request?.topic, run.provider, run.model, generationStatusLabel(run.status)].join(" ").toLocaleLowerCase("zh-Hant");
    return !query || searchable.includes(query);
  });
  if (!runs.length) {
    container.innerHTML = `<div class="history-empty">${state.opportunityHistory.length ? "沒有符合篩選條件的紀錄。" : "尚無探索紀錄，產生第一批後會顯示在這裡。"}</div>`;
    return;
  }
  container.innerHTML = runs.map((run) => `
    <button class="opportunity-history-item ${run.id === state.opportunityResult?.id ? "active" : ""}" type="button" data-opportunity-run="${escapeHtml(run.id)}">
      <strong>${escapeHtml(run.request?.topic || run.seed || "未命名探索")}</strong>
      <span>${escapeHtml(run.topic_type_label || "一般主題")} · ${escapeHtml(generationStatusLabel(run.status))}</span>
      <small>AI 研究完成 · ${formatDateTime(run.updated_at || run.generated_at || run.created_at)}</small>
    </button>`).join("");
  container.querySelectorAll("[data-opportunity-run]").forEach((button) => {
    button.addEventListener("click", () => loadOpportunityGeneration(button.dataset.opportunityRun));
  });
}

async function loadOpportunityGeneration(generationId, options = {}) {
  if (!generationId) return;
  try {
    const result = await api(`/api/v1/opportunities/${encodeURIComponent(generationId)}`);
    state.opportunityResult = result;
    state.opportunityCompareOpen = (result.opportunities || []).filter((item) => item.status === "saved").length >= 2;
    renderOpportunityResults(result);
    renderOpportunityHistory();
    if (options.updateUrl !== false) updatePageUrl("opportunities", result.id, true);
    showOpportunityMessage(`已載入「${result.request?.topic || result.seed}」的完整探索紀錄。`, false);
  } catch (error) {
    showOpportunityMessage(error.message || "找不到指定的探索紀錄。", true);
  }
}

async function initializeOpportunityPage(generationId = null) {
  if (!state.opportunityHistoryLoaded) await loadOpportunityHistory();
  const targetId = generationId || state.opportunityResult?.id || state.opportunityHistory[0]?.id;
  if (!targetId) return;
  if (state.opportunityResult?.id === targetId) {
    renderOpportunityResults(state.opportunityResult);
    return;
  }
  await loadOpportunityGeneration(targetId, { updateUrl: Boolean(!generationId) });
}

function toggleOpportunityHistory(open) {
  state.opportunityHistoryOpen = open;
  document.querySelector("#opportunityHistoryPanel").classList.toggle("collapsed", !open);
  document.querySelector(".opportunity-layout").classList.toggle("history-collapsed", !open);
  document.querySelector("#opportunityHistoryToggle").classList.toggle("hidden", open);
}

function renderOpportunityComparison() {
  const container = document.querySelector("#opportunityCompare");
  const saved = (state.opportunityResult?.opportunities || []).filter((item) => item.status === "saved").slice(0, 3);
  const compareButton = document.querySelector("#opportunityCompareButton");
  compareButton.classList.toggle("hidden", saved.length < 2);
  compareButton.textContent = state.opportunityCompareOpen ? "收合比較" : `比較收藏（${saved.length}）`;
  if (saved.length < 2 || !state.opportunityCompareOpen) {
    container.classList.add("hidden");
    return;
  }
  container.classList.remove("hidden");
  container.innerHTML = `<div class="opportunity-compare-header"><div><span class="section-kicker">SHORTLIST</span><h2>收藏短名單比較</h2><p>最多並排比較三題；分數僅是啟發式內容適配，不代表市場需求。</p></div></div><div class="opportunity-compare-grid">${saved.map((item) => {
    const brief = item.brief || {};
    const displayFormats = [...new Set((item.recommended_formats || brief.recommended_formats || []).map(formatLabel))];
    return `<article><span>${escapeHtml(item.type || "內容角度")}</span><h3>${escapeHtml(item.topic)}</h3><dl><div><dt>目標受眾</dt><dd>${escapeHtml(brief.target_audience || "待確認")}</dd></div><div><dt>Hook</dt><dd>${escapeHtml(brief.hook || "待確認")}</dd></div><div><dt>建議形式</dt><dd>${escapeHtml(displayFormats.join("、") || "待確認")}</dd></div><div><dt>投入程度</dt><dd>${escapeHtml(brief.estimated_effort || "待評估")}</dd></div><div><dt>證據需求</dt><dd>${escapeHtml(normalizeTextList(brief.evidence_needed).join("、") || "尚未指定")}</dd></div></dl><button class="primary-button small" type="button" data-compare-use="${escapeHtml(item.id)}">使用這題</button></article>`;
  }).join("")}</div>`;
  container.querySelectorAll("[data-compare-use]").forEach((button) => {
    button.addEventListener("click", () => {
      const item = saved.find((candidate) => candidate.id === button.dataset.compareUse);
      if (item) startNewTask({ item, generation: state.opportunityResult });
    });
  });
}

function opportunityScoreRow(label, value) {
  return `<div><span>${label}</span><i><b style="width:${Math.max(0, Math.min(100, value))}%"></b></i><strong>${value}</strong></div>`;
}

function showOpportunityMessage(message, isError = false) {
  const bar = document.querySelector("#opportunityMessage");
  bar.textContent = message;
  bar.classList.toggle("error", Boolean(isError));
  bar.classList.add("show");
}

function renderPlan() {
  const columns = [
    { key: "requirements", label: "1 需求設定", match: (item) => item.stage === "requirements" },
    { key: "ai_creation", label: "2 AI 研究與創作", match: (item) => item.stage === "ai_creation" },
    { key: "production_package", label: "3 製作素材包", match: (item) => item.stage === "production_package" },
    { key: "approval_publish", label: "4 核准與發布", match: (item) => item.stage === "approval_publish" },
  ];
  document.querySelector("#planSummary").innerHTML = columns.map((column) => {
    const count = state.history.filter(column.match).length;
    return `<span>${column.label}：${count}</span>`;
  }).join("");
  document.querySelector("#pipelineBoard").innerHTML = columns.map((column) => {
    const items = state.history.filter(column.match);
    const cards = items.length ? items.map((workflow) => {
      const stageOptions = columns.map((stage) => `
        <option value="${stage.key}" ${stage.key === workflow.stage ? "selected" : ""}>${stage.label}</option>`).join("");
      return `
        <article class="pipeline-card">
          <strong>${escapeHtml(workflow.input.topic)}</strong>
          <p>${escapeHtml(statusLabel(workflow.status))} · ${escapeHtml(outputTypeLabel(workflow.input.output_type))}</p>
          <footer><time>${formatDateTime(workflow.updated_at)}</time><button type="button" data-open-workflow="${escapeHtml(workflow.id)}">開啟</button></footer>
          <details class="pipeline-manage">
            <summary>管理任務</summary>
            <div class="pipeline-manage-controls">
              <label>
                <span>所在階段</span>
                <select data-stage-select aria-label="調整「${escapeHtml(workflow.input.topic)}」的階段">${stageOptions}</select>
              </label>
              <button class="pipeline-update" type="button" data-update-stage="${escapeHtml(workflow.id)}" data-current-stage="${escapeHtml(workflow.stage)}">更新階段</button>
              <button class="pipeline-delete" type="button" data-delete-workflow="${escapeHtml(workflow.id)}" data-workflow-topic="${escapeHtml(workflow.input.topic)}">刪除任務</button>
            </div>
          </details>
        </article>`;
    }).join("") : '<div class="pipeline-empty">目前沒有任務</div>';
    return `<section class="pipeline-column" data-pipeline-stage="${column.key}"><div class="pipeline-column-header"><h2>${column.label}</h2><span>${items.length}</span></div><div class="pipeline-cards">${cards}</div></section>`;
  }).join("");
  document.querySelectorAll("#pipelineBoard [data-open-workflow]").forEach((button) => {
    button.addEventListener("click", () => openHistoryWorkflow(button.dataset.openWorkflow));
  });
  document.querySelectorAll("#pipelineBoard [data-update-stage]").forEach((button) => {
    button.addEventListener("click", () => updateWorkflowStage(button));
  });
  document.querySelectorAll("#pipelineBoard [data-delete-workflow]").forEach((button) => {
    button.addEventListener("click", () => deleteWorkflow(button));
  });
}

async function updateWorkflowStage(button) {
  if (state.busy) return;
  const workflowId = button.dataset.updateStage;
  const currentStage = button.dataset.currentStage;
  const targetStage = button.closest(".pipeline-manage-controls").querySelector("[data-stage-select]").value;
  if (targetStage === currentStage) {
    showPlanMessage("這個任務已經位於所選階段。");
    return;
  }

  const movingBackward = stageOrder.indexOf(targetStage) < stageOrder.indexOf(currentStage);
  if (movingBackward && !window.confirm("退回階段會清除該階段之後的舊腳本、素材包與審核結果，執行紀錄仍會保留。是否繼續？")) return;

  state.busy = true;
  button.disabled = true;
  button.textContent = movingBackward ? "正在退回…" : "正在推進…";
  try {
    const workflow = await api(`/api/v1/workflows/${workflowId}/stage`, {
      method: "PATCH",
      body: JSON.stringify({
        target_stage: targetStage,
        note: movingBackward ? "由內容計畫手動退回階段" : "由內容計畫手動推進階段",
      }),
    });
    if (state.workflow?.id === workflowId) state.workflow = workflow;
    await loadHistory();
    const reachedTarget = workflow.stage === targetStage;
    showPlanMessage(
      reachedTarget
        ? `「${workflow.input.topic}」已移至「${stageLabel(targetStage)}」。`
        : `流程在「${stageLabel(workflow.stage)}」暫停，需要先處理人工判斷。`,
      !reachedTarget,
    );
  } catch (error) {
    showPlanMessage(error.message || "無法更新任務階段，請稍後重試。", true);
    button.disabled = false;
    button.textContent = "更新階段";
  } finally {
    state.busy = false;
  }
}

async function deleteWorkflow(button) {
  if (state.busy) return;
  const workflowId = button.dataset.deleteWorkflow;
  const topic = button.dataset.workflowTopic;
  if (!window.confirm(`將永久刪除「${topic}」及其需求、AI 產出與執行紀錄，且無法復原。是否刪除？`)) return;

  state.busy = true;
  button.disabled = true;
  button.textContent = "刪除中…";
  try {
    await api(`/api/v1/workflows/${workflowId}`, { method: "DELETE" });
    if (state.workflow?.id === workflowId) resetInterface();
    await loadHistory();
    showPlanMessage(`已永久刪除「${topic}」。`);
  } catch (error) {
    showPlanMessage(error.message || "無法刪除任務，請稍後重試。", true);
    button.disabled = false;
    button.textContent = "刪除任務";
  } finally {
    state.busy = false;
  }
}

function showPlanMessage(message, isError = false) {
  const planMessage = document.querySelector("#planMessage");
  planMessage.textContent = message;
  planMessage.classList.toggle("error", Boolean(isError));
  planMessage.classList.add("show");
}

function renderAssets() {
  renderAssetPacingGuide();
  const assets = buildAssetEntries();
  const projects = buildAssetProjects(assets);
  const counts = {
    all: assets.length,
    script: assets.filter((item) => item.type === "script").length,
    production: assets.filter((item) => item.type === "production").length,
    reference: assets.filter((item) => item.type === "reference").length,
  };
  document.querySelector("#assetMetrics").innerHTML = [
    ["所有素材", counts.all, "由 PostgreSQL 工作流同步"],
    ["完整逐字稿", counts.script, "可直接複製使用"],
    ["製作素材包", counts.production, "分鏡、視覺與發布文案"],
    ["參考分析", counts.reference, "品牌與創作依據"],
  ].map(([label, value, note]) => `<article class="overview-metric"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`).join("");

  const query = document.querySelector("#assetSearch").value.trim().toLowerCase();
  const type = document.querySelector("#assetTypeFilter").value;
  const status = document.querySelector("#assetStatusFilter").value;
  const filtered = projects.filter((project) => (
    (type === "all" || project.assets.some((asset) => asset.type === type))
    && (status === "all" || project.workflow.status === status)
    && (!query || project.searchText.includes(query))
  ));

  const grid = document.querySelector("#assetGrid");
  if (!filtered.length) {
    grid.innerHTML = '<div class="library-empty"><strong>找不到符合條件的素材</strong><p>調整搜尋或篩選條件，或先建立一個新的內容任務。</p></div>';
    return;
  }
  grid.innerHTML = filtered.map((project) => renderAssetProject(project)).join("");
  grid.querySelectorAll("[data-open-workflow]").forEach((button) => {
    button.addEventListener("click", () => openHistoryWorkflow(button.dataset.openWorkflow));
  });
  grid.querySelectorAll("[data-copy-script]").forEach((button) => {
    button.addEventListener("click", async () => {
      const workflow = state.history.find((item) => item.id === button.dataset.copyScript);
      const fullText = workflow?.artifacts?.script?.full_text;
      if (!fullText) return;
      try {
        await navigator.clipboard.writeText(fullText);
        button.textContent = "已複製";
        trackEvent("artifact.copied", { artifact_type: "script", source: "assets" }, workflow.id);
        showAssetMessage(`已複製「${workflow.input.topic}」的完整逐字稿。`);
      } catch {
        showAssetMessage("瀏覽器無法存取剪貼簿，請開啟內容後手動複製。", true);
      }
    });
  });
  grid.querySelectorAll("[data-preview-asset]").forEach((button) => {
    button.addEventListener("click", () => openAssetPreview(assets.find((asset) => asset.key === button.dataset.previewAsset)));
  });
  grid.querySelectorAll("[data-download-asset]").forEach((button) => {
    button.addEventListener("click", () => downloadAsset(assets.find((asset) => asset.key === button.dataset.downloadAsset), "txt"));
  });
  grid.querySelectorAll("[data-copy-media-prompt]").forEach((button) => {
    button.addEventListener("click", () => copyMediaPrompt(button));
  });
  grid.querySelectorAll("[data-generate-shot-image]").forEach((button) => {
    button.addEventListener("click", () => generateShotImage(button));
  });
  bindAssetTimelineWorkspace(grid);
}

function renderAssetPacingGuide() {
  const summary = document.querySelector("#assetPacingSummary");
  const keys = state.assetPacingProfile === "auto"
    ? ["short", "review", "long"]
    : [state.assetPacingProfile];
  summary.innerHTML = keys.map((key) => {
    const preset = assetPacingPresets[key];
    return `<article class="${state.assetPacingProfile === key ? "active" : ""}"><header><strong>${escapeHtml(preset.label)}</strong><span>${escapeHtml(preset.duration)}</span></header><p><b>畫面刷新</b>${escapeHtml(preset.refresh)}</p><p><b>鏡頭停留</b>${escapeHtml(preset.shot)}</p><small>${escapeHtml(preset.note)}</small></article>`;
  }).join("");
}

function resolveAssetPacingProfile(workflow) {
  if (state.assetPacingProfile !== "auto") return state.assetPacingProfile;
  if (workflow.input.output_type === "short_video") return "short";
  if (workflow.input.output_type === "long_video") return "long";
  return "review";
}

function inferAssetVisualType(shot) {
  if (shot.visual_type) return shot.visual_type;
  const text = `${shot.section || ""} ${shot.visual || ""}`.toLowerCase();
  if (/比較|前後|before|after|測試|實測/.test(text)) return "實測／前後比較";
  if (/螢幕|介面|操作|screen|dashboard|ui/.test(text)) return "螢幕錄影／操作示範";
  if (/圖表|數據|規格|chart|data/.test(text)) return "圖表／標註畫面";
  if (/產品|特寫|開箱|product|detail/.test(text)) return "產品實拍／特寫";
  return "情境 B-roll";
}

function inferAssetVisualPurpose(shot) {
  if (shot.visual_purpose) return shot.visual_purpose;
  const text = `${shot.section || ""} ${shot.visual || ""}`;
  if (/實測|比較|成果|證明|數據|案例/.test(text)) return "證明口播主張";
  if (/步驟|操作|流程|教學|介面/.test(text)) return "解釋操作／流程";
  return "補充情境並維持節奏";
}

function assetShotTiming(workflow, shots, shot, shotIndex) {
  const profile = resolveAssetPacingProfile(workflow);
  const preset = assetPacingPresets[profile];
  const startSeconds = shots.slice(0, shotIndex).reduce((total, item) => total + Number(item.duration_seconds || 0), 0);
  const fallbackDuration = profile === "short" && startSeconds < 6
    ? 2
    : Math.round((preset.shotSeconds[0] + preset.shotSeconds[1]) / 2);
  const durationSeconds = Number(shot.broll_duration_seconds || fallbackDuration);
  return {
    profile,
    preset,
    startSeconds,
    endSeconds: startSeconds + durationSeconds,
    durationSeconds,
    visualType: inferAssetVisualType(shot),
    purpose: inferAssetVisualPurpose(shot),
    transition: shot.transition || (shotIndex === 0 ? "直接切入／先看成果" : "直接切換（Hard cut）"),
    rationale: shot.timing_rationale || (startSeconds < 6
      ? "前 6 秒優先快速兌現標題承諾"
      : `依${preset.label}節奏，在語意轉折處提供有意義的畫面變化`),
  };
}

function formatAssetTimeline(seconds) {
  const safe = Math.max(0, Math.round(Number(seconds) || 0));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
}

function buildAssetProjects(assets) {
  const projects = [];
  state.history.forEach((workflow) => {
    const workflowAssets = assets.filter((asset) => asset.workflow.id === workflow.id);
    if (!workflowAssets.length) return;
    projects.push({
      workflow,
      assets: workflowAssets,
      script: workflow.artifacts?.script || null,
      production: workflow.artifacts?.production_package || null,
      references: workflow.artifacts?.reference_analysis || workflow.artifacts?.script?.reference_analysis || null,
      searchText: workflowAssets.map((asset) => asset.searchText).join(" "),
    });
  });
  return projects;
}

function renderAssetProject(project) {
  const { workflow, script, production, references, assets } = project;
  const scriptAsset = assets.find((asset) => asset.type === "script");
  const productionAsset = assets.find((asset) => asset.type === "production");
  const referenceAsset = assets.find((asset) => asset.type === "reference");
  const sectionCount = script?.sections?.length || 0;
  const shotCount = production?.storyboard?.length || 0;
  const referenceCount = references?.source_count || references?.sources?.length || 0;
  const platformLabels = visibleDistributionKit(production?.distribution_kit || []).map((item) => item.label || platformLabel(item.platform)).filter(Boolean);
  const relationReady = Boolean(script && production);
  return `<article class="asset-project-card">
    <header class="asset-project-header">
      <div>
        <div class="asset-project-status"><span class="asset-status">${escapeHtml(statusLabel(workflow.status))}</span><time>${formatDateTime(workflow.updated_at)}</time></div>
        <h2>${escapeHtml(workflow.input.topic)}</h2>
        <p>${escapeHtml(script?.summary || "內容任務已建立，製作素材仍在準備中。")}</p>
      </div>
      <button class="primary-button asset-open" type="button" data-open-workflow="${escapeHtml(workflow.id)}">開啟任務</button>
    </header>
    <div class="asset-workflow-link" aria-label="素材關聯流程">
      <span class="${script ? "ready" : "pending"}"><i>1</i><strong>完整逐字稿</strong><small>${script ? `${script.word_count || 0} 字 · ${sectionCount} 個段落` : "尚未產生"}</small></span>
      <b>→</b>
      <span class="${production ? "ready" : "pending"}"><i>2</i><strong>製作規劃</strong><small>${production ? `${shotCount} 個分鏡 · B-roll 建議` : "等待逐字稿完成"}</small></span>
      <b>→</b>
      <span class="${platformLabels.length ? "ready" : "pending"}"><i>3</i><strong>發布素材</strong><small>${platformLabels.length ? platformLabels.join("、") : "尚未產生"}</small></span>
    </div>
    <div class="asset-project-meta"><span>${escapeHtml(outputTypeLabel(workflow.input.output_type))}</span><span>${referenceCount} 份參考資料</span><span class="${relationReady ? "linked" : ""}">${relationReady ? "逐字稿與製作包已關聯" : "等待建立完整關聯"}</span></div>
    <details class="asset-timeline-workspace" data-asset-timeline-workflow="${escapeHtml(workflow.id)}" ${assetCueEditWorkflowId === workflow.id ? "open" : ""}>
      <summary><span><strong>剪輯時間軸與 B-roll 審核</strong><small>點擊時間段查看畫面，並可直接人工新增、刪除或調整</small></span><em>${production ? `${productionVisualCues(production).length} 個素材段` : "尚未產生"}</em></summary>
      <div class="asset-timeline-body">${renderAssetTimelineWorkspace(workflow)}</div>
    </details>
    <details class="asset-alignment" ${relationReady ? "" : "open"}>
      <summary><span><strong>查看逐段製作對照</strong><small>旁白 → 分鏡畫面 → B-roll 搜尋建議</small></span><em>${sectionCount} 個段落</em></summary>
      <div class="asset-alignment-body">${renderAssetAlignment(workflow)}</div>
    </details>
    <footer class="asset-project-actions">
      <div>
        ${scriptAsset ? `<button class="secondary-button" type="button" data-preview-asset="${escapeHtml(scriptAsset.key)}">預覽逐字稿</button><button class="secondary-button" type="button" data-copy-script="${escapeHtml(workflow.id)}">複製逐字稿</button>` : ""}
        ${productionAsset ? `<button class="secondary-button" type="button" data-preview-asset="${escapeHtml(productionAsset.key)}">預覽製作包</button><button class="secondary-button" type="button" data-download-asset="${escapeHtml(productionAsset.key)}">下載製作包</button>` : ""}
        ${referenceAsset ? `<button class="text-button" type="button" data-preview-asset="${escapeHtml(referenceAsset.key)}">查看參考依據</button>` : ""}
      </div>
    </footer>
  </article>`;
}

function renderAssetTimelineWorkspace(workflow) {
  const production = workflow.artifacts?.production_package;
  const shots = production?.storyboard || [];
  const totalSeconds = productionDuration(production);
  if (!production || !shots.length || !totalSeconds) {
    return '<div class="asset-alignment-empty"><strong>剪輯時間軸尚未產生</strong><p>完成製作規劃後，這裡會把逐字稿段落與 B-roll 時間點放在同一條時間軸。</p></div>';
  }
  const editing = assetCueEditWorkflowId === workflow.id;
  const cues = editing ? assetCueDrafts : productionVisualCues(production);
  const summary = cueCoverageSummary(cues, totalSeconds);
  return `${productionSyncNotice(production)}
    <div class="asset-timeline-intro">
      <div><strong>以時間點檢查剪輯畫面</strong><span>點擊彩色素材段，會定位到下方的用途、畫面說明與搜尋詞。</span></div>
      ${editing ? '<span class="rhythm-edit-state">人工審核中</span>' : `<button class="secondary-button" type="button" data-asset-edit-timeline="${escapeHtml(workflow.id)}">✎ 審核與調整時間軸</button>`}
    </div>
    <div class="rhythm-metrics asset-timeline-metrics">
      <article><span>影片總長</span><strong>${timelineSecondsLabel(totalSeconds)}</strong></article>
      <article><span>視覺素材覆蓋</span><strong>${timelineSecondsLabel(summary.visualSeconds)} · ${summary.visualPercent}%</strong></article>
      <article><span>B-roll 占比</span><strong>${timelineSecondsLabel(summary.brollSeconds)} · ${summary.brollPercent}%</strong></article>
      <article><span>待審素材段</span><strong>${cues.length} 段</strong></article>
    </div>
    ${renderAssetEditingTimeline(workflow, shots, cues, totalSeconds)}
    ${editing ? renderAssetVisualCueEditor(workflow, cues, shots, totalSeconds) : ""}`;
}

function renderAssetEditingTimeline(workflow, shots, cues, totalSeconds) {
  let elapsed = 0;
  const timelineMinWidth = Math.min(2400, Math.max(900, Math.round(totalSeconds * 12), shots.length * 82));
  const segments = shots.map((shot, index) => {
    const duration = Number(shot.duration_seconds || 0);
    const start = elapsed;
    elapsed += duration;
    return `<div class="rhythm-segment" style="width:${duration / totalSeconds * 100}%" title="${escapeHtml(shot.section || `段落 ${index + 1}`)}"><strong>${index + 1}. ${escapeHtml(shot.section || `段落 ${index + 1}`)}</strong><span>${timelineSecondsLabel(start)}–${timelineSecondsLabel(elapsed)}</span></div>`;
  }).join("");
  const cueBars = cues.map((cue, index) => {
    const left = Math.max(0, Number(cue.start_seconds)) / totalSeconds * 100;
    const width = Math.min(Number(cue.duration_seconds), totalSeconds) / totalSeconds * 100;
    const lane = assetCueLane(cue.cue_type);
    const focused = assetFocusedCue?.workflowId === workflow.id && assetFocusedCue?.index === index;
    const adjustedLeft = `calc(50px + ${left}% - ${left / 2}px)`;
    const safeWidth = Math.max(width, 1.4);
    const adjustedWidth = `calc(${safeWidth}% - ${safeWidth / 2}px)`;
    return `<button type="button" class="rhythm-cue cue-${escapeHtml(cue.cue_type)} ${focused ? "focused" : ""}" style="left:${adjustedLeft};width:${adjustedWidth};top:${10 + lane * 35}px" data-asset-cue-index="${index}" data-workflow-id="${escapeHtml(workflow.id)}" aria-label="查看 ${escapeHtml(cue.label)}，${cue.duration_seconds} 秒"><span>${escapeHtml(cue.label)}</span></button>`;
  }).join("");
  const cards = cues.map((cue, index) => {
    const focused = assetFocusedCue?.workflowId === workflow.id && assetFocusedCue?.index === index;
    return `<article class="asset-cue-review-card ${focused ? "focused" : ""}" data-asset-cue-card="${index}">
      <header><div><span>${escapeHtml(visualCueTypeLabels[cue.cue_type] || cue.cue_type)} · ${timelineSecondsLabel(cue.start_seconds)}–${timelineSecondsLabel(Number(cue.start_seconds) + Number(cue.duration_seconds))}</span><strong>${escapeHtml(cue.label)}</strong></div><em>${cue.source === "ai" ? "AI 建議" : "人工調整"}</em></header>
      <p>${escapeHtml(cue.visual_description || "尚未填寫畫面說明")}</p>
      <dl><div><dt>使用目的</dt><dd>${escapeHtml(cue.purpose || "待補充")}</dd></div><div><dt>搜尋詞</dt><dd>${escapeHtml(cue.search_query || "待補充")}</dd></div></dl>
      <button class="text-button" type="button" data-asset-focus-cue="${index}" data-workflow-id="${escapeHtml(workflow.id)}">${assetCueEditWorkflowId === workflow.id ? "編輯此素材段" : "在預覽中查看"}</button>
    </article>`;
  }).join("");
  return `<section class="rhythm-timeline-panel asset-review-timeline">
    <div class="rhythm-section-heading"><div><strong>全片段落與素材配置</strong><span>上排是逐字稿／分鏡段落，下排是會在該時間出現的 B-roll 與視覺。</span></div></div>
    ${renderAssetTimelinePreview(workflow, cues, totalSeconds)}
    <div class="asset-timeline-scroll" aria-label="可水平捲動的完整剪輯時間軸">
      <div class="asset-timeline-canvas" style="min-width:${timelineMinWidth}px">
        <div class="rhythm-segments">${segments}</div>
        <div class="rhythm-cue-lanes asset-multitrack-lanes"><div class="asset-track-label track-visual">B-roll</div><div class="asset-track-label track-subtitle">字幕</div><div class="asset-track-label track-card">圖卡</div>${cueBars}<div class="asset-playhead" data-asset-playhead-line style="left:calc(50px + ${(Number(assetTimelinePlayheads[workflow.id] || 0) / totalSeconds) * 100}% - ${(Number(assetTimelinePlayheads[workflow.id] || 0) / totalSeconds) * 50}px)"></div></div>
        <div class="rhythm-axis"><span>0:00</span><span>${timelineSecondsLabel(Math.round(totalSeconds / 2))}</span><span>${timelineSecondsLabel(totalSeconds)}</span></div>
      </div>
    </div>
    <div class="rhythm-legend"><span class="broll">B-roll</span><span class="product_shot">產品實拍</span><span class="screen_recording">螢幕錄影</span><span class="image">圖片</span><span class="subtitle">字幕</span><span class="title_card">圖卡</span></div>
    <div class="asset-cue-review-list">${cards || '<p class="asset-cue-empty">目前沒有素材段，可進入人工審核後新增。</p>'}</div>
  </section>`;
}

function renderAssetVisualCueEditor(workflow, cues, shots, totalSeconds) {
  const shotOptions = shots.map((shot, index) => `<option value="${shot.shot || index + 1}">${shot.shot || index + 1}. ${escapeHtml(shot.section || `段落 ${index + 1}`)}</option>`).join("");
  const typeOptions = Object.entries(visualCueTypeLabels).map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
  const rows = cues.map((cue, index) => `<article class="visual-cue-editor-row" data-asset-cue-editor="${index}">
    <header><strong>素材段 ${index + 1}</strong><span>${cue.source === "ai" ? "AI 建議，可人工修改" : "人工調整"}</span><button type="button" data-asset-remove-cue="${index}" aria-label="移除素材段 ${index + 1}">移除</button></header>
    <div class="visual-cue-fields">
      <label><span>素材類型</span><select data-asset-cue-field="cue_type" data-index="${index}">${typeOptions}</select></label>
      <label><span>對應段落</span><select data-asset-cue-field="shot" data-index="${index}">${shotOptions}</select></label>
      <label><span>開始秒數</span><input type="number" min="0" max="${totalSeconds - 1}" data-asset-cue-field="start_seconds" data-index="${index}" value="${cue.start_seconds}" /></label>
      <label><span>時長（秒）</span><input type="number" min="1" max="${totalSeconds}" data-asset-cue-field="duration_seconds" data-index="${index}" value="${cue.duration_seconds}" /></label>
      <label class="wide"><span>素材名稱</span><input maxlength="200" data-asset-cue-field="label" data-index="${index}" value="${escapeHtml(cue.label)}" /></label>
      <label class="wide"><span>畫面說明</span><textarea maxlength="2000" data-asset-cue-field="visual_description" data-index="${index}">${escapeHtml(cue.visual_description || "")}</textarea></label>
      <label><span>使用目的</span><input maxlength="500" data-asset-cue-field="purpose" data-index="${index}" value="${escapeHtml(cue.purpose || "")}" /></label>
      <label><span>B-roll／素材搜尋詞</span><input maxlength="1000" data-asset-cue-field="search_query" data-index="${index}" value="${escapeHtml(cue.search_query || "")}" /></label>
    </div>
  </article>`).join("");
  return `<section class="visual-cue-editor asset-cue-editor"><div class="rhythm-section-heading"><div><strong>人工審核與調整</strong><span>保存後會同步 AI 內容工作台，並重新執行視覺統籌與 YouTube 參考驗證。</span></div><div class="asset-add-track-actions"><button class="secondary-button" type="button" data-asset-add-cue="${escapeHtml(workflow.id)}" data-cue-type="broll">＋ B-roll</button><button class="secondary-button" type="button" data-asset-add-cue="${escapeHtml(workflow.id)}" data-cue-type="subtitle">＋ 字幕</button><button class="secondary-button" type="button" data-asset-add-cue="${escapeHtml(workflow.id)}" data-cue-type="title_card">＋ 圖卡</button></div></div><div class="visual-cue-editor-list">${rows}</div><footer><button class="secondary-button" type="button" data-asset-cancel-edit="${escapeHtml(workflow.id)}">取消</button><button class="primary-button" type="button" data-asset-save-cues="${escapeHtml(workflow.id)}">儲存並重新驗證</button></footer><small class="asset-save-note">重新驗證可能需要約 1–2 分鐘；人工內容會先保存，完成後需重新進行最終核准。</small></section>`;
}

function bindAssetTimelineWorkspace(grid) {
  grid.querySelectorAll("[data-asset-edit-timeline]").forEach((button) => button.addEventListener("click", () => {
    const workflow = state.history.find((item) => item.id === button.dataset.assetEditTimeline);
    if (!workflow?.artifacts?.production_package) return;
    assetCueEditWorkflowId = workflow.id;
    assetCueDrafts = productionVisualCues(workflow.artifacts.production_package);
    assetFocusedCue = null;
    renderAssets();
    document.querySelector(`[data-asset-timeline-workflow="${workflow.id}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  grid.querySelectorAll("[data-asset-cue-index], [data-asset-focus-cue]").forEach((button) => button.addEventListener("click", () => {
    const workflowId = button.dataset.workflowId;
    const index = Number(button.dataset.assetCueIndex ?? button.dataset.assetFocusCue);
    assetFocusedCue = { workflowId, index };
    const details = grid.querySelector(`[data-asset-timeline-workflow="${workflowId}"]`);
    details.open = true;
    details.querySelectorAll("[data-asset-cue-card]").forEach((card) => card.classList.toggle("focused", Number(card.dataset.assetCueCard) === index));
    details.querySelectorAll("[data-asset-cue-index]").forEach((cueBar) => cueBar.classList.toggle("focused", Number(cueBar.dataset.assetCueIndex) === index));
    const cue = (assetCueEditWorkflowId === workflowId ? assetCueDrafts : productionVisualCues(state.history.find((item) => item.id === workflowId)?.artifacts?.production_package))[index];
    if (cue) updateAssetTimelinePreview(details, workflowId, Number(cue.start_seconds || 0));
    const target = assetCueEditWorkflowId === workflowId
      ? details.querySelector(`[data-asset-cue-editor="${index}"]`)
      : details.querySelector(".asset-remotion-preview");
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
  }));
  grid.querySelectorAll("[data-asset-cue-field]").forEach((field) => {
    const index = Number(field.dataset.index);
    const key = field.dataset.assetCueField;
    field.value = String(assetCueDrafts[index]?.[key] ?? "");
    field.addEventListener("input", () => {
      assetCueDrafts[index][key] = ["shot", "start_seconds", "duration_seconds"].includes(key) ? Number(field.value) : field.value;
    });
  });
  grid.querySelectorAll("[data-asset-add-cue]").forEach((button) => button.addEventListener("click", () => {
    const workflow = state.history.find((item) => item.id === button.dataset.assetAddCue);
    const shot = Number(workflow?.artifacts?.production_package?.storyboard?.[0]?.shot || 1);
    const cueType = button.dataset.cueType || "broll";
    const defaults = cueType === "subtitle"
      ? { label: "新增字幕", visual_description: "輸入字幕內容", purpose: "補充口播字幕" }
      : cueType === "title_card"
        ? { label: "新增圖卡", visual_description: "輸入圖卡重點", purpose: "強調關鍵訊息" }
        : { label: "新增 B-roll", visual_description: "", purpose: "補充畫面" };
    assetCueDrafts.push({ id: `manual-${Date.now()}`, shot, cue_type: cueType, label: defaults.label, start_seconds: Math.round(Number(assetTimelinePlayheads[workflow.id] || 0)), duration_seconds: 3, visual_description: defaults.visual_description, purpose: defaults.purpose, search_query: "", source: "manual" });
    assetFocusedCue = { workflowId: workflow.id, index: assetCueDrafts.length - 1 };
    renderAssets();
    document.querySelector(`[data-asset-cue-editor="${assetCueDrafts.length - 1}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }));
  grid.querySelectorAll("[data-asset-preview-scrubber]").forEach((input) => input.addEventListener("input", () => {
    const workflowId = input.dataset.assetPreviewScrubber;
    const details = grid.querySelector(`[data-asset-timeline-workflow="${workflowId}"]`);
    updateAssetTimelinePreview(details, workflowId, Number(input.value));
  }));
  grid.querySelectorAll("[data-asset-remove-cue]").forEach((button) => button.addEventListener("click", () => {
    assetCueDrafts.splice(Number(button.dataset.assetRemoveCue), 1);
    assetFocusedCue = null;
    renderAssets();
  }));
  grid.querySelectorAll("[data-asset-cancel-edit]").forEach((button) => button.addEventListener("click", () => {
    assetCueEditWorkflowId = null;
    assetCueDrafts = [];
    assetFocusedCue = null;
    renderAssets();
  }));
  grid.querySelectorAll("[data-asset-save-cues]").forEach((button) => button.addEventListener("click", () => saveAssetVisualCues(button)));
}

async function saveAssetVisualCues(button) {
  const workflowId = button.dataset.assetSaveCues;
  const workflow = state.history.find((item) => item.id === workflowId);
  const totalSeconds = productionDuration(workflow?.artifacts?.production_package);
  const invalid = assetCueDrafts.find((cue) => !String(cue.label || "").trim() || Number(cue.duration_seconds) < 1 || Number(cue.start_seconds) < 0 || Number(cue.start_seconds) + Number(cue.duration_seconds) > totalSeconds);
  if (invalid) return showAssetMessage("請確認每個素材段都有名稱，且開始時間與時長沒有超出影片範圍。", true);
  button.disabled = true;
  button.textContent = "保存與驗證中…";
  showAssetMessage("正在保存人工調整，並重新執行視覺統籌與影片參考驗證…");
  try {
    const updated = await api(`/api/v1/workflows/${encodeURIComponent(workflowId)}/production-plan`, {
      method: "PATCH",
      body: JSON.stringify({ visual_cues: assetCueDrafts }),
    });
    state.history = state.history.map((item) => item.id === workflowId ? updated : item);
    if (state.workflow?.id === workflowId) state.workflow = updated;
    assetCueEditWorkflowId = null;
    assetCueDrafts = [];
    assetFocusedCue = null;
    renderAssets();
    showAssetMessage("B-roll 時間軸已保存並同步工作台；發布前請重新完成最終人工核准。");
  } catch (error) {
    showAssetMessage(error.message || "無法保存素材時間軸，請稍後重試。", true);
    button.disabled = false;
    button.textContent = "儲存並重新驗證";
  }
}

function renderAssetAlignment(workflow) {
  const script = workflow.artifacts?.script;
  const production = workflow.artifacts?.production_package;
  const sections = script?.sections || [];
  const shots = production?.storyboard || [];
  if (!sections.length) return '<div class="asset-alignment-empty"><strong>逐字稿尚未產生</strong><p>完成 AI 內容製作後，這裡會顯示逐段對照。</p></div>';
  if (!shots.length) return '<div class="asset-alignment-empty"><strong>製作規劃尚未產生</strong><p>逐字稿已完成；分鏡與 B-roll 建議產生後會自動連結到各段。</p></div>';
  const youtubeReview = production.youtube_reference_review;
  const youtubeReviewHtml = youtubeReview ? `<div class="asset-youtube-review ${youtubeReview.status === "verified" ? "verified" : "unavailable"}"><strong>${youtubeReview.status === "verified" ? "Gemini 時間碼已複核" : "Gemini 尚無可採用時間碼"}</strong><span>${escapeHtml(youtubeReview.summary || "影片參考驗證代理已完成檢查。")}</span>${youtubeReview.temporal_precision_seconds ? `<small>時間定位精度約 ±${escapeHtml(youtubeReview.temporal_precision_seconds)} 秒；仍建議剪輯前人工快速確認。</small>` : ""}</div>` : "";
  return `${youtubeReviewHtml}<div class="asset-alignment-list">${sections.map((section, index) => {
    const match = matchStoryboardShots(section, shots, index, sections.length);
    return `<article class="asset-alignment-row">
      <div class="asset-script-segment"><span class="asset-segment-number">${index + 1}</span><div><strong>${escapeHtml(section.label || `段落 ${index + 1}`)}</strong><p>${escapeHtml(compactExcerpt(section.voiceover, 240))}</p></div></div>
      <div class="asset-relation-arrow"><span>→</span><small>${escapeHtml(match.label)}</small></div>
      <div class="asset-shot-stack">${match.shots.length ? match.shots.map((shot) => {
        const broll = sectionBrollSuggestions(workflow, section, shot);
        const shotIndex = shots.indexOf(shot);
        const timing = assetShotTiming(workflow, shots, shot, shotIndex < 0 ? index : shotIndex);
        return `<div class="asset-shot-segment matched"><header><strong>分鏡 ${shot.shot || index + 1}${shot.duration_seconds ? ` · 口播約 ${shot.duration_seconds} 秒` : ""}</strong><span>已連結</span></header><p><b>畫面：</b>${escapeHtml(shot.visual || section.visual_direction || "待補充")}</p><div class="asset-shot-timing"><span><b>建議時間軸</b>${formatAssetTimeline(timing.startSeconds)}–${formatAssetTimeline(timing.endSeconds)}（B-roll ${timing.durationSeconds} 秒）</span><span><b>畫面類型</b>${escapeHtml(timing.visualType)}</span><span><b>使用目的</b>${escapeHtml(timing.purpose)}</span><span><b>轉場</b>${escapeHtml(timing.transition)}</span>${shot.on_screen_text ? `<span class="asset-shot-text"><b>畫面文字</b>${escapeHtml(shot.on_screen_text)}</span>` : ""}<small>節奏理由：${escapeHtml(timing.rationale)}</small></div><div class="asset-broll"><b>B-roll 搜尋：</b>${broll.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>${renderShotMediaTools(workflow, shot, shotIndex < 0 ? index : shotIndex, broll)}</div>`;
      }).join("") : `<div class="asset-shot-segment unmatched"><strong>尚未找到對應分鏡</strong><p>請回到原始任務重新產生製作規劃。</p></div>`}</div>
    </article>`;
  }).join("")}</div>`;
}

function findShotGenerationPrompt(workflow, shot, shotIndex) {
  const production = workflow.artifacts?.production_package || {};
  const prompts = production.visual_plan?.generation_prompts || [];
  const shotNumber = Number(shot.shot || shotIndex + 1);
  const exact = prompts.find((item) => Number(item.shot) === shotNumber);
  if (exact) return exact;
  const legacyPrompt = production.visual_plan?.image_prompts?.[shotIndex];
  if (!legacyPrompt) return null;
  return {
    shot: shotNumber,
    label: shot.section || `分鏡 ${shotNumber}`,
    image_prompt: legacyPrompt,
    video_prompt: `${legacyPrompt}。加入自然連續動作、穩定鏡頭運動、清楚首尾畫面，片長 4 秒，避免閃爍與物體變形。`,
    negative_prompt: "浮水印、亂碼、錯字、物體變形、閃爍、未授權商標",
    aspect_ratio: workflow.input.output_type === "short_video" ? "9:16" : "16:9",
  };
}

function renderShotMediaTools(workflow, shot, shotIndex, broll) {
  const production = workflow.artifacts?.production_package || {};
  const shotNumber = Number(shot.shot || shotIndex + 1);
  const prompt = findShotGenerationPrompt(workflow, shot, shotIndex);
  const references = (production.visual_plan?.youtube_references || []).filter((item) => Number(item.shot) === shotNumber);
  const generated = (production.generated_assets || []).filter((item) => item.kind === "image" && Number(item.shot) === shotNumber);
  const searchQuery = broll[0] || `${workflow.input.topic} ${shot.section || "B-roll"}`;
  const youtubeSearch = `https://www.youtube.com/results?search_query=${encodeURIComponent(searchQuery)}`;
  const referenceHtml = references.length
    ? references.map((item) => {
      const verified = item.verification_status === "verified";
      const timestamp = item.start_timestamp && item.end_timestamp ? `約 ${item.start_timestamp}–${item.end_timestamp}` : "查看影片";
      return `<a class="asset-youtube-reference ${verified ? "verified" : ""}" href="${escapeHtml(item.deep_link || item.url)}" target="_blank" rel="noopener noreferrer"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.channel || "YouTube")}</span><em>${escapeHtml(timestamp)}</em>${verified ? '<b class="asset-reference-verified">Gemini 已複核</b>' : ""}<small>${escapeHtml(item.description || item.usage_note)}</small>${item.filming_takeaway ? `<small><b>可參考：</b>${escapeHtml(item.filming_takeaway)}</small>` : ""}</a>`;
    }).join("")
    : `<a class="asset-youtube-reference search" href="${youtubeSearch}" target="_blank" rel="noopener noreferrer"><strong>在 YouTube 搜尋相符畫面</strong><span>${escapeHtml(searchQuery)}</span><small>目前尚無 AI 驗證過的單一影片連結。</small></a>`;
  const promptHtml = prompt ? `<details class="asset-generation-pack"><summary><span><strong>AI 圖片／影片生成包</strong><small>${escapeHtml(prompt.aspect_ratio || "16:9")} · 已依此分鏡整理</small></span></summary><div class="asset-prompt-body"><label>圖片提示詞<textarea readonly>${escapeHtml(prompt.image_prompt)}</textarea></label><label>影片提示詞<textarea readonly>${escapeHtml(prompt.video_prompt)}</textarea></label>${prompt.negative_prompt ? `<p><b>避免項目：</b>${escapeHtml(prompt.negative_prompt)}</p>` : ""}<div class="asset-prompt-actions"><button class="secondary-button" type="button" data-copy-media-prompt="image" data-workflow-id="${escapeHtml(workflow.id)}" data-shot="${shotNumber}">複製圖片提示</button><button class="secondary-button" type="button" data-copy-media-prompt="video" data-workflow-id="${escapeHtml(workflow.id)}" data-shot="${shotNumber}">複製影片提示</button><button class="primary-button" type="button" data-generate-shot-image="true" data-workflow-id="${escapeHtml(workflow.id)}" data-shot="${shotNumber}">一鍵生成圖片</button></div><small class="asset-video-note">影片提示可直接貼到支援的 AI 影片工具；OpenAI 目前沒有可用的影片生成 API。</small></div></details>` : `<div class="asset-generation-empty">重新執行視覺統籌後，這裡會產生逐鏡圖片與影片提示詞。</div>`;
  const generatedHtml = generated.length ? `<div class="asset-generated-grid">${generated.map((item) => `<figure><img src="${escapeHtml(item.url)}" alt="${escapeHtml(item.label)}" loading="lazy"><figcaption><strong>${escapeHtml(item.label)}</strong><a href="${escapeHtml(item.url)}" download>下載 PNG</a></figcaption></figure>`).join("")}</div>` : "";
  return `<section class="asset-media-tools"><div class="asset-reference-block"><header><strong>YouTube 拍攝參考</strong><small>只供構圖與節奏研究，使用前請另行確認授權。</small></header>${referenceHtml}</div>${promptHtml}${generatedHtml}</section>`;
}

function mediaPromptFromButton(button) {
  const workflow = state.history.find((item) => item.id === button.dataset.workflowId);
  const production = workflow?.artifacts?.production_package;
  const shots = production?.storyboard || [];
  const shotIndex = shots.findIndex((item, index) => Number(item.shot || index + 1) === Number(button.dataset.shot));
  if (!workflow || shotIndex < 0) return null;
  return { workflow, prompt: findShotGenerationPrompt(workflow, shots[shotIndex], shotIndex) };
}

async function copyMediaPrompt(button) {
  const context = mediaPromptFromButton(button);
  if (!context?.prompt) return showAssetMessage("找不到這個分鏡的提示詞，請重新執行視覺統籌。", true);
  const kind = button.dataset.copyMediaPrompt;
  const value = kind === "video" ? context.prompt.video_prompt : context.prompt.image_prompt;
  try {
    await navigator.clipboard.writeText(value);
    button.textContent = "已複製";
    showAssetMessage(`已複製${kind === "video" ? "影片" : "圖片"}提示詞。`);
  } catch {
    showAssetMessage("瀏覽器無法存取剪貼簿，請直接從提示詞欄位複製。", true);
  }
}

async function generateShotImage(button) {
  const context = mediaPromptFromButton(button);
  if (!context?.prompt || button.disabled) return;
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "生成中（約 1–2 分鐘）…";
  try {
    const updated = await api(`/api/v1/workflows/${encodeURIComponent(context.workflow.id)}/generated-images`, {
      method: "POST",
      body: JSON.stringify({
        shot: Number(button.dataset.shot),
        label: context.prompt.label || `分鏡 ${button.dataset.shot}`,
        prompt: context.prompt.image_prompt,
        aspect_ratio: context.prompt.aspect_ratio || "16:9",
      }),
    });
    const index = state.history.findIndex((item) => item.id === updated.id);
    if (index >= 0) state.history[index] = updated;
    if (state.workflow?.id === updated.id) state.workflow = updated;
    renderAssets();
    showAssetMessage(`分鏡 ${button.dataset.shot} 的圖片已生成，可直接預覽或下載。`);
  } catch (error) {
    button.disabled = false;
    button.textContent = originalText;
    showAssetMessage(error.message || "圖片生成失敗，請稍後再試。", true);
  }
}

function matchStoryboardShots(section, shots, index, sectionCount) {
  const normalize = (value) => String(value || "").toLowerCase().replace(/[\s\p{P}\p{S}]+/gu, "");
  const sectionLabel = normalize(section.label || section.id);
  let matches = shots.filter((item) => {
    const shotLabel = normalize(item.section);
    return shotLabel && sectionLabel && (shotLabel === sectionLabel || shotLabel.includes(sectionLabel) || sectionLabel.includes(shotLabel));
  });
  if (matches.length) return { shots: matches, label: "名稱對應" };
  const anchors = ["開場", "hook", "背景", "context", "洞察", "insight", "行動", "action", "設計", "檢查一", "檢查二", "檢查三", "判斷", "結論", "cta"];
  const sharedAnchor = anchors.find((anchor) => sectionLabel.includes(anchor));
  if (sharedAnchor) {
    matches = shots.filter((item) => normalize(item.section).includes(sharedAnchor));
    if (matches.length) return { shots: matches, label: matches.length > 1 ? `主題對應 · ${matches.length} 鏡` : "主題對應" };
  }
  matches = shots.filter((item) => {
    const scriptText = normalize(section.voiceover).slice(0, 80);
    const shotText = normalize(item.voiceover).slice(0, 80);
    return scriptText && shotText && (scriptText.includes(shotText) || shotText.includes(scriptText));
  });
  if (matches.length) return { shots: matches, label: matches.length > 1 ? `旁白對應 · ${matches.length} 鏡` : "旁白對應" };
  if (shots.length === sectionCount && shots[index]) return { shots: [shots[index]], label: "順序對應" };
  return { shots: [], label: "尚未對應" };
}

function sectionBrollSuggestions(workflow, section, shot) {
  if (shot?.broll_queries?.length) return shot.broll_queries.slice(0, 4);
  const globalQueries = workflow.artifacts?.production_package?.visual_plan?.broll_queries || [];
  return [
    `${workflow.input.topic} ${section.label || ""}`.trim(),
    shot?.visual || section.visual_direction,
    ...globalQueries,
  ].filter(Boolean).filter((item, index, values) => values.indexOf(item) === index).slice(0, 3);
}

function buildAssetEntries() {
  const entries = [];
  state.history.forEach((workflow) => {
    const script = workflow.artifacts?.script;
    const production = workflow.artifacts?.production_package;
    const references = workflow.artifacts?.reference_analysis || script?.reference_analysis;
    const platformLabels = visibleDistributionKit(production?.distribution_kit || []).map((item) => item.label || platformLabel(item.platform));
    if (script) {
      entries.push({
        key: `${workflow.id}:script`, type: "script", icon: "稿", label: "完整逐字稿", purpose: "錄音、拍攝、交付或進一步改寫", workflow,
        excerpt: compactExcerpt(script.summary || script.full_text, 110),
        meta: [`${script.word_count || 0} 字`, `品質 ${script.quality_score || "－"} 分`, outputTypeLabel(workflow.input.output_type)],
        searchText: [workflow.input.topic, script.title, script.summary, script.full_text, ...workflow.input.platforms].join(" ").toLowerCase(),
      });
    }
    if (production) {
      entries.push({
        key: `${workflow.id}:production`, type: "production", icon: "製", label: "製作素材包", purpose: "安排分鏡、拍攝畫面與跨平台發布", workflow,
        excerpt: `包含 ${(production.storyboard || []).length} 個分鏡、視覺方向、B-roll 搜尋詞與跨平台發布文案。`,
        meta: [`${(production.storyboard || []).length} 個分鏡`, `${platformLabels.length} 個平台`, platformLabels.join("、") || "待設定平台"],
        searchText: [workflow.input.topic, ...platformLabels, JSON.stringify(production.visual_plan || {})].join(" ").toLowerCase(),
      });
    }
    if (references?.has_references) {
      const sourceNames = (references.sources || []).map((source) => source.name);
      entries.push({
        key: `${workflow.id}:reference`, type: "reference", icon: "參", label: "參考分析", purpose: "核對品牌方向、來源與 AI 創作依據", workflow,
        excerpt: compactExcerpt(references.priority_rule || "已整理品牌規範與創作參考。", 110),
        meta: [`${references.source_count || sourceNames.length} 份來源`, ...sourceNames.slice(0, 2)],
        searchText: [workflow.input.topic, references.priority_rule, ...sourceNames].join(" ").toLowerCase(),
      });
    }
  });
  return entries;
}

function assetPayload(asset) {
  if (!asset) return null;
  if (asset.type === "script") return asset.workflow.artifacts?.script || null;
  if (asset.type === "production") return asset.workflow.artifacts?.production_package || null;
  return asset.workflow.artifacts?.reference_analysis || asset.workflow.artifacts?.script?.reference_analysis || null;
}

function assetExportText(asset) {
  const payload = assetPayload(asset) || {};
  const topic = asset.workflow.input.topic;
  if (asset.type === "script") {
    return [
      `# ${payload.title || topic}`,
      payload.summary ? `摘要：${payload.summary}` : "",
      payload.word_count ? `字數：${payload.word_count} 字` : "",
      "",
      payload.full_text || "目前沒有完整逐字稿內容。",
    ].filter((line, index, values) => line || (index > 0 && values[index - 1])).join("\n");
  }
  if (asset.type === "production") {
    const storyboard = (payload.storyboard || []).map((shot) => [
      `## 分鏡 ${shot.shot || "－"}｜${shot.section || "未命名段落"}`,
      shot.duration_seconds ? `時間：${shot.duration_seconds} 秒` : "",
      shot.voiceover ? `旁白：${shot.voiceover}` : "",
      shot.visual ? `畫面：${shot.visual}` : "",
      (shot.broll_queries || []).length ? `B-roll：${shot.broll_queries.join("、")}` : "",
    ].filter(Boolean).join("\n")).join("\n\n");
    const editorial = (payload.editorial_plan || []).map((item) => `- ${item.section}：${item.purpose}`).join("\n");
    const visual = payload.visual_plan || {};
    const distribution = visibleDistributionKit(payload.distribution_kit || []).map((item) => [
      `## ${item.label || item.platform || "發布平台"}`,
      `標題：${item.title || ""}`,
      `文案：${item.caption || ""}`,
      `CTA：${item.cta || ""}`,
      (item.hashtags || []).length ? `標籤：${item.hashtags.join(" ")}` : "",
    ].filter(Boolean).join("\n")).join("\n\n");
    return [
      `# ${topic}｜製作素材包`, payload.summary || "", "",
      storyboard ? "# 分鏡規劃\n\n" + storyboard : "",
      editorial ? "# 版面規劃\n\n" + editorial : "",
      "# 視覺方向",
      visual.style ? `風格：${visual.style}` : "",
      (visual.palette || []).length ? `色彩：${visual.palette.join("、")}` : "",
      (visual.image_prompts || []).length ? `AI 圖像提示：\n${visual.image_prompts.map((item) => `- ${item}`).join("\n")}` : "",
      (visual.broll_queries || []).length ? `B-roll 搜尋詞：\n${visual.broll_queries.map((item) => `- ${item}`).join("\n")}` : "",
      distribution ? "# 平台發布文案\n\n" + distribution : "",
    ].filter(Boolean).join("\n\n");
  }
  const sources = (payload.sources || []).map((source, index) => [
    `${index + 1}. ${source.name || source.title || "未命名來源"}`,
    source.kind || source.type ? `類型：${source.kind || source.type}` : "",
    source.focus ? `參考重點：${source.focus}` : "",
    source.status ? `狀態：${source.status}` : "",
  ].filter(Boolean).join("\n")).join("\n\n");
  return [
    `# ${topic}｜參考分析`,
    payload.priority_rule ? `使用優先順序：${payload.priority_rule}` : "",
    payload.reference_boundary ? `創作邊界：${payload.reference_boundary}` : "",
    "",
    sources || "目前沒有可列出的參考來源。",
  ].filter((line, index, values) => line || (index > 0 && values[index - 1])).join("\n");
}

function openAssetPreview(asset) {
  if (!asset) return;
  state.activeAssetPreview = asset;
  document.querySelector("#assetPreviewModalKind").className = `asset-kind ${asset.type}`;
  document.querySelector("#assetPreviewModalKind").innerHTML = `<i>${escapeHtml(asset.icon)}</i>${escapeHtml(asset.label)}`;
  document.querySelector("#assetPreviewModalTitle").textContent = asset.workflow.input.topic;
  document.querySelector("#assetPreviewModalMeta").textContent = `${statusLabel(asset.workflow.status)} · 更新於 ${formatDateTime(asset.workflow.updated_at)}`;
  document.querySelector("#assetPreviewModalContent").textContent = assetExportText(asset);
  document.querySelector("#assetPreviewModal").classList.remove("hidden");
  document.body.classList.add("modal-open");
  document.querySelector("#assetPreviewModal [data-close-asset-preview]").focus();
  trackEvent("artifact.previewed", { artifact_type: asset.type, source: "assets" }, asset.workflow.id);
}

function closeAssetPreview() {
  document.querySelector("#assetPreviewModal").classList.add("hidden");
  document.body.classList.remove("modal-open");
  state.activeAssetPreview = null;
}

function downloadAsset(asset, format = "txt") {
  if (!asset) return;
  const payload = {
    workflow_id: asset.workflow.id,
    topic: asset.workflow.input.topic,
    asset_type: asset.type,
    asset_label: asset.label,
    workflow_status: asset.workflow.status,
    updated_at: asset.workflow.updated_at,
    content: assetPayload(asset),
  };
  const isJson = format === "json";
  const content = isJson ? JSON.stringify(payload, null, 2) : assetExportText(asset);
  const blob = new Blob([content], { type: isJson ? "application/json;charset=utf-8" : "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const safeTopic = asset.workflow.input.topic.replace(/[\\/:*?"<>|]/g, "-").slice(0, 60) || "素材";
  link.href = url;
  link.download = `${safeTopic}-${asset.label}.${format}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  trackEvent("artifact.downloaded", { artifact_type: asset.type, format, source: "assets" }, asset.workflow.id);
  showAssetMessage(`已下載「${asset.workflow.input.topic}」的${asset.label}（${format.toUpperCase()}）。`);
}

function compactExcerpt(value, maxLength) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength)}…` : normalized;
}

function showAssetMessage(message, isError = false) {
  const bar = document.querySelector("#assetMessage");
  bar.textContent = message;
  bar.classList.toggle("error", Boolean(isError));
  bar.classList.add("show");
}

function renderPublishing() {
  const allWorkflows = state.history.filter((workflow) => workflow.artifacts?.production_package);
  const activeWorkflows = allWorkflows.filter((workflow) => !isWorkflowArchived(workflow));
  const statuses = activeWorkflows.map((workflow) => publicationState(workflow));
  document.querySelector("#publishingMetrics").innerHTML = [
    ["等待核准", statuses.filter((item) => item === "awaiting").length, "需要人工確認", "attention"],
    ["可發布", statuses.filter((item) => item === "ready").length, "已完成所有內容", "success"],
    ["已排程", statuses.filter((item) => item === "scheduled").length, "本機排程紀錄"],
    ["已發布", statuses.filter((item) => item === "published").length, "已登記公開網址"],
  ].map(([label, value, note, tone = ""]) => `<article class="overview-metric ${tone}"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`).join("");

  const filter = document.querySelector("#publishingStatusFilter").value;
  const workflows = filter === "archived"
    ? allWorkflows.filter((workflow) => isWorkflowArchived(workflow))
    : activeWorkflows;
  const filtered = workflows.filter((workflow) => filter === "all" || publicationState(workflow) === filter);
  const list = document.querySelector("#publishingList");
  if (!filtered.length) {
    list.innerHTML = '<div class="library-empty"><strong>目前沒有符合條件的發布內容</strong><p>製作素材包完成後，內容會自動出現在這裡。</p></div>';
    return;
  }

  list.innerHTML = filtered.map((workflow) => {
    const publication = workflow.artifacts.publication || {};
    const currentStatus = publicationState(workflow);
    const kit = workflow.artifacts.production_package.distribution_kit || [];
    const visibleKit = kit.map((item, index) => ({ item, index })).filter(({ item }) => isVisiblePlatform(item.platform));
    const scheduledValue = toDatetimeLocal(publication.scheduled_at);
    const canManage = workflow.status === "completed";
    const platformRows = visibleKit.map(({ item, index }) => renderPublishingCopyRow(workflow.id, item, index)).join("");
    return `
      <article class="publishing-card">
        <div class="publishing-card-main">
          <header><span class="publication-status ${currentStatus}">${escapeHtml(publicationLabel(currentStatus))}</span><time>${formatDateTime(workflow.updated_at)}</time></header>
          <h2>${escapeHtml(workflow.input.topic)}</h2>
          <p>${escapeHtml(workflow.artifacts.script?.summary || "製作素材包已完成，等待後續發布處理。")}</p>
          <div class="publishing-platforms">${visibleKit.map(({ item }) => `<span>${escapeHtml(item.label || platformLabel(item.platform))}</span>`).join("")}</div>
          ${canManage ? `<div class="publishing-package-actions"><button type="button" data-download-publication="${escapeHtml(workflow.id)}">↓ 下載完整發布包</button></div><div class="publishing-copy-grid">${platformRows}</div>` : ""}
          ${currentStatus === "scheduled" && publication.scheduled_at ? `<div class="schedule-note"><strong>排程時間</strong><span>${formatFullDateTime(publication.scheduled_at)}</span></div>` : ""}
          ${currentStatus === "published" && publication.published_url ? `<div class="publication-result"><strong>已發布${isVisiblePlatform(publication.target_platform) ? `至 ${escapeHtml(platformLabel(publication.target_platform))}` : ""}</strong><br><a href="${escapeHtml(publication.published_url)}" target="_blank" rel="noreferrer">${escapeHtml(publication.published_url)} ↗</a>${publication.published_at ? `<br>${formatFullDateTime(publication.published_at)}` : ""}</div>` : ""}
        </div>
        <aside class="publishing-actions">
          <button class="secondary-button" type="button" data-open-workflow="${escapeHtml(workflow.id)}">${workflow.status === "waiting_for_human" ? "前往審核" : "查看成果"}</button>
          ${renderPublishingManagement(workflow, currentStatus, scheduledValue, canManage)}
        </aside>
      </article>`;
  }).join("");

  list.querySelectorAll("[data-open-workflow]").forEach((button) => {
    button.addEventListener("click", () => openHistoryWorkflow(button.dataset.openWorkflow));
  });
  list.querySelectorAll("[data-publication-status]").forEach((select) => {
    select.addEventListener("change", () => {
      select.closest(".publication-manage").querySelector(".publication-date").classList.toggle("hidden", select.value !== "scheduled");
    });
  });
  list.querySelectorAll("[data-save-publication]").forEach((button) => {
    button.addEventListener("click", () => savePublication(button));
  });
  list.querySelectorAll("[data-copy-publication]").forEach((button) => {
    button.addEventListener("click", () => copyPublishingPlatform(button));
  });
  list.querySelectorAll("[data-download-publication]").forEach((button) => {
    button.addEventListener("click", () => downloadPublishingPackage(button.dataset.downloadPublication));
  });
  list.querySelectorAll("[data-open-publishing-platform]").forEach((link) => {
    link.addEventListener("click", () => markPublishingStarted(link));
  });
  list.querySelectorAll("[data-reopen-workflow]").forEach((button) => {
    button.addEventListener("click", () => reopenReturnedWorkflow(button));
  });
  list.querySelectorAll("[data-archive-workflow]").forEach((button) => {
    button.addEventListener("click", () => setWorkflowArchived(button));
  });
}

function renderPublishingManagement(workflow, currentStatus, scheduledValue, canManage) {
  if (currentStatus === "archived") {
    return `<div class="publishing-lock">這筆已封存，不會出現在預設發布清單。</div>
      <button class="secondary-button" type="button" data-archive-workflow="${escapeHtml(workflow.id)}" data-archived="false">取消封存</button>`;
  }
  if (canManage) {
    return `<details class="publication-manage">
      <summary>管理發布狀態</summary>
      <label><span>發布狀態</span><select data-publication-status>
        <option value="ready" ${currentStatus === "ready" ? "selected" : ""}>可發布</option>
        <option value="returned" ${currentStatus === "returned" ? "selected" : ""}>退回修改</option>
        <option value="scheduled" ${currentStatus === "scheduled" ? "selected" : ""}>已排程</option>
        <option value="publishing" ${currentStatus === "publishing" ? "selected" : ""}>人工發布中</option>
        <option value="published" ${currentStatus === "published" ? "selected" : ""}>已發布</option>
        <option value="failed" ${currentStatus === "failed" ? "selected" : ""}>發布失敗</option>
      </select></label>
      <label class="publication-date ${currentStatus === "scheduled" ? "" : "hidden"}"><span>排程日期與時間</span><input type="datetime-local" data-publication-date value="${escapeHtml(scheduledValue)}" /></label>
      <label><span>實際發布平台</span><select data-publication-platform><option value="">請選擇</option>${normalizePlatforms(workflow.input.platforms).map((platform) => `<option value="${escapeHtml(platform)}" ${workflow.artifacts.publication?.target_platform === platform ? "selected" : ""}>${escapeHtml(platformLabel(platform))}</option>`).join("")}</select></label>
      <label><span>實際貼文／影片網址</span><input type="url" data-publication-url maxlength="2000" placeholder="https://..." value="${escapeHtml(workflow.artifacts.publication?.published_url || "")}" /></label>
      <label><span>發布備註或退回原因</span><textarea data-publication-note maxlength="1000" placeholder="例如：縮圖已更新、平台仍在審核中">${escapeHtml(workflow.artifacts.publication?.note || "")}</textarea></label>
      <p class="publication-help">「退回修改」只會暫停發布，不會刪除腳本或素材；完成修改後可重新設為「可發布」。</p>
      <button class="primary-button" type="button" data-save-publication="${escapeHtml(workflow.id)}">儲存發布狀態</button>
    </details>`;
  }
  if (currentStatus === "blocked") {
    const rejectionReason = latestRejectionReason(workflow);
    return `<details class="publication-manage">
      <summary>處理退回任務</summary>
      <div class="publication-rejection"><strong>退回原因</strong>${escapeHtml(rejectionReason)}</div>
      <label><span>本次修改說明 *</span><textarea data-reopen-note maxlength="2000" placeholder="例如：補強開場差異點，並重新核對產品規格"></textarea></label>
      <p class="publication-help">重新開啟後會保留上一版紀錄，從 AI 內容製作階段產生修訂版，再次進入人工核准。</p>
      <button class="primary-button" type="button" data-reopen-workflow="${escapeHtml(workflow.id)}">重新開啟修改</button>
      <div class="publication-secondary-actions"><button class="secondary-button" type="button" data-open-workflow="${escapeHtml(workflow.id)}">查看上一版</button><button class="secondary-button" type="button" data-archive-workflow="${escapeHtml(workflow.id)}" data-archived="true">封存</button></div>
    </details>`;
  }
  return `<div class="publishing-lock">完成最終核准後即可排程發布</div>`;
}

function latestRejectionReason(workflow) {
  const decisions = workflow.artifacts?.human_decisions || [];
  const rejectedDecision = [...decisions].reverse().find((decision) => decision.approved === false);
  if (rejectedDecision?.note) return rejectedDecision.note;
  const logEntry = [...(workflow.execution_log || [])].reverse().find((entry) => entry.event_type === "human.decided" && entry.status === "cancelled");
  return logEntry?.summary || "未提供退回原因";
}

function isWorkflowArchived(workflow) {
  return workflow.artifacts?.archive?.archived === true;
}

async function reopenReturnedWorkflow(button) {
  if (state.busy) return;
  const workflowId = button.dataset.reopenWorkflow;
  const note = button.closest(".publication-manage").querySelector("[data-reopen-note]").value.trim();
  if (!note) {
    showPublishingMessage("請先填寫本次修改說明。", true);
    return;
  }
  if (!window.confirm("將保留上一版紀錄，並重新呼叫 AI 產生修訂內容。是否繼續？")) return;

  state.busy = true;
  button.disabled = true;
  button.textContent = "正在重新開啟…";
  try {
    const workflow = await api(`/api/v1/workflows/${workflowId}/reopen`, {
      method: "POST",
      body: JSON.stringify({ note }),
    });
    state.workflow = workflow;
    await loadHistory();
    await openHistoryWorkflow(workflowId, { scroll: false });
    setBusy(true, "正在建立修訂版…");
    showMessage("已保留上一版，AI 正在依修改說明建立修訂版。", false);
    await runWorkflow();
  } catch (error) {
    showPublishingMessage(error.message || "無法重新開啟任務，請稍後重試。", true);
  } finally {
    state.busy = false;
    setBusy(false);
  }
}

async function setWorkflowArchived(button) {
  if (state.busy) return;
  const workflowId = button.dataset.archiveWorkflow;
  const archived = button.dataset.archived === "true";
  if (archived && !window.confirm("封存後會從預設發布清單移除，但仍可從「已封存」篩選找回。是否繼續？")) return;

  state.busy = true;
  button.disabled = true;
  button.textContent = archived ? "封存中…" : "恢復中…";
  try {
    const workflow = await api(`/api/v1/workflows/${workflowId}/archive`, {
      method: "PATCH",
      body: JSON.stringify({ archived }),
    });
    if (state.workflow?.id === workflowId) state.workflow = workflow;
    await loadHistory();
    showPublishingMessage(archived ? `「${workflow.input.topic}」已封存。` : `「${workflow.input.topic}」已取消封存。`);
  } catch (error) {
    showPublishingMessage(error.message || "無法更新封存狀態，請稍後重試。", true);
    button.disabled = false;
    button.textContent = archived ? "封存" : "取消封存";
  } finally {
    state.busy = false;
  }
}

async function savePublication(button) {
  if (state.busy) return;
  const workflowId = button.dataset.savePublication;
  const panel = button.closest(".publication-manage");
  const publicationStatus = panel.querySelector("[data-publication-status]").value;
  const localDate = panel.querySelector("[data-publication-date]").value;
  const publishedUrl = panel.querySelector("[data-publication-url]").value.trim();
  const targetPlatform = panel.querySelector("[data-publication-platform]").value;
  const publicationNote = panel.querySelector("[data-publication-note]").value.trim();
  if (publicationStatus === "scheduled" && !localDate) {
    showPublishingMessage("請先選擇發布日期與時間。", true);
    return;
  }
  if (publicationStatus === "published" && !publishedUrl) {
    showPublishingMessage("標記為已發布前，請貼上實際貼文或影片網址。", true);
    return;
  }

  state.busy = true;
  button.disabled = true;
  button.textContent = "儲存中…";
  try {
    const workflow = await api(`/api/v1/workflows/${workflowId}/publication`, {
      method: "PATCH",
      body: JSON.stringify({
        status: publicationStatus,
        scheduled_at: publicationStatus === "scheduled" ? new Date(localDate).toISOString() : null,
        published_url: publishedUrl,
        target_platform: targetPlatform,
        note: publicationNote || "由半自動發布中心更新",
      }),
    });
    if (state.workflow?.id === workflowId) state.workflow = workflow;
    await loadHistory();
    showPublishingMessage(`「${workflow.input.topic}」已更新為「${publicationLabel(publicationStatus)}」。`);
  } catch (error) {
    showPublishingMessage(error.message || "無法更新發布狀態，請稍後重試。", true);
    button.disabled = false;
    button.textContent = "儲存發布狀態";
  } finally {
    state.busy = false;
  }
}

function publicationState(workflow) {
  if (isWorkflowArchived(workflow)) return "archived";
  if (workflow.status === "waiting_for_human") return "awaiting";
  if (workflow.status === "cancelled") return "blocked";
  if (workflow.status !== "completed") return "preparing";
  return workflow.artifacts?.publication?.status || "ready";
}

function publicationLabel(status) {
  return ({
    awaiting: "等待核准", ready: "可發布", returned: "退回修改", scheduled: "已排程", published: "已發布",
    publishing: "人工發布中", failed: "發布失敗", blocked: "工作流已退回", archived: "已封存", preparing: "製作中",
  })[status] || status;
}

function platformPublishUrl(platform) {
  return ({
    youtube: "https://studio.youtube.com/",
    instagram: "https://www.instagram.com/",
    threads: "https://www.threads.net/",
  })[platform] || "";
}

function publicationPlatformText(item) {
  const hashtags = Array.isArray(item.hashtags) ? item.hashtags.join(" ") : (item.hashtags || "");
  return [item.title, item.caption, item.cta, hashtags].filter(Boolean).join("\n\n");
}

function renderPublishingCopyRow(workflowId, item, index) {
  const platform = item.platform || "";
  const excerpt = compactExcerpt(publicationPlatformText(item), 88) || "尚無平台文案";
  const publishUrl = platformPublishUrl(platform);
  return `<div class="publishing-copy-row"><div><strong>${escapeHtml(item.label || platformLabel(platform))}</strong><small>${escapeHtml(excerpt)}</small></div><span><button class="secondary-button" type="button" data-copy-publication="${escapeHtml(workflowId)}" data-platform-index="${index}">複製文案</button>${publishUrl ? ` <a href="${escapeHtml(publishUrl)}" target="_blank" rel="noreferrer" data-open-publishing-platform="${escapeHtml(workflowId)}" data-platform="${escapeHtml(platform)}">前往平台 ↗</a>` : ""}</span></div>`;
}

async function copyPublishingPlatform(button) {
  const workflow = state.history.find((item) => item.id === button.dataset.copyPublication);
  const kit = workflow?.artifacts?.production_package?.distribution_kit || [];
  const item = kit[Number(button.dataset.platformIndex)];
  if (!item) return;
  try {
    await navigator.clipboard.writeText(publicationPlatformText(item));
    showPublishingMessage(`已複製 ${item.label || platformLabel(item.platform)} 發布文案。`);
    trackEvent("publication.copy", { platform: item.platform || "unknown" }, workflow.id);
  } catch (_) {
    showPublishingMessage("瀏覽器無法存取剪貼簿，請改由查看成果頁手動複製。", true);
  }
}

function publishingPackagePayload(workflow) {
  const production = workflow.artifacts?.production_package || {};
  return {
    exported_at: new Date().toISOString(),
    workflow_id: workflow.id,
    topic: workflow.input.topic,
    platforms: normalizePlatforms(workflow.input.platforms),
    script: workflow.artifacts?.script || {},
    distribution_kit: visibleDistributionKit(production.distribution_kit || []),
    storyboard: production.storyboard || [],
    visual_cues: production.visual_cues || [],
    youtube_references: production.visual_plan?.youtube_references || [],
    publication: workflow.artifacts?.publication || {},
  };
}

function downloadPublishingPackage(workflowId) {
  const workflow = state.history.find((item) => item.id === workflowId);
  if (!workflow) return;
  const blob = new Blob([JSON.stringify(publishingPackagePayload(workflow), null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${workflow.input.topic.replace(/[\\/:*?"<>|]/g, "-").slice(0, 60) || "發布包"}-發布包.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  trackEvent("publication.package_downloaded", { format: "json" }, workflow.id);
  showPublishingMessage(`已下載「${workflow.input.topic}」的完整發布包。`);
}

async function markPublishingStarted(link) {
  const workflowId = link.dataset.openPublishingPlatform;
  const platform = link.dataset.platform;
  try {
    await api(`/api/v1/workflows/${workflowId}/publication`, {
      method: "PATCH",
      body: JSON.stringify({ status: "publishing", target_platform: platform, note: `已前往 ${platformLabel(platform)} 人工發布` }),
    });
    await loadHistory();
  } catch (error) {
    showPublishingMessage(error.message || "無法更新人工發布狀態。", true);
  }
}

function toDatetimeLocal(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function showPublishingMessage(message, isError = false) {
  const bar = document.querySelector("#publishingMessage");
  bar.textContent = message;
  bar.classList.toggle("error", Boolean(isError));
  bar.classList.add("show");
}

async function loadHistory(showErrors = false) {
  try {
    state.history = await api("/api/v1/workflows?limit=100&offset=0");
    renderHistory();
    if (state.currentPage === "dashboard") renderDashboard();
    if (state.currentPage === "opportunities" && state.opportunityResult) renderOpportunityResults(state.opportunityResult);
    if (state.currentPage === "plan") renderPlan();
    if (state.currentPage === "assets") renderAssets();
    if (state.currentPage === "publishing") renderPublishing();
    if (state.currentPage === "performance") renderPerformance();
    if (state.currentPage === "settings") renderSettings();
    renderNotifications();
  } catch (error) {
    if (showErrors) showMessage(error.message || "無法讀取歷史任務。", true);
  }
}

function renderHistory() {
  const list = document.querySelector("#historyList");
  if (!state.history.length) {
    list.innerHTML = '<div class="history-empty">還沒有歷史任務。完成第一筆內容後，紀錄會顯示在這裡。</div>';
    return;
  }
  list.innerHTML = state.history.map((workflow) => {
    const referenceCount = workflow.input.reference_materials?.length || 0;
    return `
      <article class="history-item">
        <div class="history-item-main">
          <div class="history-title-row">
            <strong>${escapeHtml(workflow.input.topic)}</strong>
            <span class="history-status ${escapeHtml(workflow.status)}">${escapeHtml(statusLabel(workflow.status))}</span>
          </div>
          <div class="history-meta">
            <span>${escapeHtml(outputTypeLabel(workflow.input.output_type))}</span>
            <span>目標 ${workflow.input.target_word_count} 字</span>
            <span>${referenceCount} 份參考資料</span>
            <time>${formatDateTime(workflow.updated_at)}</time>
          </div>
        </div>
        <button class="history-open" type="button" data-workflow-id="${escapeHtml(workflow.id)}">開啟紀錄</button>
      </article>`;
  }).join("");
  list.querySelectorAll("[data-workflow-id]").forEach((button) => {
    button.addEventListener("click", () => openHistoryWorkflow(button.dataset.workflowId));
  });
}

async function openHistoryWorkflow(workflowId, options = {}) {
  const sourcePage = options.returnPage || (state.currentPage !== "workbench" ? state.currentPage : null);
  let workflow = state.history.find((item) => item.id === workflowId);
  if (!workflow) {
    try {
      workflow = await api(`/api/v1/workflows/${encodeURIComponent(workflowId)}`);
      if (!state.history.some((item) => item.id === workflow.id)) state.history.unshift(workflow);
    } catch (error) {
      showPage("workbench", { updateUrl: false });
      showMessage(error.message || "找不到指定的工作紀錄。", true);
      return;
    }
  }
  if (state.currentPage !== "workbench") showPage("workbench", { updateUrl: false });
  // 既有任務即使經重新整理或直接網址開啟，也要保留一個可靠的返回出口。
  // 有明確來源時回到來源；無來源時預設回素材中心。
  state.workbenchReturnPage = sourcePage || "assets";
  syncWorkbenchReturnButton();
  state.pendingOpportunityHandoff = null;
  state.pendingAdoptionSync = getPendingAdoptionSync(workflow.id);
  state.workflow = workflow;
  trackEvent("workflow.opened", { source: "history" }, workflow.id);
  populateForm(workflow.input);
  resetAgentStates();
  decisionPanel.classList.add("hidden");
  resultPanel.classList.add("hidden");
  document.querySelector("#artifactFeedbackNote").value = "";
  document.querySelector("#artifactFeedbackStatus").textContent = "";
  document.querySelectorAll("[data-feedback-rating]").forEach((button) => button.classList.remove("selected"));
  executionLogPanel.classList.add("hidden");
  historyPanel.classList.add("hidden");
  setExecutionLogCollapsed(true);
  document.querySelector("#resultStatus").textContent = "預覽已就緒";
  activePreviewTab = "script";
  scriptViewMode = "full";
  scriptEditMode = false;
  rhythmViewMode = "timeline";
  rhythmEditMode = false;
  rhythmDraftCues = [];
  document.querySelectorAll(".preview-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === "script"));
  renderWorkflow(workflow);
  if (workflow.status === "running") {
    beginLiveWorkflowProgress(workflow, "已重新連線，正在取得最新代理進度");
  }
  if (options.updateUrl !== false) updatePageUrl("workbench", null, Boolean(options.replaceUrl), null, workflow.id);
  if (state.pendingAdoptionSync) showAdoptionSyncWarning(state.pendingAdoptionSync);
  else if (!["failed", "completed", "waiting_for_human"].includes(workflow.status)) {
    showMessage(`已載入「${workflow.input.topic}」的完整紀錄。`, false);
  }
  if (options.scroll !== false) document.querySelector("#resultPanel:not(.hidden), #executionLogPanel:not(.hidden), #workflowForm")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function populateForm(input) {
  form.reset();
  document.querySelector("#topic").value = input.topic || "";
  document.querySelector("#goal").value = input.goal || "education";
  document.querySelector("#outputType").value = input.output_type || "short_video";
  document.querySelector("#targetWordCount").value = input.target_word_count || 500;
  document.querySelector("#brandVoice").value = input.brand_voice || "品牌預設語氣";
  document.querySelector("#targetAudience").value = input.target_audience || "";
  document.querySelector("#brandName").value = input.brand_name || "";
  document.querySelector("#contentLanguage").value = languageLabel(input.language);
  document.querySelector("#contentRegion").value = input.region || "台灣";
  document.querySelector("#constraints").value = (input.constraints || []).join("\n");
  document.querySelectorAll(".platform-chip").forEach((chip) => {
    const selected = (input.platforms || []).includes(chip.querySelector("input").value);
    chip.querySelector("input").checked = selected;
    chip.classList.toggle("selected", selected);
  });

  const materials = input.reference_materials || [];
  const brandMaterials = materials.filter((item) => item.kind === "brand_brief");
  const contentMaterial = materials.find((item) => item.kind !== "brand_brief" && item.content);
  const urls = materials.filter((item) => item.source_url).map((item) => item.source_url);
  document.querySelector("#brandBrief").value = brandMaterials.map((item) => item.content).filter(Boolean).join("\n\n");
  document.querySelector("#referenceText").value = contentMaterial?.content || "";
  document.querySelector("#referenceTextType").value = contentMaterial?.kind || "creator_reference";
  document.querySelector("#referenceFocus").value = contentMaterial?.focus || "開場、敘事節奏與 CTA 結構";
  document.querySelector("#referenceUrls").value = urls.join("\n");
  document.querySelector("#referenceBoundary").value = input.reference_boundary || "";
  document.querySelector("#referenceFileList").textContent = materials.some((item) => item.content && item.name !== "品牌 Brief" && item.name !== "貼上的參考文字")
    ? "先前上傳的檔案內容已保存在此任務紀錄中"
    : "尚未選擇檔案";
  document.querySelector("#referenceSection").toggleAttribute("open", Boolean(materials.length));
  wordCountTouched = true;
  updateEstimatedDuration();
}

function renderExecutionLog(entries) {
  if (!entries.length) return;
  executionLogPanel.classList.remove("hidden");
  document.querySelector("#executionLogCount").textContent = `${entries.length} 筆紀錄`;
  document.querySelector("#executionLogList").innerHTML = entries.map((entry) => {
    const confidence = entry.details?.is_simulated
      ? '<em class="confidence-badge">本機規則</em>'
      : entry.confidence == null ? "" : `<em class="confidence-badge">信心 ${Math.round(entry.confidence * 100)}%</em>`;
    const risk = entry.risk_level ? `<em class="risk-badge ${escapeHtml(entry.risk_level)}">${riskLabel(entry.risk_level)}</em>` : "";
    const agent = entry.agent ? `<span class="log-agent">${escapeHtml(agentLabel(entry.agent))}</span>` : "";
    const riskExplanation = getRiskExplanation(entry);
    const details = flattenDetails(entry.details || {});
    return `
      <li class="log-entry ${escapeHtml(entry.status)}">
        <span class="log-marker">${logMarker(entry)}</span>
        <div class="log-main">
          <div class="log-title-row"><strong>${escapeHtml(entry.title)}</strong>${agent}</div>
          <p>${escapeHtml(entry.summary)}</p>
          ${riskExplanation ? `<div class="log-risk-reason ${escapeHtml(entry.risk_level)}"><strong>${riskLabel(entry.risk_level)}判定原因</strong><span>${escapeHtml(riskExplanation.reason)}</span><small>${escapeHtml(riskExplanation.guidance)}</small></div>` : ""}
          ${details.length ? `<details class="log-details"><summary>查看輸出摘要</summary><div class="log-detail-grid">${details.map((detail) => `<span class="log-detail-chip">${escapeHtml(detail)}</span>`).join("")}</div></details>` : ""}
        </div>
        <div class="log-meta"><time>${formatTime(entry.occurred_at)}</time>${confidence}${risk}</div>
      </li>`;
  }).join("");
}

function getRiskExplanation(entry) {
  if (!entry.risk_level) return null;
  const details = entry.details || {};
  const issues = Array.isArray(details.issues) ? details.issues.filter(Boolean) : [];
  const fallbackReason = issues.length
    ? issues.slice(0, 3).join("；")
    : entry.risk_level === "high"
      ? "此階段未通過必要的安全或品質條件，需要人工確認後才能繼續。"
      : entry.risk_level === "medium"
        ? "此階段存在需要留意的非阻擋性問題，建議在進入下一階段前複核。"
        : "必要檢查已通過，未發現阻擋流程的問題。";
  const fallbackGuidance = entry.risk_level === "high"
    ? "請先檢查來源、關鍵主張與發布邊界，完成人工判斷後再繼續。"
    : entry.risk_level === "medium"
      ? "建議查看問題與依據；必要時補充資料、修改內容或重新執行此階段。"
      : "可以繼續流程；正式發布前仍需完成最終人工核准。";
  return {
    reason: details.risk_reason || fallbackReason,
    guidance: details.risk_guidance || fallbackGuidance,
  };
}

function flattenDetails(details) {
  const labels = {
    goal: "目標", output_type: "形式", target_word_count: "目標字數", platforms: "平台", research_mode: "研究模式",
    agent_strategy: "代理策略", artifact: "成果", word_count: "字數",
    section_count: "段落", shot_count: "分鏡", platform_count: "發布平台",
    reason: "原因", approved: "核准", issues: "問題", evidence: "依據",
    reference_count: "參考資料", priority_rule: "使用順序",
  };
  return Object.entries(details)
    .filter(([key, value]) => !["artifact_summary", "risk_reason", "risk_guidance"].includes(key) && value !== null && value !== undefined && value !== "" && (!Array.isArray(value) || value.length))
    .slice(0, 6)
    .map(([key, value]) => {
      const normalized = Array.isArray(value) ? value.join("、") : typeof value === "object" ? JSON.stringify(value) : String(value);
      return `${labels[key] || key}：${normalized}`;
    });
}

function agentLabel(agent) {
  return ({
    orchestrator: "流程協調代理", reference_analyst: "參考分析代理", research: "研究代理", verification: "驗證代理",
    writer: "撰寫代理", editorial_critic: "品質主編", production_planner: "製作規劃代理", visual_director: "視覺統籌代理",
    youtube_reference_verifier: "影片參考驗證代理", production_quality: "製作包 QA 代理", publishing_preflight: "發布前 Preflight",
  })[agent] || agent;
}

function riskLabel(risk) {
  return ({ low: "低風險", medium: "中風險", high: "高風險" })[risk] || risk;
}

function logMarker(entry) {
  if (entry.status === "running") return "…";
  if (entry.status === "waiting") return "!";
  if (entry.status === "failed") return "×";
  if (entry.status === "rejected") return "×";
  if (entry.status === "approved") return "✓";
  if (entry.event_type === "agent.completed") return "AI";
  return "✓";
}

function formatTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("zh-TW", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date);
}

function formatDateTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("zh-TW", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(date);
}

function formatFullDateTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("zh-TW", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(date);
}

function formatTimestamp(value) {
  return formatFullDateTime(value);
}

function statusLabel(status) {
  return ({
    draft: "草稿", running: "執行中", waiting_for_human: "等待核准",
    completed: "已完成", cancelled: "已退回", failed: "失敗",
  })[status] || status;
}

function stageLabel(stage) {
  return ({
    requirements: "需求設定", ai_creation: "AI 研究與創作",
    production_package: "製作素材包", approval_publish: "核准與發布",
  })[stage] || stage;
}

function outputTypeLabel(outputType) {
  return ({
    short_video: "短影音腳本", long_video: "長影音腳本", short_text_post: "短貼文",
    thread: "串文", long_text_article: "長文文章", carousel_slides: "圖文輪播",
  })[outputType] || outputType;
}

function referenceSourceStatusLabel(status) {
  return ({ verified: "已驗證", provided: "使用者提供", configured: "系統設定" })[String(status || "").toLowerCase()] || "待確認";
}

function normalizeTextList(value) {
  const values = Array.isArray(value) ? value : typeof value === "string" ? value.split("\n") : [];
  return values.map((item) => String(item || "").trim()).filter(Boolean);
}

function normalizeGoal(value) {
  const normalized = String(value || "education").toLowerCase();
  return ({
    education: "education", "知識教育": "education", awareness: "awareness", "品牌認知": "awareness",
    conversion: "conversion", "轉換推廣": "conversion", community: "community", "社群互動": "community",
    news_reaction: "news_reaction", "新聞快評": "news_reaction",
    unboxing_review: "unboxing_review", "開箱測評": "unboxing_review", "開箱評測": "unboxing_review",
  })[normalized] || "education";
}

function normalizeOutputType(value) {
  const normalized = String(value || "short_video").toLowerCase();
  if (["short_video", "long_video", "short_text_post", "thread", "long_text_article", "carousel_slides"].includes(normalized)) return normalized;
  if (normalized.includes("長影音") || normalized.includes("long video")) return "long_video";
  if (normalized.includes("短影音") || normalized.includes("short video") || normalized.includes("reel")) return "short_video";
  if (normalized.includes("輪播") || normalized.includes("carousel")) return "carousel_slides";
  if (normalized.includes("串文") || normalized.includes("thread")) return "thread";
  if (normalized.includes("長文") || normalized.includes("article")) return "long_text_article";
  if (normalized.includes("貼文") || normalized.includes("post")) return "short_text_post";
  return "short_video";
}

function normalizePlatforms(values) {
  return normalizeTextList(values).map((value) => ({
    youtube: "youtube", instagram: "instagram", threads: "threads",
  })[value.toLowerCase()] || value.toLowerCase()).filter(isVisiblePlatform);
}

function platformLabel(value) {
  return ({ youtube: "YouTube", instagram: "Instagram", threads: "Threads" })[String(value || "").toLowerCase()] || value;
}

function isVisiblePlatform(value) {
  return ["youtube", "instagram", "threads"].includes(String(value || "").toLowerCase());
}

function visibleDistributionKit(items) {
  return (Array.isArray(items) ? items : []).filter((item) => isVisiblePlatform(item?.platform));
}

function formatLabel(value) {
  return outputTypeLabel(normalizeOutputType(value));
}

function languageLabel(code) {
  const label = ({ "zh-Hant": "繁體中文", "zh-Hans": "簡體中文", en: "英文", "繁體中文": "繁體中文", "簡體中文": "簡體中文", "英文": "英文" })[code];
  return label || "繁體中文";
}

function languageCode(label) {
  return ({ "繁體中文": "zh-Hant", "簡體中文": "zh-Hans", "英文": "en", "zh-hant": "zh-Hant", "zh-hans": "zh-Hans", en: "en" })[String(label || "繁體中文").toLowerCase()] || ({ "繁體中文": "zh-Hant", "簡體中文": "zh-Hans", "英文": "en" })[label] || "zh-Hant";
}

function opportunityItemStatusLabel(status) {
  return ({ new: "新題目", saved: "已收藏", dismissed: "已略過", adopted: "已採用" })[status] || status;
}

function generationStatusLabel(status) {
  return ({ pending: "產生中", completed: "已完成", failed: "失敗" })[status] || status || "已完成";
}

function confidenceLabel(value) {
  return ({ low: "低", medium: "中", high: "高" })[value] || "低";
}

function fitLevelLabel(value) {
  return ({ low: "低", medium: "中", high: "高" })[value] || "中";
}

function highestOpportunityConfidence(items) {
  const order = { low: 1, medium: 2, high: 3 };
  return items.reduce((best, item) => order[item.data_confidence] > order[best] ? item.data_confidence : best, "low");
}

function generationModeLabel(mode) {
  return ({ local_rule: "內容分析完成", llm: "AI 研究完成", hybrid: "AI 研究完成", openai_web_search: "AI 研究完成" })[String(mode || "").split("+")[0]] || "內容分析完成";
}

function providerLabel(provider) {
  return ({ local_rule: "本機規則代理", local: "本機代理", openai: "OpenAI" })[provider] || provider || "本機規則代理";
}

function scoringMethodLabel(method) {
  return ({
    local_rule_v2_no_live_signals: "啟發式規則（無即時市場訊號）",
    openai_structured_v1_with_web_search: "OpenAI 結構化評估（含網路引用）",
    openai_structured_v1_no_citations: "OpenAI 結構化評估（未取得引用）",
  })[method] || method || "啟發式規則";
}

function renderPreview(tabName) {
  const workflow = state.workflow;
  if (!workflow?.artifacts?.script) return;
  const script = workflow.artifacts.script;
  const production = workflow.artifacts.production_package || {};
  const container = document.querySelector("#previewContent");

  if (tabName === "script") {
    renderScriptPreview(container, script);
    return;
  }

  if (tabName === "references") {
    const analysis = script.reference_analysis || {};
    if (!analysis.has_references) {
      container.innerHTML = emptyPreview("這次任務沒有加入參考資料");
      return;
    }
    const sources = (analysis.sources || []).map((source) => `
      <article class="preview-card reference-source-card">
        <h4>${escapeHtml(source.kind_label)} · ${escapeHtml(source.name)}</h4>
        <p>參考重點：${escapeHtml(source.focus || "整體參考")}</p>
        ${source.source_url ? `<small>來源網址：${escapeHtml(source.source_url)}</small>` : `<small>已讀取 ${source.content_length || 0} 字</small>`}
      </article>`).join("");
    const guidance = (analysis.applied_guidance || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
    const signals = (analysis.style_signals || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
    const safeguards = (analysis.safeguards || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
    container.innerHTML = `
      <div class="preview-list">
        <article class="preview-card reference-rule-card"><h4>使用優先順序</h4><p>${escapeHtml(analysis.priority_rule)}</p></article>
        ${sources}
        ${guidance ? `<article class="preview-card"><h4>已套用的品牌與事實指引</h4><ul>${guidance}</ul></article>` : ""}
        ${signals ? `<article class="preview-card"><h4>提取的高層次風格特徵</h4><ul>${signals}</ul></article>` : ""}
        <article class="preview-card"><h4>創作邊界與保護措施</h4>${analysis.reference_boundary ? `<p>${escapeHtml(analysis.reference_boundary)}</p>` : ""}<ul>${safeguards}</ul></article>
      </div>`;
    return;
  }

  if (tabName === "research") {
    const sources = script.research.sources.map((source) => `
      <article class="preview-card"><h4>${source.status === "verified" ? "✓" : "•"} ${escapeHtml(source.title)}</h4><p>類型：${escapeHtml(source.type)} · 狀態：${escapeHtml(referenceSourceStatusLabel(source.status))}</p></article>`).join("");
    const notes = script.review_notes.map((note) => `<span class="tag">${escapeHtml(note)}</span>`).join("");
    container.innerHTML = `
      <div class="preview-list">
        <article class="preview-card"><h4>研究摘要</h4><p>${escapeHtml(script.research.summary)}</p><small>${script.research.external_verification ? `已檢查 ${script.research.claims_checked} 個核心敘述` : "尚未連接外部搜尋與事實查核來源"}</small></article>
        ${sources}
        <article class="preview-card"><h4>品質主編備註</h4><div class="tag-list">${notes}</div></article>
      </div>`;
    return;
  }

  if (tabName === "storyboard") {
    const shots = production.storyboard || [];
    const editorial = production.editorial_plan || [];
    const syncNotice = productionSyncNotice(production);
    container.innerHTML = syncNotice + (shots.length ? `<div class="storyboard-grid">${shots.map((shot) => `
      <article class="preview-card">
        <h4><span class="shot-number">${shot.shot}</span>${escapeHtml(shot.section)}${shot.duration_seconds ? ` · ${shot.duration_seconds} 秒` : ""}</h4>
        <p>${escapeHtml(shot.voiceover)}</p><small>畫面：${escapeHtml(shot.visual)}</small>
      </article>`).join("")}</div>` : editorial.length ? `<div class="preview-list">${editorial.map((item) => `<article class="preview-card"><h4>${escapeHtml(item.section)}</h4><p>${escapeHtml(item.purpose)}</p></article>`).join("")}</div>` : emptyPreview("版面規劃正在產生中"));
    return;
  }

  if (tabName === "visual") {
    const visual = production.visual_plan;
    if (!visual) { container.innerHTML = emptyPreview("視覺規劃正在產生中"); return; }
    container.innerHTML = productionSyncNotice(production) + visualReviewNotice(production, true) + `
      <div class="preview-list">
        <article class="preview-card"><h4>視覺風格</h4><p>${escapeHtml(visual.style)}</p><div class="tag-list">${visual.palette.map((color) => `<span class="tag">${escapeHtml(color)}</span>`).join("")}</div></article>
        <article class="preview-card"><h4>AI 圖像提示</h4>${visual.image_prompts.map((prompt) => `<p>・${escapeHtml(prompt)}</p>`).join("")}</article>
        <article class="preview-card"><h4>B-roll 搜尋詞</h4><div class="tag-list">${visual.broll_queries.map((query) => `<span class="tag">${escapeHtml(query)}</span>`).join("")}</div></article>
      </div>`;
    container.querySelector("#runVisualReviewButton")?.addEventListener("click", runVisualReview);
    return;
  }

  if (tabName === "rhythm") {
    renderRhythmPreview(container, production, workflow);
    return;
  }

  const kit = visibleDistributionKit(production.distribution_kit || []);
  container.innerHTML = kit.length ? `<div class="distribution-grid">${kit.map((item) => `
    <article class="distribution-card">
      <header><strong>${escapeHtml(item.label)}</strong><span>發布草稿</span></header>
      <h4>${escapeHtml(item.title)}</h4>
      <p>${escapeHtml(item.caption)}</p>
      <p><b>CTA：</b>${escapeHtml(item.cta)}</p>
      <small>${item.hashtags.map(escapeHtml).join("　")}</small>
    </article>`).join("")}</div>` : emptyPreview("發布素材正在產生中");
}

const visualCueTypeLabels = {
  broll: "B-roll",
  product_shot: "產品實拍",
  screen_recording: "螢幕錄影",
  image: "圖片／圖卡",
  text_overlay: "文字疊加",
  subtitle: "字幕",
  title_card: "圖卡",
};

function assetCueLane(cueType) {
  if (cueType === "subtitle") return 1;
  if (["title_card", "text_overlay"].includes(cueType)) return 2;
  return 0;
}

function activeAssetCue(cues, playhead, types) {
  return [...cues].reverse().find((cue) => types.includes(cue.cue_type)
    && playhead >= Number(cue.start_seconds)
    && playhead < Number(cue.start_seconds) + Number(cue.duration_seconds));
}

function renderAssetPreviewStage(cues, playhead) {
  const visual = activeAssetCue(cues, playhead, ["broll", "product_shot", "screen_recording", "image"]);
  const subtitle = activeAssetCue(cues, playhead, ["subtitle"]);
  const card = activeAssetCue(cues, playhead, ["title_card", "text_overlay"]);
  return `<div class="asset-preview-visual"><span>${escapeHtml(visualCueTypeLabels[visual?.cue_type] || "A-roll／主畫面")}</span><strong>${escapeHtml(visual?.label || "主畫面持續播放")}</strong><p>${escapeHtml(visual?.visual_description || "拖曳下方播放頭，檢查每個時間點的 B-roll、字幕與圖卡組合。")}</p></div>${card ? `<div class="asset-preview-title-card">${escapeHtml(card.visual_description || card.label)}</div>` : ""}${subtitle ? `<div class="asset-preview-subtitle">${escapeHtml(subtitle.visual_description || subtitle.label)}</div>` : ""}`;
}

function renderAssetTimelinePreview(workflow, cues, totalSeconds) {
  const playhead = Math.min(totalSeconds, Math.max(0, Number(assetTimelinePlayheads[workflow.id] || 0)));
  return `<section class="asset-remotion-preview" aria-label="Remotion 多軌預覽"><header><div><strong>Remotion 多軌預覽</strong><span>同步檢查 B-roll、字幕與圖卡在同一時間點的畫面。</span></div><em data-asset-preview-status>預覽草稿</em></header><div class="asset-preview-stage" data-asset-preview-stage>${renderAssetPreviewStage(cues, playhead)}</div><footer><time data-asset-preview-time>${timelineSecondsLabel(playhead)}</time><input type="range" min="0" max="${totalSeconds}" step="1" value="${playhead}" data-asset-preview-scrubber="${escapeHtml(workflow.id)}" aria-label="影片播放頭" /><time>${timelineSecondsLabel(totalSeconds)}</time></footer></section>`;
}

function updateAssetTimelinePreview(details, workflowId, playhead) {
  const workflow = state.history.find((item) => item.id === workflowId);
  const production = workflow?.artifacts?.production_package;
  if (!details || !production) return;
  const totalSeconds = productionDuration(production);
  const safe = Math.min(totalSeconds, Math.max(0, Number(playhead || 0)));
  assetTimelinePlayheads[workflowId] = safe;
  const cues = assetCueEditWorkflowId === workflowId ? assetCueDrafts : productionVisualCues(production);
  const stage = details.querySelector("[data-asset-preview-stage]");
  if (stage) {
    stage.innerHTML = renderAssetPreviewStage(cues, safe);
    stage.classList.remove("located");
    void stage.offsetWidth;
    stage.classList.add("located");
  }
  const time = details.querySelector("[data-asset-preview-time]");
  if (time) time.textContent = timelineSecondsLabel(safe);
  const status = details.querySelector("[data-asset-preview-status]");
  if (status) status.textContent = `已定位到 ${timelineSecondsLabel(safe)}`;
  const scrubber = details.querySelector(`[data-asset-preview-scrubber="${CSS.escape(workflowId)}"]`);
  if (scrubber) scrubber.value = String(safe);
  const line = details.querySelector("[data-asset-playhead-line]");
  if (line) {
    const position = safe / totalSeconds * 100;
    line.style.left = `calc(50px + ${position}% - ${position / 2}px)`;
  }
}

function productionDuration(production) {
  return Number(production?.estimated_duration_seconds)
    || (production?.storyboard || []).reduce((total, shot) => total + Number(shot.duration_seconds || 0), 0);
}

function productionVisualCues(production) {
  if (Array.isArray(production?.visual_cues)) return production.visual_cues.map((cue) => ({ ...cue }));
  let elapsed = 0;
  return (production?.storyboard || []).flatMap((shot, index) => {
    const start = elapsed;
    const shotDuration = Number(shot.duration_seconds || 0);
    elapsed += shotDuration;
    return [{
      id: `ai-shot-${shot.shot || index + 1}`,
      shot: Number(shot.shot || index + 1),
      cue_type: "broll",
      label: `${shot.section || `分鏡 ${index + 1}`} B-roll`,
      start_seconds: start,
      duration_seconds: Number(shot.broll_duration_seconds || 3),
      visual_description: shot.visual || "",
      purpose: shot.visual_purpose || "補充情境並維持節奏",
      search_query: (shot.broll_queries || [])[0] || "",
      source: "ai",
    }, {
      id: `ai-subtitle-${shot.shot || index + 1}`,
      shot: Number(shot.shot || index + 1),
      cue_type: "subtitle",
      label: `${shot.section || `分鏡 ${index + 1}`} 字幕`,
      start_seconds: start,
      duration_seconds: Math.max(1, shotDuration),
      visual_description: String(shot.voiceover || "").slice(0, 120),
      purpose: "對齊口播字幕",
      search_query: "",
      source: "ai",
    }];
  });
}

function timelineSecondsLabel(seconds) {
  const safe = Math.max(0, Math.round(Number(seconds) || 0));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
}

function unionDuration(cues, predicate) {
  const intervals = cues
    .filter(predicate)
    .map((cue) => [Number(cue.start_seconds), Number(cue.start_seconds) + Number(cue.duration_seconds)])
    .sort((a, b) => a[0] - b[0]);
  const merged = [];
  intervals.forEach(([start, end]) => {
    const previous = merged[merged.length - 1];
    if (!previous || start > previous[1]) merged.push([start, end]);
    else previous[1] = Math.max(previous[1], end);
  });
  return merged.reduce((total, [start, end]) => total + end - start, 0);
}

function cueCoverageSummary(cues, totalSeconds) {
  const replacing = new Set(["broll", "product_shot", "screen_recording", "image"]);
  const visualSeconds = Math.min(totalSeconds, unionDuration(cues, (cue) => replacing.has(cue.cue_type)));
  const brollSeconds = Math.min(totalSeconds, unionDuration(cues, (cue) => cue.cue_type === "broll"));
  return {
    visualSeconds,
    visualPercent: totalSeconds ? Math.round(visualSeconds / totalSeconds * 100) : 0,
    brollSeconds,
    brollPercent: totalSeconds ? Math.round(brollSeconds / totalSeconds * 100) : 0,
  };
}

function visualReviewNotice(production, withAction = false) {
  const review = production?.visual_review;
  const status = review?.status || "not_reviewed";
  const reviewed = status === "reviewed";
  const pending = status === "pending_retry";
  const notes = [...(review?.alignment_notes || []), ...(review?.issues || [])].slice(0, 4);
  const title = reviewed ? "視覺統籌代理已完成校準" : pending ? "視覺統籌代理等待重試" : "尚未經視覺統籌代理校準";
  const detail = reviewed
    ? (review.summary || "圖像提示、逐字稿與 B-roll 已完成對齊檢查。")
    : pending ? "人工內容已保留，請稍後重新執行代理審查。" : "目前可能仍包含舊版通用提示，可執行代理依完整逐字稿重新整理。";
  return `<section class="visual-review-notice ${reviewed ? "reviewed" : pending ? "pending" : ""}">
    <div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span>${notes.length ? `<ul>${notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("")}</ul>` : ""}</div>
    ${withAction ? '<button id="runVisualReviewButton" class="secondary-button" type="button">重新執行視覺統籌</button>' : ""}
  </section>`;
}

async function runVisualReview() {
  if (!state.workflow || state.busy) return;
  setBusy(true, "視覺統籌代理校準中…");
  setAgentState("visual_director", "running");
  showMessage("視覺統籌正在校準提示；Gemini 影片參考驗證代理會另外讀取候選影片並複核時間碼。", false);
  try {
    state.workflow = await api(`/api/v1/workflows/${encodeURIComponent(state.workflow.id)}/visual-review`, { method: "POST" });
    renderWorkflow(state.workflow);
    await loadHistory();
    showMessage("視覺校準與 YouTube 時間碼驗證已完成，請確認更新後的參考片段。", false);
  } catch (error) {
    showMessage(error.message || "視覺統籌代理暫時無法完成校準。", true);
  } finally {
    setBusy(false);
  }
}

function renderRhythmPreview(container, production, workflow) {
  const shots = production.storyboard || [];
  const totalSeconds = productionDuration(production);
  if (!shots.length || !totalSeconds) {
    container.innerHTML = emptyPreview("影片節奏與素材規劃正在產生中");
    return;
  }
  const cues = rhythmEditMode ? rhythmDraftCues : productionVisualCues(production);
  const summary = cueCoverageSummary(cues, totalSeconds);
  const modeButtons = `
    <div class="rhythm-toolbar">
      <div class="rhythm-view-switch" role="tablist" aria-label="節奏與素材檢視方式">
        <button type="button" data-rhythm-view="timeline" class="${rhythmViewMode === "timeline" ? "active" : ""}" aria-selected="${rhythmViewMode === "timeline"}">剪輯時間軸</button>
        <button type="button" data-rhythm-view="map" class="${rhythmViewMode === "map" ? "active" : ""}" aria-selected="${rhythmViewMode === "map"}">內容結構圖</button>
      </div>
      ${rhythmEditMode
        ? '<span class="rhythm-edit-state">人工編輯中</span>'
        : '<button id="editVisualCuesButton" class="secondary-button" type="button">✎ 人工調整 B-roll／視覺</button>'}
    </div>`;
  const metrics = `<div class="rhythm-metrics">
    <article><span>影片總長</span><strong>${timelineSecondsLabel(totalSeconds)}</strong></article>
    <article><span>視覺素材覆蓋</span><strong>${timelineSecondsLabel(summary.visualSeconds)} · ${summary.visualPercent}%</strong></article>
    <article><span>B-roll 占比</span><strong>${timelineSecondsLabel(summary.brollSeconds)} · ${summary.brollPercent}%</strong></article>
    <article><span>素材段</span><strong>${cues.length} 段</strong></article>
  </div>`;
  container.innerHTML = `${productionSyncNotice(production)}${visualReviewNotice(production)}${modeButtons}${metrics}
    <div id="rhythmVisualization"></div>
    ${rhythmEditMode ? renderVisualCueEditor(cues, shots, totalSeconds) : ""}`;

  const visual = container.querySelector("#rhythmVisualization");
  visual.innerHTML = rhythmViewMode === "map"
    ? renderContentStructureMap(shots, cues)
    : renderEditingTimeline(shots, cues, totalSeconds);
  bindRhythmPreview(container, production, workflow, totalSeconds);
}

function renderEditingTimeline(shots, cues, totalSeconds) {
  let elapsed = 0;
  const segments = shots.map((shot, index) => {
    const duration = Number(shot.duration_seconds || 0);
    const start = elapsed;
    elapsed += duration;
    return `<div class="rhythm-segment" style="width:${duration / totalSeconds * 100}%" aria-label="${escapeHtml(shot.section)} ${duration} 秒"><strong>${index + 1}. ${escapeHtml(shot.section)}</strong><span>${timelineSecondsLabel(start)}–${timelineSecondsLabel(elapsed)}</span></div>`;
  }).join("");
  const cueBars = cues.map((cue, index) => {
    const left = Math.max(0, Number(cue.start_seconds)) / totalSeconds * 100;
    const width = Math.min(Number(cue.duration_seconds), totalSeconds) / totalSeconds * 100;
    return `<button type="button" class="rhythm-cue cue-${escapeHtml(cue.cue_type)}" style="left:${left}%;width:${Math.max(width, 1.4)}%;top:${8 + (index % 3) * 25}px" data-cue-index="${index}" aria-label="${escapeHtml(cue.label)}，${cue.duration_seconds} 秒"><span>${escapeHtml(cue.label)}</span></button>`;
  }).join("");
  const list = cues.map((cue, index) => `<article class="rhythm-cue-row">
    <div><span>${escapeHtml(visualCueTypeLabels[cue.cue_type] || cue.cue_type)} · ${timelineSecondsLabel(cue.start_seconds)}–${timelineSecondsLabel(Number(cue.start_seconds) + Number(cue.duration_seconds))}</span><strong>${escapeHtml(cue.label)}</strong><p>${escapeHtml(cue.visual_description || "尚未填寫畫面說明")}</p></div>
    ${rhythmEditMode ? `<button type="button" data-focus-cue="${index}">編輯</button>` : ""}
  </article>`).join("");
  return `<section class="rhythm-timeline-panel">
    <div class="rhythm-section-heading"><div><strong>全片段落與素材配置</strong><span>素材可與口播同時存在；文字疊加不計入取代畫面的占比。</span></div></div>
    <div class="rhythm-segments">${segments}</div>
    <div class="rhythm-cue-lanes">${cueBars}</div>
    <div class="rhythm-axis"><span>0:00</span><span>${timelineSecondsLabel(Math.round(totalSeconds / 2))}</span><span>${timelineSecondsLabel(totalSeconds)}</span></div>
    <div class="rhythm-legend"><span class="broll">B-roll</span><span class="product_shot">產品實拍</span><span class="screen_recording">螢幕錄影</span><span class="image">圖片／圖卡</span><span class="text_overlay">文字疊加</span></div>
    <div class="rhythm-cue-list">${list || "<p>目前沒有素材段。</p>"}</div>
  </section>`;
}

function renderContentStructureMap(shots, cues) {
  const nodes = shots.map((shot, index) => {
    const shotNumber = Number(shot.shot || index + 1);
    const linked = cues.filter((cue) => Number(cue.shot) === shotNumber);
    return `<article class="structure-node"><header><span>段落 ${shotNumber}</span><time>${shot.duration_seconds || 0} 秒</time></header><strong>${escapeHtml(shot.section)}</strong><p>${escapeHtml((shot.voiceover || "").slice(0, 110))}</p><footer>${linked.length ? linked.map((cue) => `<span>${escapeHtml(visualCueTypeLabels[cue.cue_type] || cue.cue_type)} ${cue.duration_seconds}s</span>`).join("") : "<em>尚無素材</em>"}</footer></article>`;
  }).join("");
  return `<section class="structure-map-panel"><div class="structure-root"><strong>${escapeHtml(state.workflow?.artifacts?.script?.title || state.workflow?.input?.topic || "影片主軸")}</strong><span>逐字稿主軸 → 內容段落 → 節奏與素材</span></div><div class="structure-line"></div><div class="structure-nodes">${nodes}</div></section>`;
}

function renderVisualCueEditor(cues, shots, totalSeconds) {
  const shotOptions = shots.map((shot, index) => `<option value="${shot.shot || index + 1}">${shot.shot || index + 1}. ${escapeHtml(shot.section)}</option>`).join("");
  const typeOptions = Object.entries(visualCueTypeLabels).map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
  const rows = cues.map((cue, index) => `<article class="visual-cue-editor-row" data-cue-editor="${index}">
    <header><strong>素材段 ${index + 1}</strong><span>${escapeHtml(cue.source === "ai" ? "AI 建議，可人工修改" : "人工新增")}</span><button type="button" data-remove-cue="${index}" aria-label="移除素材段 ${index + 1}">移除</button></header>
    <div class="visual-cue-fields">
      <label><span>素材類型</span><select data-cue-field="cue_type" data-index="${index}">${typeOptions}</select></label>
      <label><span>對應段落</span><select data-cue-field="shot" data-index="${index}">${shotOptions}</select></label>
      <label><span>開始秒數</span><input type="number" min="0" max="${totalSeconds - 1}" data-cue-field="start_seconds" data-index="${index}" value="${cue.start_seconds}" /></label>
      <label><span>時長（秒）</span><input type="number" min="1" max="${totalSeconds}" data-cue-field="duration_seconds" data-index="${index}" value="${cue.duration_seconds}" /></label>
      <label class="wide"><span>素材名稱</span><input maxlength="200" data-cue-field="label" data-index="${index}" value="${escapeHtml(cue.label)}" /></label>
      <label class="wide"><span>畫面說明</span><textarea maxlength="2000" data-cue-field="visual_description" data-index="${index}">${escapeHtml(cue.visual_description || "")}</textarea></label>
      <label><span>使用目的</span><input maxlength="500" data-cue-field="purpose" data-index="${index}" value="${escapeHtml(cue.purpose || "")}" /></label>
      <label><span>B-roll／素材搜尋詞</span><input maxlength="1000" data-cue-field="search_query" data-index="${index}" value="${escapeHtml(cue.search_query || "")}" /></label>
    </div>
  </article>`).join("");
  return `<section class="visual-cue-editor"><div class="rhythm-section-heading"><div><strong>人工調整素材段</strong><span>可修改 AI 建議，也可以新增 B-roll、圖片、文字卡或螢幕錄影。</span></div><button id="addVisualCueButton" class="secondary-button" type="button">＋ 新增素材段</button></div><div class="visual-cue-editor-list">${rows}</div><footer><button id="cancelVisualCueEditButton" class="secondary-button" type="button">取消</button><button id="saveVisualCuesButton" class="primary-button" type="button">儲存素材時間軸</button></footer></section>`;
}

function bindRhythmPreview(container, production, workflow, totalSeconds) {
  container.querySelectorAll("[data-rhythm-view]").forEach((button) => button.addEventListener("click", () => {
    rhythmViewMode = button.dataset.rhythmView;
    renderRhythmPreview(container, production, workflow);
  }));
  container.querySelector("#editVisualCuesButton")?.addEventListener("click", () => {
    rhythmDraftCues = productionVisualCues(production);
    rhythmEditMode = true;
    renderRhythmPreview(container, production, workflow);
  });
  container.querySelector("#cancelVisualCueEditButton")?.addEventListener("click", () => {
    rhythmEditMode = false;
    rhythmDraftCues = [];
    renderRhythmPreview(container, production, workflow);
  });
  container.querySelector("#addVisualCueButton")?.addEventListener("click", () => {
    const shot = Number(production.storyboard?.[0]?.shot || 1);
    rhythmDraftCues.push({ id: `manual-${Date.now()}`, shot, cue_type: "broll", label: "新增 B-roll", start_seconds: 0, duration_seconds: 3, visual_description: "", purpose: "補充畫面", search_query: "", source: "manual" });
    renderRhythmPreview(container, production, workflow);
    container.querySelector(`[data-cue-editor="${rhythmDraftCues.length - 1}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  container.querySelectorAll("[data-cue-field]").forEach((field) => {
    const index = Number(field.dataset.index);
    const key = field.dataset.cueField;
    field.value = String(rhythmDraftCues[index]?.[key] ?? "");
    field.addEventListener("input", () => {
      rhythmDraftCues[index][key] = ["shot", "start_seconds", "duration_seconds"].includes(key) ? Number(field.value) : field.value;
    });
  });
  container.querySelectorAll("[data-remove-cue]").forEach((button) => button.addEventListener("click", () => {
    rhythmDraftCues.splice(Number(button.dataset.removeCue), 1);
    renderRhythmPreview(container, production, workflow);
  }));
  container.querySelectorAll("[data-focus-cue], [data-cue-index]").forEach((button) => button.addEventListener("click", () => {
    const index = Number(button.dataset.focusCue ?? button.dataset.cueIndex);
    container.querySelector(`[data-cue-editor="${index}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }));
  container.querySelector("#saveVisualCuesButton")?.addEventListener("click", () => saveVisualCues(totalSeconds));
}

async function saveVisualCues(totalSeconds) {
  if (!state.workflow || state.busy) return;
  const invalid = rhythmDraftCues.find((cue) => !String(cue.label || "").trim() || Number(cue.duration_seconds) < 1 || Number(cue.start_seconds) < 0 || Number(cue.start_seconds) + Number(cue.duration_seconds) > totalSeconds);
  if (invalid) {
    showMessage("請確認每個素材段都有名稱，且開始時間與時長沒有超出影片範圍。", true);
    return;
  }
  setAgentState("visual_director", "running");
  setBusy(true, "正在保存素材時間軸…");
  try {
    state.workflow = await api(`/api/v1/workflows/${encodeURIComponent(state.workflow.id)}/production-plan`, {
      method: "PATCH",
      body: JSON.stringify({ visual_cues: rhythmDraftCues }),
    });
    rhythmEditMode = false;
    rhythmDraftCues = [];
    renderWorkflow(state.workflow);
    await loadHistory();
    showMessage("B-roll 與視覺時間軸已保存；請重新完成最終人工核准。", false);
  } catch (error) {
    showMessage(error.message || "無法保存素材時間軸，請稍後重試。", true);
  } finally {
    setBusy(false);
  }
}

function productionSyncNotice(production) {
  const notices = [];
  if (production?.script_sync_status === "human_visual_revision") notices.push(`<div class="production-sync-notice success" role="status"><strong>已保存人工調整的素材時間軸</strong><span>分鏡、B-roll 與視覺素材已建立新版本；發布前需要重新完成最終核准。</span></div>`);
  if (production?.script_sync_status === "kept_after_manual_edit") notices.push(`<div class="production-sync-notice" role="status"><strong>目前沿用修改前的製作規劃</strong><span>逐字稿已由人工更新，但分鏡、視覺與 B-roll 尚未依新版重做。你仍可回到「完整腳本」重新存檔並選擇重新設計。</span></div>`);
  const productionReview = production?.production_quality_review;
  if (productionReview) notices.push(renderQualityGateNotice("製作包 QA", productionReview, "ready_for_approval"));
  const publishingReview = production?.publishing_preflight_review;
  if (publishingReview) notices.push(renderQualityGateNotice("發布前 Preflight", publishingReview, "ready_for_human_approval"));
  return notices.join("");
}

function renderQualityGateNotice(label, review, readyKey) {
  const ready = Boolean(review?.[readyKey]);
  const issues = normalizeTextList(review?.issues).slice(0, 4);
  return `<div class="quality-gate-notice ${ready ? "passed" : "blocked"}" role="status"><div><strong>${escapeHtml(label)}：${ready ? "通過" : "需要處理"}</strong><span>${escapeHtml(review.summary || "品質檢查已完成。")}</span>${issues.length ? `<ul>${issues.map((issue) => `<li>${escapeHtml(issue)}</li>`).join("")}</ul>` : ""}</div><em>${Number.isFinite(Number(review.score)) ? `${Number(review.score)} 分` : `信心 ${Math.round(Number(review.confidence || 0) * 100)}%`}</em></div>`;
}

function renderScriptPreview(container, script) {
  const difference = script.word_count - script.target_word_count;
  const differenceText = difference === 0 ? "符合目標" : difference > 0 ? `比目標多 ${difference} 字` : `比目標少 ${Math.abs(difference)} 字`;
  const countClass = Math.abs(difference) <= Math.max(20, script.target_word_count * 0.1) ? "count-match" : "count-over";
  const switcher = `
    <div class="script-preview-actions">
      <div class="script-view-switch" role="tablist" aria-label="腳本檢視方式">
        <button class="script-view-button ${scriptViewMode === "full" ? "active" : ""}" data-script-view="full" type="button">完整逐字稿</button>
        <button class="script-view-button ${scriptViewMode === "sections" ? "active" : ""}" data-script-view="sections" type="button">分段腳本與畫面</button>
      </div>
      ${scriptEditMode ? "" : '<button id="editScriptButton" class="secondary-button script-edit-button" type="button">✎ 人工修改腳本</button>'}
    </div>`;
  const snippets = (state.workspaceAIProfile?.script_snippets || []).slice(0, 50);
  const canManageSnippets = ["owner", "admin"].includes(state.authContext?.workspace?.role);
  const snippetEntry = `<section class="script-snippet-entry" aria-label="常用語快捷插入入口">
    <div><strong>常用語快捷插入</strong><span>${snippets.length ? `已設定 ${snippets.length} 則；進入編輯後可插入游標所在位置。` : "這個 Workspace 尚未設定腳本常用語。"}</span></div>
    ${snippets.length
      ? '<button id="openScriptSnippetEditorButton" class="secondary-button" type="button">開始插入常用語 →</button>'
      : canManageSnippets ? '<button id="configureScriptSnippetsButton" class="secondary-button" type="button">前往設定常用語 →</button>' : '<em>請聯絡 Workspace 管理員新增</em>'}
  </section>`;

  if (scriptEditMode) {
    const snippetButtons = snippets.map((snippet, index) => `<button type="button" data-script-snippet="${index}" title="插入：${escapeHtml(snippet)}"><span>${escapeHtml(snippet)}</span>${index < 9 ? `<kbd>${navigator.platform?.includes("Mac") ? "⌥" : "Alt+"}${index + 1}</kbd>` : ""}</button>`).join("");
    const snippetBar = `<section class="script-snippet-bar" aria-label="腳本常用語快捷鍵"><header><div><strong>常用語快捷插入</strong><span>${snippets.length ? "先將游標放在想插入的位置，再點選常用語。" : "尚未設定常用語；Workspace 管理員可在系統設定中新增。"}</span></div>${snippets.length ? `<em>${snippets.length} 則</em>` : ""}</header>${snippets.length ? `<div>${snippetButtons}</div><small id="scriptSnippetStatus" role="status" aria-live="polite"></small>` : ""}</section>`;
    container.innerHTML = `${switcher}
      <article class="script-editor-panel">
        <div class="script-editor-heading"><div><strong>人工修改完整腳本／逐字稿</strong><span>儲存後會建立新版本，並重新要求最終人工核准。</span></div><span id="scriptEditWordCount">${script.full_text.replace(/\s/g, "").length} 字</span></div>
        ${snippetBar}
        <textarea id="scriptEditText" maxlength="50000" aria-label="人工修改完整逐字稿">${escapeHtml(script.full_text)}</textarea>
        <div id="scriptRevisionChoice" class="script-revision-choice hidden">
          <div><strong>是否依新版逐字稿重新設計製作規劃？</strong><span>重新設計會再次執行製作規劃代理，更新分鏡、視覺方向、B-roll 與發布素材。</span></div>
          <div class="script-revision-options">
            <button id="keepProductionButton" class="secondary-button" type="button">只存逐字稿，沿用原規劃</button>
            <button id="regenerateProductionButton" class="primary-button" type="button">存檔並重新設計</button>
          </div>
        </div>
        <div class="script-editor-actions"><button id="cancelScriptEditButton" class="secondary-button" type="button">取消</button><button id="prepareScriptSaveButton" class="primary-button" type="button">儲存修改</button></div>
      </article>`;
    bindScriptEditor(container, script, snippets);
    return;
  }

  if (scriptViewMode === "full") {
    const paragraphs = script.full_text.split(/\n\s*\n/).filter(Boolean).map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`).join("");
    container.innerHTML = `${switcher}${snippetEntry}
      <article class="transcript-panel">
        <div class="transcript-toolbar">
          <div><strong>完整口播逐字稿</strong><span>${script.word_count} 字</span><span class="${countClass}">${differenceText}</span></div>
          <button id="copyTranscriptButton" class="copy-button" type="button">複製逐字稿</button>
        </div>
        <div class="transcript-body">${paragraphs}</div>
      </article>`;
  } else {
    const blocks = script.sections.map((section) => `
      <article class="script-block">
        <div class="script-block-label">${escapeHtml(section.label)}</div>
        <div><p>${escapeHtml(section.voiceover)}</p><small>畫面建議：${escapeHtml(section.visual_direction)}</small></div>
      </article>`).join("");
    container.innerHTML = `${switcher}${snippetEntry}
      <div class="script-overview">
        <aside class="script-sidebar">
          <span>實際字數</span><strong>${script.word_count} 字</strong>
          <span>目標字數</span><strong>${script.target_word_count} 字</strong>
          <span>字數差異</span><strong class="${countClass}">${differenceText}</strong>
          <span>品質狀態</span><strong class="quality-number">${script.quality_score == null ? "本機檢查" : script.quality_score}</strong>
          <span>段落數</span><strong>${script.sections.length} 個段落</strong>
        </aside>
        <div class="script-sections">${blocks}</div>
      </div>`;
  }

  container.querySelectorAll("[data-script-view]").forEach((button) => {
    button.addEventListener("click", () => {
      scriptViewMode = button.dataset.scriptView;
      renderScriptPreview(container, script);
    });
  });
  container.querySelector("#editScriptButton")?.addEventListener("click", () => {
    scriptEditMode = true;
    renderScriptPreview(container, script);
  });
  container.querySelector("#openScriptSnippetEditorButton")?.addEventListener("click", () => {
    scriptEditMode = true;
    renderScriptPreview(container, script);
    window.setTimeout(() => {
      container.querySelector("#scriptEditText")?.focus();
      container.querySelector(".script-snippet-bar")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }, 0);
  });
  container.querySelector("#configureScriptSnippetsButton")?.addEventListener("click", () => {
    showPage("settings");
    window.setTimeout(() => {
      const field = document.querySelector("#workspaceScriptSnippetInput");
      field?.scrollIntoView({ behavior: "smooth", block: "center" });
      field?.focus();
    }, 0);
  });
  container.querySelector("#copyTranscriptButton")?.addEventListener("click", async (event) => {
    await navigator.clipboard.writeText(script.full_text);
    event.currentTarget.textContent = "已複製";
    trackEvent("artifact.copied", { artifact_type: "script", source: "workbench" }, state.workflow?.id || null);
  });
}

function bindScriptEditor(container, script, snippets = []) {
  const editor = container.querySelector("#scriptEditText");
  const counter = container.querySelector("#scriptEditWordCount");
  const choice = container.querySelector("#scriptRevisionChoice");
  const prepareButton = container.querySelector("#prepareScriptSaveButton");
  editor.addEventListener("input", () => {
    counter.textContent = `${editor.value.replace(/\s/g, "").length} 字`;
    choice.classList.add("hidden");
    prepareButton.classList.remove("hidden");
  });
  const insertSnippet = (index) => {
    const snippet = snippets[index];
    if (!snippet) return;
    const start = editor.selectionStart ?? editor.value.length;
    const end = editor.selectionEnd ?? start;
    const before = editor.value.slice(0, start);
    const after = editor.value.slice(end);
    const prefix = before && !before.endsWith("\n") ? "\n\n" : "";
    const suffix = after && !after.startsWith("\n") ? "\n\n" : "";
    const inserted = `${prefix}${snippet}${suffix}`;
    editor.setRangeText(inserted, start, end, "end");
    editor.dispatchEvent(new Event("input", { bubbles: true }));
    editor.focus();
    const status = container.querySelector("#scriptSnippetStatus");
    if (status) status.textContent = `已插入：${snippet}`;
  };
  container.querySelectorAll("[data-script-snippet]").forEach((button) => button.addEventListener("click", () => insertSnippet(Number(button.dataset.scriptSnippet))));
  editor.addEventListener("keydown", (event) => {
    if (!event.altKey || event.ctrlKey || event.metaKey || !/^[1-9]$/.test(event.key)) return;
    const index = Number(event.key) - 1;
    if (!snippets[index]) return;
    event.preventDefault();
    insertSnippet(index);
  });
  container.querySelector("#cancelScriptEditButton").addEventListener("click", () => {
    scriptEditMode = false;
    renderScriptPreview(container, script);
  });
  prepareButton.addEventListener("click", () => {
    const revisedText = editor.value.trim();
    if (!revisedText) {
      showMessage("完整逐字稿不可為空白。", true);
      editor.focus();
      return;
    }
    if (revisedText === script.full_text.trim()) {
      showMessage("逐字稿內容沒有變更。", true);
      return;
    }
    choice.classList.remove("hidden");
    prepareButton.classList.add("hidden");
    choice.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  container.querySelector("#keepProductionButton").addEventListener("click", () => saveScriptRevision(editor.value, false));
  container.querySelector("#regenerateProductionButton").addEventListener("click", () => saveScriptRevision(editor.value, true));
}

async function saveScriptRevision(fullText, regenerateProduction) {
  if (!state.workflow || state.busy) return;
  setBusy(true, regenerateProduction ? "正在依新版逐字稿重新設計分鏡與 B-roll…" : "正在保存人工修訂稿…");
  showMessage(
    regenerateProduction
      ? "人工修訂稿正在存檔；製作規劃代理將依新版內容重新建立分鏡、視覺與 B-roll。"
      : "人工修訂稿正在存檔；原有分鏡與 B-roll 將保留，並標示為沿用舊版規劃。",
    false,
  );
  try {
    state.workflow = await api(`/api/v1/workflows/${encodeURIComponent(state.workflow.id)}/script`, {
      method: "PATCH",
      body: JSON.stringify({ full_text: fullText.trim(), regenerate_production: regenerateProduction }),
    });
    scriptEditMode = false;
    renderWorkflow(state.workflow);
    await loadHistory();
    showMessage(
      regenerateProduction
        ? "人工修訂稿已存檔，分鏡、視覺與 B-roll 已依新版重新產生；請重新完成最終核准。"
        : "人工修訂稿已存檔；目前沿用原製作規劃，請確認標示後重新完成最終核准。",
      false,
    );
  } catch (error) {
    showMessage(error.message || "無法保存人工修訂稿，請稍後重試。", true);
  } finally {
    setBusy(false);
  }
}

function emptyPreview(message) {
  return `<article class="preview-card"><h4>${escapeHtml(message)}</h4><p>完成後會自動顯示在此處。</p></article>`;
}

function latestActiveAgentLog(workflow) {
  const events = (workflow?.execution_log || []).filter((entry) =>
    entry.agent && ["agent.started", "agent.completed", "agent.failed"].includes(entry.event_type)
  );
  const latest = events.at(-1);
  return latest?.event_type === "agent.started" && latest.status === "running" ? latest : null;
}

function formatElapsed(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function progressDetail(agentName, elapsedSeconds) {
  if (!agentName) return "正在確認上一階段產出並準備下一個代理。";
  if (elapsedSeconds < 20) return `${agentLabel(agentName)}正在整理輸入與輸出格式。`;
  if (elapsedSeconds < 60) return `${agentLabel(agentName)}正在等待模型回傳，頁面可以保持開啟。`;
  if (elapsedSeconds < 120) return `${agentLabel(agentName)}正在處理較完整的內容，既有產出會持續保留。`;
  return `${agentLabel(agentName)}仍在完成大型結構化輸出與格式驗證，超過安全上限時會自動顯示可重試狀態。`;
}

function updateLiveWorkflowProgress(workflow, options = {}) {
  if (!liveProgressPanel || liveProgressPanel.classList.contains("hidden")) return;
  const activeLog = latestActiveAgentLog(workflow);
  const activeAgent = activeLog?.agent || null;
  const stage = workflow?.stage || "ai_creation";
  const sequence = stageAgentOrder[stage] || [];
  const completedAgents = new Set((workflow?.agent_results || []).map((result) => result.agent));
  const failedLog = [...(workflow?.execution_log || [])].reverse().find((entry) => entry.event_type === "agent.failed" && entry.agent);
  const activeIndex = activeAgent ? sequence.indexOf(activeAgent) : -1;
  const activeStartedAt = activeLog?.occurred_at ? new Date(activeLog.occurred_at).getTime() : liveProgressStartedAt;
  const activeElapsed = Math.max(0, (Date.now() - activeStartedAt) / 1000);
  const expected = agentExpectedSeconds[activeAgent] || 75;
  const withinAgent = Math.min(.88, activeElapsed / expected);
  const completedCount = sequence.filter((agent) => completedAgents.has(agent)).length;
  let percent = sequence.length
    ? Math.round(((activeIndex >= 0 ? activeIndex + withinAgent : completedCount) / sequence.length) * 100)
    : 8;
  percent = Math.max(4, Math.min(96, percent));
  if (["waiting_for_human", "completed"].includes(workflow?.status)) percent = 100;
  if (options.failed || workflow?.status === "failed") liveProgressPanel.classList.add("failed");

  document.querySelector("#liveProgressStage").textContent = `${stageLabel(stage)}｜即時進度`;
  document.querySelector("#liveProgressAgent").textContent = options.failed
    ? "目前階段暫時中斷"
    : activeAgent ? `${agentLabel(activeAgent)}執行中` : (options.heading || "正在準備代理工作");
  document.querySelector("#liveProgressElapsed").textContent = formatElapsed((Date.now() - liveProgressStartedAt) / 1000);
  document.querySelector("#liveProgressBar").style.width = `${percent}%`;
  document.querySelector("#liveProgressPercent").textContent = `${percent}%`;
  document.querySelector("#liveProgressDetail").textContent = options.detail || progressDetail(activeAgent, activeElapsed);

  if (activeAgent) setAgentState(activeAgent, "running");
  if (workflow?.status === "failed" && failedLog?.agent) setAgentState(failedLog.agent, "failed");
  completedAgents.forEach((agent) => setAgentState(agent, "completed"));
}

async function pollLiveWorkflowProgress(workflowId) {
  if (liveProgressPollPending || !workflowId || state.workflow?.id !== workflowId) return;
  liveProgressPollPending = true;
  try {
    const latest = await api(`/api/v1/workflows/${encodeURIComponent(workflowId)}`);
    if (state.workflow?.id !== workflowId) return;
    if ((latest.revision_count || 0) >= (state.workflow.revision_count || 0)) {
      state.workflow = latest;
      renderWorkflow(latest);
      updateLiveWorkflowProgress(latest);
    }
  } catch {
    // The main mutation request remains authoritative; a missed poll is retried.
  } finally {
    liveProgressPollPending = false;
  }
}

function beginLiveWorkflowProgress(workflow, heading = "正在啟動 AI 工作流") {
  if (!liveProgressPanel || !workflow) return;
  if (liveProgressTimer) window.clearInterval(liveProgressTimer);
  if (liveProgressPollTimer) window.clearInterval(liveProgressPollTimer);
  if (liveProgressHideTimer) window.clearTimeout(liveProgressHideTimer);
  liveProgressStartedAt = Date.now();
  liveProgressPanel.classList.remove("hidden", "failed");
  updateLiveWorkflowProgress(workflow, { heading });
  liveProgressTimer = window.setInterval(() => updateLiveWorkflowProgress(state.workflow), 1000);
  window.setTimeout(() => pollLiveWorkflowProgress(workflow.id), 900);
  liveProgressPollTimer = window.setInterval(() => pollLiveWorkflowProgress(workflow.id), 2500);
}

function endLiveWorkflowProgress(workflow) {
  if (liveProgressTimer) window.clearInterval(liveProgressTimer);
  if (liveProgressPollTimer) window.clearInterval(liveProgressPollTimer);
  liveProgressTimer = null;
  liveProgressPollTimer = null;
  updateLiveWorkflowProgress(workflow);
  if (!liveProgressPanel || liveProgressPanel.classList.contains("failed") || workflow?.status === "failed") return;
  document.querySelector("#liveProgressAgent").textContent = workflow?.status === "waiting_for_human" ? "本階段完成，等待你的確認" : "本階段已完成";
  document.querySelector("#liveProgressDetail").textContent = "最新成果與執行紀錄已自動刷新。";
  document.querySelector("#liveProgressBar").style.width = "100%";
  document.querySelector("#liveProgressPercent").textContent = "100%";
  liveProgressHideTimer = window.setTimeout(() => liveProgressPanel.classList.add("hidden"), 2200);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function setStage(stage) {
  const currentIndex = stageOrder.indexOf(stage);
  document.querySelectorAll(".stage-item").forEach((item) => {
    const index = stageOrder.indexOf(item.dataset.stage);
    item.classList.toggle("active", index === currentIndex);
    item.classList.toggle("done", index < currentIndex || state.workflow?.status === "completed");
    const number = item.querySelector(".stage-number");
    number.textContent = item.classList.contains("done") ? "✓" : String(index + 1);
  });
}

function setAgentState(agentName, status) {
  const row = document.querySelector(`[data-agent="${agentName}"]`);
  if (!row) return;
  row.classList.remove("running", "completed", "skipped", "failed");
  row.classList.add(status);
  row.querySelector(".agent-state").textContent = ({ running: "執行中", completed: "完成", skipped: "未使用", failed: "失敗" })[status] || status;
}

function resetAgentStates() {
  document.querySelectorAll(".agent-row").forEach((row) => {
    row.classList.remove("running", "completed", "skipped", "failed");
    row.querySelector(".agent-state").textContent = row.dataset.agent === "reference_analyst" ? "依需求" : "待命";
  });
}

function getWorkbenchDraftKey(handoff = state.pendingOpportunityHandoff) {
  const generationId = handoff?.sourceGenerationId || "standalone";
  const opportunityId = handoff?.sourceOpportunityId || "new";
  const revision = Number(handoff?.sourceRevision ?? 0);
  return [workbenchDraftStoragePrefix, state.workspaceId, generationId, opportunityId, revision]
    .map((part) => encodeURIComponent(String(part)))
    .join(":");
}

function captureWorkbenchDraft() {
  const fields = Object.fromEntries(workbenchDraftFieldIds.map((id) => [id, document.querySelector(`#${id}`).value]));
  return {
    version: 2,
    workspaceId: state.workspaceId,
    updatedAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString(),
    source: state.pendingOpportunityHandoff ? { ...state.pendingOpportunityHandoff } : null,
    fields,
    platforms: [...document.querySelectorAll(".platform-chip input:checked")].map((input) => input.value),
    referenceSectionOpen: document.querySelector("#referenceSection").open,
    // 未建立任務前只保存檔名等中繼資料，不把可能含敏感資訊的檔案內容寫入 localStorage。
    referenceFiles: draftReferenceFiles.map((file) => ({ name: file.name, size: file.size, lastModified: file.lastModified })),
  };
}

function markWorkbenchDraftDirty() {
  if (draftRestoring || state.workflow) return;
  draftDirty = true;
  if (draftSaveTimer) window.clearTimeout(draftSaveTimer);
  draftSaveTimer = window.setTimeout(() => saveWorkbenchDraft(), 180);
}

function saveWorkbenchDraft() {
  if (!draftDirty || state.workflow) return false;
  if (draftSaveTimer) window.clearTimeout(draftSaveTimer);
  draftSaveTimer = null;
  try {
    window.localStorage.setItem(getWorkbenchDraftKey(), JSON.stringify(captureWorkbenchDraft()));
    draftDirty = false;
    return true;
  } catch {
    draftDirty = false;
    showMessage("瀏覽器無法保存這份草稿；請先建立任務，或移除較大的參考檔案後再試。", true);
    return false;
  }
}

function clearWorkbenchDraft(key = getWorkbenchDraftKey()) {
  if (draftSaveTimer) window.clearTimeout(draftSaveTimer);
  draftSaveTimer = null;
  try {
    window.localStorage.removeItem(key);
  } catch {
    // localStorage may be unavailable in a locked-down browser; the live form still works.
  }
}

function restoreWorkbenchDraft() {
  let draft;
  try {
    const serialized = window.localStorage.getItem(getWorkbenchDraftKey());
    if (!serialized) return false;
    draft = JSON.parse(serialized);
  } catch {
    clearWorkbenchDraft();
    return false;
  }
  const source = state.pendingOpportunityHandoff;
  const storedSource = draft?.source;
  const sourceMatches = source
    ? storedSource
      && storedSource.sourceGenerationId === source.sourceGenerationId
      && storedSource.sourceOpportunityId === source.sourceOpportunityId
      && Number(storedSource.sourceRevision ?? 0) === Number(source.sourceRevision ?? 0)
    : !storedSource;
  const expired = !draft?.expiresAt || Date.parse(draft.expiresAt) <= Date.now();
  if (draft?.version !== 2 || draft.workspaceId !== state.workspaceId || !sourceMatches || !draft.fields || expired) {
    clearWorkbenchDraft();
    return false;
  }

  draftRestoring = true;
  workbenchDraftFieldIds.forEach((id) => {
    if (Object.prototype.hasOwnProperty.call(draft.fields, id)) document.querySelector(`#${id}`).value = draft.fields[id];
  });
  const platforms = Array.isArray(draft.platforms) ? draft.platforms : [];
  document.querySelectorAll(".platform-chip").forEach((chip) => {
    const selected = platforms.includes(chip.querySelector("input").value);
    chip.querySelector("input").checked = selected;
    chip.classList.toggle("selected", selected);
  });
  const storedFileMetadata = Array.isArray(draft.referenceFiles)
    ? draft.referenceFiles.filter((file) => file && file.name).slice(0, 10)
    : [];
  draftReferenceFiles = [];
  renderReferenceFileReview();
  document.querySelector("#referenceFileList").textContent = storedFileMetadata.length
    ? `為保護資料，請重新選擇：${storedFileMetadata.map((file) => file.name).join("、")}`
    : "尚未選擇檔案";
  document.querySelector("#referenceSection").toggleAttribute("open", Boolean(draft.referenceSectionOpen));
  wordCountTouched = true;
  draftDirty = false;
  updateEstimatedDuration();
  window.queueMicrotask(() => { draftRestoring = false; });
  return true;
}

function readPendingAdoptionSyncs() {
  try {
    const value = JSON.parse(window.localStorage.getItem(adoptionRetryStorageKey()) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

function savePendingAdoptionSync(handoff, workflowId) {
  const record = { handoff: { ...handoff }, workflowId, savedAt: new Date().toISOString() };
  const records = readPendingAdoptionSyncs();
  records[workflowId] = record;
  try {
    window.localStorage.setItem(adoptionRetryStorageKey(), JSON.stringify(records));
  } catch {
    // Keep the compensation action available for this session even if storage is unavailable.
  }
  state.pendingAdoptionSync = record;
  return record;
}

function getPendingAdoptionSync(workflowId) {
  if (state.pendingAdoptionSync?.workflowId === workflowId) return state.pendingAdoptionSync;
  const record = readPendingAdoptionSyncs()[workflowId] || null;
  state.pendingAdoptionSync = record;
  return record;
}

function clearPendingAdoptionSync(workflowId) {
  const records = readPendingAdoptionSyncs();
  delete records[workflowId];
  try {
    if (Object.keys(records).length) window.localStorage.setItem(adoptionRetryStorageKey(), JSON.stringify(records));
    else window.localStorage.removeItem(adoptionRetryStorageKey());
  } catch {
    // The server update already succeeded, so a storage cleanup failure is non-blocking.
  }
  if (state.pendingAdoptionSync?.workflowId === workflowId) state.pendingAdoptionSync = null;
}

async function retryPendingAdoptionSync(record, button) {
  button.disabled = true;
  button.textContent = "同步中…";
  try {
    await syncAdoptedOpportunity(record.handoff, record.workflowId);
    clearPendingAdoptionSync(record.workflowId);
    showMessage("選題已成功同步為「已採用」，不需要重新建立任務。", false);
  } catch (error) {
    showAdoptionSyncWarning(record, `再次同步失敗：${error.message || "請確認服務連線後再試。"}`);
  }
}

function showAdoptionSyncWarning(record, prefix = "") {
  const context = prefix ? `${prefix} ` : "";
  showMessage(
    `${context}任務 ${record.workflowId} 已保存；選題的「已採用」狀態尚未同步，不要重新建立任務。`,
    true,
    { label: "重試同步已採用", onClick: (button) => retryPendingAdoptionSync(record, button) },
  );
}

function workflowFailureSummary(workflow) {
  const logs = [...(workflow.execution_log || [])].reverse();
  const failedLog = logs.find((entry) => entry.status === "failed" || String(entry.event_type || "").includes("failed"));
  if (failedLog?.summary) return failedLog.summary;
  const failedAgent = [...(workflow.agent_results || [])].reverse().find((result) => result.status === "failed");
  return failedAgent?.summary || "AI 執行未完成，請查看執行紀錄確認失敗階段。";
}

function showWorkflowFailedMessage(workflow) {
  const summary = workflowFailureSummary(workflow);
  showMessage(
    `AI 執行失敗：${summary} 任務與既有產出已保存，可從失敗的階段直接重試。`,
    true,
    {
      label: "重試目前階段",
      onClick: (button) => retryFailedWorkflow(workflow, button),
    },
  );
}

async function retryFailedWorkflow(workflow, button) {
  if (state.busy || state.workflow?.id !== workflow.id) return;
  setBusy(true, "正在重試失敗階段…");
  button.disabled = true;
  button.textContent = "重試中…";
  try {
    await runWorkflow({ retryFailed: true });
    await loadHistory();
    if (state.workflow?.status === "failed") showWorkflowFailedMessage(state.workflow);
    else if (state.workflow?.status === "waiting_for_human") renderWorkflow(state.workflow);
    else showMessage("已重新啟動並完成失敗階段。", false);
  } catch (error) {
    showMessage(
      `目前階段仍無法完成：${error.message || "請確認服務連線後再試。"}`,
      true,
      { label: "再次重試", onClick: (retryButton) => retryFailedWorkflow(workflow, retryButton) },
    );
  } finally {
    setBusy(false);
  }
}

function resetInterface() {
  if (liveProgressTimer) window.clearInterval(liveProgressTimer);
  if (liveProgressPollTimer) window.clearInterval(liveProgressPollTimer);
  if (liveProgressHideTimer) window.clearTimeout(liveProgressHideTimer);
  liveProgressTimer = null;
  liveProgressPollTimer = null;
  liveProgressHideTimer = null;
  liveProgressPanel?.classList.add("hidden");
  liveProgressPanel?.classList.remove("failed");
  draftRestoring = true;
  if (draftSaveTimer) window.clearTimeout(draftSaveTimer);
  draftSaveTimer = null;
  draftReferenceFiles = [];
  draftDirty = false;
  state.workflow = null;
  state.pendingOpportunityHandoff = null;
  state.pendingAdoptionSync = null;
  form.reset();
  document.querySelector("#brandVoice").value = "專業、清楚，但保有自然的對話感";
  document.querySelector("#referenceFileList").textContent = "尚未選擇檔案";
  renderReferenceFileReview();
  document.querySelector("#referenceSection").removeAttribute("open");
  document.querySelectorAll(".platform-chip").forEach((chip, index) => {
    chip.querySelector("input").checked = index === 0;
    chip.classList.toggle("selected", index === 0);
  });
  resetAgentStates();
  setStage("requirements");
  decisionPanel.classList.add("hidden");
  resultPanel.classList.add("hidden");
  document.querySelector("#artifactFeedbackNote").value = "";
  document.querySelector("#artifactFeedbackStatus").textContent = "";
  document.querySelectorAll("[data-feedback-rating]").forEach((button) => button.classList.remove("selected"));
  executionLogPanel.classList.add("hidden");
  historyPanel.classList.add("hidden");
  setExecutionLogCollapsed(true);
  activePreviewTab = "script";
  scriptViewMode = "full";
  scriptEditMode = false;
  rhythmViewMode = "timeline";
  rhythmEditMode = false;
  rhythmDraftCues = [];
  wordCountTouched = false;
  document.querySelector("#targetWordCount").value = "500";
  updateEstimatedDuration();
  document.querySelectorAll(".preview-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === "script"));
  messageBar.classList.remove("show", "error");
  document.querySelector("#topic").focus();
  window.queueMicrotask(() => { draftRestoring = false; });
}

function setBusy(busy, label = "") {
  state.busy = busy;
  submitButton.disabled = busy;
  submitButton.querySelector("span").textContent = busy ? label : "開始 AI 製作";
}

function showMessage(message, isError = false, action = null) {
  messageBar.replaceChildren();
  const text = document.createElement("span");
  text.textContent = message;
  messageBar.append(text);
  if (action?.label && typeof action.onClick === "function") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "message-action";
    button.textContent = action.label;
    button.addEventListener("click", () => action.onClick(button));
    messageBar.append(button);
  }
  messageBar.classList.toggle("error", Boolean(isError));
  messageBar.classList.add("show");
}

function validationFieldLabel(location) {
  const key = [...(Array.isArray(location) ? location : [])].reverse().find((part) => typeof part === "string" && !["body", "query", "path", "header"].includes(part));
  return ({
    topic: "內容主題", goal: "內容目標", platforms: "發布平台", output_type: "輸出形式",
    target_word_count: "目標字數", brand_voice: "品牌語氣", constraints: "限制與注意事項",
    reference_materials: "參考資料", reference_boundary: "參考使用邊界", workspace_id: "工作空間",
    source_opportunity_id: "來源題目", source_generation_id: "來源探索紀錄", status: "狀態",
    feedback_note: "回饋備註", adopted_workflow_id: "採用任務",
    invite_code: "邀請碼", display_name: "顯示名稱", email: "電子郵件", password: "密碼",
  })[key] || key || "輸入內容";
}

function translateValidationMessage(message, type = "") {
  const text = String(message || "").trim();
  if (!text) return "格式不正確";
  const knownMessages = {
    "value must contain non-whitespace characters": "不可只包含空白",
    "platforms must contain at least one non-whitespace value": "請至少選擇一個有效平台",
    "source id must not be blank": "來源識別碼不可空白",
    "source_opportunity_id and source_generation_id must be provided together": "來源題目與來源探索紀錄必須同時提供",
    "adopted_workflow_id must not be blank": "採用任務識別碼不可空白",
    "invalid regeneration request": "單題重做設定不正確",
    "invalid workspace id": "工作空間識別碼格式不正確",
    "workspace_id must match the authenticated workspace context": "工作空間與目前連線範圍不一致",
    "status, feedback_note or adopted_workflow_id is required": "請提供狀態、回饋備註或採用任務",
    "adopted_workflow_id is only valid when status is adopted": "只有標記為已採用時才能指定採用任務",
    "adopted_workflow_id is required when status is adopted": "標記為已採用時必須指定對應任務",
  };
  if (knownMessages[text]) return knownMessages[text];
  if (text === "Field required" || type === "missing") return "此欄位為必填";
  if (/at least 1 (item|character)/i.test(text) || /must not be blank/i.test(text)) return "不可空白";
  if (/valid integer|integer/i.test(text)) return "請輸入有效的整數";
  if (/greater than or equal to/i.test(text)) return "數值低於允許的最小值";
  if (/less than or equal to/i.test(text)) return "數值超過允許的最大值";
  if (/valid (url|list|string|dictionary|boolean)/i.test(text) || /input should be/i.test(text)) return "輸入格式不正確";
  if (/^value error,?\s*/i.test(text)) return translateValidationMessage(text.replace(/^value error,?\s*/i, ""), type);
  return text;
}

function readableApiDetail(detail, status) {
  if (typeof detail === "string" && detail.trim()) return translateValidationMessage(detail);
  if (Array.isArray(detail)) {
    const messages = detail.map((issue) => {
      if (typeof issue === "string") return issue;
      if (!issue || typeof issue !== "object") return "輸入內容格式不正確";
      return `${validationFieldLabel(issue.loc)}：${translateValidationMessage(issue.msg || issue.message, issue.type)}`;
    }).filter(Boolean);
    if (messages.length) return `請修正以下內容：${messages.join("；")}`;
  }
  if (detail && typeof detail === "object") {
    if (Array.isArray(detail.errors) && detail.errors.length) {
      const errors = readableApiDetail(detail.errors, status);
      const summary = translateValidationMessage(detail.message || detail.msg, detail.type);
      return detail.message || detail.msg ? `${summary}：${errors.replace(/^請修正以下內容：/, "")}` : errors;
    }
    if (detail.message || detail.msg) return translateValidationMessage(detail.message || detail.msg, detail.type);
    const messages = Object.entries(detail).map(([key, value]) => {
      const normalized = typeof value === "string" ? value : JSON.stringify(value);
      return `${validationFieldLabel([key])}：${normalized}`;
    });
    if (messages.length) return `請修正以下內容：${messages.join("；")}`;
  }
  return status === 422 ? "送出的內容格式不正確，請檢查必填欄位。" : `請求失敗（${status}）`;
}

async function api(url, options = {}) {
  let response;
  try {
    const method = String(options.method || "GET").toUpperCase();
    const csrfHeaders = !["GET", "HEAD", "OPTIONS"].includes(method) && state.authContext?.csrf_token
      ? { "X-CSRF-Token": state.authContext.csrf_token }
      : {};
    response = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers: { "Content-Type": "application/json", "X-Workspace-ID": state.workspaceId, ...csrfHeaders, ...(options.headers || {}) },
    });
  } catch (error) {
    setEngineStatus("offline");
    throw new Error("無法連線本機 Python 服務。請確認服務已啟動，再按「重新連線」。");
  }
  setEngineStatus("healthy");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && state.authStatus?.authentication_required) {
      state.authContext = null;
      showAuthScreen();
    }
    const error = new Error(readableApiDetail(data?.detail ?? data, response.status));
    error.status = response.status;
    throw error;
  }
  return data;
}

async function checkHealth(showFeedback = false) {
  if (showFeedback || state.engineStatus !== "healthy") setEngineStatus("checking");
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch("/health", { signal: controller.signal, cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.healthInfo = await response.json().catch(() => ({}));
    setEngineStatus("healthy");
    const opportunityBadge = document.querySelector("#opportunityProviderBadge");
    if (opportunityBadge) {
      opportunityBadge.textContent = state.opportunityResult ? "100%" : "0%";
    }
    if (state.currentPage === "settings") renderSettings();
    if (showFeedback && state.currentPage === "opportunities") showOpportunityMessage("Python 服務連線正常，可以繼續產生或讀取紀錄。", false);
    return true;
  } catch (error) {
    setEngineStatus("offline");
    if (showFeedback && state.currentPage === "opportunities") showOpportunityMessage("仍無法連線本機 Python 服務。請先啟動服務，再按「重新連線」。", true);
    return false;
  } finally {
    window.clearTimeout(timeout);
  }
}

function setEngineStatus(status) {
  state.engineStatus = status;
  const container = document.querySelector("#engineStatus");
  if (!container) return;
  container.classList.remove("checking", "healthy", "offline");
  container.classList.add(status);
  document.querySelector("#engineStatusText").textContent = ({ checking: "正在檢查本機服務…", healthy: "本機服務運作中", offline: "服務離線，仍可查看已載入內容" })[status];
  document.querySelector("#engineReconnectButton").classList.toggle("hidden", status !== "offline");
}

function delay(ms) { return new Promise((resolve) => window.setTimeout(resolve, ms)); }

async function collectReferenceMaterials() {
  const materials = [];
  const focus = document.querySelector("#referenceFocus").value;
  const brandBrief = document.querySelector("#brandBrief").value.trim();
  const referenceText = document.querySelector("#referenceText").value.trim();
  const referenceType = document.querySelector("#referenceTextType").value;
  const urls = document.querySelector("#referenceUrls").value.split("\n").map((item) => item.trim()).filter(Boolean);

  if (brandBrief) {
    materials.push({ name: "品牌 Brief", kind: "brand_brief", content: brandBrief, focus: "品牌方向與不可違反事項" });
  }
  if (referenceText) {
    materials.push({ name: "貼上的參考文字", kind: referenceType, content: referenceText, focus });
  }
  urls.forEach((sourceUrl, index) => {
    materials.push({ name: `參考網址 ${index + 1}`, kind: "creator_reference", source_url: sourceUrl, focus });
  });

  const failedFiles = draftReferenceFiles.filter((file) => file.error);
  if (failedFiles.length) throw new Error(`請先移除或重新上傳解析失敗的文件：${failedFiles.map((file) => file.name).join("、")}`);
  const pendingFiles = draftReferenceFiles.filter((file) => !file.approved);
  if (pendingFiles.length) throw new Error(`請先確認是否納入這些文件：${pendingFiles.map((file) => file.name).join("、")}`);
  for (const file of draftReferenceFiles) {
    const kind = file.kind || "general";
    materials.push({ name: file.name, kind, content: file.content, focus: kind === "brand_brief" ? "品牌方向與不可違反事項" : focus });
  }
  return materials.slice(0, 20);
}

function updateEstimatedDuration() {
  const wordCount = Math.max(0, Number(document.querySelector("#targetWordCount").value) || 0);
  const totalSeconds = Math.round((wordCount / 220) * 60);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  document.querySelector("#estimatedDuration").textContent = minutes ? `約 ${minutes} 分 ${seconds} 秒` : `約 ${seconds} 秒`;
}

async function initializeApp() {
  const authenticated = await initializeAuthentication();
  if (!authenticated) return;
  await startAuthenticatedApplication();
}

async function startAuthenticatedApplication() {
  updateEstimatedDuration();
  const route = routeFromLocation();
  showPage(route.page, { updateUrl: false, generationId: route.generationId, itemId: route.itemId, workflowId: route.workflowId });
  await Promise.allSettled([loadHistory(), checkHealth(), loadUsage(), loadWorkspaceAIProfile()]);
  await restoreRouteState(route);
  if (!healthPollTimer) healthPollTimer = window.setInterval(() => checkHealth(), 15000);
  applicationStarted = true;
}

window.addEventListener("popstate", async () => {
  const route = routeFromLocation();
  showPage(route.page, { updateUrl: false, generationId: route.generationId, itemId: route.itemId, workflowId: route.workflowId });
  await restoreRouteState(route);
});

window.addEventListener("beforeunload", () => {
  if (draftDirty) saveWorkbenchDraft();
  if (healthPollTimer) window.clearInterval(healthPollTimer);
});

initializeApp();
