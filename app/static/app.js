const state = {
  sessionToken: localStorage.getItem('pithy.sessionToken') || '',
  security: null,
  settings: null,
  latestSkill: null,
  latestExportContent: '',
  latestExportFormat: 'json',
  latestExportName: 'skill-export',
  visualSteps: [],
  logTimer: null,
  currentSessionId: localStorage.getItem('pithy.currentSessionId') || 'default',
  sessions: [],
  // Abort controller for current SSE stream
  _abortController: null,
};

const I18N = {
  'zh-CN': {
    appTitle: 'Pithy Local Agent',
    chatTitle: '交互界面',
    settingsTitle: '设置中心',
    logsTitle: '日志中心',
    lockTitle: '本机 Agent 安全入口',
    lockDescription: '若已设置启动密码，请先解锁；首次使用可直接设置启动密码。',
    locked: '应用已锁定',
    unlocked: '应用已解锁',
    noSkill: '暂无技能',
    saveSuccess: '保存成功',
    copied: '已复制导出内容',
    noExport: '暂无可复制/下载的导出内容',
    unlockSuccess: '解锁成功',
    passwordSet: '启动密码设置成功',
    newSession: '新建会话',
    sessionDeleted: '会话已删除',
    confirmDeleteSession: '确认删除此会话及所有消息？',
  },
  'en-US': {
    appTitle: 'Pithy Local Agent',
    chatTitle: 'Chat Workspace',
    settingsTitle: 'Settings Center',
    logsTitle: 'Log Viewer',
    lockTitle: 'Local Agent Secure Entry',
    lockDescription: 'Unlock with the startup password, or configure one on first use.',
    locked: 'Application is locked',
    unlocked: 'Application is unlocked',
    noSkill: 'No skills available',
    saveSuccess: 'Saved successfully',
    copied: 'Export content copied',
    noExport: 'No export content available',
    unlockSuccess: 'Unlocked successfully',
    passwordSet: 'Startup password configured',
    newSession: 'New Session',
    sessionDeleted: 'Session deleted',
    confirmDeleteSession: 'Delete this session and all its messages?',
  },
};

const chatLog = document.getElementById('chat-log');
const chatDebug = document.getElementById('chat-debug');
const statusEl = document.getElementById('status');
const skillResultEl = document.getElementById('skill-result');
const skillVersionsEl = document.getElementById('skill-versions');
const skillVersionSelectEl = document.getElementById('skill-version-select');
const logsOutputEl = document.getElementById('logs-output');
const lockScreenEl = document.getElementById('lock-screen');
const lockFeedbackEl = document.getElementById('lock-feedback');
const toolManifestsOutputEl = document.getElementById('tool-manifests-output');
const visualStepsOutputEl = document.getElementById('visual-steps-output');
const visualStepFeedbackEl = document.getElementById('visual-step-feedback');
const ocrStatusOutputEl = document.getElementById('ocr-status-output');
const releaseInfoOutputEl = document.getElementById('release-info-output');

function t(key) {
  const lang = (state.settings && state.settings.language) || (state.security && state.security.language) || 'zh-CN';
  return (I18N[lang] && I18N[lang][key]) || I18N['zh-CN'][key] || key;
}

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (state.sessionToken) headers['X-Session-Token'] = state.sessionToken;
  const resp = await fetch(path, { ...options, headers });
  const data = await resp.json();
  if (!resp.ok) {
    if (resp.status === 423) {
      state.sessionToken = '';
      localStorage.removeItem('pithy.sessionToken');
      await refreshSecurityStatus();
      updateLockUI();
    }
    if (typeof data.detail === 'string') throw new Error(data.detail);
    if (data.detail && typeof data.detail === 'object') {
      const code = data.detail.code || 'API_ERROR';
      const message = data.detail.message || JSON.stringify(data.detail);
      throw new Error(`[${code}] ${message}`);
    }
    throw new Error(JSON.stringify(data));
  }
  return data;
}

function applyTheme(theme) {
  const resolved = theme === 'system'
    ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : theme;
  document.body.dataset.theme = resolved;
}

function applyTranslations() {
  document.title = t('appTitle');
  document.getElementById('app-title').textContent = t('appTitle');
  document.getElementById('chat-title').textContent = t('chatTitle');
  document.getElementById('settings-title').textContent = t('settingsTitle');
  document.getElementById('logs-title').textContent = t('logsTitle');
  document.getElementById('lock-title').textContent = t('lockTitle');
  document.getElementById('lock-description').textContent = t('lockDescription');
}

function renderMarkdown(text) {
  if (!text) return '';

  // ── Try marked library ──
  if (typeof marked !== 'undefined' && typeof marked.parse === 'function') {
    // Configure highlight extension once
    if (!renderMarkdown._configured) {
      try {
        if (typeof hljs !== 'undefined' && typeof markedHighlight !== 'undefined' &&
            typeof markedHighlight.markedHighlight === 'function') {
          marked.use(markedHighlight.markedHighlight({
            langPrefix: 'hljs language-',
            highlight(code, lang) {
              if (lang && hljs.getLanguage(lang)) {
                return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
              }
              return hljs.highlightAuto(code).value;
            },
          }));
        }
      } catch (e) {
        console.warn('[renderMarkdown] highlight setup error:', e);
      }
      renderMarkdown._configured = true;
    }
    try {
      const html = marked.parse(text, { breaks: true, gfm: true });
      if (typeof html === 'string') return html;
    } catch (e) {
      console.warn('[renderMarkdown] marked.parse error:', e);
    }
  }

  // ── Fallback: basic markdown → HTML ──
  let s = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  // code blocks ```
  s = s.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code class="language-$1">$2</code></pre>');
  // inline code
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  // bold
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // italic
  s = s.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // headings
  s = s.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  s = s.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  s = s.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  // unordered list
  s = s.replace(/^[-*] (.+)$/gm, '<li>$1</li>');
  // line breaks (outside <pre>)
  s = s.replace(/\n/g, '<br>');
  return s;
}

/** Wrap pre>code blocks with copy button after inserting html */
function enhanceCodeBlocks(el) {
  el.querySelectorAll('pre').forEach(pre => {
    if (pre.parentElement && pre.parentElement.classList.contains('code-block-wrap')) return;
    const wrap = document.createElement('div');
    wrap.className = 'code-block-wrap';
    pre.parentNode.insertBefore(wrap, pre);
    wrap.appendChild(pre);

    // Language label
    const codeEl = pre.querySelector('code');
    if (codeEl) {
      const langMatch = codeEl.className.match(/language-(\S+)/);
      if (langMatch) {
        const langLabel = document.createElement('span');
        langLabel.className = 'code-lang-label';
        langLabel.textContent = langMatch[1];
        wrap.appendChild(langLabel);
      }
    }

    const btn = document.createElement('button');
    btn.className = 'copy-code-btn';
    btn.textContent = '复制';
    btn.onclick = () => {
      const code = pre.querySelector('code') ? pre.querySelector('code').innerText : pre.innerText;
      navigator.clipboard && navigator.clipboard.writeText(code).then(() => {
        btn.textContent = '已复制 ✓';
        btn.classList.add('copied');
        setTimeout(() => { btn.textContent = '复制'; btn.classList.remove('copied'); }, 2000);
      });
    };
    wrap.appendChild(btn);
  });
}

