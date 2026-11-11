(() => {
  const $ = id => document.getElementById(id);
  let users = [];

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

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  }

  function formatTime(value) {
    if (!value) return "暂无";
    return new Date(value * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
  }

  function toast(message) {
    $("toast").textContent = message;
    $("toast").classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => $("toast").classList.remove("show"), 2200);
  }

  function showAdmin() {
    $("loginView").classList.add("hidden");
    $("adminView").classList.remove("hidden");
  }

  function showLogin() {
    $("adminView").classList.add("hidden");
    $("loginView").classList.remove("hidden");
  }

  async function loadDashboard() {
    const payload = await api("/api/admin/dashboard");
    const totals = payload.totals || {};
    users = payload.users || [];
    $("usersMetric").textContent = Number(totals.users || 0).toLocaleString("zh-CN");
    $("answeredMetric").textContent = Number(totals.answered || 0).toLocaleString("zh-CN");
    $("accuracyMetric").textContent = totals.answered ? `${Math.round(totals.correct / totals.answered * 100)}%` : "0%";
    $("activeMetric").textContent = Number(totals.active_users || 0).toLocaleString("zh-CN");
    $("registrationCode").value = payload.registrationCode || "";
    $("updatedAt").textContent = `更新时间：${new Date().toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}`;
    renderUsers();
    renderTopics(payload.topics || []);
  }

  function renderUsers() {
    const keyword = $("userSearch").value.trim().toLowerCase();
    const visible = users.filter(user => `${user.username} ${user.display_name}`.toLowerCase().includes(keyword));
    $("userRows").innerHTML = visible.map(user => {
      const accuracy = user.answered ? Math.round(user.correct / user.answered * 100) : 0;
      const active = user.last_activity || user.last_login_at || user.created_at;
      return `<tr><td><strong>${escapeHtml(user.display_name)}</strong><small>@${escapeHtml(user.username)} · #${user.id}</small></td><td>${user.answered}</td><td class="accuracy">${accuracy}%</td><td>${user.wrong}</td><td>${user.favorites}</td><td>${escapeHtml(formatTime(active))}</td><td><button class="danger" data-reset="${user.id}" data-name="${escapeHtml(user.username)}">重置密码</button></td></tr>`;
    }).join("");
    $("emptyUsers").classList.toggle("hidden", visible.length > 0);
    $("userRows").querySelectorAll("[data-reset]").forEach(button => button.addEventListener("click", () => resetPassword(button.dataset.reset, button.dataset.name)));
  }

  function renderTopics(topics) {
    $("topicStats").innerHTML = topics.length ? topics.map(topic => {
      const accuracy = topic.answered ? Math.round(topic.correct / topic.answered * 100) : 0;
      return `<article class="topic-card"><header><span>${escapeHtml(topic.topic)}</span><b>${accuracy}%</b></header><p class="muted">${topic.answered} 次作答 · ${topic.users} 位用户</p><div class="bar"><i style="width:${accuracy}%"></i></div></article>`;
    }).join("") : '<p class="empty">暂无答题统计</p>';
  }

  async function resetPassword(userId, username) {
    if (!window.confirm(`确定重置用户 ${username} 的密码吗？该用户当前登录会立即失效。`)) return;
    try {
      const payload = await api("/api/admin/users/reset-password", { method: "POST", body: { userId: Number(userId) } });
      $("passwordResult").innerHTML = `用户 <strong>${escapeHtml(payload.username)}</strong> 的临时密码：<code>${escapeHtml(payload.temporaryPassword)}</code>　请立即复制并私下发送给用户。`;
      $("passwordResult").classList.remove("hidden");
      toast("密码已重置");
    } catch (error) {
      if (error.status === 401) return showLogin();
      toast(error.message);
    }
  }

  $("loginForm").addEventListener("submit", async event => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button[type=submit]");
    button.disabled = true;
    $("loginError").textContent = "";
    try {
      await api("/api/admin/login", { method: "POST", body: Object.fromEntries(new FormData(event.currentTarget).entries()) });
      event.currentTarget.reset();
      showAdmin();
      await loadDashboard();
    } catch (error) {
      $("loginError").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });

  $("registrationForm").addEventListener("submit", async event => {
    event.preventDefault();
    const code = $("registrationCode").value.trim();
    try {
      await api("/api/admin/registration-code", { method: "POST", body: { registrationCode: code } });
      toast("注册码已更新并立即生效");
    } catch (error) {
      if (error.status === 401) return showLogin();
      toast(error.message);
    }
  });

  $("userSearch").addEventListener("input", renderUsers);
  $("refreshBtn").addEventListener("click", () => loadDashboard().then(() => toast("数据已刷新")).catch(error => error.status === 401 ? showLogin() : toast(error.message)));
  $("logoutBtn").addEventListener("click", async () => {
    try { await api("/api/admin/logout", { method: "POST", body: {} }); } catch {}
    showLogin();
  });

  api("/api/admin/session").then(() => { showAdmin(); return loadDashboard(); }).catch(showLogin);
})();
