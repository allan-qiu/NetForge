(() => {
  let bank = [];
  const defaultProgress = { answered: 0, correct: 0, wrongIds: [], favoriteIds: [], today: {}, history: {} };
  const typeNames = { single: "单选题", multiple: "多选题", judge: "判断题" };
  const views = [...document.querySelectorAll(".view")];
  const $ = id => document.getElementById(id);
  let progress = structuredClone(defaultProgress);
  let currentUser = null;
  let selectedTopic = "全部";
  let session = null;

  async function api(path, options = {}) {
    const response = await fetch(path, {
      method: options.method || "GET",
      credentials: "same-origin",
      headers: options.body ? { "Content-Type": "application/json" } : {},
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    const payload = await response.json().catch(() => ({ ok: false, error: "服务器返回了无效数据" }));
    if (!response.ok) {
      const error = new Error(payload.error || "请求失败");
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function showAuthError(message = "") { $("authError").textContent = message; }

  function setAuthTab(name) {
    const login = name === "login";
    $("loginTab").classList.toggle("active", login);
    $("registerTab").classList.toggle("active", !login);
    $("loginForm").classList.toggle("hidden", !login);
    $("registerForm").classList.toggle("hidden", login);
    showAuthError();
  }

  async function boot() {
    renderMetrics();
    try {
      const me = await api("/api/me");
      await completeLogin(me.user);
    } catch (error) {
      document.body.classList.remove("authenticated");
      document.body.classList.add("auth-pending");
      if (error.status && error.status !== 401) showAuthError(error.message);
    }
  }

  async function completeLogin(user) {
    currentUser = user;
    const questionPayload = await api("/api/questions");
    bank = Array.isArray(questionPayload.questions) ? questionPayload.questions : [];
    $("userName").textContent = user.display_name || user.username;
    document.body.classList.remove("auth-pending");
    document.body.classList.add("authenticated");
    renderTopics();
    renderMetrics();
    showView("homeView");
    await loadServerState();
  }

  async function loadServerState(showNotice = false) {
    const payload = await api("/api/state");
    progress = Object.assign(structuredClone(defaultProgress), payload.progress || {});
    progress.wrongIds = Array.isArray(progress.wrongIds) ? progress.wrongIds : [];
    progress.favoriteIds = Array.isArray(progress.favoriteIds) ? progress.favoriteIds : [];
    progress.today = progress.today || {};
    renderMetrics();
    if (showNotice) toast("云端进度已刷新");
  }

  async function submitAuth(form, mode) {
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    showAuthError();
    const values = Object.fromEntries(new FormData(form).entries());
    try {
      const payload = await api(`/api/${mode}`, { method: "POST", body: values });
      form.reset();
      await completeLogin(payload.user);
    } catch (error) {
      showAuthError(error.message);
    } finally {
      button.disabled = false;
    }
  }

  async function logout() {
    try { await api("/api/logout", { method: "POST", body: {} }); } catch {}
    currentUser = null;
    session = null;
    progress = structuredClone(defaultProgress);
    document.body.classList.remove("authenticated");
    document.body.classList.add("auth-pending");
    setAuthTab("login");
  }

  function showView(id) {
    views.forEach(view => view.classList.toggle("active", view.id === id));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function toast(message) {
    const element = $("toast");
    element.textContent = message;
    element.classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => element.classList.remove("show"), 1800);
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  }

  function shuffle(items) {
    const copy = [...items];
    for (let i = copy.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [copy[i], copy[j]] = [copy[j], copy[i]];
    }
    return copy;
  }

  function todayKey() {
    return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  }

  function renderTopics() {
    const preferred = ["全部", "网络基础", "路由交换", "WLAN", "网络安全", "数据中心", "网络管理", "综合技术"];
    const found = new Set(bank.map(question => question.topic));
    const topics = preferred.filter(topic => topic === "全部" || found.has(topic));
    $("topicChips").innerHTML = topics.map(topic => `<button class="chip ${topic === selectedTopic ? "active" : ""}" data-topic="${topic}">${topic}</button>`).join("");
    $("topicChips").querySelectorAll("button").forEach(button => {
      button.addEventListener("click", () => { selectedTopic = button.dataset.topic; renderTopics(); });
    });
  }

  function renderMetrics() {
    const accuracy = progress.answered ? Math.round(progress.correct / progress.answered * 100) : 0;
    $("questionTotal").textContent = bank.length.toLocaleString("zh-CN");
    $("accuracyMetric").textContent = `${accuracy}%`;
    $("answeredMetric").textContent = progress.answered || 0;
    $("correctMetric").textContent = progress.correct || 0;
    $("wrongMetric").textContent = progress.wrongIds.length;
    $("favoriteMetric").textContent = progress.favoriteIds.length;
    $("todayMetric").textContent = progress.today[todayKey()] || 0;
    $("wrongCountTop").textContent = progress.wrongIds.length;
    $("favoriteCountTop").textContent = progress.favoriteIds.length;
  }

  function questionById(id) { return bank.find(question => question.id === id); }

  async function saveSession() {
    try {
      await api("/api/active-session", { method: "POST", body: { activeSession: session } });
    } catch (error) {
      if (error.status === 401) logout();
    }
  }

  async function loadSession() {
    try {
      const payload = await api("/api/active-session");
      return payload.activeSession;
    } catch {
      return null;
    }
  }

  function startQuiz({ ids, topic = "随机训练" }) {
    if (!ids.length) return toast("当前筛选条件下没有可用题目");
    session = { ids, topic, index: 0, selected: [], submitted: false, results: [] };
    saveSession();
    showView("quizView");
    renderQuestion();
  }

  function startCustom() {
    const type = $("typeSelect").value;
    const count = Number($("countSelect").value);
    const candidates = bank.filter(question => (selectedTopic === "全部" || question.topic === selectedTopic) && (type === "all" || question.type === type));
    startQuiz({ ids: shuffle(candidates).slice(0, count).map(question => question.id), topic: selectedTopic === "全部" ? "综合训练" : selectedTopic });
  }

  function currentQuestion() { return session ? questionById(session.ids[session.index]) : null; }

  function renderQuestion(resume = false) {
    const question = currentQuestion();
    if (!question) return finishQuiz();
    if (!resume) {
      session.selected = [];
      session.submitted = false;
    }
    $("quizTopic").textContent = session.topic;
    $("quizIndex").textContent = `${session.index + 1} / ${session.ids.length}`;
    $("progressBar").style.width = `${(session.index / session.ids.length) * 100}%`;
    $("questionType").textContent = typeNames[question.type] || "练习题";
    $("reviewFlag").classList.toggle("hidden", !question.needsReview);
    $("questionText").textContent = question.question;
    $("multiTip").classList.toggle("hidden", question.type !== "multiple");
    $("feedbackPanel").classList.remove("show");
    $("submitBtn").classList.remove("hidden");
    $("nextBtn").classList.add("hidden");
    $("starBtn").classList.toggle("active", progress.favoriteIds.includes(question.id));
    $("starBtn").textContent = progress.favoriteIds.includes(question.id) ? "★" : "☆";
    $("optionsList").innerHTML = question.options.map((option, index) => `<button class="option-btn" data-key="${option.key}"><span class="key">${index + 1}</span><span>${escapeHtml(option.text)}</span></button>`).join("");
    $("optionsList").querySelectorAll("button").forEach(button => button.addEventListener("click", () => toggleOption(button.dataset.key)));
    $("optionsList").querySelectorAll("button").forEach(button => button.classList.toggle("selected", session.selected.includes(button.dataset.key)));
    if (session.submitted) {
      const lastResult = [...session.results].reverse().find(result => result.id === question.id);
      if (lastResult?.answer) revealAnswer(lastResult);
      else toast("该题的旧进度缺少解析，请重新开始本题");
    }
    saveSession();
  }

  function toggleOption(key) {
    if (!session || session.submitted) return;
    const question = currentQuestion();
    if (question.type === "multiple") {
      session.selected = session.selected.includes(key) ? session.selected.filter(item => item !== key) : [...session.selected, key];
    } else {
      session.selected = [key];
    }
    $("optionsList").querySelectorAll("button").forEach(button => button.classList.toggle("selected", session.selected.includes(button.dataset.key)));
    saveSession();
  }

  async function submitAnswer() {
    if (!session || session.submitted) return;
    if (!session.selected.length) return toast("请先选择答案");
    const question = currentQuestion();
    $("submitBtn").disabled = true;
    try {
      const result = await api("/api/attempts", { method: "POST", body: { questionId: question.id, selected: session.selected, skipped: false } });
      const correct = result.correct;
      session.submitted = true;
      const savedResult = {
        id: question.id,
        correct,
        skipped: false,
        selected: [...session.selected],
        answer: result.answer,
        explanation: result.explanation,
        source: result.source,
        needsReview: result.needsReview,
      };
      session.results.push(savedResult);
      progress.answered += 1;
      progress.today[todayKey()] = (progress.today[todayKey()] || 0) + 1;
      if (correct) {
        progress.correct += 1;
        progress.wrongIds = progress.wrongIds.filter(id => id !== question.id);
      } else if (!progress.wrongIds.includes(question.id)) {
        progress.wrongIds.push(question.id);
      }
      renderMetrics();
      saveSession();
      revealAnswer(savedResult);
    } catch (error) {
      if (error.status === 401) return logout();
      toast(error.message);
    } finally {
      $("submitBtn").disabled = false;
    }
  }

  function revealAnswer(result) {
    const answer = Array.isArray(result.answer) ? result.answer : [];
    $("optionsList").querySelectorAll("button").forEach(button => {
      const key = button.dataset.key;
      button.disabled = true;
      button.classList.remove("selected");
      if (answer.includes(key)) button.classList.add("correct");
      else if (session.selected.includes(key)) button.classList.add("incorrect");
    });
    $("feedbackTitle").textContent = result.correct ? "回答正确" : "回答错误";
    $("feedbackTitle").className = result.correct ? "correct" : "wrong";
    $("correctAnswer").textContent = `正确答案：${answer.join("、")}`;
    $("explanationText").textContent = result.explanation || "暂无解析";
    $("sourceText").textContent = `来源：${result.source || "题库"}${result.needsReview ? " · 本题建议核验" : ""}`;
    $("feedbackPanel").classList.add("show");
    $("submitBtn").classList.add("hidden");
    $("nextBtn").classList.remove("hidden");
  }

  function skipQuestion() {
    if (!session || session.submitted) return nextQuestion();
    const question = currentQuestion();
    session.results.push({ id: question.id, correct: false, skipped: true, selected: [] });
    api("/api/attempts", { method: "POST", body: { questionId: question.id, selected: [], skipped: true } }).catch(() => {});
    nextQuestion();
  }

  function nextQuestion() {
    session.index += 1;
    if (session.index >= session.ids.length) finishQuiz();
    else renderQuestion();
  }

  function finishQuiz() {
    if (!session) return showView("homeView");
    const totalAnswered = session.results.filter(result => !result.skipped).length;
    const correct = session.results.filter(result => result.correct).length;
    const wrong = session.results.filter(result => !result.correct && !result.skipped).length;
    const skipped = session.results.filter(result => result.skipped).length;
    const score = totalAnswered ? Math.round(correct / totalAnswered * 100) : 0;
    $("resultScore").textContent = `${score}%`;
    $("resultCorrect").textContent = correct;
    $("resultWrong").textContent = wrong;
    $("resultSkipped").textContent = skipped;
    $("resultMessage").textContent = score >= 85 ? "状态很好。保持节奏，并开始处理收藏与错题。" : score >= 60 ? "基础已经形成，建议立即重练本轮错题。" : "先别追求题量，回看解析并按模块进行专项训练。";
    $("retryWrongBtn").disabled = !session.results.some(result => !result.correct);
    api("/api/active-session", { method: "POST", body: { activeSession: null } }).catch(() => {});
    showView("resultView");
  }

  async function toggleFavorite() {
    const question = currentQuestion();
    if (!question) return;
    try {
      const result = await api("/api/favorites/toggle", { method: "POST", body: { questionId: question.id } });
      if (result.favorite && !progress.favoriteIds.includes(question.id)) progress.favoriteIds.push(question.id);
      if (!result.favorite) progress.favoriteIds = progress.favoriteIds.filter(id => id !== question.id);
      renderMetrics();
      $("starBtn").classList.toggle("active", result.favorite);
      $("starBtn").textContent = result.favorite ? "★" : "☆";
    } catch (error) {
      toast(error.message);
    }
  }

  function showCollection(kind) {
    const ids = kind === "wrong" ? progress.wrongIds : progress.favoriteIds;
    $("collectionTitle").textContent = kind === "wrong" ? "错题本" : "收藏题";
    const items = ids.map(questionById).filter(Boolean);
    $("collectionList").innerHTML = items.map(question => `<article class="collection-item"><header><span>${question.topic} · ${typeNames[question.type]}</span><span>${question.id}</span></header><h3>${escapeHtml(question.question)}</h3><p>答题后显示正确答案与详细解析。</p><button data-id="${question.id}">练这一题</button></article>`).join("");
    $("emptyCollection").classList.toggle("show", !items.length);
    $("collectionList").querySelectorAll("button").forEach(button => button.addEventListener("click", () => startQuiz({ ids: [button.dataset.id], topic: kind === "wrong" ? "错题重练" : "收藏复习" })));
    showView("collectionView");
  }

  async function showHistory() {
    $("collectionTitle").textContent = "答题记录";
    $("collectionList").innerHTML = "";
    $("emptyCollection").classList.remove("show");
    showView("collectionView");
    try {
      const payload = await api("/api/attempts?limit=100");
      $("collectionList").innerHTML = payload.attempts.map(attempt => {
        const date = new Date(attempt.createdAt * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
        const status = attempt.skipped ? "已跳过" : attempt.correct ? "回答正确" : "回答错误";
        const className = attempt.correct ? "correct" : "wrong";
        const review = attempt.skipped ? "<p>本题已跳过，未显示答案与解析。</p>" : `<p>你的答案：${attempt.selected.join("、") || "未作答"} · 正确答案：${attempt.answer.join("、")}</p><p><strong>解析：</strong>${escapeHtml(attempt.explanation || "暂无解析")}</p><small>来源：${escapeHtml(attempt.source || "题库")}${attempt.needsReview ? " · 本题建议核验" : ""}</small>`;
        return `<article class="collection-item"><header><span>${attempt.topic} · ${typeNames[attempt.type] || "练习题"}</span><span>${escapeHtml(date)}</span></header><h3>${escapeHtml(attempt.question)}</h3><p class="attempt-status ${className}">${status}</p>${review}</article>`;
      }).join("");
      $("emptyCollection").classList.toggle("show", !payload.attempts.length);
    } catch (error) {
      toast(error.message);
    }
  }

  $("loginTab").addEventListener("click", () => setAuthTab("login"));
  $("registerTab").addEventListener("click", () => setAuthTab("register"));
  $("loginForm").addEventListener("submit", event => { event.preventDefault(); submitAuth(event.currentTarget, "login"); });
  $("registerForm").addEventListener("submit", event => { event.preventDefault(); submitAuth(event.currentTarget, "register"); });
  $("logoutBtn").addEventListener("click", logout);
  $("quickStartBtn").addEventListener("click", () => startQuiz({ ids: shuffle(bank).slice(0, 20).map(question => question.id), topic: "随机训练" }));
  $("customStartBtn").addEventListener("click", startCustom);
  $("continueBtn").addEventListener("click", async () => {
    const saved = await loadSession();
    if (!saved?.ids?.length) return toast("暂无未完成的训练");
    session = saved;
    showView("quizView");
    renderQuestion(true);
  });
  $("submitBtn").addEventListener("click", submitAnswer);
  $("nextBtn").addEventListener("click", nextQuestion);
  $("skipBtn").addEventListener("click", skipQuestion);
  $("starBtn").addEventListener("click", toggleFavorite);
  $("exitQuizBtn").addEventListener("click", () => { saveSession(); showView("homeView"); });
  $("backHomeBtn").addEventListener("click", () => { session = null; showView("homeView"); });
  $("retryWrongBtn").addEventListener("click", () => {
    const ids = session.results.filter(result => !result.correct).map(result => result.id);
    startQuiz({ ids, topic: "本轮错题" });
  });
  $("wrongBookBtn").addEventListener("click", () => showCollection("wrong"));
  $("favoriteBtn").addEventListener("click", () => showCollection("favorite"));
  $("historyBtn").addEventListener("click", showHistory);
  $("exitCollectionBtn").addEventListener("click", () => showView("homeView"));
  $("syncBtn").addEventListener("click", () => loadServerState(true).catch(error => toast(error.message)));
  document.addEventListener("keydown", event => {
    if (!$("quizView").classList.contains("active") || !session) return;
    if (/^[1-8]$/.test(event.key) && !session.submitted) {
      const question = currentQuestion();
      const option = question.options[Number(event.key) - 1];
      if (option) toggleOption(option.key);
    } else if (event.key === "Enter") submitAnswer();
    else if (event.key.toLowerCase() === "n" && session.submitted) nextQuestion();
  });

  boot();
})();