/** Format a Date as HH:MM */
function formatTime(d) {
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

/** Format relative time for session list */
function relativeTime(dateStr) {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr.replace(' ', 'T'));
    const diff = Date.now() - d.getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return '刚刚';
    if (mins < 60) return `${mins}分钟前`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}小时前`;
    const days = Math.floor(hrs / 24);
    return `${days}天前`;
  } catch { return ''; }
}

function _makeActionBar(role, text) {
  const bar = document.createElement('div');
  bar.className = 'msg-action-bar';

  // Copy button (all roles)
  const copyBtn = document.createElement('button');
  copyBtn.className = 'msg-action-btn';
  copyBtn.title = '复制';
  copyBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  copyBtn.onclick = () => {
    navigator.clipboard && navigator.clipboard.writeText(text).then(() => {
      copyBtn.innerHTML = '✓';
      setTimeout(() => {
        copyBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
      }, 1500);
    });
  };
  bar.appendChild(copyBtn);

  // Retry button (user messages only)
  if (role === 'user') {
    const retryBtn = document.createElement('button');
    retryBtn.className = 'msg-action-btn';
    retryBtn.title = '重新发送';
    retryBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.51"/></svg>';
    retryBtn.onclick = () => {
      const input = document.getElementById('chat-input');
      if (!input) return;
      input.value = text;
      autoResizeTextarea(input);
      updateCharCounter(input);
      input.focus();
    };
    bar.appendChild(retryBtn);
  }

  return bar;
}

function appendLine(role, text) {
  // Remove empty-state placeholder on first message
  const empty = chatLog.querySelector('.chat-empty');
  if (empty) empty.remove();

  const row = document.createElement('div');
  row.className = `msg-row ${role}`;

  // Timestamp
  const timeEl = document.createElement('span');
  timeEl.className = 'msg-time';
  timeEl.textContent = formatTime(new Date());

  if (role === 'assistant') {
    const wrap = document.createElement('div');
    wrap.className = 'msg-content-wrap';
    const avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    avatar.textContent = 'AI';
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    bubble.innerHTML = renderMarkdown(text);
    enhanceCodeBlocks(bubble);
    wrap.appendChild(avatar);
    wrap.appendChild(bubble);
    row.appendChild(wrap);
    row.appendChild(_makeActionBar('assistant', text));
  } else if (role === 'user') {
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    bubble.textContent = text;
    row.appendChild(bubble);
    row.appendChild(_makeActionBar('user', text));
  } else {
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    bubble.textContent = text;
    row.appendChild(bubble);
  }

  row.appendChild(timeEl);
  chatLog.appendChild(row);
  chatLog.scrollTop = chatLog.scrollHeight;
}

/** Show animated thinking indicator in chat */
function showThinking() {
  const row = document.createElement('div');
  row.className = 'msg-row assistant';
  row.id = 'thinking-row';
  const wrap = document.createElement('div');
  wrap.className = 'msg-content-wrap';
  const avatar = document.createElement('div');
  avatar.className = 'msg-avatar';
  avatar.textContent = 'AI';
  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.innerHTML = '<div class="msg-thinking"><span></span><span></span><span></span></div>';
  wrap.appendChild(avatar);
  wrap.appendChild(bubble);
  row.appendChild(wrap);
  const empty = chatLog.querySelector('.chat-empty');
  if (empty) empty.remove();
  chatLog.appendChild(row);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function hideThinking() {
  const row = document.getElementById('thinking-row');
  if (row) row.remove();
}

/** Auto-resize textarea */
function autoResizeTextarea(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 220) + 'px';
}

// ── Toast notifications ──────────────────────────────────────────────────
function showToast(message, type = 'info', duration = 3000) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('out');
    setTimeout(() => toast.remove(), 220);
  }, duration);
}

function showError(message) {
  appendLine('error', message);
  chatDebug.textContent = message;
}

// ── Tab switching ────────────────────────────────────────────────────────
function initTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      const pane = document.getElementById(`tab-${tab}`);
      if (pane) pane.classList.add('active');
    });
  });
}

function setSkillResult(value) {
  skillResultEl.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

function renderVisualSteps() {
  const pretty = state.visualSteps.map((step, idx) => ({ index: idx + 1, ...step }));
  visualStepsOutputEl.textContent = JSON.stringify(pretty, null, 2);
}

function getSelectedStepIndex() {
  const raw = document.getElementById('visual-selected-step').value.trim();
  const value = Number(raw || 0);
  if (!Number.isInteger(value) || value < 1 || value > state.visualSteps.length) {
    throw new Error('请选择有效的步骤序号');
  }
  return value - 1;
}

function validateVisualStep(kind, name, params) {
  if (!name) throw new Error('步骤名称不能为空');
  if (typeof params !== 'object' || params === null || Array.isArray(params)) {
    throw new Error('步骤参数必须是 JSON 对象');
  }
  if (kind === 'llm' && !params.prompt) {
    throw new Error('llm 步骤必须提供 prompt 参数');
  }
  if (kind === 'tool' && name.length < 2) {
    throw new Error('tool 步骤名称长度不足');
  }
}

function buildVisualSkillSpec() {
  return {
    name: document.getElementById('visual-skill-name').value.trim() || 'visual_skill',
    version: document.getElementById('visual-skill-version').value.trim() || '1.0.0',
    description: document.getElementById('visual-skill-description').value.trim(),
    steps: [...state.visualSteps],
  };
}

function syncSettingsForm() {
  if (!state.settings) return;
  document.getElementById('pref-language').value = state.settings.language;
  document.getElementById('pref-theme').value = state.settings.theme;
  document.getElementById('pref-log-lines').value = state.settings.log_lines;
  document.getElementById('pref-log-level').value = state.settings.log_level;
  document.getElementById('pref-auto-refresh-logs').checked = state.settings.auto_refresh_logs;
  document.getElementById('pref-send-shortcut').value = state.settings.send_shortcut;
  const sysEl = document.getElementById('pref-system-prompt');
  if (sysEl) sysEl.value = state.settings.system_prompt || '';
}

function updateLockUI() {
  const hasPassword = state.security && state.security.has_password;
  const locked = state.security && state.security.locked;
  document.getElementById('setup-box').classList.toggle('hidden', !!hasPassword);
  document.getElementById('unlock-box').classList.toggle('hidden', !hasPassword);
  lockScreenEl.classList.toggle('hidden', !locked);
  document.getElementById('lock-app').style.display = hasPassword ? 'inline-block' : 'none';
  statusEl.textContent = locked ? t('locked') : t('unlocked');
}

async function refreshSecurityStatus() {
  state.security = await api('/api/security/status');
  if (!state.settings) {
    state.settings = {
      theme: state.security.theme,
      language: state.security.language,
      log_lines: 120,
      log_level: 'INFO',
      auto_refresh_logs: false,
      send_shortcut: 'Ctrl+Enter',
    };
  }
}

async function loadAppSettings() {
  state.settings = await api('/api/settings');
  syncSettingsForm();
  applyTheme(state.settings.theme);
  applyTranslations();
  scheduleLogRefresh();
}

async function refreshHealth() {
  try {
    const h = await api('/api/health');
    statusEl.textContent = `${state.security && state.security.locked ? t('locked') : t('unlocked')} | cpu=${h.cpu_percent}% mem=${h.memory_percent}%`;
  } catch (e) {
    statusEl.textContent = `health error: ${e.message}`;
  }
}

async function refreshHistory() {
  if (!state.currentSessionId) {
    chatLog.innerHTML = '';
    return;
  }
  const history = await api(`/api/history?session_id=${encodeURIComponent(state.currentSessionId)}`);
  chatLog.innerHTML = '';
  history.forEach(item => appendLine(item.role, item.content));
}

// ── Session management ──────────────────────────────────────────────────────

function renderSessionList() {
  const listEl = document.getElementById('session-list');
  if (!listEl) return;
  listEl.innerHTML = '';
  state.sessions.forEach(s => {
    const li = document.createElement('li');
    li.className = 'session-item' + (s.session_id === state.currentSessionId ? ' active' : '');
    li.dataset.sessionId = s.session_id;

    // Session icon
    const icon = document.createElement('div');
    icon.className = 'session-icon';
    icon.textContent = '💬';
    li.appendChild(icon);

    // Session info block
    const infoDiv = document.createElement('div');
    infoDiv.className = 'session-info';
    infoDiv.onclick = () => switchSession(s.session_id);

    // Session name (click to switch)
    const nameSpan = document.createElement('span');
    nameSpan.className = 'session-name';
    nameSpan.textContent = s.name || s.session_id;
    const metaSpan = document.createElement('span');
    metaSpan.className = 'session-meta';
    metaSpan.textContent = `${s.message_count} 条消息`;
    infoDiv.appendChild(nameSpan);
    infoDiv.appendChild(metaSpan);
    li.appendChild(infoDiv);

    // Relative time badge
    const timeEl = document.createElement('span');
    timeEl.className = 'session-time';
    timeEl.textContent = relativeTime(s.updated_at || s.created_at);
    li.appendChild(timeEl);

    // Action buttons group
    const actions = document.createElement('div');
    actions.className = 'session-actions';

    // Rename button
    const renameBtn = document.createElement('button');
    renameBtn.className = 'session-action-btn';
    renameBtn.textContent = '✏';
    renameBtn.title = '重命名';
    renameBtn.onclick = async (e) => {
      e.stopPropagation();
      const newName = prompt('重命名会话', s.name || s.session_id);
      if (!newName || !newName.trim() || newName.trim() === s.name) return;
      await api(`/api/sessions/${encodeURIComponent(s.session_id)}`, {
        method: 'PATCH',
        body: JSON.stringify({ name: newName.trim() }),
      });
      s.name = newName.trim();
      nameSpan.textContent = s.name;
      if (s.session_id === state.currentSessionId) updateCurrentSessionLabel();
    };
    actions.appendChild(renameBtn);

    // Auto-title button
    const titleBtn = document.createElement('button');
    titleBtn.className = 'session-action-btn';
    titleBtn.textContent = '✨';
    titleBtn.title = 'AI 生成标题';
    titleBtn.onclick = async (e) => {
      e.stopPropagation();
      titleBtn.textContent = '…';
      titleBtn.disabled = true;
      try {
        const res = await api(`/api/sessions/${encodeURIComponent(s.session_id)}/generate-title`, { method: 'POST' });
        if (res.name) {
          s.name = res.name;
          nameSpan.textContent = res.name;
          if (s.session_id === state.currentSessionId) updateCurrentSessionLabel();
        }
      } catch (err) {
        console.warn('generate title failed', err);
      } finally {
        titleBtn.textContent = '✨';
        titleBtn.disabled = false;
      }
    };
    actions.appendChild(titleBtn);

    const delBtn = document.createElement('button');
    delBtn.className = 'session-action-btn session-del-btn';
    delBtn.textContent = '🗑';
    delBtn.title = '删除会话';
    delBtn.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(`删除「${s.name || s.session_id}」及其全部消息？此操作不可撤销。`)) return;
      await api(`/api/sessions/${encodeURIComponent(s.session_id)}`, { method: 'DELETE' });
      const deletedCurrent = state.currentSessionId === s.session_id;
      await loadSessions();
      if (deletedCurrent) {
        const nextSessionId = (state.sessions[0] && state.sessions[0].session_id) || '';
        await switchSession(nextSessionId);
      }
    };
    actions.appendChild(delBtn);

    li.appendChild(actions);
    listEl.appendChild(li);
  });
}


function updateCurrentSessionLabel() {
  const labelEl = document.getElementById('current-session-label');
  if (!labelEl) return;
  const s = state.sessions.find(x => x.session_id === state.currentSessionId);
  const name = s ? (s.name || s.session_id) : (state.currentSessionId || '');
  if (name) {
    labelEl.textContent = name;
    labelEl.classList.remove('hidden');
  } else {
    labelEl.textContent = '';
    labelEl.classList.add('hidden');
  }
}

async function switchSession(sessionId) {
  state.currentSessionId = sessionId || '';
  if (state.currentSessionId) {
    localStorage.setItem('pithy.currentSessionId', state.currentSessionId);
  } else {
    localStorage.removeItem('pithy.currentSessionId');
  }
  updateCurrentSessionLabel();
  renderSessionList();
  chatLog.innerHTML = '';
  chatDebug.textContent = '';
  await refreshHistory();
}

async function loadSessions() {
  const data = await api('/api/sessions');
  state.sessions = data.sessions || [];
  // If current session no longer exists in list, clear current selection
  if (!state.sessions.find(s => s.session_id === state.currentSessionId)) {
    state.currentSessionId = '';
    localStorage.removeItem('pithy.currentSessionId');
  }
  renderSessionList();
  updateCurrentSessionLabel();
}

async function createNewSession() {
  const name = prompt(t('newSession') + '（输入名称，留空自动生成）', '');
  if (name === null) return; // cancelled
  const res = await api('/api/sessions', {
    method: 'POST',
    body: JSON.stringify({ name: name.trim() }),
  });
  await loadSessions();
  await switchSession(res.session_id);
}

async function loadConfig() {
  const cfg = await api('/api/config/model');
  document.getElementById('cfg-provider').value = cfg.provider;
  document.getElementById('cfg-model').value = cfg.model;
  document.getElementById('cfg-base-url').value = cfg.base_url || '';
  document.getElementById('cfg-api-key').value = '';
  document.getElementById('cfg-secret-key').value = '';
  document.getElementById('cfg-temperature').value = cfg.temperature;
  document.getElementById('cfg-max-tokens').value = cfg.max_tokens;
  const cwEl = document.getElementById('cfg-context-window');
  if (cwEl) cwEl.value = cfg.context_window || 8192;
  const toEl = document.getElementById('cfg-timeout');
  if (toEl) toEl.value = cfg.timeout_seconds || 60;
}

async function loadTools() {
  const tools = await api('/api/tools');
  const box = document.getElementById('tool-list');
  box.innerHTML = '';
  tools.forEach(tool => {
    const row = document.createElement('div');
    row.className = 'row';

    const info = document.createElement('span');
    const badge = tool.source === 'custom'
      ? `<span style="font-size:10px;padding:1px 6px;border-radius:999px;background:var(--accent-t);color:var(--accent);border:1px solid var(--accent-b);margin-left:5px;font-weight:700">自定义</span>`
      : '';
    info.innerHTML = `${tool.name} <span style="color:var(--text-3);font-size:11px">(${tool.risk_level})</span>${badge}`;

    const btns = document.createElement('div');
    btns.style.cssText = 'display:flex;gap:6px';

    const toggleBtn = document.createElement('button');
    toggleBtn.className = 'btn btn-ghost btn-sm';
    toggleBtn.textContent = tool.enabled ? '禁用' : '启用';
    toggleBtn.onclick = async () => {
      await api(`/api/tools/${tool.name}`, { method: 'PATCH', body: JSON.stringify({ enabled: !tool.enabled }) });
      await loadTools();
    };
    btns.appendChild(toggleBtn);

    if (tool.source === 'custom') {
      const delBtn = document.createElement('button');
      delBtn.className = 'btn btn-danger btn-sm';
      delBtn.textContent = '删除';
      delBtn.onclick = async () => {
        if (!confirm(`确认删除自定义工具「${tool.name}」？此操作不可撤销。`)) return;
        try {
          await api(`/api/tools/custom/${encodeURIComponent(tool.name)}`, { method: 'DELETE' });
          showToast(`工具「${tool.name}」已删除`, 'success');
          await loadTools();
          await loadToolManifests();
        } catch (e) {
          showToast(e.message, 'error');
          setSkillResult(e.message);
        }
      };
      btns.appendChild(delBtn);
    }

    row.appendChild(info);
    row.appendChild(btns);
    box.appendChild(row);
  });
}

async function refreshOcrStatus() {
  const status = await api('/api/tools/ocr/status');
  ocrStatusOutputEl.textContent = JSON.stringify(status, null, 2);
  return status;
}

async function loadToolManifests() {
  const manifests = await api('/api/tools/manifests');
  toolManifestsOutputEl.textContent = JSON.stringify(manifests, null, 2);
  return manifests;
}

async function getLatestSkill() {
  if (state.latestSkill) return state.latestSkill;
  const skills = await api('/api/skills');
  if (!skills.length) throw new Error(t('noSkill'));
  state.latestSkill = skills[0];
  return state.latestSkill;
}

async function loadSkillList() {
  const skills = await api('/api/skills');
  const box = document.getElementById('skill-list');
  if (!box) return;
  box.innerHTML = '';

  if (!skills.length) {
    box.innerHTML = `<div style="padding:16px;text-align:center;color:var(--text-3);font-size:13px">暂无技能，请先导入或创建</div>`;
    return;
  }

  skills.forEach(skill => {
    const row = document.createElement('div');
    row.className = 'row';
    row.style.cssText = 'flex-direction:column;align-items:stretch;gap:6px;padding:10px 12px';

    // Top row: name + badges + buttons
    const topRow = document.createElement('div');
    topRow.style.cssText = 'display:flex;align-items:center;gap:8px';

    const nameSpan = document.createElement('span');
    nameSpan.style.cssText = 'font-weight:700;font-size:13px;color:var(--text-1);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
    nameSpan.textContent = skill.name;

    const versionBadge = document.createElement('span');
    versionBadge.style.cssText = 'font-size:10px;padding:1px 6px;border-radius:999px;background:var(--surface-3);color:var(--text-3);border:1px solid var(--border);white-space:nowrap;flex-shrink:0';
    versionBadge.textContent = `v${skill.version}`;

    const enabledBadge = document.createElement('span');
    enabledBadge.style.cssText = `font-size:10px;padding:1px 6px;border-radius:999px;flex-shrink:0;white-space:nowrap;${skill.enabled
      ? 'background:rgba(24,160,88,.1);color:#18a058;border:1px solid rgba(24,160,88,.25)'
      : 'background:var(--surface-3);color:var(--text-3);border:1px solid var(--border)'}`;
    enabledBadge.textContent = skill.enabled ? '已启用' : '已禁用';

    const btns = document.createElement('div');
    btns.style.cssText = 'display:flex;gap:4px;flex-shrink:0';

    const toggleBtn = document.createElement('button');
    toggleBtn.className = 'btn btn-ghost btn-sm';
    toggleBtn.textContent = skill.enabled ? '禁用' : '启用';
    toggleBtn.onclick = async () => {
      try {
        await api(`/api/skills/${skill.id}`, { method: 'PATCH', body: JSON.stringify({ enabled: !skill.enabled }) });
        showToast(`技能「${skill.name}」已${skill.enabled ? '禁用' : '启用'}`, 'success');
        await loadSkillList();
      } catch (e) { showToast(e.message, 'error'); }
    };

    const runBtn = document.createElement('button');
    runBtn.className = 'btn btn-ghost btn-sm';
    runBtn.textContent = '▶ 运行';
    runBtn.onclick = async () => {
      try {
        const res = await api(`/api/skills/${skill.id}/run`, {
          method: 'POST',
          body: JSON.stringify({ input_text: '执行技能', context: {} }),
        });
        setSkillResult(res);
        showToast('技能运行完成', 'success');
      } catch (e) { setSkillResult(e.message); showToast(e.message, 'error'); }
    };

    const delBtn = document.createElement('button');
    delBtn.className = 'btn btn-danger btn-sm';
    delBtn.textContent = '删除';
    delBtn.onclick = async () => {
      if (!confirm(`确认删除技能「${skill.name}」及其所有版本？此操作不可撤销。`)) return;
      try {
        await api(`/api/skills/${skill.id}`, { method: 'DELETE' });
        showToast(`技能「${skill.name}」已删除`, 'success');
        state.latestSkill = null;
        await loadSkillList();
      } catch (e) { showToast(e.message, 'error'); }
    };

    btns.appendChild(toggleBtn);
    btns.appendChild(runBtn);
    btns.appendChild(delBtn);
    topRow.appendChild(nameSpan);
    topRow.appendChild(versionBadge);
    topRow.appendChild(enabledBadge);
    topRow.appendChild(btns);

    row.appendChild(topRow);

    // Description row
    if (skill.description) {
      const desc = document.createElement('div');
      desc.style.cssText = 'font-size:12px;color:var(--text-3);line-height:1.5';
      desc.textContent = skill.description;
      row.appendChild(desc);
    }

    // Meta row
    const meta = document.createElement('div');
    meta.style.cssText = 'font-size:11px;color:var(--text-4)';
    meta.textContent = `ID: ${skill.id} · 创建于 ${skill.created_at || ''}`;
    row.appendChild(meta);

    box.appendChild(row);
  });
}

async function loadSkillVersions() {
  const latest = await getLatestSkill();
  const data = await api(`/api/skills/${latest.id}/versions`);
  skillVersionsEl.textContent = JSON.stringify(data.versions, null, 2);
  const oldValue = skillVersionSelectEl.value;
  skillVersionSelectEl.innerHTML = '<option value="">请选择版本</option>';
  data.versions.forEach(v => {
    const option = document.createElement('option');
    option.value = String(v.version_id);
    option.textContent = `#${v.version_id} ${v.version} (${v.source})`;
    skillVersionSelectEl.appendChild(option);
  });
  if (oldValue && data.versions.some(v => String(v.version_id) === oldValue)) {
    skillVersionSelectEl.value = oldValue;
  } else if (data.versions.length) {
    skillVersionSelectEl.value = String(data.versions[0].version_id);
  }
  return { latest, versions: data.versions };
}

