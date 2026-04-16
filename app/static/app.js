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

function appendLine(role, text) {
  const div = document.createElement('div');
  div.innerHTML = `<b>${role}:</b> ${text}`;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function showError(message) {
  appendLine('error', message);
  chatDebug.textContent = message;
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
  const history = await api('/api/history');
  chatLog.innerHTML = '';
  history.forEach(item => appendLine(item.role, item.content));
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
}

async function loadTools() {
  const tools = await api('/api/tools');
  const box = document.getElementById('tool-list');
  box.innerHTML = '';
  tools.forEach(tool => {
    const row = document.createElement('div');
    row.className = 'row';
    row.innerHTML = `<span>${tool.name} (${tool.risk_level})</span><button>${tool.enabled ? '禁用' : '启用'}</button>`;
    row.querySelector('button').onclick = async () => {
      await api(`/api/tools/${tool.name}`, { method: 'PATCH', body: JSON.stringify({ enabled: !tool.enabled }) });
      await loadTools();
    };
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
  await refreshHistory();
  await loadConfig();
  await refreshReleaseInfo();
  await loadTools();
  await refreshOcrStatus();
  await loadToolManifests();
  try {
    state.latestSkill = null;
    await loadSkillVersions();
  } catch (e) {
    skillVersionsEl.textContent = String(e.message || e);
  }
  await refreshLogs();
}

document.getElementById('send-btn').onclick = async () => {
  const msg = document.getElementById('chat-input').value.trim();
  if (!msg) return;
  appendLine('user', msg);
  document.getElementById('chat-input').value = '';
  try {
    const res = await api('/api/chat', { method: 'POST', body: JSON.stringify({ message: msg }) });
    appendLine('assistant', res.reply);
    chatDebug.textContent = JSON.stringify(res.brain || res, null, 2);
  } catch (e) {
    appendLine('error', e.message);
  }
};

document.getElementById('chat-input').addEventListener('keydown', (event) => {
  const shortcut = ((state.settings && state.settings.send_shortcut) || 'Ctrl+Enter').toLowerCase();
  if (shortcut === 'ctrl+enter' && event.ctrlKey && event.key === 'Enter') {
    event.preventDefault();
    document.getElementById('send-btn').click();
  }
});

document.getElementById('refresh-history').onclick = () => refreshHistory().catch(e => showError(e.message));

document.getElementById('save-settings').onclick = async () => {
  try {
    const payload = {
      theme: document.getElementById('pref-theme').value,
      language: document.getElementById('pref-language').value,
      log_lines: Number(document.getElementById('pref-log-lines').value || 120),
      log_level: document.getElementById('pref-log-level').value,
      auto_refresh_logs: document.getElementById('pref-auto-refresh-logs').checked,
      send_shortcut: document.getElementById('pref-send-shortcut').value || 'Ctrl+Enter',
    };
    state.settings = await api('/api/settings', { method: 'PUT', body: JSON.stringify(payload) });
    applyTheme(state.settings.theme);
    applyTranslations();
    scheduleLogRefresh();
    setSkillResult(t('saveSuccess'));
  } catch (e) {
    setSkillResult(e.message);
  }
};

document.getElementById('save-cfg').onclick = async () => {
  const payload = {
    provider: document.getElementById('cfg-provider').value || 'mock',
    model: document.getElementById('cfg-model').value || 'mock-model',
    base_url: document.getElementById('cfg-base-url').value,
    api_key: document.getElementById('cfg-api-key').value,
    secret_key: document.getElementById('cfg-secret-key').value,
    temperature: Number(document.getElementById('cfg-temperature').value || 0.5),
    max_tokens: Number(document.getElementById('cfg-max-tokens').value || 512),
    timeout_seconds: 30,
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

document.getElementById('import-skill').onclick = async () => {
  try {
    state.latestSkill = null;
    const payload = {
      format: document.getElementById('skill-import-format').value,
      content: document.getElementById('skill-import-content').value,
    };
    const res = await api('/api/skills/import', { method: 'POST', body: JSON.stringify(payload) });
    setSkillResult(res);
    await loadSkillVersions();
  } catch (e) {
    setSkillResult(e.message);
  }
};

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

