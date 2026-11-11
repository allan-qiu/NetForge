(() => {
  const $ = id => document.getElementById(id);
  const typeNames = { single: "单选题", multiple: "多选题", judge: "判断题" };
  const state = { user: null, view: "home", mode: "practice", questionPage: 1, pageSize: 20, totalPages: 1, filters: {}, practice: null, exam: null, examTimer: null, contentType: "announcement", rankingPeriod: "week" };

  function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[c])); }
  function formatTime(value) { return value ? new Date(value * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "暂无"; }
  function percent(correct, total) { return total ? `${Math.round(correct / total * 100)}%` : "0%"; }
  function uuid() { return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`; }
  function payloadData(payload) { return payload && Object.prototype.hasOwnProperty.call(payload, "data") ? payload.data : payload; }

  async function api(path, options = {}) {
    const init = { method: options.method || "GET", credentials: "same-origin", headers: {}, signal: options.signal };
    if (options.form) init.body = options.form;
    else if (options.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(options.body); }
    const response = await fetch(path, init);
    const payload = await response.json().catch(() => ({ ok: false, error: { message: "服务器返回了无效数据" } }));
    if (!response.ok) { const error = new Error(typeof payload.error === "string" ? payload.error : payload.error?.message || "请求失败"); error.status = response.status; error.code = payload.error?.code; throw error; }
    const data=payloadData(payload);
    if(path.endsWith('/report'))state.reportItems=data.items||[];
    return data;
  }

  function toast(message) { $("toast").textContent = message; $("toast").classList.add("show"); clearTimeout(toast.timer); toast.timer = setTimeout(() => $("toast").classList.remove("show"), 3400); }
  function setTheme(theme) { window.NFAppearance.setMode(theme); }
  function renderProfileButton(user) { const button=$("profileBtn"),name=user.display_name||user.displayName||user.username||"我",url=user.avatarUrl||user.avatar_url||"";button.textContent="";if(url){const img=document.createElement("img");img.src=url;img.alt="";button.append(img);}else button.textContent=name.slice(0,1); }
  function renderAvatar(imageId,fallbackId,url,name) { const image=$(imageId),fallback=$(fallbackId);image.classList.toggle("hidden",!url);fallback.classList.toggle("hidden",Boolean(url));if(url)image.src=url;else{image.removeAttribute("src");fallback.textContent=(name||"—").slice(0,1);} }
  function setEmpty(element, empty, message) { element.classList.toggle("empty", empty); if (empty) element.textContent = message; }
  function showVisitor() { $("visitorView").classList.remove("hidden"); $("authView").classList.add("hidden"); $("appViews").classList.add("hidden"); $("primaryNav").classList.add("hidden"); $("profileBtn").classList.add("hidden"); }
  function showAuth(mode = "login") { $("visitorView").classList.add("hidden"); $("authView").classList.remove("hidden"); setAuthMode(mode); }
  function setAuthMode(mode) { const login = mode === "login", register = mode === "register", reset = mode === "reset"; $("loginTab").setAttribute("aria-selected", String(login)); $("registerTab").setAttribute("aria-selected", String(register)); $("resetTab").setAttribute("aria-selected", String(reset)); $("loginForm").classList.toggle("hidden", !login); $("registerForm").classList.toggle("hidden", !register); $("resetForm").classList.toggle("hidden", !reset); $("authTitle").textContent = login ? "登录学习空间" : register ? "加入团队学习" : "使用一次性凭据重置密码"; $("authError").textContent = ""; }

  async function completeLogin(user) {
    state.user = user;
    $('personalAnalysisBody').textContent='';$('personalAnalysisStatus').textContent='正在读取你的专属分析…';$('generatePersonalAnalysis').textContent='生成我的AI总体分析';
    await window.NFAppearance.load('user',user.id);
    state.motivation = null;
    api('/api/v2/me/motivation').then(data=>{state.motivation=data;$("welcomeText").textContent=`${data.text} — ${data.source}`;}).catch(()=>{$("welcomeText").textContent='把今天的一次练习，变成明天的一份底气。';});
    $("visitorView").classList.add("hidden"); $("authView").classList.add("hidden"); $("appViews").classList.remove("hidden"); $("primaryNav").classList.remove("hidden"); $("profileBtn").classList.remove("hidden");
    renderProfileButton(user);
    await navigate("home");
  }

  async function submitAuth(form, mode) {
    const button = form.querySelector("button[type=submit]"); button.disabled = true; $("authError").textContent = "";
    try { const data = await api(`/api/${mode}`, { method: "POST", body: Object.fromEntries(new FormData(form).entries()) }); form.reset(); await completeLogin(data.user); }
    catch (error) { $("authError").textContent = error.message; }
    finally { button.disabled = false; }
  }

  function setActiveView(name) {
    document.querySelectorAll("[data-view]").forEach(view => view.classList.toggle("active", view.dataset.view === name));
    document.querySelectorAll("[data-nav]").forEach(item => { const active=item.dataset.nav===name;item.classList.toggle("active",active);if(item.closest("nav"))active?item.setAttribute("aria-current","page"):item.removeAttribute("aria-current"); });
    state.view = name; window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function navigate(name) {
    setActiveView(name);
    try {
      if (name === "home") await loadHome(); else if (name === "bank") await loadQuestions(); else if (name === "exam") await loadExamPapers(); else if (name === "learning") await loadLearning(); else if (name === "ranking") await loadRanking(); else if (name === "prep") await loadContent(); else if (name === "profile") await loadProfile();
    } catch (error) { if (error.status === 401) return logoutLocal(); toast(error.message); }
  }

  async function loadHome() {
    const data = await api("/api/v2/home"); const metrics = data.metrics || data.progress || {};
    $("welcomeText").textContent = state.motivation ? `${state.motivation.text} — ${state.motivation.source}` : '正在为你挑选今天的学习寄语…';
    $("homeToday").textContent = metrics.todayAnswered ?? metrics.today ?? 0; $("homeAnswered").textContent = metrics.answered ?? 0; $("homeAccuracy").textContent = metrics.accuracy != null ? `${Math.round(metrics.accuracy)}%` : percent(metrics.correct || 0, metrics.answered || 0); $("homeQuestions").textContent = (metrics.questionCount ?? data.questionCount ?? 0).toLocaleString("zh-CN");
    $("lastPractice").textContent = data.lastPractice ? `${data.lastPractice.title || "练习"} · ${formatTime(data.lastPractice.updatedAt)}` : "暂无记录";
    $("weakPoint").textContent = data.weakPoints?.length ? data.weakPoints.map(x => x.name || x).slice(0, 3).join("、") : "数据不足";
    $("recentExam").textContent = data.recentExam ? `${data.recentExam.title || "模拟考试"} · ${data.recentExam.score ?? "—"} 分` : "暂无记录";
    renderContentItems($("homeAnnouncements"), data.announcements || [], "暂无公告");
    $("continueBtn").disabled = !data.activePractice;
    loadPersonalAnalysis();
  }

  function queryString(values) { const params = new URLSearchParams(); Object.entries(values).forEach(([key, value]) => { if (value !== "" && value != null) params.set(key, value); }); return params.toString(); }
  async function loadQuestions(page = state.questionPage) {
    document.querySelectorAll('[data-mode]').forEach(button=>button.classList.toggle('active',button.dataset.mode===state.mode));
    $('startFilteredBtn').textContent=state.mode==='study'?'选择题数开始背题':'按当前筛选开始';
    state.questionPage = page; const params = { page, pageSize: state.pageSize, ...state.filters }; const data = await api(`/api/v2/questions?${queryString(params)}`); const items = data.items || [];
    state.questionItems = items; state.totalPages = Math.max(1, Math.ceil((data.total || 0) / (data.pageSize || state.pageSize))); $("questionCount").textContent = `${Number(data.total || 0).toLocaleString("zh-CN")} 道题`;
    $("questionList").innerHTML = items.map(item => `<article class="question-row"><div><div class="tags"><span class="tag">${escapeHtml(item.topic || "未分类")}</span><span class="tag">${escapeHtml(typeNames[item.type] || item.type || "题目")}</span>${item.difficulty ? `<span class="tag">${escapeHtml(item.difficulty)}</span>` : ""}</div><h3>${escapeHtml(item.question)}</h3><p>${escapeHtml(item.source || "来源未标注")}</p></div><button class="secondary" data-question="${escapeHtml(item.id)}">${state.mode === "study" ? "背题" : "练习"}</button></article>`).join("");
    $("questionEmpty").classList.toggle("hidden", items.length > 0); $("pageInfo").textContent = `${page} / ${state.totalPages}`; $("prevPage").disabled = page <= 1; $("nextPage").disabled = page >= state.totalPages;
    $("questionList").querySelectorAll("[data-question]").forEach(button => button.addEventListener("click", () => startPractice({ questionIds: [button.dataset.question] })));
    fillTopicOptions(data.facets?.topics || data.topics || []);
  }

  function fillTopicOptions(topics) { document.querySelectorAll('select[name="topic"]').forEach(select => { if (select.options.length > 1 || !topics.length) return; topics.forEach(topic => { const value = typeof topic === "string" ? topic : topic.value || topic.name; const option = document.createElement("option"); option.value = value; option.textContent = typeof topic === "string" ? topic : `${topic.name || value}${topic.count != null ? ` (${topic.count})` : ""}`; select.append(option); }); }); }

  function normalizePractice(data) { const session = data.session || data; session.questions = session.questions || session.items || []; session.answers = session.answers || {}; session.index = Number(session.index || 0); return session; }
  async function startPractice(extra = {}, trigger = null) {
    if (trigger?.disabled) return;
    if (trigger) trigger.disabled = true;
    try { const data = await api("/api/v2/practice-sessions", { method: "POST", body: { mode: state.mode, filters: state.filters, ...extra } }); state.practice = normalizePractice(data); if (!state.practice.questions.length && !state.practice.questionIds?.length) return toast("当前条件下没有可用题目"); if (!state.practice.questions.length) state.practice.questions = state.practice.questionIds.map(id => ({ id })); setActiveView("practice"); await renderPractice(); }
    catch (error) { if (error.status === 401) return logoutLocal(); toast(error.message); }
    finally { if (trigger) trigger.disabled = false; }
  }

  async function continuePractice() { try { const home = await api("/api/v2/home"); const active = home.activePractice; if (!active?.id) return toast("暂无未完成练习"); state.practice = normalizePractice(await api(`/api/v2/practice-sessions/${active.id}`)); state.mode = state.practice.mode || "practice"; setActiveView("practice"); await renderPractice(); } catch (error) { toast(error.message); } }
  async function practiceQuestion() { const item = state.practice.questions[state.practice.index]; if (!item) return null; if (item.question && item.options) return item; const detail = await api(`/api/v2/questions/${item.id || item.questionId}`); state.practice.questions[state.practice.index] = detail.question || detail; return state.practice.questions[state.practice.index]; }

  async function renderPractice() {
    const session = state.practice, question = await practiceQuestion(); if (!question) return navigate("bank"); const total = session.questions.length, saved = session.answers[question.id] || {}; session.selected = [...(saved.selected || [])];
    $("practiceTitle").textContent = state.mode === "study" ? "背题模式" : session.title || "练习模式"; $("practiceIndex").textContent = `${session.index + 1} / ${total}`; $("practiceProgress").style.width = `${(session.index + 1) / total * 100}%`;
    $("questionType").textContent = typeNames[question.type] || question.type || "题目"; $("questionTopic").textContent = question.topic || "未分类"; $("questionDifficulty").textContent = question.difficulty || "难度未标注"; $("questionText").textContent = question.question || "题目加载失败";
    $("questionOptions").innerHTML = (question.options || []).map((option, index) => `<button class="option" data-key="${escapeHtml(option.key)}" aria-pressed="${session.selected.includes(option.key)}"><span>${String.fromCharCode(65 + index)}</span><span>${escapeHtml(option.text)}</span></button>`).join("");
    $("questionOptions").querySelectorAll(".option").forEach(button => button.addEventListener("click", () => togglePracticeOption(question, button.dataset.key)));
    $("answerPanel").classList.add("hidden"); $("submitAnswer").classList.remove("hidden"); $("nextQuestion").classList.add("hidden"); $("previousQuestion").disabled = session.index === 0; $("favoriteQuestion").classList.toggle("is-active",Boolean(question.favorite)); $("favoriteQuestion").setAttribute("aria-pressed",String(Boolean(question.favorite)));
    renderPracticeSheet(); if (saved.revealed) revealPractice(saved);
    if (state.mode === "study" && !saved.revealed) await submitPractice(true);
  }

  function togglePracticeOption(question, key) { if (state.practice.answers[question.id]?.revealed || state.mode === "study") return; if (question.type === "multiple") state.practice.selected = state.practice.selected.includes(key) ? state.practice.selected.filter(x => x !== key) : [...state.practice.selected, key]; else state.practice.selected = [key]; $("questionOptions").querySelectorAll(".option").forEach(button => button.setAttribute("aria-pressed", String(state.practice.selected.includes(button.dataset.key)))); }
  function renderPracticeSheet() { $("practiceSheet").innerHTML = state.practice.questions.map((question, index) => `<button data-index="${index}" class="${index === state.practice.index ? "current" : ""} ${state.practice.answers[question.id || question.questionId]?.revealed ? "answered" : ""}" aria-label="第 ${index + 1} 题">${index + 1}</button>`).join(""); $("practiceSheet").querySelectorAll("button").forEach(button => button.addEventListener("click", () => movePractice(Number(button.dataset.index) - state.practice.index))); }
  async function submitPractice(studyReveal = false) { const question = await practiceQuestion(); if (!studyReveal && !state.practice.selected.length) return toast("请先选择答案"); $("submitAnswer").disabled = true; try { const result = await api(`/api/v2/practice-sessions/${state.practice.id}/answers`, { method: "POST", body: { questionId: question.id, selected: studyReveal ? [] : state.practice.selected, submissionId: uuid(), studyReveal } }); const answer = { ...result, selected: [...state.practice.selected], revealed: true }; state.practice.answers[question.id] = answer; revealPractice(answer); } catch (error) { toast(error.message); } finally { $("submitAnswer").disabled = false; } }
  function revealPractice(result) { const answer = result.answer || result.correctAnswer || []; $("questionOptions").querySelectorAll(".option").forEach(button => { button.disabled = true; if (answer.includes(button.dataset.key)) button.classList.add("correct"); else if ((result.selected || []).includes(button.dataset.key)) button.classList.add("incorrect"); }); $("answerStatus").textContent = state.mode === "study" ? "背题讲解" : result.correct ? "回答正确" : "回答错误"; $("correctAnswer").textContent = `正确答案：${answer.join("、")}`; $("answerExplanation").textContent = result.explanation || "暂无解析"; $("answerSource").textContent = `来源：${result.source || "未标注"}`; const confidence={high:"高",medium:"中",low:"低"}[result.explanationConfidence]||"未评级"; $("answerConfidence").textContent=`校订置信度：${confidence}${result.needsReview?" · 本题受产品版本或题意影响，建议结合参考资料复核":""}`; const refs=(result.references||[]).filter(x=>/^https:\/\//.test(x)); $("answerReferences").innerHTML=refs.length?`<strong>校订参考：</strong>${refs.map((url,index)=>`<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">资料 ${index+1}</a>`).join(" · ")}`:""; $("answerPanel").classList.remove("hidden"); $("submitAnswer").classList.add("hidden"); $("nextQuestion").classList.remove("hidden"); renderPracticeSheet(); }
  async function movePractice(delta) {
    const session = state.practice, next = session.index + delta;
    if (next < 0) return;
    $("previousQuestion").disabled = true; $("nextQuestion").disabled = true;
    try {
      if (next >= session.questions.length) {
        await api(`/api/v2/practice-sessions/${session.id}`, { method: "PATCH", body: { status: "completed", currentIndex: session.index } });
        state.practice = null; toast("本轮练习已完成"); await navigate("home"); return;
      }
      await api(`/api/v2/practice-sessions/${session.id}`, { method: "PATCH", body: { status: "active", currentIndex: next } });
      session.index = next; await renderPractice();
    } catch (error) { if (error.status === 401) return logoutLocal(); toast(`练习进度未保存：${error.message}`); }
    finally { if (state.practice) { $("previousQuestion").disabled = state.practice.index === 0; $("nextQuestion").disabled = false; } }
  }

  async function toggleFavorite() { const question = await practiceQuestion(); try { const data = await api("/api/favorites/toggle", { method: "POST", body: { questionId: question.id } }); question.favorite = data.favorite; $("favoriteQuestion").classList.toggle("is-active",Boolean(data.favorite)); $("favoriteQuestion").setAttribute("aria-pressed",String(Boolean(data.favorite))); toast(data.favorite ? "已收藏" : "已取消收藏"); } catch (error) { toast(error.message); } }
  function openFeedback() { const question = state.practice?.questions[state.practice.index]; if (!question) return; $("feedbackForm").reset(); $("feedbackForm").elements.questionId.value = question.id || question.questionId; $("feedbackDialog").showModal(); }
  async function submitFeedback(form) { const body = Object.fromEntries(new FormData(form).entries()); try { await api("/api/v2/question-feedback", { method: "POST", body }); $("feedbackDialog").close(); toast("反馈已提交，可在个人中心查看处理状态"); } catch (error) { form.querySelector(".form-error").textContent = error.message; } }

  async function loadExamPapers() { const data = await api("/api/v2/exam-papers"); const items = data.items || [], active = data.activeAttempts || []; $("examPaperList").innerHTML = active.map(item=>`<article><strong>${escapeHtml(item.title)}</strong><small>未完成 · 截止 ${formatTime(item.endsAt)}</small><button class="primary" data-attempt="${escapeHtml(item.id)}">继续考试</button></article>`).join("") + items.map(item => `<article><strong>${escapeHtml(item.title)}</strong><small>${item.questionCount || 0} 题 · ${item.durationMinutes || 0} 分钟 · 及格 ${item.passScore || 0}%</small><button class="primary" data-paper="${escapeHtml(item.id)}">开始考试</button></article>`).join(""); setEmpty($("examPaperList"), !items.length && !active.length, "暂无已发布试卷"); $("examPaperList").querySelectorAll("[data-paper]").forEach(button => button.addEventListener("click", () => startExam({ paperId: button.dataset.paper }))); $("examPaperList").querySelectorAll("[data-attempt]").forEach(button=>button.addEventListener("click",()=>resumeExam(button.dataset.attempt))); fillTopicOptions(data.topics || []); }
  async function resumeExam(id) { try { state.exam=await api(`/api/v2/exam-attempts/${id}`); state.exam.index=0; state.exam.answers=state.exam.answers||{}; state.exam.marked=state.exam.marked||[]; setActiveView("exam-attempt"); renderExam(); startExamTimer(); } catch(error) { toast(error.message); } }
  async function startExam(body) { try { const data = await api("/api/v2/exam-attempts", { method: "POST", body }); state.exam = data.attempt || data; state.exam.index = 0; state.exam.answers = state.exam.answers || {}; state.exam.marked = state.exam.marked || []; setActiveView("exam-attempt"); renderExam(); startExamTimer(); } catch (error) { $("examConfigError").textContent = error.message; toast(error.message); } }
  function currentExamQuestion() { return state.exam.questions?.[state.exam.index]; }
  function renderExam() { const exam = state.exam, question = currentExamQuestion(); if (!question) return; const selected = exam.answers[question.id] || []; $("examTitle").textContent = exam.title || "模拟考试"; $("examProgress").style.width = `${(exam.index + 1) / exam.questions.length * 100}%`; $("examQuestionType").textContent = typeNames[question.type] || question.type; $("examQuestionTopic").textContent = question.topic || "未分类"; $("examQuestionText").textContent = question.question; $("examQuestionOptions").innerHTML = (question.options || []).map((option, i) => `<button class="option" data-key="${escapeHtml(option.key)}" aria-pressed="${selected.includes(option.key)}"><span>${String.fromCharCode(65 + i)}</span><span>${escapeHtml(option.text)}</span></button>`).join(""); $("examQuestionOptions").querySelectorAll("button").forEach(button => button.addEventListener("click", () => selectExamOption(question, button.dataset.key))); $("examPrevious").disabled = exam.index === 0; $("examNext").disabled = exam.index >= exam.questions.length - 1; const marked=exam.marked.includes(question.id);$("markExamQuestion").classList.toggle("active",marked);$("markExamQuestion").setAttribute("aria-pressed",String(marked)); renderExamSheet(); }
  async function selectExamOption(question, key) { let selected = state.exam.answers[question.id] || []; selected = question.type === "multiple" ? (selected.includes(key) ? selected.filter(x => x !== key) : [...selected, key]) : [key]; state.exam.answers[question.id] = selected; $("examQuestionOptions").querySelectorAll("button").forEach(button => button.setAttribute("aria-pressed", String(selected.includes(button.dataset.key)))); try { await api(`/api/v2/exam-attempts/${state.exam.id}/answers`, { method: "PUT", body: { questionId: question.id, selected } }); renderExamSheet(); } catch (error) { toast(`答案暂未保存：${error.message}`); } }
  function renderExamSheet() { const questions = state.exam.questions || []; $("examSheet").innerHTML = questions.map((q, i) => `<button data-index="${i}" class="${i === state.exam.index ? "current" : ""} ${(state.exam.answers[q.id] || []).length ? "answered" : ""} ${state.exam.marked.includes(q.id) ? "marked" : ""}">${i + 1}</button>`).join(""); $("examSheet").querySelectorAll("button").forEach(button => button.addEventListener("click", () => { state.exam.index = Number(button.dataset.index); renderExam(); })); $("examUnanswered").textContent = `未答 ${questions.filter(q => !(state.exam.answers[q.id] || []).length).length} 题`;
  }
  function startExamTimer() { clearInterval(state.examTimer); const update = () => { const remaining = Math.max(0, Number(state.exam.endsAt || 0) - Math.floor(Date.now() / 1000)); $("examTimer").textContent = `${String(Math.floor(remaining / 60)).padStart(2, "0")}:${String(remaining % 60).padStart(2, "0")}`; if (!remaining) { clearInterval(state.examTimer); submitExam(true); } }; update(); state.examTimer = setInterval(update, 1000); }
  async function submitExam(auto = false) { const unanswered = (state.exam.questions || []).filter(q => !(state.exam.answers[q.id] || []).length).length; if (!auto && !window.confirm(unanswered ? `还有 ${unanswered} 题未答，确定交卷吗？` : "确定交卷吗？")) return; try { await api(`/api/v2/exam-attempts/${state.exam.id}/submit`, { method: "POST", body: { confirm: true, submissionId: uuid() } }); clearInterval(state.examTimer); const report = await api(`/api/v2/exam-attempts/${state.exam.id}/report`); renderExamReport(report); setActiveView("exam-report"); } catch (error) { toast(error.message); } }
  function renderExamReport(report) { const items = report.items || report.answers || []; $("examReport").classList.remove("empty"); $("examReport").innerHTML = `<div class="metric-grid"><article><span>得分</span><b>${escapeHtml(report.score ?? "—")}</b></article><article><span>正确率</span><b>${report.accuracy != null ? Math.round(report.accuracy) : 0}%</b></article><article><span>用时</span><b>${Math.round((report.durationSeconds || 0) / 60)} 分</b></article><article><span>结果</span><b>${report.passed ? "通过" : "继续加油"}</b></article></div><div class="stack">${items.map((item, i) => { const q=typeof item.question==="object"?item.question.question:item.question; const refs=(item.references||[]).filter(x=>/^https:\/\//.test(x)); return `<article><strong>${i + 1}. ${escapeHtml(q)}</strong><p>你的答案：${escapeHtml((item.selected || []).join("、") || "未答")} · 正确答案：${escapeHtml((item.answer || []).join("、"))}</p><p>${escapeHtml(item.explanation || "暂无解析")}</p><small>校订置信度：${escapeHtml({high:"高",medium:"中",low:"低"}[item.explanationConfidence]||"未评级")}${item.needsReview?" · 建议复核":""}</small>${refs.length?`<div class="answer-references">${refs.map((url,index)=>`<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">资料 ${index+1}</a>`).join(" · ")}</div>`:""}</article>`; }).join("")}</div>`; }

  async function loadLearning() { const [learning, heat] = await Promise.all([api("/api/v2/me/learning"), api("/api/v2/me/heatmap")]); const metrics = learning.metrics || {}; $("learningMetrics").innerHTML = [["累计作答",metrics.answered||0],["正确率",metrics.accuracy!=null?`${Math.round(metrics.accuracy)}%`:percent(metrics.correct||0,metrics.answered||0)],["累计学习",`${metrics.studyDays||0} 天`],["连续打卡",`${metrics.streakDays||0} 天`]].map(([label,value])=>`<article><span>${label}</span><b>${value}</b></article>`).join(""); renderTrend(learning.trend || []); renderHeatmap(heat.items || heat.days || []); renderMiniQuestions($("wrongList"), learning.wrongItems || [], "暂无错题"); renderMiniQuestions($("favoriteList"), learning.favoriteItems || [], "暂无收藏"); }
  function renderTrend(items) { if (!items.length) return setEmpty($("trendChart"), true, "数据不足"); $("trendChart").classList.remove("empty"); const max = Math.max(...items.map(x => x.answered || 0), 1); $("trendChart").innerHTML = items.map(x => `<span class="bar-item"><i style="height:${Math.max(2,(x.answered||0)/max*160)}px" title="${x.answered||0} 题"></i><small>${escapeHtml((x.day||"").slice(5))}</small></span>`).join(""); }
  function renderHeatmap(items) { if (!items.length) return setEmpty($("heatmap"), true, "暂无学习记录"); $("heatmap").classList.remove("empty"); const max = Math.max(...items.map(x => x.count || 0),1); $("heatmap").innerHTML = items.map(x => `<i data-level="${Math.min(3,Math.ceil((x.count||0)/max*3))}" title="${escapeHtml(x.day)}：${x.count||0} 题"></i>`).join(""); }
  function renderMiniQuestions(element, items, emptyText) { element.innerHTML = items.map(item => `<article><strong>${escapeHtml(item.question)}</strong><small>${escapeHtml(item.topic || "未分类")}</small><button class="secondary" data-review="${escapeHtml(item.id)}">练习</button></article>`).join(""); setEmpty(element,!items.length,emptyText); element.querySelectorAll("[data-review]").forEach(button=>button.addEventListener("click",()=>startPractice({questionIds:[button.dataset.review]}))); }

  async function loadRanking() { await upgradeRanking(); }
  async function openPublicProfile(userId) { try { const data=await api(`/api/v2/users/${encodeURIComponent(userId)}/profile`);renderAvatar("rankingProfileAvatar","rankingProfileFallback",data.avatarUrl,data.displayName||data.username);$("rankingProfileName").textContent=data.displayName||data.username;$("rankingProfileUsername").textContent=`@${data.username}`;$("rankingProfileAnswered").textContent=Number(data.practiceAnswered||0).toLocaleString("zh-CN");$("rankingProfileAccuracy").textContent=`${Math.round(data.accuracy||0)}%`;$("rankingProfileSpeed").textContent=data.averageDurationSeconds?`${data.averageDurationSeconds} 秒/题`:"暂无";$("rankingProfileDialog").showModal(); } catch(error){toast(error.message);} }
  async function loadContent() { const data = await api(`/api/v2/content?type=${state.contentType}&pageSize=200`); renderContentItems($("contentList"),data.items||[],"暂无已发布内容"); }
  function renderContentItems(element, items, emptyText) { element.innerHTML = items.map(item => `<article><small>${escapeHtml(formatTime(item.publishedAt || item.updatedAt))}</small><h2>${escapeHtml(item.title)}</h2><p>${escapeHtml(item.summary || item.body || item.contentText || "")}</p>${item.source ? `<small>来源：${escapeHtml(item.source)}</small>` : ""}</article>`).join(""); setEmpty(element,!items.length,emptyText); }

  async function loadProfile() { const data = await api("/api/v2/me/profile"); state.user = { ...state.user, ...data }; $("profileUsername").value = data.username || state.user.username || ""; $("profileForm").elements.displayName.value = data.displayName || data.display_name || ""; renderAvatar("profileAvatarPreview","profileAvatarFallback",data.avatarUrl,data.displayName||data.username); const feedback = await api("/api/v2/me/feedback"); const items = feedback.items || []; $("myFeedback").innerHTML = items.map(item => `<article><strong>${escapeHtml(item.typeLabel || item.type)}</strong><p>${escapeHtml(item.description)}</p><small>${escapeHtml(item.statusLabel || item.status)} · ${formatTime(item.createdAt)}</small></article>`).join(""); setEmpty($("myFeedback"),!items.length,"暂无反馈记录"); }
  async function saveProfile(form) { const button=form.querySelector("button[type=submit]"),file=$("avatarFile").files[0];button.disabled=true;try { let avatar={};if(file){const upload=new FormData();upload.append("avatar",file);avatar=await api("/api/v2/me/avatar",{method:"POST",form:upload});}const data=await api("/api/v2/me/profile",{method:"PATCH",body:{displayName:form.elements.displayName.value}});state.user={...state.user,...data,...avatar};renderProfileButton(state.user);renderAvatar("profileAvatarPreview","profileAvatarFallback",state.user.avatarUrl,state.user.displayName||state.user.username);form.elements.avatar.value="";toast("个人资料已保存");}catch(error){toast(error.message);}finally{button.disabled=false;} }
  async function changePassword(form) { const values=Object.fromEntries(new FormData(form).entries()); try{await api("/api/v2/me/password",{method:"POST",body:values});form.reset();toast("密码已修改");}catch(error){toast(error.message);} }
  async function logoutAll(){if(!window.confirm("确定退出所有设备吗？当前设备也需要重新登录。"))return;try{await api("/api/v2/me/logout-all",{method:"POST",body:{confirm:true}});}catch{}logoutLocal();}
  async function logout(){try{await api("/api/logout",{method:"POST",body:{}});}catch{}logoutLocal();}
  function logoutLocal(){personalAnalysisRequest++;personalAnalysisController?.abort();$('personalAnalysisBody').textContent='';$('personalAnalysisStatus').textContent='';closeLearningDocument();state.user=null;state.practice=null;state.exam=null;state.motivation=null;window.NFAppearance.reset();clearInterval(state.examTimer);showVisitor();}

  function installEvents(){
    installLearningUpgrade();
    document.querySelectorAll("[data-open-auth]").forEach(x=>x.addEventListener("click",()=>showAuth(x.dataset.openAuth))); document.querySelector("[data-close-auth]").addEventListener("click",showVisitor); $("loginTab").addEventListener("click",()=>setAuthMode("login")); $("registerTab").addEventListener("click",()=>setAuthMode("register")); $("resetTab").addEventListener("click",()=>setAuthMode("reset")); $("loginForm").addEventListener("submit",e=>{e.preventDefault();submitAuth(e.currentTarget,"login");}); $("registerForm").addEventListener("submit",e=>{e.preventDefault();submitAuth(e.currentTarget,"register");}); $("resetForm").addEventListener("submit",async e=>{e.preventDefault();const button=e.currentTarget.querySelector("button[type=submit]");button.disabled=true;try{await api("/api/reset-password",{method:"POST",body:Object.fromEntries(new FormData(e.currentTarget).entries())});e.currentTarget.reset();setAuthMode("login");toast("密码已重置，请使用新密码登录");}catch(error){$("authError").textContent=error.message;}finally{button.disabled=false;}});
    document.querySelectorAll("[data-nav]").forEach(x=>x.addEventListener("click",e=>{e.preventDefault();if(state.user)navigate(x.dataset.nav);})); $("themeBtn").addEventListener("click",()=>setTheme(document.documentElement.dataset.theme==="dark"?"light":"dark"));
    $("homePracticeBtn").addEventListener("click",()=>{state.mode="practice";state.filters={};startPractice({count:20},$("homePracticeBtn"));}); $("continueBtn").addEventListener("click",continuePractice); document.querySelectorAll("[data-mode]").forEach(button=>button.addEventListener("click",()=>{state.mode=button.dataset.mode;document.querySelectorAll("[data-mode]").forEach(x=>x.classList.toggle("active",x===button));$('startFilteredBtn').textContent=state.mode==='study'?'选择题数开始背题':'按当前筛选开始';loadQuestions(1);}));
    $("questionFilters").addEventListener("submit",e=>{e.preventDefault();state.filters=Object.fromEntries(new FormData(e.currentTarget).entries());loadQuestions(1);}); $("prevPage").addEventListener("click",()=>loadQuestions(state.questionPage-1)); $("nextPage").addEventListener("click",()=>loadQuestions(state.questionPage+1)); $("startFilteredBtn").addEventListener("click",()=>state.mode==='study'?openStudyConfig(state.filters):startPractice({filters:state.filters},$('startFilteredBtn')));
    $("exitPractice").addEventListener("click",()=>navigate("bank")); $("submitAnswer").addEventListener("click",()=>submitPractice(false)); $("nextQuestion").addEventListener("click",()=>movePractice(1)); $("previousQuestion").addEventListener("click",()=>movePractice(-1)); $("favoriteQuestion").addEventListener("click",toggleFavorite); $("feedbackBtn").addEventListener("click",openFeedback); $("cancelFeedback").addEventListener("click",()=>$("feedbackDialog").close()); $("feedbackForm").addEventListener("submit",e=>{e.preventDefault();submitFeedback(e.currentTarget);});
    $("examConfig").addEventListener("submit",e=>{e.preventDefault();const values=Object.fromEntries(new FormData(e.currentTarget).entries());values.questionCount=Number(values.questionCount);values.durationMinutes=Number(values.durationMinutes);values.passScore=Number(values.passScore);startExam({configuration:values});}); $("examPrevious").addEventListener("click",()=>{state.exam.index--;renderExam();}); $("examNext").addEventListener("click",()=>{state.exam.index++;renderExam();}); $("markExamQuestion").addEventListener("click",()=>{const id=currentExamQuestion().id;state.exam.marked=state.exam.marked.includes(id)?state.exam.marked.filter(x=>x!==id):[...state.exam.marked,id];renderExam();}); $("submitExam").addEventListener("click",()=>submitExam(false)); $("exitExam").addEventListener("click",()=>{toast("考试进度已在服务器保存");navigate("exam");});
    document.querySelectorAll("[data-period]").forEach(button=>button.addEventListener("click",()=>{state.rankingPeriod=button.dataset.period;document.querySelectorAll("[data-period]").forEach(x=>x.classList.toggle("active",x===button));loadRanking();})); $("closeRankingProfile").addEventListener("click",()=>$("rankingProfileDialog").close()); document.querySelectorAll("[data-content]").forEach(button=>button.addEventListener("click",()=>{state.contentType=button.dataset.content;document.querySelectorAll("[data-content]").forEach(x=>x.classList.toggle("active",x===button));loadContent();}));
    $("avatarFile").addEventListener("change",()=>{const file=$("avatarFile").files[0];if(!file)return renderAvatar("profileAvatarPreview","profileAvatarFallback",state.user?.avatarUrl,state.user?.displayName||state.user?.username);if(file.size>2*1024*1024){$("avatarFile").value="";return toast("头像不能超过2MB");}const reader=new FileReader();reader.onload=()=>renderAvatar("profileAvatarPreview","profileAvatarFallback",reader.result,state.user?.displayName);reader.readAsDataURL(file);});
    $("profileForm").addEventListener("submit",e=>{e.preventDefault();saveProfile(e.currentTarget);}); $("passwordForm").addEventListener("submit",e=>{e.preventDefault();changePassword(e.currentTarget);}); $("logoutAllBtn").addEventListener("click",logoutAll); $("logoutBtn").addEventListener("click",logout);
    document.addEventListener("keydown",event=>{if(document.querySelector('dialog[open]')||state.view!=="practice"||!state.practice||event.ctrlKey||event.altKey||event.metaKey||["INPUT","TEXTAREA","SELECT"].includes(document.activeElement?.tagName))return;const question=state.practice.questions[state.practice.index];const index="ABCDEFGH".indexOf(event.key.toUpperCase());if(index>=0&&question?.options?.[index])togglePracticeOption(question,question.options[index].key);});
  }

  let studyFilters={};
  function openStudyConfig(filters){studyFilters={...filters};$('studyConfigDialog').showModal();}
  let personalAnalysisRequest=0,personalAnalysisController=null;
  async function loadPersonalAnalysis(generate=false){
    if(!state.user)return;
    const userId=state.user.id,request=++personalAnalysisRequest,button=$('generatePersonalAnalysis');
    personalAnalysisController?.abort();const controller=new AbortController();personalAnalysisController=controller;
    button.disabled=true;$('personalAnalysisStatus').textContent=generate?'正在分析你的学习记录，请稍候…':'正在读取你的专属分析…';
    try{const data=await api('/api/v2/me/ai-summary',{method:generate?'POST':'GET',...(generate?{body:{confirm:true}}:{}),signal:controller.signal});
      if(request!==personalAnalysisRequest||state.user?.id!==userId)return;
      window.NFDocument.render($('personalAnalysisBody'),data.text||'');
      $('personalAnalysisStatus').textContent=data.ready?`生成于 ${formatTime(data.generatedAt)}${data.stale?' · 有新的学习记录，建议更新分析':''}`:'尚未生成分析。开始练习后，点击上方按钮获取你的学习建议。';
      button.textContent=data.ready?'更新我的AI分析':'生成我的AI总体分析';
    }catch(error){if(error.name!=='AbortError'&&request===personalAnalysisRequest&&state.user?.id===userId)$('personalAnalysisStatus').textContent=`分析暂不可用：${error.message}。可点击按钮重试。`;}
    finally{if(request===personalAnalysisRequest){button.disabled=false;personalAnalysisController=null;}}
  }
  function installLearningUpgrade() {
    $('homeStudyBtn').addEventListener('click',()=>openStudyConfig({}));
    $('cancelStudyConfig').addEventListener('click',()=>$('studyConfigDialog').close());
    $('studyConfigForm').addEventListener('submit',event=>{event.preventDefault();const value=event.currentTarget.elements.count.value;state.mode='study';$('studyConfigDialog').close();startPractice({mode:'study',filters:studyFilters,count:value==='all'?'all':Number(value)},$('beginStudy'));});
    $('generatePersonalAnalysis').addEventListener('click',()=>loadPersonalAnalysis(true));
    const appearance=document.createElement('section');appearance.className='panel';appearance.innerHTML='<form data-appearance></form>';$('profileView').append(appearance);window.NFAppearance.mount();
    const wrongButton=document.createElement('button');wrongButton.id='homeWrongBtn';wrongButton.className='secondary';wrongButton.textContent='直达错题本';wrongButton.title='连续重新练习全部当前错题';$('homeAccuracy').parentElement.append(wrongButton);
    const wrongTitle=$('wrongList').previousElementSibling;wrongTitle.textContent='错题本';const retry=document.createElement('button');retry.className='secondary';retry.textContent='一键重练全部错题';wrongTitle.after(retry);
    [wrongButton,retry].forEach(button=>button.addEventListener('click',()=>{state.mode='practice';startPractice({allWrong:true,filters:{}},button);}));
    const documentDialog=$('learningDocument');
    ['closeLearningDocument','closeLearningDocumentBottom'].forEach(id=>$(id).addEventListener('click',closeLearningDocument));
    documentDialog.addEventListener('cancel',event=>{event.preventDefault();closeLearningDocument();});
    documentDialog.addEventListener('close',()=>{if(!documentDialog.open)releaseLearningDocument();});
    let backdropPressed=false;
    const outside=event=>{const rect=documentDialog.getBoundingClientRect();return event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom;};
    documentDialog.addEventListener('pointerdown',event=>{backdropPressed=event.target===documentDialog&&outside(event);});
    documentDialog.addEventListener('click',event=>{if(backdropPressed&&event.target===documentDialog&&outside(event))closeLearningDocument();backdropPressed=false;});
    $('downloadLearningDocument').addEventListener('click',()=>{const text=`${$('learningDocumentTitle').textContent}\n\n${$('learningDocumentBody').textContent}\n\n${$('learningDocumentSource').textContent}`;const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='NetForge学习文档.txt';a.click();URL.revokeObjectURL(url);});
    const observer=new MutationObserver(()=>{
      if(state.view==='practice'&&!$('answerPanel').classList.contains('hidden'))mountPracticeDocuments();
      if(state.view==='exam-report')mountExamDocuments();
    });observer.observe($('answerPanel'),{attributes:true,attributeFilter:['class']});observer.observe($('examReport'),{childList:true});
  }

  function documentButtons(container,context,refs) {
    container.querySelectorAll('.learning-document-actions').forEach(x=>x.remove());
    const actions=document.createElement('div');actions.className='actions learning-document-actions';
    const ai=document.createElement('button');ai.className='secondary';ai.type='button';ai.textContent='AI讲解 / 解析';ai.addEventListener('click',()=>openLearningDocument('AI讲解 / 解析','/api/v2/ai/explain',context,ai));actions.append(ai);
    refs.forEach((url,index)=>{const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=`校订参考文档 ${index+1}`;button.addEventListener('click',()=>openLearningDocument(`校订参考 ${index+1}`,'/api/v2/reference-document',{...context,index},button));actions.append(button);});container.append(actions);
  }
  function mountPracticeDocuments(){const q=state.practice?.questions[state.practice.index],result=q&&state.practice.answers[q.id];if(!q||!result)return;
    $('answerReferences').innerHTML='';documentButtons($('answerReferences'),{questionId:q.id,sessionId:state.practice.id},result.references||[]);
    (result.references||[]).forEach((url,index)=>{if(!/^https:\/\//.test(url))return;const link=document.createElement('a');link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.textContent=`原始资料 ${index+1}`;$('answerReferences').append(link,document.createTextNode(' '));});
  }
  function mountExamDocuments(){const rows=$('examReport').querySelectorAll('.stack > article');rows.forEach((row,index)=>{if(row.querySelector('.learning-document-actions'))return;const item=state.reportItems?.[index];if(item)documentButtons(row,{questionId:item.question.id,attemptId:state.exam.id},item.references||[]);});}
  let documentRequest=0,documentController=null,documentOpener=null;
  function releaseLearningDocument(){
    ++documentRequest;documentController?.abort();documentController=null;document.documentElement.classList.remove('document-open');
    const opener=documentOpener;documentOpener=null;if(opener?.isConnected){opener.disabled=false;opener.focus({preventScroll:true});}
  }
  function closeLearningDocument(){releaseLearningDocument();if($('learningDocument').open)$('learningDocument').close();}
  async function openLearningDocument(title,path,body,button){
    documentController?.abort();const controller=new AbortController();documentController=controller;documentOpener=button;
    const requestId=++documentRequest;button.disabled=true;$('learningDocumentTitle').textContent=title;$('learningDocumentBody').textContent='正在生成学习文档，请稍候…';$('learningDocumentSource').textContent='';$('downloadLearningDocument').disabled=true;
    document.documentElement.classList.add('document-open');if(!$('learningDocument').open)$('learningDocument').showModal();$('learningDocument').querySelector('.document-content').scrollTop=0;$('closeLearningDocument').focus();
    try{const result=await api(path,{method:'POST',body,signal:controller.signal});if(requestId!==documentRequest||!$('learningDocument').open)return;window.NFDocument.render($('learningDocumentBody'),result.text);
      if(result.source){const a=document.createElement('a');a.href=result.source;a.target='_blank';a.rel='noopener noreferrer';a.textContent='来源：'+result.source;$('learningDocumentSource').append(a);}$('downloadLearningDocument').disabled=false;
    }catch(error){if(requestId===documentRequest&&error.name!=='AbortError')$('learningDocumentBody').textContent=error.message;}finally{button.disabled=false;if(documentController===controller)documentController=null;}
  }
  let rankingRequest=0;
  async function upgradeRanking(){const serial=++rankingRequest;try{const data=await api(`/api/v2/leaderboard?period=${state.rankingPeriod}`);if(serial!==rankingRequest||state.view!=='ranking')return;
    $('rankingList').classList.toggle('empty',!(data.items||[]).length);
    $('rankingList').innerHTML=(data.items||[]).map(item=>`<article><b class="rank">${item.practiceRank}</b><div><button class="profile-link" data-profile="${item.userId}">${escapeHtml(item.displayName)}</button><small>练习前 ${item.percentile}% · 模考第 ${item.examRank} / 前 ${item.examPercentile}%</small></div><span>练习 ${item.practiceScore} 分</span><span class="exam-score">模考 ${item.examScore} 分</span></article>`).join('');
    $('rankingList').querySelectorAll('[data-profile]').forEach(button=>button.addEventListener('click',()=>openPublicProfile(button.dataset.profile)));
    let details=$('rankingDetails');if(!details){details=document.createElement('details');details.id='rankingDetails';details.className='panel';$('rankingList').parentElement.after(details);}details.innerHTML=`<summary>详情：分数与前10%的计算方式</summary>${Object.values(data.formula||{}).map(x=>`<p>${escapeHtml(x)}</p>`).join('')}<p>当前参与用户 ${data.total} 人；题库 ${data.questionCount} 题。两项分数各自独立，不合并为总分。</p>`;
  }catch(error){toast(error.message);}}

  async function boot(){installEvents();setTheme(localStorage.getItem("nf-theme")||"dark");try{const config=await api("/api/config");$("publicQuestionCount").textContent=Number(config.questionCount||0).toLocaleString("zh-CN");}catch{$("publicQuestionCount").textContent="—";}try{const me=await api("/api/me");await completeLogin(me.user);}catch{showVisitor();}}
  boot();
})();