function getTargetVersionId() {
  const manualValue = document.getElementById('skill-version-id').value.trim();
  if (manualValue) return Number(manualValue);
  return Number(skillVersionSelectEl.value || 0);
}

async function copyExportContent() {
  if (!state.latestExportContent) throw new Error(t('noExport'));
  if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error('clipboard API unavailable');
  await navigator.clipboard.writeText(state.latestExportContent);
  setSkillResult(t('copied'));
}

function downloadExportContent() {
  if (!state.latestExportContent) throw new Error(t('noExport'));
  const ext = state.latestExportFormat === 'yaml' ? 'yaml' : 'json';
  const blob = new Blob([state.latestExportContent], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${state.latestExportName}.${ext}`;
  a.click();
  URL.revokeObjectURL(url);
  setSkillResult(`downloaded ${state.latestExportName}.${ext}`);
}

async function refreshLogs() {
  const limit = Number(document.getElementById('pref-log-lines').value || (state.settings && state.settings.log_lines) || 120);
  const level = document.getElementById('log-level-filter').value || (state.settings && state.settings.log_level) || '';
  const search = document.getElementById('log-search').value || '';
  const params = new URLSearchParams({ limit: String(limit) });
  if (level) params.set('level', level);
  if (search) params.set('search', search);
  const data = await api(`/api/logs?${params.toString()}`);
  logsOutputEl.textContent = data.lines.join('\n');
}

async function refreshReleaseInfo() {
  const info = await api('/api/release/info');
  releaseInfoOutputEl.textContent = JSON.stringify(info, null, 2);
  return info;
}

function scheduleLogRefresh() {
  if (state.logTimer) clearInterval(state.logTimer);
  if (state.settings && state.settings.auto_refresh_logs) {
    state.logTimer = setInterval(() => {
      if (!state.security || !state.security.locked) refreshLogs().catch(() => {});
    }, 5000);
  }
}

async function loadProtectedData() {
  // Sessions first (non-blocking: failures must not prevent history/config from loading)
  try { await loadSessions(); } catch (e) { console.warn('loadSessions failed:', e.message); }
  await refreshHistory();
  await loadConfig();
  await refreshReleaseInfo();
  await loadTools();
  await refreshOcrStatus();
  await loadToolManifests();
  try {
    state.latestSkill = null;
    await loadSkillList();
    await loadSkillVersions();
  } catch (e) {
    skillVersionsEl.textContent = String(e.message || e);
  }
  await refreshLogs();
}

document.getElementById('stop-btn').onclick = () => {
  if (state._abortController) {
    state._abortController.abort();
    state._abortController = null;
  }
};

document.getElementById('send-btn').onclick = async () => {
  const input = document.getElementById('chat-input');
  const msg = input.value.trim();
  if (!msg) return;
  appendLine('user', msg);
  input.value = '';
  autoResizeTextarea(input);
  updateCharCounter(input);

  const sendBtn = document.getElementById('send-btn');
  const stopBtn = document.getElementById('stop-btn');
  sendBtn.classList.add('btn-loading');
  sendBtn.disabled = true;
  stopBtn.classList.remove('hidden');
  input.disabled = true;

  // Create abort controller for this request
  state._abortController = new AbortController();

  // Remove old empty state
  const empty = chatLog.querySelector('.chat-empty');
  if (empty) empty.remove();

  // Create step indicator row
  const stepRow = document.createElement('div');
  stepRow.id = 'step-indicator';
  stepRow.style.cssText = 'padding:4px 20px 2px;';
  const stepPill = document.createElement('div');
  stepPill.style.cssText = 'display:inline-flex;align-items:center;gap:7px;font-size:12px;color:var(--text-3);padding:5px 12px;background:var(--surface-3);border:1px solid var(--border);border-radius:999px;max-width:90%;';
  stepPill.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink:0;animation:spin .8s linear infinite"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg><span id="step-text">准备中…</span>';
  stepRow.appendChild(stepPill);
  chatLog.appendChild(stepRow);
  chatLog.scrollTop = chatLog.scrollHeight;

  // AI bubble (streaming into it)
  const aiRow = document.createElement('div');
  aiRow.className = 'msg-row assistant';
  const aiWrap = document.createElement('div');
  aiWrap.className = 'msg-content-wrap';
  const aiAvatar = document.createElement('div');
  aiAvatar.className = 'msg-avatar';
  aiAvatar.textContent = 'AI';
  const aiBubble = document.createElement('div');
  aiBubble.className = 'msg-bubble';
  aiBubble.innerHTML = '<div class="msg-thinking"><span></span><span></span><span></span></div>';
  aiWrap.appendChild(aiAvatar);
  aiWrap.appendChild(aiBubble);
  aiRow.appendChild(aiWrap);

  let fullText = '';
  let streamStarted = false;

  const headers = { 'Content-Type': 'application/json' };
  if (state.sessionToken) headers['X-Session-Token'] = state.sessionToken;

  try {
    const resp = await fetch('/api/chat/stream', {
      method: 'POST',
      headers,
      body: JSON.stringify({ message: msg, session_id: state.currentSessionId }),
      signal: state._abortController.signal,
    });

    if (!resp.ok) {
      const errData = await resp.json().catch(() => ({}));
      throw new Error(errData.detail || `HTTP ${resp.status}`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      const parts = buf.split('\n\n');
      buf = parts.pop(); // keep incomplete chunk

      for (const part of parts) {
        const line = part.trim();
        if (!line || line.startsWith(':')) continue; // skip empty lines and keepalive comments
        if (!line.startsWith('data:')) continue;
        let evt;
        try { evt = JSON.parse(line.slice(5).trim()); } catch { continue; }

        if (evt.type === 'trace') {
          // Store trace id for debug
          chatDebug.textContent = `trace_id: ${evt.trace_id}`;

        } else if (evt.type === 'plan') {
          // Render plan as a collapsible card above the AI bubble
          const planCard = document.createElement('div');
          planCard.className = 'plan-card';
          const header = document.createElement('div');
          header.className = 'plan-card-header';
          header.innerHTML = `<span class="plan-icon">🗺</span><strong>执行计划</strong><span class="plan-badge">${evt.steps.length} 步</span><span class="plan-toggle">▾</span>`;
          const body = document.createElement('div');
          body.className = 'plan-card-body';
          (evt.steps || []).forEach(s => {
            const item = document.createElement('div');
            item.className = 'plan-step-item';
            item.dataset.stepIndex = s.index;
            const typeIcon = s.type === 'tool' ? '🔧' : '💭';
            item.innerHTML = `<span class="plan-step-num">${s.index}</span><span class="plan-step-icon">${typeIcon}</span><span class="plan-step-task">${s.task}</span>${s.tool ? `<span class="plan-step-tool">${s.tool}</span>` : ''}`;
            body.appendChild(item);
          });
          header.onclick = () => {
            body.classList.toggle('hidden');
            header.querySelector('.plan-toggle').textContent = body.classList.contains('hidden') ? '▸' : '▾';
          };
          planCard.appendChild(header);
          planCard.appendChild(body);
          const si = document.getElementById('step-indicator');
          if (si) chatLog.insertBefore(planCard, si);
          else chatLog.appendChild(planCard);
          chatLog.scrollTop = chatLog.scrollHeight;

        } else if (evt.type === 'step_start') {
          // Highlight the active plan step
          const stepEl = document.querySelector(`.plan-step-item[data-step-index="${evt.index}"]`);
          if (stepEl) {
            document.querySelectorAll('.plan-step-item.active').forEach(el => el.classList.remove('active'));
            stepEl.classList.add('active');
          }

        } else if (evt.type === 'step_done') {
          // Mark step as complete
          const stepEl = document.querySelector(`.plan-step-item[data-step-index="${evt.index}"]`);
          if (stepEl) {
            stepEl.classList.remove('active');
            stepEl.classList.add('done');
            stepEl.querySelector('.plan-step-icon').textContent = '✅';
          }

        } else if (evt.type === 'step') {
          const stepText = document.getElementById('step-text');
          if (stepText) {
            const icon = _stepIcon(evt.step);
            stepText.textContent = `${icon} ${evt.detail}`;
          }
          chatLog.scrollTop = chatLog.scrollHeight;

        } else if (evt.type === 'token') {
          if (!streamStarted) {
            // Replace thinking dots with real content
            aiBubble.innerHTML = '';
            chatLog.appendChild(aiRow);
            streamStarted = true;
          }
          fullText += evt.text;
          aiBubble.innerHTML = renderMarkdown(fullText);
          chatLog.scrollTop = chatLog.scrollHeight;

        } else if (evt.type === 'done') {
          // Finalize
          if (!streamStarted) {
            chatLog.appendChild(aiRow);
          }
          enhanceCodeBlocks(aiBubble);
          // Add action bar
          const aiActionBar = _makeActionBar('assistant', fullText);
          aiRow.appendChild(aiActionBar);
          const timeEl = document.createElement('span');
          timeEl.className = 'msg-time';
          timeEl.textContent = formatTime(new Date());
          aiRow.appendChild(timeEl);

          // Show token usage badge if available
          if (evt.token_usage && evt.token_usage.total_tokens > 0) {
            const usageBadge = document.createElement('div');
            usageBadge.className = 'token-usage-badge';
            usageBadge.title = `prompt: ${evt.token_usage.prompt_tokens} + completion: ${evt.token_usage.completion_tokens}`;
            usageBadge.textContent = `${evt.token_usage.total_tokens} tokens`;
            aiRow.appendChild(usageBadge);
          }

          chatDebug.textContent = JSON.stringify(evt, null, 2);
          const actualSessionId = evt.session_id || state.currentSessionId || '';
          const sessionChanged = actualSessionId !== state.currentSessionId;
          state.currentSessionId = actualSessionId;
          if (state.currentSessionId) localStorage.setItem('pithy.currentSessionId', state.currentSessionId);
          if (evt.session_name) {
            const existing = state.sessions.find(s => s.session_id === actualSessionId);
            if (existing) existing.name = evt.session_name;
          }
          await loadSessions().catch(() => {});
          if (sessionChanged) await switchSession(actualSessionId);
          else updateCurrentSessionLabel();

        } else if (evt.type === 'error') {
          aiBubble.textContent = evt.message;
          aiBubble.style.color = 'var(--danger)';
          if (!streamStarted) chatLog.appendChild(aiRow);
        }
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      // User aborted - show subtle indicator
      if (streamStarted) {
        enhanceCodeBlocks(aiBubble);
      } else if (!streamStarted) {
        aiBubble.innerHTML = '<span style="color:var(--text-3);font-size:12px">已停止生成</span>';
        chatLog.appendChild(aiRow);
      }
    } else {
      appendLine('error', e.message);
    }
  } finally {
    // Remove step indicator
    const si = document.getElementById('step-indicator');
    if (si) si.remove();
    sendBtn.classList.remove('btn-loading');
    sendBtn.disabled = false;
    stopBtn.classList.add('hidden');
    state._abortController = null;
    input.disabled = false;
    input.focus();
  }
};

function _stepIcon(step) {
  const icons = {
    memory: '🧠', think: '💭', thought: '💡', tool: '🔧',
    tool_done: '✅', answer: '✍️', error: '❌',
  };
  return icons[step] || '⚡';
}

document.getElementById('chat-input').addEventListener('keydown', (event) => {
  const shortcut = ((state.settings && state.settings.send_shortcut) || 'Ctrl+Enter').toLowerCase();
  if (shortcut === 'ctrl+enter' && event.ctrlKey && event.key === 'Enter') {
    event.preventDefault();
    document.getElementById('send-btn').click();
  }
});

// Auto-resize & char counter
function updateCharCounter(el) {
  const counter = document.getElementById('char-counter');
  if (!counter) return;
  const len = el.value.length;
  const limit = 4000;
  counter.textContent = len > 200 ? `${len}` : '';
  counter.classList.toggle('near-limit', len >= limit * 0.8);
  counter.classList.toggle('at-limit', len >= limit);
}

const chatInput = document.getElementById('chat-input');
chatInput.addEventListener('input', () => {
  autoResizeTextarea(chatInput);
  updateCharCounter(chatInput);
});

// Suggestion chips
document.querySelectorAll('.chip[data-msg]').forEach(chip => {
  chip.addEventListener('click', () => {
    chatInput.value = chip.dataset.msg;
    autoResizeTextarea(chatInput);
    updateCharCounter(chatInput);
    chatInput.focus();
  });
});

document.getElementById('refresh-history').onclick = () => refreshHistory().catch(e => showError(e.message));

document.getElementById('export-session-btn') && (document.getElementById('export-session-btn').onclick = async () => {
  if (!state.currentSessionId) { showToast('请先选择一个会话', 'error'); return; }
  try {
    const fmt = 'markdown';
    const res = await api(`/api/sessions/${encodeURIComponent(state.currentSessionId)}/export?format=${fmt}`);
    const blob = new Blob([res.content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = res.filename; a.click();
    URL.revokeObjectURL(url);
    showToast(`已导出 ${res.filename}`, 'success');
  } catch (e) { showToast(e.message, 'error'); }
});

document.getElementById('save-settings').onclick = async () => {
  try {
    const payload = {
      theme: document.getElementById('pref-theme').value,
      language: document.getElementById('pref-language').value,
      log_lines: Number(document.getElementById('pref-log-lines').value || 120),
      log_level: document.getElementById('pref-log-level').value,
      auto_refresh_logs: document.getElementById('pref-auto-refresh-logs').checked,
      send_shortcut: document.getElementById('pref-send-shortcut').value || 'Ctrl+Enter',
      system_prompt: (document.getElementById('pref-system-prompt') || {}).value || '',
    };
    state.settings = await api('/api/settings', { method: 'PUT', body: JSON.stringify(payload) });
    applyTheme(state.settings.theme);
    applyTranslations();
    scheduleLogRefresh();
    showToast(t('saveSuccess'), 'success');
    setSkillResult(t('saveSuccess'));
  } catch (e) {
    setSkillResult(e.message);
  }
};

document.getElementById('save-system-prompt') && (document.getElementById('save-system-prompt').onclick = async () => {
  try {
    const sysPEl = document.getElementById('pref-system-prompt');
    if (!sysPEl) return;
    const payload = {
      ...(state.settings || {}),
      system_prompt: sysPEl.value || '',
    };
    state.settings = await api('/api/settings', { method: 'PUT', body: JSON.stringify(payload) });
    showToast('系统提示词已保存', 'success');
  } catch (e) {
    showToast(e.message, 'error');
  }
});

document.getElementById('save-cfg').onclick = async () => {
  const payload = {
    provider: document.getElementById('cfg-provider').value || 'mock',
    model: document.getElementById('cfg-model').value || 'mock-model',
    base_url: document.getElementById('cfg-base-url').value,
    api_key: document.getElementById('cfg-api-key').value,
    secret_key: document.getElementById('cfg-secret-key').value,
    temperature: Number(document.getElementById('cfg-temperature').value || 0.5),
    max_tokens: Number(document.getElementById('cfg-max-tokens').value || 2048),
    context_window: Number((document.getElementById('cfg-context-window') || {}).value || 8192),
    timeout_seconds: Number((document.getElementById('cfg-timeout') || {}).value || 60),
  };
  try {
    await api('/api/config/model', { method: 'PUT', body: JSON.stringify(payload) });
    await loadConfig();
    setSkillResult(t('saveSuccess'));
  } catch (e) {
    setSkillResult(e.message);
  }
};

document.getElementById('test-cfg').onclick = async () => {
  try {
    const res = await api('/api/config/model/test', { method: 'POST' });
    setSkillResult(res);
  } catch (e) {
    setSkillResult(e.message);
  }
};

document.getElementById('visual-add-step').onclick = () => {
  try {
    const kind = document.getElementById('visual-step-kind').value;
    const name = document.getElementById('visual-step-name').value.trim();
    const raw = document.getElementById('visual-step-params').value.trim() || '{}';
    const params = JSON.parse(raw);
    validateVisualStep(kind, name, params);
    state.visualSteps.push({ kind, name, params });
    visualStepFeedbackEl.textContent = `已添加步骤 #${state.visualSteps.length}`;
    renderVisualSteps();
  } catch (e) {
    visualStepFeedbackEl.textContent = e.message;
    setSkillResult(e.message);
  }
};

