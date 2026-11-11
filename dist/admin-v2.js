(() => {
  const $ = id => document.getElementById(id);
  const state = {
    admin: null,
    role: "",
    roles: [],
    view: "dashboard",
    dashboard: null,
    questions: [], questionPage: 1, questionTotal: 0,
    exams: [], users: [], userPage: 1, userTotal: 0,
    codes: [], content: [], feedback: [], admins: [], audit: [], auditPage: 1,
  };
  const rolePermissions = {
    super_admin: ["dashboard", "questions", "exams", "users", "content", "permissions", "system"],
    question_admin: ["dashboard", "questions", "exams"],
    support_admin: ["dashboard", "users", "content"],
  };
  const roleLabels = { super_admin: "超级管理员", question_admin: "题库管理员", support_admin: "普通管理员" };
  const $all = selector => [...document.querySelectorAll(selector)];
  let drawerOpener = null;

  async function api(path, options = {}) {
    const init = { method: options.method || "GET", credentials: "same-origin", headers: {} };
    if (options.form) init.body = options.form;
    else if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    const response = await fetch(path, init);
    const payload = await response.json().catch(() => ({ ok: false, error: { message: "服务器返回了无效数据" } }));
    if (!response.ok || payload.ok === false) {
      const detail = typeof payload.error === "string" ? payload.error : payload.error?.message;
      const error = new Error(detail || "请求失败");
      error.status = response.status;
      error.code = payload.error?.code;
      error.requestId = payload.requestId;
      throw error;
    }
    return payload.data ?? payload;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  }
  function query(params) {
    const result = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => { if (value !== "" && value !== undefined && value !== null) result.set(key, value); });
    return result.toString();
  }
  function formatTime(value) {
    if (!value) return "暂无";
    return new Date(Number(value) * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
  }
  function percent(correct, total) { return total ? `${Math.round(Number(correct || 0) / Number(total) * 100)}%` : "数据不足"; }
  function toast(message, bad = false) {
    const node = $("toast"); node.textContent = message; node.style.background = bad ? "var(--red)" : "var(--cyan)"; node.classList.add("show");
    clearTimeout(toast.timer); toast.timer = setTimeout(() => node.classList.remove("show"), 3400);
  }
  function setTheme(theme) { window.NFAppearance.setMode(theme); }
  function empty(message = "暂无数据") { return `<div class="empty-slot"><p class="muted">${escapeHtml(message)}</p></div>`; }
  function statusBadge(status) {
    const labels = { active: "正常", disabled: "已禁用", deleted: "已软删除", published: "已发布", draft: "草稿", archived: "已下架", unpublished: "已下架", open: "待处理", processing: "处理中", resolved: "已解决", closed: "已关闭", revoked: "已撤销", expired: "已过期", exhausted: "已用完", verified: "已验证", invalid: "校验失败" };
    const tone = ["active", "published", "resolved", "verified"].includes(status) ? "good" : ["disabled", "draft", "processing", "open", "expired", "exhausted"].includes(status) ? "warn" : "bad";
    return `<span class="status ${tone}">${escapeHtml(labels[status] || status || "未知")}</span>`;
  }
  function can(permission) { return state.roles.some(role => (rolePermissions[role] || []).includes(permission)); }
  function handleError(error) {
    if (error.status === 401) { showLogin(); return; }
    toast(`${error.message}${error.requestId ? `（请求 ${error.requestId}）` : ""}`, true);
  }

  function showLogin() { state.admin = null; state.role = ""; state.roles = [];window.NFAppearance.reset();closeDrawers(); $("adminApp").classList.add("hidden"); $("loginView").classList.remove("hidden"); }
  function showApp() {
    window.NFAppearance.load('admin',state.admin.id);
    $('adminPersonalIdentity').textContent=`${state.admin.displayName||state.admin.username} · ${state.roles.map(x=>roleLabels[x]||x).join('、')}`;
    $("loginView").classList.add("hidden"); $("adminApp").classList.remove("hidden");
    $("adminIdentity").textContent = state.admin?.displayName || state.admin?.username || "管理员";
    $("adminRole").textContent = state.roles.map(role => roleLabels[role] || role).join("、") || "角色待确认";
    $all("[data-permission]").forEach(node => node.classList.toggle("hidden", !can(node.dataset.permission)));
    const firstAllowed = $all(".nav-item").find(node => !node.classList.contains("hidden"));
    showView(can(state.view) ? state.view : firstAllowed?.dataset.view || "dashboard");
  }
  function showView(name) {
    if (!can(name)) return toast("当前角色无权访问该模块", true);
    state.view = name;
    $all(".view").forEach(node => node.classList.toggle("active", node.id === `view-${name}`));
    $all(".nav-item").forEach(node => { const active=node.dataset.view===name;node.classList.toggle("active",active);active?node.setAttribute("aria-current","page"):node.removeAttribute("aria-current"); });
    const view = $(`view-${name}`); $("viewTitle").textContent = view.dataset.title; $("viewKicker").textContent = view.dataset.kicker;
    document.querySelector(".sidebar").classList.remove("open"); $("overlay").classList.add("hidden");
    loadCurrent();
  }
  function showDrawer(id) { const drawer=$(id);if(drawer.classList.contains('hidden'))drawerOpener=document.activeElement;drawer.setAttribute("role","dialog");drawer.setAttribute("aria-modal","true");drawer.classList.remove("hidden");$("overlay").classList.remove("hidden");requestAnimationFrame(()=>drawer.querySelector("input:not([type=hidden]),textarea,select,button")?.focus()); }
  function closeDrawers() { const hadOpen=$all(".drawer").some(node=>!node.classList.contains("hidden"));$all(".drawer").forEach(node=>node.classList.add("hidden"));$("overlay").classList.add("hidden");document.querySelector(".sidebar").classList.remove("open");if(hadOpen&&drawerOpener?.isConnected)drawerOpener.focus();drawerOpener=null; }

  async function loadCurrent() {
    const loaders = { dashboard: loadDashboard, questions: loadQuestions, exams: loadExams, users: loadUsersAndCodes, content: loadContentAndFeedback, permissions: loadPermissions, system: loadSystem };
    try { await loaders[state.view]?.(); $("updatedAt").textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}`; } catch (error) { handleError(error); }
  }

  async function loadDashboard() {
    const data = await api("/api/v2/admin/dashboard"); state.dashboard = data;
    const totals = data.totals || {};
    const metrics = [
      ["总用户", totals.users], ["今日新增", totals.newUsersToday], ["今日活跃", totals.dau], ["累计答题", totals.answers],
      ["今日答题", totals.answersToday], ["题库题数", totals.questions], ["模拟考试", totals.examAttempts], ["待处理反馈", totals.openFeedback],
    ];
    $("dashboardMetrics").innerHTML = metrics.map(([label, value]) => `<article class="metric-card"><span>${label}</span><b>${value === undefined ? "—" : Number(value).toLocaleString("zh-CN")}</b><small>真实数据</small></article>`).join("");
    const daily = data.daily || [];
    const maxDaily = Math.max(1, ...daily.map(item => Number(item.answers || 0)));
    $("dailyTrend").innerHTML = daily.length ? daily.map(item => `<div class="trend-row"><span>${escapeHtml(item.day)}</span><div class="track"><i style="width:${Math.min(100, Number(item.answers || 0) / maxDaily * 100)}%"></i></div><small>${Number(item.answers || 0)} 题</small></div>`).join("") : empty("暂无近 14 日活动数据");
    const topics = data.topics || [];
    $("topicQuality").innerHTML = topics.length ? topics.map(item => { const rate = item.answered ? Math.round(item.correct / item.answered * 100) : 0; return `<div class="bar-row"><span>${escapeHtml(item.topic)}</span><div class="track"><i style="width:${rate}%"></i></div><small>${item.answered ? `${rate}%` : "数据不足"}</small></div>`; }).join("") : empty("暂无知识点统计");
    const signals = data.riskSignals || [];
    $("riskSignals").innerHTML = signals.length ? `<table><thead><tr><th>题目</th><th>异常信号</th><th>样本量</th><th>状态</th></tr></thead><tbody>${signals.map(item => `<tr><td>${escapeHtml(item.questionId)}</td><td>${escapeHtml(item.reason)}</td><td>${Number(item.sampleSize || 0)}</td><td>${statusBadge(item.status || "open")}</td></tr>`).join("")}</tbody></table>` : empty("暂无需要人工复核的异常信号");
  }

  async function loadQuestions() {
    const data = await api(`/api/v2/admin/questions?${query({ page: state.questionPage, pageSize: 20, q: $("questionSearch").value.trim(), status: $("questionStatus").value })}`);
    state.questions = data.items || []; state.questionTotal = Number(data.total || 0);
    $("questionTable").innerHTML = state.questions.length ? `<table><thead><tr><th>ID / 题干</th><th>题型</th><th>知识点</th><th>难度</th><th>状态</th><th>版本</th><th>操作</th></tr></thead><tbody>${state.questions.map(item => `<tr><td><strong>${escapeHtml(item.id)}</strong><small>${escapeHtml(item.question)}</small></td><td>${escapeHtml(item.type)}</td><td>${escapeHtml(item.topic || "未分类")}</td><td>${escapeHtml(item.difficulty || "未设置")}</td><td>${statusBadge(item.status)}</td><td>v${Number(item.revision || 1)}</td><td><div class="row-actions"><button data-question-edit="${escapeHtml(item.id)}">编辑</button><button data-question-history="${escapeHtml(item.id)}">版本</button><button class="danger" data-question-status="${escapeHtml(item.id)}" data-next-status="${item.status === "archived" ? "published" : "archived"}">${item.status === "archived" ? "恢复" : "下架"}</button></div></td></tr>`).join("")}</tbody></table>` : empty("没有符合条件的题目");
    renderPager("questionPager", state.questionPage, state.questionTotal, page => { state.questionPage = page; loadQuestions().catch(handleError); });
  }
  function renderPager(id, page, total, onChange) {
    const pages = Math.max(1, Math.ceil(total / 20)); const node = $(id);
    node.innerHTML = `<button data-page="prev" ${page <= 1 ? "disabled" : ""}>上一页</button><span>${page} / ${pages} · ${total} 条</span><button data-page="next" ${page >= pages ? "disabled" : ""}>下一页</button>`;
    node.querySelectorAll("button").forEach(button => button.addEventListener("click", () => onChange(button.dataset.page === "prev" ? page - 1 : page + 1)));
  }
  function openQuestion(item = null) {
    const form = $("questionForm"); form.reset(); form.elements.id.value = item?.id || ""; form.elements.question.value = item?.question || ""; form.elements.type.value = item?.type || "single"; form.elements.status.value = item?.status || "draft"; form.elements.topic.value = item?.topic || ""; form.elements.difficulty.value = item?.difficulty || "medium"; form.elements.options.value = (item?.options || []).map(option => `${option.key}|${option.text}`).join("\n"); form.elements.answer.value = (item?.answer || []).join(","); form.elements.source.value = item?.source || ""; form.elements.explanation.value = item?.explanation || ""; form.elements.references.value = (item?.references || []).join("\n"); $("questionEditorTitle").textContent = item ? `编辑 ${item.id}` : "新增题目"; showDrawer("questionEditor");
  }
  async function openQuestionHistory(questionId) {
    const data = await api(`/api/v2/admin/questions/${encodeURIComponent(questionId)}/revisions`);
    $("revisionTitle").textContent = `${questionId} · 版本历史`;
    const items = data.items || [];
    $("revisionList").innerHTML = items.length ? `<table><thead><tr><th>版本</th><th>时间</th><th>操作者</th><th>变更说明</th></tr></thead><tbody>${items.map(item => `<tr><td>v${Number(item.version)}</td><td>${formatTime(item.createdAt)}</td><td>${escapeHtml(item.createdBy)}</td><td>${escapeHtml(item.changeSummary || "—")}</td></tr>`).join("")}</tbody></table>` : empty("暂无版本记录");
    showDrawer("revisionDrawer");
  }
  function questionPayload(form) {
    const values = Object.fromEntries(new FormData(form).entries());
    const options = values.options.split(/\r?\n/).filter(Boolean).map((line, row) => { const index = line.indexOf("|"); if (index <= 0 || !line.slice(index + 1).trim()) throw new Error(`第 ${row + 1} 个选项格式错误，应为“键|内容”`); return { key: line.slice(0, index).trim().toUpperCase(), text: line.slice(index + 1).trim() }; });
    const answer = values.answer.split(/[,，、]/).map(x => x.trim().toUpperCase()).filter(Boolean);
    if (options.length < 2) throw new Error("题目至少需要两个选项");
    if (new Set(options.map(x => x.key)).size !== options.length) throw new Error("选项键不能重复");
    if (!answer.length || answer.some(key => !options.some(option => option.key === key))) throw new Error("答案必须引用已有选项");
    if (["single", "judge"].includes(values.type) && answer.length !== 1) throw new Error("单选题和判断题只能有一个答案");
    if (values.type === "multiple" && answer.length < 2) throw new Error("多选题至少需要两个答案");
    return { question: values.question.trim(), type: values.type, status: values.status, topic: values.topic.trim(), difficulty: values.difficulty, source: values.source.trim(), explanation: values.explanation.trim(), references: values.references.split(/\r?\n/).map(x=>x.trim()).filter(Boolean), answer, options };
  }

  async function loadExams() {
    const data = await api(`/api/v2/admin/exam-papers?${query({ page: 1, pageSize: 50, q: $("examSearch").value.trim() })}`); state.exams = data.items || [];
    $("examTable").innerHTML = state.exams.length ? `<table><thead><tr><th>试卷</th><th>题量</th><th>时长</th><th>及格线</th><th>参与 / 均分</th><th>状态</th><th>操作</th></tr></thead><tbody>${state.exams.map(item => `<tr><td><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.id)}</small></td><td>${Number(item.questionCount || 0)}</td><td>${Number(item.durationMinutes || 0)} 分钟</td><td>${Number(item.passPercent || 0)}%</td><td>${Number(item.attempts || 0)} / ${item.averageScore ?? "—"}</td><td>${statusBadge(item.status)}</td><td><div class="row-actions"><button data-exam-copy="${escapeHtml(item.id)}">复制</button><button class="danger" data-exam-publish="${escapeHtml(item.id)}" data-action="${item.status === "published" ? "unpublish" : "publish"}">${item.status === "published" ? "下架" : "发布"}</button></div></td></tr>`).join("")}</tbody></table>` : empty("暂无试卷");
  }

  async function loadUsersAndCodes() { if(state.roles.includes("super_admin")) await Promise.all([loadUsers(),loadCodes()]); else { await loadUsers(); state.codes=[]; $("codeTable").innerHTML=empty("当前角色无权管理注册码"); } }
  async function loadUsers() {
    const data = await api(`/api/v2/admin/users?${query({ page: state.userPage, pageSize: 20, q: $("userSearch").value.trim(), status: $("userStatus").value })}`); state.users = data.items || []; state.userTotal = Number(data.total || 0);
    $("userTable").innerHTML = state.users.length ? `<table><thead><tr><th>用户</th><th>状态</th><th>注册 / 登录</th><th>练习</th><th>模拟考试</th><th>操作</th></tr></thead><tbody>${state.users.map(item => `<tr><td><strong>${escapeHtml(item.displayName || item.username)}</strong><small>@${escapeHtml(item.username)} · #${escapeHtml(item.id)}</small></td><td>${statusBadge(item.status || "active")}</td><td>${formatTime(item.createdAt)}<small>${formatTime(item.lastLoginAt)}</small></td><td>${Number(item.practiceAnswers || 0)} 题<small>${percent(item.practiceCorrect, item.practiceAnswers)}</small></td><td>${Number(item.examAttempts || 0)} 次</td><td>${!state.roles.includes("super_admin")?"仅查看":`<div class="row-actions">${item.status === "disabled" ? `<button data-user-action="restore" data-user="${escapeHtml(item.id)}">解禁</button>` : `<button class="danger" data-user-action="disable" data-user="${escapeHtml(item.id)}">禁用</button>`}<button data-user-action="reset-token" data-user="${escapeHtml(item.id)}">重置凭据</button>${item.status === "deleted" ? `<button data-user-action="restore" data-user="${escapeHtml(item.id)}">恢复</button>` : `<button class="danger" data-user-action="soft-delete" data-user="${escapeHtml(item.id)}">软删除</button>`}</div>`}</td></tr>`).join("")}</tbody></table>` : empty("没有符合条件的用户");
    renderPager("userPager", state.userPage, state.userTotal, page => { state.userPage = page; loadUsers().catch(handleError); });
  }
  async function loadCodes() {
    const data = await api("/api/v2/admin/registration-codes?page=1&pageSize=100"); state.codes = data.items || [];
    $("codeTable").innerHTML = state.codes.length ? `<table><thead><tr><th>注册码</th><th>备注</th><th>使用</th><th>有效期</th><th>状态</th><th>操作</th></tr></thead><tbody>${state.codes.map(item => `<tr><td><code>${escapeHtml(item.maskedCode || item.code || "—")}</code></td><td>${escapeHtml(item.label || "—")}</td><td>${Number(item.usedCount || 0)} / ${item.maxUses ?? "不限"}</td><td>${formatTime(item.expiresAt)}</td><td>${statusBadge(item.status || "active")}</td><td><div class="row-actions">${item.status === "active" ? `<button class="danger" data-code-revoke="${escapeHtml(item.id)}">撤销</button>` : "—"}</div></td></tr>`).join("")}</tbody></table>` : empty("暂无注册码");
  }

  async function loadContentAndFeedback() { await Promise.all([loadContent(), loadFeedback()]); }
  async function loadContent() {
    const data = await api(`/api/v2/admin/content?${query({ page: 1, pageSize: 100, type: $("contentType").value })}`); state.content = data.items || [];
    $("contentTable").innerHTML = state.content.length ? `<table><thead><tr><th>标题</th><th>类型</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${state.content.map(item => `<tr><td>${escapeHtml(item.title)}</td><td>${escapeHtml(item.type)}</td><td>${statusBadge(item.status)}</td><td>${formatTime(item.updatedAt)}</td><td><div class="row-actions"><button data-content-edit="${escapeHtml(item.id)}">编辑</button></div></td></tr>`).join("")}</tbody></table>` : empty("暂无公告或文章");
  }
  async function loadFeedback() {
    const data = await api(`/api/v2/admin/feedback?${query({ page: 1, pageSize: 100, status: $("feedbackStatus").value })}`); state.feedback = data.items || [];
    $("feedbackTable").innerHTML = state.feedback.length ? `<table><thead><tr><th>题目 / 类型</th><th>说明</th><th>提交人</th><th>状态</th><th>操作</th></tr></thead><tbody>${state.feedback.map(item => `<tr><td>${escapeHtml(item.questionId)}<small>${escapeHtml(item.type)}</small></td><td>${escapeHtml(item.description)}</td><td>${escapeHtml(item.userDisplayName || "匿名")}</td><td>${statusBadge(item.status)}</td><td><div class="row-actions"><button data-feedback="${escapeHtml(item.id)}" data-feedback-status="processing">处理中</button><button data-feedback="${escapeHtml(item.id)}" data-feedback-status="resolved">解决</button><button data-feedback="${escapeHtml(item.id)}" data-feedback-status="closed">关闭</button></div></td></tr>`).join("")}</tbody></table>` : empty("暂无纠错工单");
  }
  function openContent(item = null) { const form = $("contentForm"); form.reset(); form.dataset.id = item?.id || ""; form.elements.type.value = item?.type || "announcement"; form.elements.status.value = item?.status || "draft"; form.elements.title.value = item?.title || ""; form.elements.source.value = item?.source || ""; form.elements.body.value = item?.body || ""; $("contentTitle").textContent = item ? "编辑内容" : "新建内容"; showDrawer("contentEditor"); }
  function openExam() { const form = $("examForm"); form.reset(); form.elements.durationMinutes.value = 60; form.elements.passPercent.value = 60; form.elements.questionCount.value = 50; showDrawer("examEditor"); }

  async function loadPermissions() {
    const data = await api("/api/v2/admin/admins");
    const roles = [
      ["super_admin", "全模块及高风险系统操作"], ["question_admin", "题库、导入和试卷管理"], ["support_admin", "用户查询、纠错和内容管理"],
    ];
    $("roleMatrix").innerHTML = roles.map(([role, description]) => `<article class="role-card"><h3>${roleLabels[role]}</h3><p class="muted">${description}</p><ul>${rolePermissions[role].map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul></article>`).join("");
    state.admins = data.items || [];
    $("adminTable").innerHTML = state.admins.length ? `<table><thead><tr><th>管理员</th><th>角色</th><th>状态</th><th>最近登录</th><th>操作</th></tr></thead><tbody>${state.admins.map(item => `<tr><td>${escapeHtml(item.displayName || item.username)}<small>@${escapeHtml(item.username)}</small></td><td>${item.roles.map(role=>escapeHtml(roleLabels[role]||role)).join("、")}</td><td>${statusBadge(item.status || "active")}</td><td>${formatTime(item.lastLoginAt)}</td><td><div class="row-actions"><button data-admin-role="${item.id}">变更角色</button><button class="${item.status==="active"?"danger":""}" data-admin-status="${item.id}" data-next="${item.status==="active"?"disabled":"active"}">${item.status==="active"?"禁用":"启用"}</button></div></td></tr>`).join("")}</tbody></table>` : empty("暂无管理员账号");
  }

  async function loadSystem() {
    const [data, auditData] = await Promise.all([state.dashboard ? Promise.resolve(state.dashboard) : api("/api/v2/admin/dashboard"), api(`/api/v2/admin/audit-logs?${query({ page: state.auditPage, pageSize: 20, q: $("auditSearch").value.trim() })}`)]);
    state.audit = auditData.items || [];
    const services = data.services || [];
    $("serviceStatus").innerHTML = services.length ? services.map(item => `<div class="trend-row"><span>${escapeHtml(item.name)}</span><div>${statusBadge(item.status)}</div><small>${escapeHtml(item.detail || "")}</small></div>`).join("") : empty("暂无服务状态数据");
    const backups = data.backups || [];
    $("backupList").innerHTML = backups.length ? backups.map(item => `<p><strong>${escapeHtml(item.name)}</strong><small>${formatTime(item.createdAt)} · ${escapeHtml(item.status)}</small></p>`).join("") : empty("暂无备份元数据");
    $("auditTable").innerHTML = state.audit.length ? `<table><thead><tr><th>时间</th><th>操作者</th><th>操作</th><th>目标</th><th>结果</th><th>请求 ID</th></tr></thead><tbody>${state.audit.map(item => `<tr><td>${formatTime(item.createdAt)}</td><td>${escapeHtml(item.actor)}</td><td>${escapeHtml(item.action)}</td><td>${escapeHtml(item.target)}</td><td>${statusBadge(item.success ? "active" : "archived")}</td><td><code>${escapeHtml(item.requestId || "—")}</code></td></tr>`).join("")}</tbody></table>` : empty("暂无审计记录");
    renderPager("auditPager", state.auditPage, Number(auditData.total || 0), page => { state.auditPage = page; loadSystem().catch(handleError); });
  }

  function downloadCsv(filename, rows) {
    if (!rows.length) return toast("当前没有可导出的数据", true);
    const keys = Object.keys(rows[0]); const encode = value => `"${String(value ?? "").replaceAll('"', '""')}"`;
    const csv = "\ufeff" + [keys.map(encode).join(","), ...rows.map(row => keys.map(key => encode(row[key])).join(","))].join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" })); const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url);
  }

  $("loginForm").addEventListener("submit", async event => { event.preventDefault(); const form = event.currentTarget; const button = form.querySelector("button[type=submit]"); button.disabled = true; $("loginError").textContent = ""; try { const data = await api("/api/admin/login", { method: "POST", body: Object.fromEntries(new FormData(form).entries()) }); state.admin = data.admin || data; state.roles = state.admin.roles?.length ? state.admin.roles : [state.admin.role || "super_admin"]; state.role = state.admin.role || state.roles[0]; form.reset(); showApp(); } catch (error) { $("loginError").textContent = error.message; } finally { button.disabled = false; } });
  $("logoutBtn").addEventListener("click", async () => { try { await api("/api/admin/logout", { method: "POST", body: {} }); } catch {} state.admin = null; showLogin(); });
  $("mainNav").addEventListener("click", event => { const button = event.target.closest("[data-view]"); if (button) showView(button.dataset.view); });
  $("refreshBtn").addEventListener("click", () => loadCurrent());
  $all("[data-theme-toggle]").forEach(button=>button.addEventListener("click",()=>setTheme(document.documentElement.dataset.theme==="dark"?"light":"dark")));
  $("menuBtn").addEventListener("click", () => { document.querySelector(".sidebar").classList.add("open"); $("overlay").classList.remove("hidden"); });
  $("overlay").addEventListener("click", closeDrawers); $all("[data-close]").forEach(button => button.addEventListener("click", closeDrawers));
  document.addEventListener("keydown",event=>{if(event.key==="Escape"&&($all(".drawer").some(node=>!node.classList.contains("hidden"))||document.querySelector(".sidebar").classList.contains("open")))closeDrawers();});
  $all(".subtabs").forEach(tabset => tabset.addEventListener("click", event => { const button = event.target.closest("[data-subview]"); if (!button) return; const section = tabset.closest(".view"); section.querySelectorAll(".subtabs button").forEach(node => node.classList.toggle("active", node === button)); section.querySelectorAll(".subview").forEach(node => node.classList.toggle("active", node.id === button.dataset.subview)); }));

  $("questionSearch").addEventListener("change", () => { state.questionPage = 1; loadQuestions().catch(handleError); }); $("questionStatus").addEventListener("change", () => { state.questionPage = 1; loadQuestions().catch(handleError); });
  $("newQuestionBtn").addEventListener("click", () => openQuestion()); $("openImportBtn").addEventListener("click", () => showDrawer("importDrawer"));
  $("questionTable").addEventListener("click", async event => { const edit = event.target.closest("[data-question-edit]"); const status = event.target.closest("[data-question-status]"); const history = event.target.closest("[data-question-history]"); if (edit) return openQuestion(state.questions.find(item => String(item.id) === edit.dataset.questionEdit)); if (history) { try { await openQuestionHistory(history.dataset.questionHistory); } catch (error) { handleError(error); } return; } if (status) { const action = status.dataset.nextStatus === "archived" ? "下架" : "恢复发布"; if (!window.confirm(`确认${action}题目 ${status.dataset.questionStatus}？历史记录不会被删除。`)) return; try { await api(`/api/v2/admin/questions/${encodeURIComponent(status.dataset.questionStatus)}`, { method: "PATCH", body: { status: status.dataset.nextStatus, confirm: true } }); await loadQuestions(); toast(`题目已${action}`); } catch (error) { handleError(error); } } });
  $("questionForm").addEventListener("submit", async event => { event.preventDefault(); const id = event.currentTarget.elements.id.value; try { await api(id ? `/api/v2/admin/questions/${encodeURIComponent(id)}` : "/api/v2/admin/questions", { method: id ? "PATCH" : "POST", body: questionPayload(event.currentTarget) }); closeDrawers(); await loadQuestions(); toast("题目已保存并记录版本"); } catch (error) { handleError(error); } });
  $("previewQuestionBtn").addEventListener("click", () => { try { const data = questionPayload($("questionForm")); window.alert(`${data.question}\n\n${data.options.map(x => `${x.key}. ${x.text}`).join("\n")}\n\n答案：${data.answer.join("、")}\n解析：${data.explanation}`); } catch (error) { handleError(error); } });
  $("importForm").addEventListener("submit", async event => { event.preventDefault(); const form = new FormData(); form.append("file", $("importFile").files[0]); try { const data = await api("/api/v2/admin/question-imports/preview", { method: "POST", form }); const errors = data.errors || []; $("importPreview").innerHTML = `<p><strong>预检完成：</strong>${Number(data.validRows || 0)} 行可导入，${errors.length} 行错误，${Number(data.duplicateRows || 0)} 行重复。</p>${errors.length ? `<table><thead><tr><th>行</th><th>字段</th><th>问题</th></tr></thead><tbody>${errors.map(item => `<tr><td>${item.row}</td><td>${escapeHtml(item.field)}</td><td>${escapeHtml(item.message)}</td></tr>`).join("")}</tbody></table>` : ""}<button id="commitImportBtn" class="primary" ${errors.length ? "disabled" : ""}>确认事务导入</button>`; const commit = $("commitImportBtn"); if (commit) commit.addEventListener("click", async () => { if (!window.confirm(`确认导入 ${data.validRows} 道题？将作为一个事务执行。`)) return; try { await api(`/api/v2/admin/question-imports/${encodeURIComponent(data.batchId)}/commit`, { method: "POST", body: { confirm: true } }); closeDrawers(); await loadQuestions(); toast("导入完成"); } catch (error) { handleError(error); } }); } catch (error) { handleError(error); } });

  $("examSearch").addEventListener("change", () => loadExams().catch(handleError)); $("newExamBtn").addEventListener("click", openExam);
  $("examForm").addEventListener("submit", async event => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget).entries()); try { values.rules = values.rules ? JSON.parse(values.rules) : {}; values.durationMinutes = Number(values.durationMinutes); values.passPercent = Number(values.passPercent); values.questionCount = Number(values.questionCount); await api("/api/v2/admin/exam-papers", { method: "POST", body: values }); closeDrawers(); await loadExams(); toast("试卷已保存"); } catch (error) { handleError(error); } });
  $("examTable").addEventListener("click", async event => { const button = event.target.closest("[data-exam-publish]"); const copy = event.target.closest("[data-exam-copy]"); if (copy) { try { await api("/api/v2/admin/exam-papers", { method: "POST", body: { action: "copy", paperId: copy.dataset.examCopy } }); await loadExams(); toast("试卷已复制为草稿"); } catch (error) { handleError(error); } } if (button) { if (!window.confirm(`确认${button.dataset.action === "publish" ? "发布" : "下架"}该试卷？`)) return; try { await api("/api/v2/admin/exam-papers", { method: "POST", body: { action: button.dataset.action, paperId: button.dataset.examPublish, confirm: true } }); await loadExams(); } catch (error) { handleError(error); } } });

  $("userSearch").addEventListener("change", () => { state.userPage = 1; loadUsers().catch(handleError); }); $("userStatus").addEventListener("change", () => { state.userPage = 1; loadUsers().catch(handleError); });
  $("userTable").addEventListener("click", async event => { const button = event.target.closest("[data-user-action]"); if (!button) return; const action = button.dataset.userAction; const danger = ["disable", "soft-delete"].includes(action); let reason = ""; if (danger) { reason = window.prompt("请输入操作原因（将写入审计日志）：") || ""; if (!reason.trim()) return toast("必须填写操作原因", true); } if (!window.confirm(`确认执行 ${action}？该操作将由服务端再次校验权限。`)) return; try { const data = await api("/api/v2/admin/users", { method: "POST", body: { action, userId: button.dataset.user, reason, confirm: true } }); if (action === "reset-token") { $("resetTokenResult").innerHTML = `一次性重置凭据：<code>${escapeHtml(data.token)}</code><br><small>有效至 ${formatTime(data.expiresAt)}；仅显示本次，请安全交付用户。</small>`; $("resetTokenResult").classList.remove("hidden"); } await loadUsers(); toast("用户操作已完成"); } catch (error) { handleError(error); } });
  $("exportUsersBtn").addEventListener("click", () => downloadCsv("netforge-users.csv", state.users)); $("newCodeBtn").addEventListener("click", () => showDrawer("codeEditor"));
  $("codeForm").addEventListener("submit", async event => { event.preventDefault(); const form = event.currentTarget; const values = Object.fromEntries(new FormData(form).entries()); values.maxUses = values.maxUses ? Number(values.maxUses) : null; values.expiresAt = values.expiresAt ? Math.floor(new Date(values.expiresAt).getTime() / 1000) : null; try { const data = await api("/api/v2/admin/registration-codes", { method: "POST", body: values }); form.reset(); closeDrawers(); await loadCodes(); toast(`注册码已创建：${data.code || "请查看列表"}`); } catch (error) { handleError(error); } });
  $("codeTable").addEventListener("click", async event => { const button = event.target.closest("[data-code-revoke]"); if (!button || !window.confirm("确认撤销该注册码？撤销后不能用于新注册。")) return; try { await api("/api/v2/admin/registration-codes", { method: "POST", body: { action: "revoke", codeId: button.dataset.codeRevoke, confirm: true } }); await loadCodes(); toast("注册码已撤销"); } catch (error) { handleError(error); } });

  $("contentType").addEventListener("change", () => loadContent().catch(handleError)); $("feedbackStatus").addEventListener("change", () => loadFeedback().catch(handleError)); $("newContentBtn").addEventListener("click", () => openContent());
  $("contentTable").addEventListener("click", event => { const button = event.target.closest("[data-content-edit]"); if (button) openContent(state.content.find(item => String(item.id) === button.dataset.contentEdit)); });
  $("contentForm").addEventListener("submit", async event => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget).entries()); const id = event.currentTarget.dataset.id; try { await api("/api/v2/admin/content", { method: id ? "PATCH" : "POST", body: { ...values, id: id || undefined } }); closeDrawers(); await loadContent(); toast("内容已保存"); } catch (error) { handleError(error); } });
  $("feedbackTable").addEventListener("click", async event => { const button = event.target.closest("[data-feedback]"); if (!button) return; try { await api("/api/v2/admin/feedback", { method: "PATCH", body: { id: button.dataset.feedback, status: button.dataset.feedbackStatus } }); await loadFeedback(); toast("工单状态已更新"); } catch (error) { handleError(error); } });

  $("newAdminBtn").addEventListener("click",()=>showDrawer("adminEditor"));
  $("adminForm").addEventListener("submit",async event=>{event.preventDefault();const form=event.currentTarget;const values=Object.fromEntries(new FormData(form).entries());try{await api("/api/v2/admin/admins",{method:"POST",body:{username:values.username,displayName:values.displayName,password:values.password,roles:[values.role]}});form.reset();closeDrawers();await loadPermissions();toast("管理员已创建");}catch(error){handleError(error);}});
  $("adminTable").addEventListener("click",async event=>{const roleButton=event.target.closest("[data-admin-role]");const statusButton=event.target.closest("[data-admin-status]");if(!roleButton&&!statusButton)return;const id=(roleButton||statusButton).dataset.adminRole||(roleButton||statusButton).dataset.adminStatus;let body={confirm:true};if(roleButton){const role=window.prompt("输入角色：super_admin / question_admin / support_admin");if(!role)return;body.roles=[role];}else{body.status=statusButton.dataset.next;}if(!window.confirm("确认变更该管理员？服务端将校验最后一位超级管理员保护。"))return;try{await api(`/api/v2/admin/admins/${id}`,{method:"PATCH",body});await loadPermissions();toast("管理员权限已更新");}catch(error){handleError(error);}});

  $("createBackupBtn").addEventListener("click", async () => { if (!window.confirm("确认创建数据库与上传资源备份？该操作不会执行恢复。")) return; try { await api("/api/v2/admin/backups", { method: "POST", body: { confirm: true } }); state.dashboard = null; await loadSystem(); toast("备份任务已创建，将在完整性校验后标记可用"); } catch (error) { handleError(error); } });
  $("auditSearch").addEventListener("change", () => { state.auditPage = 1; loadSystem().catch(handleError); }); $("exportDashboardBtn").addEventListener("click", () => downloadCsv("netforge-dashboard-daily.csv", state.dashboard?.daily || []));

  let detailUser=null,detailPage=1,detailRequest=0;
  function installAdminUpgrade(){
    document.addEventListener('keydown',event=>{if(event.key!=='Tab')return;const drawer=$all('.drawer').find(x=>!x.classList.contains('hidden'));if(!drawer)return;const nodes=[...drawer.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex="0"]')].filter(x=>!x.disabled&&x.getClientRects().length);if(!nodes.length)return;const first=nodes[0],last=nodes[nodes.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}});
    const personal=document.createElement('button');personal.className='ghost';personal.type='button';personal.textContent='个人中心';personal.addEventListener('click',()=>showDrawer('adminPersonal'));document.querySelector('.sidebar-foot').prepend(personal);
    function enhance(){
      $('userTable').querySelectorAll('tbody tr').forEach((row,index)=>{const item=state.users[index];if(!item||row.querySelector('[data-user-detail]'))return;const cell=row.cells[0];cell.innerHTML=`<button class="profile-link" data-user-detail="${item.id}">${escapeHtml(item.displayName||item.username)}</button><br><button class="profile-link" data-user-detail="${item.id}">@${escapeHtml(item.username)}</button><small>#${item.id}</small>`;
        if(state.roles.includes('super_admin'))row.cells[row.cells.length-1].querySelector('.row-actions')?.insertAdjacentHTML('beforeend',`<button class="danger" data-hard-delete="${item.id}">硬删除</button>`);});
      $('codeTable').querySelectorAll('tbody tr').forEach((row,index)=>{const item=state.codes[index];if(item&&!row.querySelector('[data-code-delete]'))row.cells[row.cells.length-1].insertAdjacentHTML('beforeend',`<button class="danger" data-code-delete="${item.id}">删除该条记录</button>`);});
      $('examTable').querySelectorAll('tbody tr').forEach((row,index)=>{const item=state.exams[index];if(item&&!row.querySelector('[data-paper-delete]'))row.cells[row.cells.length-1].insertAdjacentHTML('beforeend',`<button class="danger" data-paper-delete="${escapeHtml(item.id)}">删除试卷</button>`);});
      $('riskSignals').querySelectorAll('tbody tr').forEach((row,index)=>{const item=state.dashboard?.riskSignals?.[index];if(item&&!row.querySelector('[data-risk-question]'))row.cells[row.cells.length-1].insertAdjacentHTML('beforeend',`<button class="ghost" data-risk-question="${escapeHtml(item.questionId)}">${can('questions')?'校订题目':'处理反馈'}</button>`);});
    }
    ['userTable','codeTable','examTable','riskSignals'].forEach(id=>new MutationObserver(enhance).observe($(id),{childList:true}));
    document.addEventListener('click',async event=>{const button=event.target.closest('[data-user-detail],[data-hard-delete],[data-code-delete],[data-paper-delete],[data-risk-question]');if(!button)return;
      if(button.dataset.userDetail){detailUser=Number(button.dataset.userDetail);detailPage=1;return openUserDetail();}
      button.disabled=true;
      try{
        if(button.dataset.hardDelete){const user=state.users.find(x=>String(x.id)===button.dataset.hardDelete);const typed=window.prompt(`永久删除 ${user.username} 的账号、全部学习记录、反馈、头像及AI缓存。请输入完整用户名确认：`);if(typed!==user.username)return;
          await api('/api/v2/admin/users',{method:'POST',body:{action:'hard-delete',userId:user.id,confirm:true,confirmUsername:typed}});await loadUsers();toast('用户及在线关联数据已永久删除');
        }else if(button.dataset.codeDelete){if(!window.confirm('删除该条注册码及其使用关联记录？已注册用户账号会保留，此注册码将不能再次使用。'))return;await api('/api/v2/admin/registration-codes',{method:'POST',body:{action:'delete',codeId:Number(button.dataset.codeDelete),confirm:true}});await loadCodes();toast('注册码记录已删除');
        }else if(button.dataset.paperDelete){if(!window.confirm('删除该试卷？试卷将从管理和可用列表移除，已开始考试及历史成绩报告保留。'))return;await api('/api/v2/admin/exam-papers',{method:'POST',body:{action:'delete',paperId:button.dataset.paperDelete,confirm:true}});await loadExams();toast('试卷已删除，历史成绩保留');
        }else if(button.dataset.riskQuestion){if(can('questions')){showView('questions');$('questionSearch').value=button.dataset.riskQuestion;state.questionPage=1;await loadQuestions();const question=state.questions.find(x=>x.id===button.dataset.riskQuestion);if(question)openQuestion(question);else toast('题目不存在或已移除',true);}else{showView('content');const feedbackTab=document.querySelector('[data-subview="feedbackList"]');feedbackTab?.click();$('feedbackStatus').value='open';await loadFeedback();}}
      }catch(error){handleError(error);}finally{button.disabled=false;}
    });
    $('userDetailPrev').addEventListener('click',()=>{detailPage=Math.max(1,detailPage-1);openUserDetail();});$('userDetailNext').addEventListener('click',()=>{detailPage++;openUserDetail();});
    $('userPortraitBtn').addEventListener('click',async event=>{if(!window.confirm('将此用户的学习统计及近期答题、错题和模考记录发送至 DeepSeek，生成学习画像？'))return;const uid=detailUser,button=event.currentTarget;button.disabled=true;$('userPortraitBody').textContent='正在生成学习画像…';try{const result=await api(`/api/v2/admin/users/${uid}/ai-portrait`,{method:'POST',body:{confirm:true}});if(uid===detailUser)window.NFDocument.render($('userPortraitBody'),result.text);}catch(error){if(uid===detailUser)$('userPortraitBody').textContent=error.message;}finally{button.disabled=false;}});
  }
  async function openUserDetail(){
    const uid=detailUser,page=detailPage,request=++detailRequest;
    try{const data=await api(`/api/v2/admin/users/${uid}?page=${page}`);if(request!==detailRequest||uid!==detailUser)return;const user=data.user;$('userDetailTitle').textContent=`${user.display_name} · @${user.username}`;
      const labels={id:'用户ID',username:'用户名',display_name:'昵称',status:'账号状态',avatar_url:'头像地址',created_at:'注册时间',last_login_at:'最近登录',updated_at:'更新时间',deleted_at:'软删除时间',delete_reason:'操作原因'};
      const renderRows=(title,rows)=>`<details><summary>${title}（本页 ${rows.length} 条）</summary>${rows.map(row=>`<article class="detail-record">${Object.entries(row).map(([key,value])=>`<p><strong>${escapeHtml({questionId:'题目ID',question:'题干',selected:'选择答案',correct:'是否正确',answeredAt:'作答时间',durationSeconds:'用时（秒）',source:'记录来源',topic:'知识点',id:'ID',title:'名称',status:'状态',score:'分数',questionCount:'题数',wrongCount:'错误次数',startedAt:'开始时间',submittedAt:'交卷时间',count:'数量',answered:'答题数',prefix:'注册码前缀',label:'备注',usedAt:'使用时间',description:'反馈内容',resolution:'处理结果',createdAt:'创建时间',type:'类型'}[key]||key)}：</strong>${escapeHtml(key.endsWith('At')?formatTime(value):value??'—')}</p>`).join('')}</article>`).join('')}</details>`;
      $('userDetailBody').innerHTML=`<dl class="detail-grid">${Object.entries(user).map(([key,value])=>`<div><dt>${labels[key]||escapeHtml(key)}</dt><dd>${escapeHtml(key.endsWith('_at')?formatTime(value):value??'—')}</dd></div>`).join('')}</dl><div class="metric-grid">${Object.entries(data.metrics).map(([key,value])=>`<article class="metric-card"><span>${{answered:'累计答题',correct:'答对次数',wrong:'当前错题',exams:'模考次数',submittedExams:'已交卷模考',favorites:'收藏数',feedback:'反馈数'}[key]}</span><b>${value}</b></article>`).join('')}</div>${renderRows('知识点统计',data.topics)}${renderRows('答题记录',data.answers)}${renderRows('错题本',data.wrongItems)}${renderRows('模拟考试记录',data.exams)}${renderRows('反馈记录',data.feedback)}${renderRows('注册码使用记录',data.registrationCodes)}<p>第 ${data.page} 页，每类记录最多 ${data.pageSize} 条。</p>`;
      mountUserEditor(data);
      $('userDetailPrev').disabled=detailPage<=1;$('userDetailNext').disabled=!data.hasMore;$('userPortraitBtn').classList.toggle('hidden',!state.roles.includes('super_admin'));$('userPortraitBody').textContent='';showDrawer('userDetailDrawer');
    }catch(error){handleError(error);}
  }
  function mountUserEditor(data){
    if(!state.roles.includes('super_admin'))return;
    const user=data.user,uid=user.id,labels={answered:'累计答题数',correct:'答对次数',wrong:'当前错题',exams:'模考次数',submittedExams:'已交卷模考',favorites:'收藏数',feedback:'反馈数'};
    const dateInput=value=>{if(value==null)return '';const date=new Date(value*1000);return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,19);};
    const immutable=`<dl class="detail-grid"><div><dt>用户ID</dt><dd>${uid}</dd></div><div><dt>注册时间（只读）</dt><dd>${formatTime(user.created_at)}</dd></div><div><dt>最近登录（只读）</dt><dd>${formatTime(user.last_login_at)}</dd></div><div><dt>账号状态（只读）</dt><dd>${escapeHtml({active:'正常',disabled:'已禁用',deleted:'已软删除'}[user.status]||user.status)}</dd></div></dl>`;
    const editor=document.createElement('section');editor.className='user-detail-editor';
    editor.innerHTML=immutable+`<form id="adminUserEditForm"><h3>编辑用户资料与统计</h3><div class="form-grid"><label>用户名<input name="username" value="${escapeHtml(user.username)}" pattern="[A-Za-z0-9_.-]{3,24}" minlength="3" maxlength="24" autocomplete="off" required></label><label>昵称<input name="display_name" value="${escapeHtml(user.display_name)}" maxlength="30" required></label><label class="wide">头像地址<input name="avatar_url" value="${escapeHtml(user.avatar_url||'')}" maxlength="2048"></label><label>更新时间<input name="updated_at" type="datetime-local" step="1" value="${dateInput(user.updated_at)}"></label><label>软删除时间<input name="deleted_at" type="datetime-local" step="1" value="${dateInput(user.deleted_at)}"></label><label class="wide">账号操作备注<textarea name="delete_reason" maxlength="1000">${escapeHtml(user.delete_reason||'')}</textarea></label>${Object.entries(labels).map(([key,label])=>`<label>${label}<input name="metric_${key}" type="number" min="0" max="1000000000" step="1" value="${data.metrics[key]}" required><small class="muted">真实明细 ${data.actualMetrics[key]}</small></label>`).join('')}<label class="wide">本次修改原因<textarea name="edit_reason" maxlength="1000" required></textarea></label></div><p class="muted">数字保存为统计校正，后续真实学习继续累加；不生成虚构答题、错题、考试或反馈明细，排行榜仍按真实记录计算。修改用户名后需重新登录。</p><p class="form-error" role="status" aria-live="polite"></p><button class="primary" type="submit">保存用户修改</button></form><form id="adminUserPasswordForm"><h3>更改用户密码</h3><div class="form-grid"><label>新密码<input name="newPassword" type="password" minlength="8" maxlength="128" autocomplete="new-password" required></label><label>确认新密码<input name="confirmPassword" type="password" minlength="8" maxlength="128" autocomplete="new-password" required></label><label class="wide">密码修改原因<input name="reason" maxlength="1000" required></label></div><p class="muted">保存后该用户所有旧登录会话及重置凭据将失效，需使用新密码登录。</p><p class="form-error" role="status" aria-live="polite"></p><button class="danger" type="submit">确认更改密码</button></form>`;
    $('userDetailBody').querySelector('.detail-grid').replaceWith(editor);
    $('userDetailBody').querySelector('.metric-grid').classList.add('hidden');
    $('adminUserEditForm').addEventListener('submit',async event=>{
      event.preventDefault();const form=event.currentTarget,button=form.querySelector('[type=submit]'),status=form.querySelector('[role=status]');if(button.disabled)return;button.disabled=true;status.textContent='正在保存…';
      try{const fields=Object.fromEntries(new FormData(form).entries());const profile=Object.fromEntries(['username','display_name','avatar_url','delete_reason'].map(key=>[key,fields[key]]));['updated_at','deleted_at'].forEach(key=>profile[key]=fields[key]?Math.floor(new Date(fields[key]).getTime()/1000):null);
        const metrics=Object.fromEntries(Object.keys(labels).map(key=>[key,Number(fields['metric_'+key])]));
        await api(`/api/v2/admin/users/${uid}`,{method:'PATCH',body:{profile,metrics,version:data.editVersion,reason:fields.edit_reason,confirm:true}});
        toast('用户修改已保存并记录审计');if(uid===detailUser&&!$('userDetailDrawer').classList.contains('hidden'))await openUserDetail();await loadUsers();
      }catch(error){status.textContent=error.message;}finally{button.disabled=false;}
    });
    $('adminUserPasswordForm').addEventListener('submit',async event=>{
      event.preventDefault();const form=event.currentTarget,button=form.querySelector('[type=submit]'),status=form.querySelector('[role=status]');if(button.disabled)return;
      if(form.elements.newPassword.value!==form.elements.confirmPassword.value){status.textContent='两次输入的密码不一致';return;}
      button.disabled=true;status.textContent='正在更改密码…';
      try{await api(`/api/v2/admin/users/${uid}/password`,{method:'POST',body:{password:form.elements.newPassword.value,reason:form.elements.reason.value,version:data.editVersion,confirm:true}});form.reset();toast('密码已更改，旧登录会话已退出');if(uid===detailUser&&!$('userDetailDrawer').classList.contains('hidden'))await openUserDetail();}
      catch(error){form.elements.newPassword.value='';form.elements.confirmPassword.value='';status.textContent=error.message;}finally{button.disabled=false;}
    });
  }
  installAdminUpgrade();
  setTheme('dark');
  api("/api/admin/session").then(data => { state.admin = data.admin || data; state.roles = state.admin.roles?.length ? state.admin.roles : [state.admin.role || "super_admin"]; state.role = state.admin.role || state.roles[0]; showApp(); }).catch(showLogin);
})();