document.getElementById('visual-clear-steps').onclick = () => {
  state.visualSteps = [];
  visualStepFeedbackEl.textContent = '步骤已清空';
  renderVisualSteps();
};

document.getElementById('visual-build-skill').onclick = () => {
  try {
    if (!state.visualSteps.length) throw new Error('至少添加一个步骤');
    for (const step of state.visualSteps) {
      validateVisualStep(step.kind, step.name, step.params || {});
    }
    const spec = buildVisualSkillSpec();
    document.getElementById('skill-json').value = JSON.stringify(spec, null, 2);
    setSkillResult({ ok: true, visual_steps: spec.steps.length });
    visualStepFeedbackEl.textContent = '技能 JSON 生成成功';
  } catch (e) {
    visualStepFeedbackEl.textContent = e.message;
    setSkillResult(e.message);
  }
};

document.getElementById('visual-copy-step').onclick = () => {
  try {
    const idx = getSelectedStepIndex();
    const source = state.visualSteps[idx];
    const clone = JSON.parse(JSON.stringify(source));
    clone.name = `${clone.name}_copy`;
    state.visualSteps.splice(idx + 1, 0, clone);
    document.getElementById('visual-selected-step').value = String(idx + 2);
    visualStepFeedbackEl.textContent = `已复制步骤 #${idx + 1}`;
    renderVisualSteps();
  } catch (e) {
    visualStepFeedbackEl.textContent = e.message;
    setSkillResult(e.message);
  }
};

document.getElementById('visual-move-up').onclick = () => {
  try {
    const idx = getSelectedStepIndex();
    if (idx === 0) throw new Error('已是第一个步骤');
    const [item] = state.visualSteps.splice(idx, 1);
    state.visualSteps.splice(idx - 1, 0, item);
    document.getElementById('visual-selected-step').value = String(idx);
    visualStepFeedbackEl.textContent = `步骤 #${idx + 1} 已上移`;
    renderVisualSteps();
  } catch (e) {
    visualStepFeedbackEl.textContent = e.message;
    setSkillResult(e.message);
  }
};

document.getElementById('visual-move-down').onclick = () => {
  try {
    const idx = getSelectedStepIndex();
    if (idx >= state.visualSteps.length - 1) throw new Error('已是最后一个步骤');
    const [item] = state.visualSteps.splice(idx, 1);
    state.visualSteps.splice(idx + 1, 0, item);
    document.getElementById('visual-selected-step').value = String(idx + 2);
    visualStepFeedbackEl.textContent = `步骤 #${idx + 1} 已下移`;
    renderVisualSteps();
  } catch (e) {
    visualStepFeedbackEl.textContent = e.message;
    setSkillResult(e.message);
  }
};

document.getElementById('visual-delete-step').onclick = () => {
  try {
    const idx = getSelectedStepIndex();
    state.visualSteps.splice(idx, 1);
    const next = Math.min(idx + 1, state.visualSteps.length);
    document.getElementById('visual-selected-step').value = next ? String(next) : '';
    visualStepFeedbackEl.textContent = `已删除步骤 #${idx + 1}`;
    renderVisualSteps();
  } catch (e) {
    visualStepFeedbackEl.textContent = e.message;
    setSkillResult(e.message);
  }
};

document.getElementById('refresh-ocr-status').onclick = () => refreshOcrStatus().catch(e => setSkillResult(e.message));

document.getElementById('import-tool').onclick = async () => {
  try {
    const payload = JSON.parse(document.getElementById('tool-manifest-json').value);
    const res = await api('/api/tools/import', { method: 'POST', body: JSON.stringify(payload) });
    setSkillResult(res);
    await loadTools();
    await loadToolManifests();
  } catch (e) {
    setSkillResult(e.message);
  }
};

document.getElementById('refresh-tool-manifests').onclick = async () => {
  try {
    await loadTools();
    const manifests = await loadToolManifests();
    setSkillResult({ custom_tools: manifests.length });
  } catch (e) {
    setSkillResult(e.message);
  }
};

document.getElementById('run-custom-tool').onclick = async () => {
  try {
    const toolName = document.getElementById('tool-run-name').value.trim();
    if (!toolName) throw new Error('请输入工具名');
    const raw = document.getElementById('tool-run-params').value.trim() || '{}';
    const params = JSON.parse(raw);
    const res = await api(`/api/tools/${toolName}/execute`, {
      method: 'POST',
      body: JSON.stringify({ params, authorized: true }),
    });
    setSkillResult(res);
  } catch (e) {
    setSkillResult(e.message);
  }
};

document.getElementById('save-skill').onclick = async () => {
  try {
    state.latestSkill = null;
    const payload = JSON.parse(document.getElementById('skill-json').value);
    const res = await api('/api/skills', { method: 'POST', body: JSON.stringify(payload) });
    setSkillResult(`skill id: ${res.id}`);
    await loadSkillVersions();
  } catch (e) {
    setSkillResult(e.message);
  }
};

document.getElementById('run-latest-skill').onclick = async () => {
  try {
    const latest = await getLatestSkill();
    const res = await api(`/api/skills/${latest.id}/run`, {
      method: 'POST',
      body: JSON.stringify({ input_text: '请根据输入执行技能', context: {} }),
    });
    setSkillResult(res);
  } catch (e) {
    setSkillResult(e.message);
  }
};

document.getElementById('refresh-skill-list').onclick = () => loadSkillList().catch(e => showToast(e.message, 'error'));

document.getElementById('import-skill').onclick = async () => {
  try {
    state.latestSkill = null;
    const payload = {
      format: document.getElementById('skill-import-format').value,
      content: document.getElementById('skill-import-content').value,
    };
    const res = await api('/api/skills/import', { method: 'POST', body: JSON.stringify(payload) });
    setSkillResult(res);
    showToast(`技能「${res.name}」导入成功`, 'success');
    await loadSkillList();
    await loadSkillVersions().catch(() => {});
  } catch (e) {
    setSkillResult(e.message);
    showToast(e.message, 'error');
  }
};

// Skill package (zip) import
document.getElementById('skill-package-file').addEventListener('change', async (e) => {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  document.getElementById('skill-package-label').textContent = file.name;
  const resultEl = document.getElementById('skill-import-result');
  resultEl.classList.remove('hidden');
  resultEl.textContent = '正在导入…';
  try {
    const formData = new FormData();
    formData.append('file', file);
    const headers = {};
    if (state.sessionToken) headers['X-Session-Token'] = state.sessionToken;
    const resp = await fetch('/api/skills/import/package', { method: 'POST', headers, body: formData });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || JSON.stringify(data));
    resultEl.textContent = JSON.stringify(data, null, 2);
    showToast(`技能包导入完成，共 ${data.imported} 个技能`, 'success');
    state.latestSkill = null;
    await loadSkillList();
  } catch (err) {
    resultEl.textContent = err.message;
    showToast(err.message, 'error');
  }
  // reset so same file can be re-selected
  e.target.value = '';
});

document.getElementById('load-skill-versions').onclick = async () => {
  try {
    const res = await loadSkillVersions();
    setSkillResult({ skill_id: res.latest.id, total_versions: res.versions.length });
  } catch (e) {
    setSkillResult(e.message);
  }
};

document.getElementById('export-latest-skill').onclick = async () => {
  try {
    const latest = await getLatestSkill();
    const fmt = document.getElementById('skill-export-format').value;
    const versionId = getTargetVersionId();
    const params = new URLSearchParams({ format: fmt });
    if (versionId) params.set('version_id', versionId);
    const res = await api(`/api/skills/${latest.id}/export?${params.toString()}`);
    state.latestExportContent = res.content;
    state.latestExportFormat = res.format;
    state.latestExportName = `${res.name}-${res.version}`;
    setSkillResult(res);
    document.getElementById('skill-json').value = res.content;
  } catch (e) {
    setSkillResult(e.message);
  }
};

document.getElementById('rollback-skill').onclick = async () => {
  try {
    const latest = await getLatestSkill();
    const versionId = getTargetVersionId();
    if (!versionId) throw new Error('请先输入 version_id');
    const res = await api(`/api/skills/${latest.id}/rollback`, {
      method: 'POST',
      body: JSON.stringify({ target_version_id: versionId, reason: 'ui-rollback' }),
    });
    setSkillResult(res);
    await loadSkillVersions();
  } catch (e) {
    setSkillResult(e.message);
  }
};

document.getElementById('copy-export-content').onclick = () => copyExportContent().catch(e => setSkillResult(e.message));
document.getElementById('download-export-content').onclick = () => {
  try {
    downloadExportContent();
  } catch (e) {
    setSkillResult(e.message);
  }
};
document.getElementById('refresh-logs').onclick = () => refreshLogs().catch(e => setSkillResult(e.message));
document.getElementById('refresh-release-info').onclick = () => refreshReleaseInfo().catch(e => setSkillResult(e.message));

document.getElementById('new-session-btn').onclick = () => createNewSession().catch(e => showError(e.message));

document.getElementById('lock-app').onclick = async () => {
  try {
    await api('/api/security/lock', { method: 'POST' });
    state.sessionToken = '';
    localStorage.removeItem('pithy.sessionToken');
    await refreshSecurityStatus();
    updateLockUI();
  } catch (e) {
    setSkillResult(e.message);
  }
};

document.getElementById('setup-password-btn').onclick = async () => {
  try {
    const password = document.getElementById('setup-password').value;
    const res = await api('/api/security/setup', { method: 'POST', body: JSON.stringify({ password }) });
    state.sessionToken = res.token || '';
    localStorage.setItem('pithy.sessionToken', state.sessionToken);
    await refreshSecurityStatus();
    updateLockUI();
    lockFeedbackEl.textContent = t('passwordSet');
    await loadProtectedData();
  } catch (e) {
    lockFeedbackEl.textContent = e.message;
  }
};

document.getElementById('unlock-btn').onclick = async () => {
  try {
    const password = document.getElementById('unlock-password').value;
    const res = await api('/api/security/unlock', { method: 'POST', body: JSON.stringify({ password }) });
    state.sessionToken = res.token || '';
    localStorage.setItem('pithy.sessionToken', state.sessionToken);
    await refreshSecurityStatus();
    updateLockUI();
    lockFeedbackEl.textContent = t('unlockSuccess');
    await loadProtectedData();
  } catch (e) {
    lockFeedbackEl.textContent = e.message;
  }
};

document.getElementById('pref-language').addEventListener('change', () => {
  const language = document.getElementById('pref-language').value;
  state.settings = { ...(state.settings || {}), language };
  applyTranslations();
});

document.getElementById('pref-theme').addEventListener('change', () => {
  applyTheme(document.getElementById('pref-theme').value);
});

(async () => {
  try {
    initTabs();
    await refreshSecurityStatus();
    await loadAppSettings();
    updateLockUI();
    await refreshHealth();
    if (!state.security.locked) {
      await loadProtectedData();
    }
  } catch (e) {
    showError(`初始化失败: ${e.message}`);
  }
  setInterval(refreshHealth, 4000);
})();

