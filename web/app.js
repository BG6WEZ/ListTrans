// Safe HTML escaping utility
function escapeHtml(val) {
    if (val === null || val === undefined) return '';
    return String(val)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

let _isHandling401 = false;
const AUTH_STORAGE_KEYS = ['token', 'username', 'name', 'position', 'role', 'employee_no', 'dept1', 'must_change_password', 'user_id', 'account_epoch', 'actor_token_ref'];
const PENDING_FEEDBACK_STORAGE_KEY = 'listtrans_pending_feedback_v1';

let currentActorUserId = null;
let currentActorEpoch = '';
let _actorIdentityPromise = null;
let pendingFeedbackEvents = [];

function _syncActorVarsFromStorage() {
    try {
        const tok = localStorage.getItem('token') || '';
        const tokRef = localStorage.getItem('actor_token_ref') || '';
        if (tok && tokRef && tok !== tokRef) {
            currentActorUserId = null;
            currentActorEpoch = '';
            return;
        }
        const uidRaw = localStorage.getItem('user_id');
        currentActorUserId = (uidRaw !== null && uidRaw !== '' && !Number.isNaN(Number(uidRaw))) ? Number(uidRaw) : null;
        currentActorEpoch = String(localStorage.getItem('account_epoch') || '').trim();
    } catch (_) {
        currentActorUserId = null;
        currentActorEpoch = '';
    }
}
_syncActorVarsFromStorage();

function loadPendingFeedbackEventsFromStorage() {
    try {
        const raw = localStorage.getItem(PENDING_FEEDBACK_STORAGE_KEY);
        if (!raw) return [];
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed : [];
    } catch (_) {
        return [];
    }
}

function savePendingFeedbackEventsToStorage() {
    try {
        const toPersist = pendingFeedbackEvents.filter(rec => rec && rec.status !== 'delivered');
        localStorage.setItem(PENDING_FEEDBACK_STORAGE_KEY, JSON.stringify(toPersist));
    } catch (_) {}
}

pendingFeedbackEvents = loadPendingFeedbackEventsFromStorage();

function clearAuthStorage() {
    AUTH_STORAGE_KEYS.forEach(k => localStorage.removeItem(k));
    currentActorUserId = null;
    currentActorEpoch = '';
    pendingFeedbackEvents = [];
    try { localStorage.removeItem(PENDING_FEEDBACK_STORAGE_KEY); } catch (_) {}
    if (typeof renderFeedbackDeliveryBanner === 'function') {
        renderFeedbackDeliveryBanner();
    }
}

// Intercept all fetch requests to inject authentication headers and handle token expiration
const originalFetch = window.fetch;
window.fetch = async function (url, options) {
    options = options || {};
    options.headers = options.headers || {};
    
    const token = localStorage.getItem('token');
    
    let isApi = false;
    if (typeof url === 'string') {
        isApi = url.includes('/api/') || url.startsWith('api/');
    } else if (url && typeof url.url === 'string') {
        isApi = url.url.includes('/api/') || url.url.startsWith('api/');
    }
    
    if (token && isApi) {
        if (options.headers instanceof Headers) {
            options.headers.set('Authorization', `Bearer ${token}`);
        } else if (Array.isArray(options.headers)) {
            const hasAuth = options.headers.some(h => h[0].toLowerCase() === 'authorization');
            if (!hasAuth) {
                options.headers.push(['Authorization', `Bearer ${token}`]);
            }
        } else {
            options.headers['Authorization'] = `Bearer ${token}`;
        }
    }
    
    try {
        const response = await originalFetch(url, options);
        if (response.status === 401) {
            if (!_isHandling401) {
                _isHandling401 = true;
                clearAuthStorage();
                alert("您的登录会话已失效，请重新登录。");
                window.location.href = "login.html";
            }
        } else if (response.status === 403 && typeof window._triggerForcedPasswordModal === 'function') {
            try {
                const cloned = response.clone();
                const errJson = await cloned.json();
                if (errJson && typeof errJson.detail === 'string' && errJson.detail.includes('MUST_CHANGE_PASSWORD')) {
                    window._triggerForcedPasswordModal();
                }
            } catch (_) {}
        }
        return response;
    } catch (error) {
        console.error("Fetch network error:", error);
        throw error;
    }
};

function getAuthHeaders(extraHeaders = {}) {
    const token = localStorage.getItem('token') || '';
    const headers = {
        'Content-Type': 'application/json'
    };
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }
    return Object.assign(headers, extraHeaders);
}

let items = []; // Holds all matched rows from original Excel
let selectedItem = null; // Selected row dict
let currentFilter = 'all'; // Current filter tab
let searchQuery = ''; // Search query on left list
let currentProfessional = '全部'; // Active professional tab filter
let professionals = []; // List of professionals detected in uploaded list
let originalFileName = ""; // Uploaded original list filename
let currentDocumentId = ""; // Server-issued document ID for current upload/restored workbook
let currentUploadTaskId = ""; // Server upload task ID
let currentWorkSessionId = ""; // Server work session ID

function generateClientEventId(prefix = 'evt') {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID();
    }
    return prefix + '_' + Date.now() + '_' + Math.random().toString(36).slice(2, 11);
}

function isSameWorkbenchRow(a, b) {
    if (!a || !b) return false;
    if (a.source_row_id && b.source_row_id) {
        return a.source_row_id === b.source_row_id;
    }
    return a.original_row_idx === b.original_row_idx;
}

function renderLegacyIsolationBanner(legacyCount) {
    const existing = document.getElementById('legacy-isolation-banner');
    if (!legacyCount || legacyCount <= 0) {
        if (existing) existing.remove();
        return;
    }
    const workbench = document.getElementById('workbench');
    if (!workbench || !workbench.parentNode) return;
    let banner = existing;
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'legacy-isolation-banner';
        banner.style.cssText = 'background:#fff7ed;border:1px solid #fdba74;color:#9a3412;padding:8px 14px;font-size:12px;border-radius:6px;margin:8px 16px 0 16px;display:flex;align-items:center;gap:8px;';
        workbench.parentNode.insertBefore(banner, workbench);
    }
    banner.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i><span>旧草稿/未绑定可核验服务端文档身份（' + Number(legacyCount) + ' 条），已隔离为只读复核；不会上报可信飞轮反馈，如需采集新反馈请重新上传清单。</span>';
}

function _actorMatchesRecord(rec, userId = currentActorUserId, epoch = currentActorEpoch) {
    if (!rec) return false;
    if (userId !== null && userId !== undefined && Number(rec.actor_user_id) !== Number(userId)) {
        return false;
    }
    if (epoch && String(rec.actor_account_epoch || '') !== String(epoch)) {
        return false;
    }
    if (userId === null && !epoch && (rec.actor_user_id !== null || rec.actor_account_epoch)) {
        return false;
    }
    return true;
}

function purgePendingEventsExcept(userId, accountEpoch) {
    pendingFeedbackEvents = pendingFeedbackEvents.filter(rec =>
        rec && rec.status !== 'delivered' && _actorMatchesRecord(rec, userId, accountEpoch)
    );
    savePendingFeedbackEventsToStorage();
    renderFeedbackDeliveryBanner();
}

function purgePendingEventsForDocument(documentId) {
    if (!documentId) return;
    pendingFeedbackEvents = pendingFeedbackEvents.filter(rec =>
        rec && rec.document_id !== documentId
    );
    savePendingFeedbackEventsToStorage();
    renderFeedbackDeliveryBanner();
}

function applyVerifiedActorIdentity(userId, accountEpoch) {
    const nextId = (userId !== undefined && userId !== null && userId !== '') ? Number(userId) : null;
    const nextEpoch = String(accountEpoch || '').trim();
    const prevId = currentActorUserId;
    const prevEpoch = currentActorEpoch;
    if ((prevId !== null && nextId !== null && prevId !== nextId) ||
        (prevEpoch && nextEpoch && prevEpoch !== nextEpoch)) {
        purgePendingEventsExcept(nextId, nextEpoch);
    }
    currentActorUserId = nextId;
    currentActorEpoch = nextEpoch;
    try {
        if (nextId !== null && !Number.isNaN(nextId)) localStorage.setItem('user_id', String(nextId));
        if (nextEpoch) localStorage.setItem('account_epoch', nextEpoch);
        const tok = localStorage.getItem('token') || '';
        if (tok) localStorage.setItem('actor_token_ref', tok);
    } catch (_) {}
    renderFeedbackDeliveryBanner();
}

async function ensureVerifiedActorIdentity() {
    if (currentActorUserId !== null && currentActorEpoch) {
        return { actor_user_id: currentActorUserId, actor_account_epoch: currentActorEpoch };
    }
    if (_actorIdentityPromise) return _actorIdentityPromise;
    _actorIdentityPromise = (async () => {
        try {
            const token = (typeof localStorage !== 'undefined') ? localStorage.getItem('token') : '';
            const res = await fetch(`${backendUrl}/api/auth/me`, {
                headers: token ? { 'Authorization': `Bearer ${token}` } : {}
            });
            if (res && res.ok) {
                const u = await res.json().catch(() => null);
                if (u && u.id !== undefined && u.id !== null) {
                    applyVerifiedActorIdentity(u.id, u.account_epoch || '');
                }
            }
        } catch (_) {}
        return { actor_user_id: currentActorUserId, actor_account_epoch: currentActorEpoch };
    })();
    try {
        return await _actorIdentityPromise;
    } finally {
        _actorIdentityPromise = null;
    }
}

function getActivePendingFeedbackEvents() {
    if (!currentDocumentId || !Array.isArray(items) || items.length === 0) {
        return [];
    }
    return pendingFeedbackEvents.filter(rec => {
        if (!rec || rec.status === 'delivered') return false;
        if (!_actorMatchesRecord(rec)) return false;
        if (rec.document_id !== currentDocumentId) return false;
        return items.some(it =>
            it && it.feedback_eligible !== false &&
            it.document_id === rec.document_id &&
            it.sheet_id === rec.sheet_id &&
            it.source_row_id === rec.source_row_id
        );
    });
}

function reconcilePendingFeedbackForDocument() {
    if (!Array.isArray(items) || items.length === 0) {
        renderFeedbackDeliveryBanner();
        return;
    }
    const active = getActivePendingFeedbackEvents();
    items.forEach(it => {
        if (!it || it.feedback_eligible === false) return;
        const rowEvents = active.filter(rec =>
            rec.document_id === it.document_id &&
            rec.sheet_id === it.sheet_id &&
            rec.source_row_id === it.source_row_id
        );
        const maxPendingFw = rowEvents
            .filter(r => r.kind === 'flywheel')
            .reduce((mx, r) => Math.max(mx, Number(r.event_seq || 0)), 0);
        const maxPendingFb = rowEvents
            .filter(r => r.kind === 'feedback')
            .reduce((mx, r) => Math.max(mx, Number(r.event_seq || 0)), 0);
        it._event_seq = Math.max(Number(it._event_seq || 0), Number(it.max_event_seq || 0), maxPendingFw);
        it._feedback_seq = Math.max(Number(it._feedback_seq || 0), Number(it.max_feedback_seq || 0), maxPendingFb);
    });
    renderFeedbackDeliveryBanner();
}

function renderFeedbackDeliveryBanner() {
    if (typeof document === 'undefined') return;
    const existing = document.getElementById('feedback-delivery-banner');
    const active = getActivePendingFeedbackEvents();
    if (!active.length) {
        if (existing) existing.remove();
        return;
    }
    const workbench = document.getElementById('workbench');
    if (!workbench || !workbench.parentNode) return;
    let banner = existing;
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'feedback-delivery-banner';
        workbench.parentNode.insertBefore(banner, workbench);
    }
    const retryable = active.filter(r => ['pending', 'retrying', 'failed_retryable'].includes(r.status));
    const blocked = active.filter(r => ['blocked_conflict', 'blocked_invalid'].includes(r.status));
    const isBlockedOnly = retryable.length === 0 && blocked.length > 0;
    banner.style.cssText = isBlockedOnly
        ? 'background:#fef2f2;border:1px solid #fca5a5;color:#991b1b;padding:8px 14px;font-size:12px;border-radius:6px;margin:8px 16px 0 16px;display:flex;align-items:center;justify-content:space-between;gap:12px;'
        : 'background:#fff1f2;border:1px solid #fda4af;color:#9f1239;padding:8px 14px;font-size:12px;border-radius:6px;margin:8px 16px 0 16px;display:flex;align-items:center;justify-content:space-between;gap:12px;';

    const sampleErr = (retryable[0] || blocked[0]);
    const errText = sampleErr && sampleErr.last_error_message
        ? `${sampleErr.last_http_status ? 'HTTP ' + sampleErr.last_http_status : '网络异常'}: ${sampleErr.last_error_message}`
        : '等待重试';
    const parts = [];
    if (retryable.length > 0) {
        parts.push(`<strong>${retryable.length} 条反馈/飞轮事件投递失败待重试</strong>（已保留原事件ID与序号；${escapeHtml(errText)}）`);
    }
    if (blocked.length > 0) {
        parts.push(`<strong>${blocked.length} 条事件被服务端拒绝（409/422 已停止自动重试）</strong>（${escapeHtml(blocked[0].last_error_message || blocked[0].last_error_code)}；不影响当前确认/导出状态）`);
    }
    const buttonsHtml = [];
    if (retryable.length > 0) {
        buttonsHtml.push('<button type="button" id="retry-pending-feedback-btn" class="btn btn-warning" style="padding:4px 10px;font-size:11px;">重试待投递反馈</button>');
    }
    if (blocked.length > 0) {
        buttonsHtml.push('<button type="button" id="dismiss-blocked-feedback-btn" class="btn" style="padding:4px 10px;font-size:11px;">清除冲突/无效提示</button>');
    }
    banner.innerHTML = `<span><i class="fa-solid fa-circle-exclamation"></i> ${parts.join('；')}</span><div style="display:flex;gap:8px;flex-shrink:0;">${buttonsHtml.join('')}</div>`;
    const retryBtn = document.getElementById('retry-pending-feedback-btn');
    if (retryBtn) {
        retryBtn.addEventListener('click', () => {
            retryPendingFeedbackEvents();
        });
    }
    const dismissBtn = document.getElementById('dismiss-blocked-feedback-btn');
    if (dismissBtn) {
        dismissBtn.addEventListener('click', () => {
            const activeIds = new Set(blocked.map(b => b.event_id));
            pendingFeedbackEvents = pendingFeedbackEvents.filter(rec => !activeIds.has(rec.event_id));
            savePendingFeedbackEventsToStorage();
            renderFeedbackDeliveryBanner();
        });
    }
}

// Pagination variables
let currentPage = 1;
const pageSize = 50;
let totalPages = 1;

// Configuration variables
let configs = {
    alias: {},
    units: {},
    stop_words: [],
    tolerances: {},
    weights: {},
    boosts: {}
};

const backendUrl = (window.location.protocol.startsWith('http')) ? '' : 'http://127.0.0.1:18080';

// ==========================================
// Theme Management System (Phase 4)
// ==========================================
function getSystemTheme() {
    return (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark';
}

function getCurrentTheme() {
    return localStorage.getItem('listtrans_theme') || getSystemTheme();
}

function updateThemeToggleUI(theme) {
    const icon = document.getElementById('themeToggleIcon');
    if (icon) {
        if (theme === 'light') {
            icon.className = 'fa-solid fa-sun';
            icon.title = '当前为浅色主题，点击切换为深色';
        } else {
            icon.className = 'fa-solid fa-moon';
            icon.title = '当前为深色主题，点击切换为浅色';
        }
    }
}

function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('listtrans_theme', theme);
    updateThemeToggleUI(theme);
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || getCurrentTheme();
    const next = (current === 'light') ? 'dark' : 'light';
    applyTheme(next);
}

function initTheme() {
    const theme = document.documentElement.getAttribute('data-theme') || getCurrentTheme();
    updateThemeToggleUI(theme);
    const btn = document.getElementById('themeToggleBtn');
    if (btn) {
        btn.addEventListener('click', toggleTheme);
    }
    window.addEventListener('storage', (e) => {
        if (e.key === 'listtrans_theme' && e.newValue) {
            applyTheme(e.newValue);
        }
    });
}

document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    // Process User profile info: show Position (岗位) and Real Name (姓名)
    const username = localStorage.getItem('username') || '-';
    let displayName = localStorage.getItem('name') || (username === 'admin' ? '系统管理员' : username);
    let position = localStorage.getItem('position') || (username === 'admin' ? '系统管理员' : '');
    let role = localStorage.getItem('role') || 'employee';
    let employeeNo = localStorage.getItem('employee_no') || '-';
    let currentDept = localStorage.getItem('dept1') || '';
    
    const usernameLbl = document.getElementById('username-lbl');
    const roleLbl = document.getElementById('user-role-lbl');
    const empNoLbl = document.getElementById('user-emp-no-lbl');
    const adminLink = document.getElementById('admin-panel-link');
    const logoutBtn = document.getElementById('logout-btn');
    const btnAccountSettings = document.getElementById('btn-account-settings');
    
    // User logout logic
    async function performUserLogout() {
        if (!confirm('确定要退出登录当前账号吗？')) {
            return;
        }
        try {
            const token = localStorage.getItem('token');
            if (token) {
                await fetch(`${backendUrl}/api/auth/logout`, {
                    method: 'POST',
                    headers: {
                        'Authorization': `Bearer ${token}`,
                        'Content-Type': 'application/json'
                    }
                }).catch(() => {});
            }
        } catch (e) {
            console.warn('Logout notice:', e);
        } finally {
            clearAuthStorage();
            window.location.href = 'login.html';
        }
    }
    window.handleUserLogout = performUserLogout;
    if (logoutBtn) {
        logoutBtn.addEventListener('click', performUserLogout);
    }

    let forcedPasswordMode = (localStorage.getItem('must_change_password') === '1');
    const forcedBanner = document.getElementById('forced-password-banner');

    function applyForcedPasswordModalState(isForced) {
        forcedPasswordMode = Boolean(isForced);
        localStorage.setItem('must_change_password', forcedPasswordMode ? '1' : '0');
        if (forcedBanner) {
            forcedBanner.style.display = forcedPasswordMode ? 'block' : 'none';
        }
        if (closeAccountModalBtn) {
            closeAccountModalBtn.style.display = forcedPasswordMode ? 'none' : 'inline-block';
        }
        if (cancelAccountModalBtn) {
            cancelAccountModalBtn.style.display = forcedPasswordMode ? 'none' : 'inline-block';
        }
        const newPwdEl = document.getElementById('setting-new-password');
        const confirmPwdEl = document.getElementById('setting-confirm-password');
        if (newPwdEl) newPwdEl.required = forcedPasswordMode;
        if (confirmPwdEl) confirmPwdEl.required = forcedPasswordMode;
        if (forcedPasswordMode) {
            openAccountSettingsModal();
        }
    }
    window._triggerForcedPasswordModal = () => applyForcedPasswordModalState(true);
    
    function updateUserUI() {
        if (usernameLbl) {
            usernameLbl.innerText = displayName;
            usernameLbl.title = `姓名: ${displayName} | 账号: ${username}`;
        }
        if (empNoLbl) {
            empNoLbl.innerText = employeeNo;
            empNoLbl.title = `工号: ${employeeNo}`;
        }
        if (roleLbl) {
            let positionText = position;
            if (!positionText) {
                if (role === 'admin' || username === 'admin') positionText = '系统管理员';
                else if (role === 'director') positionText = '总监';
                else if (role === 'manager') positionText = '经理';
                else positionText = '员工';
            }
            roleLbl.innerText = positionText;
            roleLbl.title = `角色职位: ${positionText}`;
            if (empNoLbl) empNoLbl.title = `工号: ${employeeNo} (${positionText})`;
        }
        if (role === 'admin' && adminLink) {
            adminLink.style.display = 'inline-flex';
        }
    }
    updateUserUI();

    // Async sync latest profile from /api/auth/me to automatically fill position and real name
    const token = localStorage.getItem('token');
    if (token) {
        fetch(`${backendUrl}/api/auth/me`, {
            headers: { 'Authorization': `Bearer ${token}` }
        }).then(r => r.ok ? r.json() : null).then(userData => {
            if (userData) {
                applyVerifiedActorIdentity(userData.id, userData.account_epoch || '');
                if (userData.username) {
                    localStorage.setItem('username', userData.username);
                }
                if (userData.name) {
                    displayName = userData.name;
                    localStorage.setItem('name', userData.name);
                }
                if (userData.position) {
                    position = userData.position;
                    localStorage.setItem('position', userData.position);
                }
                if (userData.employee_no) {
                    employeeNo = userData.employee_no;
                    localStorage.setItem('employee_no', userData.employee_no);
                }
                if (userData.dept1) {
                    currentDept = userData.dept1;
                    localStorage.setItem('dept1', userData.dept1);
                }
                if (userData.role) {
                    role = userData.role;
                    localStorage.setItem('role', userData.role);
                }
                updateUserUI();
                applyForcedPasswordModalState(Boolean(userData.must_change_password));
            }
        }).catch(err => console.log('Profile sync notice:', err));

        // Start heartbeat loop (every 20s) to keep online presence active
        function sendHeartbeat() {
            const t = localStorage.getItem('token');
            if (!t) return;
            const hasData = (typeof items !== 'undefined' && items && items.length > 0);
            fetch(`${backendUrl}/api/auth/heartbeat`, {
                method: 'POST',
                headers: getAuthHeaders(),
                body: JSON.stringify({
                    current_page: 'workbench',
                    state: hasData ? 'active' : 'idle'
                })
            }).catch(() => {});
        }
        sendHeartbeat();
        setInterval(sendHeartbeat, 20000);
    }
    
    // Account Settings Modal (Phase 2)
    const accountModal = document.getElementById('account-settings-modal');
    const closeAccountModalBtn = document.getElementById('close-account-modal-btn');
    const cancelAccountModalBtn = document.getElementById('cancel-account-modal-btn');
    const accountForm = document.getElementById('account-settings-form');
    const accountMsg = document.getElementById('account-settings-msg');

    function openAccountSettingsModal() {
        if (!accountModal) return;
        document.getElementById('modal-emp-no').innerText = employeeNo || '-';
        document.getElementById('modal-emp-name').innerText = displayName || '-';
        document.getElementById('modal-emp-dept').innerText = `${currentDept || '未分配部门'} / ${position || '员工'}`;
        document.getElementById('setting-username').value = localStorage.getItem('username') || '';
        document.getElementById('setting-old-password').value = '';
        document.getElementById('setting-new-password').value = '';
        document.getElementById('setting-confirm-password').value = '';
        if (accountMsg) accountMsg.style.display = 'none';
        accountModal.style.display = 'flex';
    }

    function closeAccountSettingsModal() {
        if (forcedPasswordMode) return;
        if (accountModal) accountModal.style.display = 'none';
    }

    if (btnAccountSettings) {
        btnAccountSettings.addEventListener('click', openAccountSettingsModal);
    }
    if (closeAccountModalBtn) {
        closeAccountModalBtn.addEventListener('click', closeAccountSettingsModal);
    }
    if (cancelAccountModalBtn) {
        cancelAccountModalBtn.addEventListener('click', closeAccountSettingsModal);
    }

    if (accountForm) {
        accountForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const newUsername = document.getElementById('setting-username').value.trim();
            const oldPassword = document.getElementById('setting-old-password').value;
            const newPassword = document.getElementById('setting-new-password').value;
            const confirmPassword = document.getElementById('setting-confirm-password').value;

            if (forcedPasswordMode && !newPassword) {
                accountMsg.style.display = 'block';
                accountMsg.style.background = 'var(--color-danger-bg)';
                accountMsg.style.color = 'var(--color-danger)';
                accountMsg.innerText = '首次登录或密码重置后必须设置新密码。';
                return;
            }
            if (newPassword && newPassword !== confirmPassword) {
                accountMsg.style.display = 'block';
                accountMsg.style.background = 'var(--color-danger-bg)';
                accountMsg.style.color = 'var(--color-danger)';
                accountMsg.innerText = '两次输入的新密码不一致，请核对。';
                return;
            }

            const btnSave = document.getElementById('save-account-settings-btn');
            btnSave.disabled = true;
            btnSave.innerText = '正在保存...';

            try {
                const res = await fetch(`${backendUrl}/api/auth/profile`, {
                    method: 'PUT',
                    headers: getAuthHeaders(),
                    body: JSON.stringify({
                        new_username: newUsername,
                        old_password: oldPassword,
                        new_password: newPassword || null
                    })
                });
                const data = await res.json();
                if (!res.ok) {
                    throw new Error(data.detail || '修改失败');
                }

                forcedPasswordMode = false;
                localStorage.setItem('must_change_password', '0');
                accountMsg.style.display = 'block';
                accountMsg.style.background = 'var(--color-success-bg)';
                accountMsg.style.color = 'var(--color-success)';
                accountMsg.innerText = data.detail || '保存成功！旧会话已失效，请重新登录。';

                if (data.relogin_required) {
                    clearAuthStorage();
                    setTimeout(() => {
                        window.location.href = 'login.html';
                    }, 900);
                    return;
                }

                if (data.access_token) {
                    localStorage.setItem('token', data.access_token);
                }
                if (data.username) {
                    localStorage.setItem('username', data.username);
                }
                if (data.name) {
                    displayName = data.name;
                    localStorage.setItem('name', data.name);
                }
                updateUserUI();

                setTimeout(() => {
                    closeAccountSettingsModal();
                    btnSave.disabled = false;
                    btnSave.innerHTML = '<i class="fa-solid fa-check"></i> 保存设置';
                }, 1200);
            } catch (err) {
                accountMsg.style.display = 'block';
                accountMsg.style.background = 'var(--color-danger-bg)';
                accountMsg.style.color = 'var(--color-danger)';
                accountMsg.innerText = err.message || '更新账号失败';
                btnSave.disabled = false;
                btnSave.innerHTML = '<i class="fa-solid fa-check"></i> 保存设置';
            }
        });
    }

    // Role constraints in UI
    if (role === 'employee') {
        const navConfig = document.getElementById('nav-config');
        if (navConfig) {
            navConfig.style.display = 'none';
        }
    }

    if (role !== 'admin') {
        const saveAllConfigsBtn = document.getElementById('save-all-configs-btn');
        if (saveAllConfigsBtn) {
            saveAllConfigsBtn.disabled = true;
            saveAllConfigsBtn.title = "权限不足，只有管理员可以保存修改配置。";
        }
    }
    
    if (role !== 'admin' && role !== 'director') {
        const selectDbBtn = document.getElementById('btn-select-db-file');
        const startDbImportBtn = document.getElementById('btn-start-db-import');
        if (selectDbBtn) {
            selectDbBtn.disabled = true;
            selectDbBtn.title = "权限不足，只有总监和管理员可以导入物料库。";
        }
        if (startDbImportBtn) {
            startDbImportBtn.disabled = true;
            startDbImportBtn.title = "权限不足，只有总监和管理员可以导入物料库。";
        }
    }

    initEventListeners();
    setupNavigation();
    
    // Check for auto-saved progress drafts on startup
    checkForDraft();
    startAutoSave();
});

function setupNavigation() {
    const navWorkbench = document.getElementById('nav-workbench');
    const navMaterials = document.getElementById('nav-materials');
    const navLlmRules = document.getElementById('nav-llm-rules');
    const navConfig = document.getElementById('nav-config');
    const navGuide = document.getElementById('nav-guide');
    
    const workbenchEl = document.getElementById('workbench');
    const materialsDashboardEl = document.getElementById('materials-dashboard');
    const llmRulesDashboardEl = document.getElementById('llm-rules-dashboard');
    const configDashboardEl = document.getElementById('config-dashboard');
    const guideDashboardEl = document.getElementById('guide-dashboard');
    const welcomeSlate = document.getElementById('welcome-slate');

    function resetAllNavActive() {
        if (navWorkbench) navWorkbench.classList.remove('active');
        if (navMaterials) navMaterials.classList.remove('active');
        if (navLlmRules) navLlmRules.classList.remove('active');
        if (navConfig) navConfig.classList.remove('active');
        if (navGuide) navGuide.classList.remove('active');

        if (workbenchEl) workbenchEl.style.display = 'none';
        if (welcomeSlate) welcomeSlate.style.display = 'none';
        if (materialsDashboardEl) materialsDashboardEl.style.display = 'none';
        if (llmRulesDashboardEl) llmRulesDashboardEl.style.display = 'none';
        if (configDashboardEl) configDashboardEl.style.display = 'none';
        if (guideDashboardEl) guideDashboardEl.style.display = 'none';
    }

    if (navWorkbench) {
        navWorkbench.addEventListener('click', () => {
            resetAllNavActive();
            navWorkbench.classList.add('active');
            if (items.length > 0) {
                workbenchEl.style.display = 'flex';
            } else {
                welcomeSlate.style.display = 'flex';
            }
        });
    }

    if (navMaterials) {
        navMaterials.addEventListener('click', () => {
            resetAllNavActive();
            navMaterials.classList.add('active');
            materialsDashboardEl.style.display = 'flex';
            
            loadAndRenderDbMetadata();
            loadAndRenderDataPortrait();
            loadAndRenderMaterialTable(1);
        });
    }

    if (navLlmRules) {
        navLlmRules.addEventListener('click', () => {
            resetAllNavActive();
            navLlmRules.classList.add('active');
            llmRulesDashboardEl.style.display = 'flex';

            loadRuleStats();
            fetchAndRenderPendingRules();
            fetchAndRenderActiveRules();
        });
    }

    if (navConfig) {
        navConfig.addEventListener('click', () => {
            resetAllNavActive();
            navConfig.classList.add('active');
            configDashboardEl.style.display = 'flex';
            
            loadAndRenderConfigs();
        });
    }

    if (navGuide) {
        navGuide.addEventListener('click', () => {
            resetAllNavActive();
            navGuide.classList.add('active');
            guideDashboardEl.style.display = 'flex';
        });
    }
}

function initEventListeners() {
    // File inputs
    const fileInput = document.getElementById('xlsx-file-input');
    const uploadTrigger = document.getElementById('upload-trigger');
    const welcomeUploadBtn = document.getElementById('welcome-upload-btn');
    
    const handleUploadClick = () => {
        if (items.length > 0) {
            document.getElementById('upload-confirm-filename').innerText = originalFileName || '当前清单';
            document.getElementById('upload-confirm-modal').style.display = 'flex';
        } else {
            fileInput.click();
        }
    };
    
    uploadTrigger.addEventListener('click', handleUploadClick);
    welcomeUploadBtn.addEventListener('click', () => fileInput.click());
    
    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFileUpload(e.target.files[0]);
        }
    });

    // Left Panel Filter tabs
    const filterTabs = document.querySelectorAll('.filter-tab');
    filterTabs.forEach(tab => {
        tab.addEventListener('click', (e) => {
            const clickedTab = e.currentTarget;
            for (const t of filterTabs) {
                t.classList.toggle('active', t === clickedTab);
                t.setAttribute('aria-pressed', String(t === clickedTab));
            }
            currentFilter = clickedTab.dataset.filter;
            currentPage = 1; // Reset to page 1
            renderTable();
        });
    });

    // Left panel Search input
    const listSearch = document.getElementById('list-search');
    listSearch.addEventListener('input', (e) => {
        searchQuery = e.target.value.toLowerCase();
        currentPage = 1; // Reset to page 1
        renderTable();
    });

    // Export button
    const exportBtn = document.getElementById('export-btn');
    exportBtn.addEventListener('click', handleExport);

    // Manual search in detail panel
    const manualSearchBtn = document.getElementById('manual-search-btn');
    const manualSearchInput = document.getElementById('manual-search-input');
    
    manualSearchBtn.addEventListener('click', runManualSearch);
    manualSearchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            runManualSearch();
        }
    });

    // Pagination buttons
    document.getElementById('prev-page-btn').addEventListener('click', () => {
        if (currentPage > 1) {
            currentPage--;
            renderTable();
        }
    });
    document.getElementById('next-page-btn').addEventListener('click', () => {
        if (currentPage < totalPages) {
            currentPage++;
            renderTable();
        }
    });

    // Parameters supplement modal listeners
    const openParamBtn = document.getElementById('open-param-modal-btn');
    const paramModal = document.getElementById('param-modal');
    const closeParamBtn = document.getElementById('close-param-modal');
    const cancelParamBtn = document.getElementById('cancel-param-btn');
    const confirmParamBtn = document.getElementById('confirm-param-btn');

    openParamBtn.addEventListener('click', () => {
        if (!selectedItem) return;
        
        const text = selectedItem.original_spec;
        const prof = selectedItem.professional || "暖通";
        
        const modelInput = document.getElementById('modal-param-model');
        const volInput = document.getElementById('modal-param-volume');
        const pwrInput = document.getElementById('modal-param-power');
        const dnInput = document.getElementById('modal-param-dn');
        
        const volWrapper = volInput.parentNode;
        const pwrWrapper = pwrInput.parentNode;
        const dnWrapper = dnInput.parentNode;
        
        const volLabel = volWrapper.querySelector('label');
        const pwrLabel = pwrWrapper.querySelector('label');
        const dnLabel = dnWrapper.querySelector('label');
        
        let volVal = '';
        let pwrVal = '';
        let dnVal = '';
        
        const modelMatch = text.match(/([A-Z]+-[A-Z]+-[A-Z0-9\-]+)/i);
        const modelVal = modelMatch ? modelMatch[1] : '';

        // Configure layout and parse values based on professional field
        if (prof === "强电") {
            volWrapper.style.display = 'flex';
            volLabel.innerText = "电缆芯数 (如 5)";
            volInput.placeholder = "如 5";
            
            pwrWrapper.style.display = 'flex';
            pwrLabel.innerText = "额定功率 Pe (kW)";
            pwrInput.placeholder = "如 15";
            
            dnWrapper.style.display = 'flex';
            dnLabel.innerText = "线芯截面积 (mm²)";
            dnInput.placeholder = "如 2.5";
            
            const cableMatch = text.match(/(\d+)\s*[*x×]\s*(\d+(?:\.\d+)?)/i);
            if (cableMatch) {
                volVal = cableMatch[1];
                dnVal = cableMatch[2];
            } else {
                const singleMatch = text.match(/(?:BV|BYJ|YJV|WDZ-YJY|BVR)-(\d+(?:\.\d+)?)/i);
                if (singleMatch) {
                    volVal = "1";
                    dnVal = singleMatch[1];
                }
            }
            const peMatch = text.match(/(?:Pe|功率)\s*[:：=]?\s*(\d+(?:\.\d+)?)/i);
            pwrVal = peMatch ? peMatch[1] : '';
            
        } else if (prof === "给排水" || prof === "纯水" || prof === "医气") {
            volWrapper.style.display = 'none';
            
            pwrWrapper.style.display = 'flex';
            pwrLabel.innerText = "工作压力 (MPa)";
            pwrInput.placeholder = "如 1.6";
            
            dnWrapper.style.display = 'flex';
            dnLabel.innerText = "管道口径 / DN (mm)";
            dnInput.placeholder = "如 100";
            
            const dnMatch = text.match(/(?:DN|Ф|Φ|管径)\s*[:：]?\s*(\d+)/i);
            dnVal = dnMatch ? dnMatch[1] : '';
            const pressMatch = text.match(/(?:压力|PN|工作压力)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:MPa|pn)?/i);
            pwrVal = pressMatch ? pressMatch[1] : '';
            
        } else if (prof === "暖通") {
            volWrapper.style.display = 'flex';
            volLabel.innerText = "风量 / 流量 (m³/h)";
            volInput.placeholder = "如 8000";
            
            pwrWrapper.style.display = 'flex';
            pwrLabel.innerText = "电机功率 (kW)";
            pwrInput.placeholder = "如 5.5";
            
            dnWrapper.style.display = 'none';
            
            const volMatch = text.match(/(?:风量)\s*[:：]?\s*(\d+)/i);
            const pwrMatch = text.match(/(?:功率)\s*[:：]?\s*(\d+(?:\.\d+)?)/i);
            volVal = volMatch ? volMatch[1] : '';
            pwrVal = pwrMatch ? pwrMatch[1] : '';
            
        } else {
            // Decoration / other
            volWrapper.style.display = 'none';
            pwrWrapper.style.display = 'none';
            
            dnWrapper.style.display = 'flex';
            dnLabel.innerText = "厚度 / 尺寸 (mm)";
            dnInput.placeholder = "如 100";
            
            const thickMatch = text.match(/(\d+(?:\.\d+)?)\s*(?:mm厚|厚)/i);
            dnVal = thickMatch ? thickMatch[1] : '';
        }

        modelInput.value = modelVal;
        volInput.value = volVal;
        pwrInput.value = pwrVal;
        dnInput.value = dnVal;
        
        paramModal.style.display = 'flex';
    });

    closeParamBtn.addEventListener('click', () => paramModal.style.display = 'none');
    cancelParamBtn.addEventListener('click', () => paramModal.style.display = 'none');
    confirmParamBtn.addEventListener('click', runParamRematch);

    // Configuration dashboard action buttons
    document.getElementById('add-alias-row').addEventListener('click', () => addConfigTableRow('alias-tbody', '', ''));
    document.getElementById('add-unit-row').addEventListener('click', () => addConfigTableRow('units-tbody', '', ''));
    document.getElementById('add-stopword-btn').addEventListener('click', addStopWordFromInput);
    document.getElementById('save-all-configs-btn').addEventListener('click', saveAllConfigurations);

    // Clickable parameter chips actions
    document.getElementById('rematch-selected-btn').addEventListener('click', rematchCurrentWithParams);
    document.getElementById('batch-apply-params-btn').addEventListener('click', batchApplyParamsAndRematch);

    // Global DB Upload and System Logs actions
    const dbUploadInput = document.getElementById('db-upload-input');
    const selectDbFileBtn = document.getElementById('btn-select-db-file');
    const startDbImportBtn = document.getElementById('btn-start-db-import');
    const dbFileLabel = document.getElementById('db-selected-file-label');
    const refreshLogsBtn = document.getElementById('btn-refresh-logs');

    selectDbFileBtn.addEventListener('click', () => dbUploadInput.click());
    
    dbUploadInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            const file = e.target.files[0];
            const lowerName = (file.name || '').trim().toLowerCase();
            if (!(lowerName.endsWith('.xlsx') || lowerName.endsWith('.xls'))) {
                e.target.value = '';
                dbFileLabel.innerText = '未选择文件';
                startDbImportBtn.disabled = true;
                alert('物料库导入仅支持 .xlsx 或 .xls 格式文件。');
                return;
            }
            dbFileLabel.innerText = `已选择: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
            startDbImportBtn.disabled = false;
        } else {
            dbFileLabel.innerText = '未选择文件';
            startDbImportBtn.disabled = true;
        }
    });

    startDbImportBtn.addEventListener('click', handleDbUploadAndImport);
    refreshLogsBtn.addEventListener('click', loadAndRenderLogs);

    const refreshPortraitBtn = document.getElementById('btn-refresh-portrait');
    if (refreshPortraitBtn) {
        refreshPortraitBtn.addEventListener('click', async () => {
            const icon = refreshPortraitBtn.querySelector('i');
            if (icon) icon.classList.add('fa-spin');
            refreshPortraitBtn.disabled = true;
            try {
                await Promise.all([
                    loadAndRenderDbMetadata(),
                    loadAndRenderDataPortrait(),
                    loadAndRenderMaterialTable(1)
                ]);
            } catch (e) {
                console.error(e);
            } finally {
                if (icon) icon.classList.remove('fa-spin');
                refreshPortraitBtn.disabled = false;
            }
        });
    }
    
    const systemResetBtn = document.getElementById('btn-system-reset');
    if (systemResetBtn) systemResetBtn.addEventListener('click', handleSystemReset);

    // Master Material Table search and pagination listeners
    const dbSearchBtn = document.getElementById('db-search-btn');
    if (dbSearchBtn) dbSearchBtn.addEventListener('click', () => loadAndRenderMaterialTable(1));

    const dbSearchResetBtn = document.getElementById('db-search-reset-btn');
    if (dbSearchResetBtn) {
        dbSearchResetBtn.addEventListener('click', () => {
            const catSel = document.getElementById('db-category-select');
            const srcInp = document.getElementById('db-search-input');
            if (catSel) catSel.value = '';
            if (srcInp) srcInp.value = '';
            loadAndRenderMaterialTable(1);
        });
    }

    const dbCategorySelect = document.getElementById('db-category-select');
    if (dbCategorySelect) dbCategorySelect.addEventListener('change', () => loadAndRenderMaterialTable(1));

    const dbSearchInput = document.getElementById('db-search-input');
    if (dbSearchInput) {
        dbSearchInput.addEventListener('keyup', (e) => {
            if (e.key === 'Enter') loadAndRenderMaterialTable(1);
        });
    }

    const dbPrevBtn = document.getElementById('db-prev-page-btn');
    if (dbPrevBtn) dbPrevBtn.addEventListener('click', () => {
        if (currentDbPage > 1) loadAndRenderMaterialTable(currentDbPage - 1);
    });

    const dbNextBtn = document.getElementById('db-next-page-btn');
    if (dbNextBtn) dbNextBtn.addEventListener('click', () => {
        if (currentDbPage < currentDbTotalPages) loadAndRenderMaterialTable(currentDbPage + 1);
    });

    // Confirm match finished button click listener
    document.getElementById('confirm-match-finished-btn').addEventListener('click', () => submitReview('submit'));
    document.getElementById('reject-match-btn').addEventListener('click', () => submitReview('reject'));

    // Save Session triggers
    const saveTrigger = document.getElementById('save-trigger');
    const saveSessionModal = document.getElementById('save-session-modal');
    const closeSaveSessionModal = document.getElementById('close-save-session-modal');
    const cancelSaveSessionBtn = document.getElementById('cancel-save-session-btn');
    const confirmSaveSessionBtn = document.getElementById('confirm-save-session-btn');
    const saveSessionNameInput = document.getElementById('save-session-name');

    if (saveTrigger) {
        saveTrigger.addEventListener('click', () => {
            if (items.length === 0) return;
            let suggestedName = originalFileName ? originalFileName.substring(0, originalFileName.lastIndexOf('.')) : '清单进度';
            const now = new Date();
            const timeStr = `${now.getMonth()+1}-${now.getDate()}_${now.getHours()}-${now.getMinutes()}`;
            saveSessionNameInput.value = `${suggestedName}_备份_${timeStr}`;
            saveSessionModal.style.display = 'flex';
        });
    }

    if (closeSaveSessionModal) closeSaveSessionModal.addEventListener('click', () => saveSessionModal.style.display = 'none');
    if (cancelSaveSessionBtn) cancelSaveSessionBtn.addEventListener('click', () => saveSessionModal.style.display = 'none');
    
    if (confirmSaveSessionBtn) {
        confirmSaveSessionBtn.addEventListener('click', async () => {
            const sessionName = saveSessionNameInput.value.trim();
            if (!sessionName) {
                alert("请输入保存进度记录的名称。");
                return;
            }
            await saveNamedSession(sessionName);
        });
    }

    // History Modal triggers
    const historyTrigger = document.getElementById('history-trigger');
    const historyModal = document.getElementById('history-modal');
    const closeHistoryModal = document.getElementById('close-history-modal');
    const closeHistoryBtn = document.getElementById('close-history-btn');

    if (historyTrigger) {
        historyTrigger.addEventListener('click', async () => {
            await fetchAndRenderHistorySessions();
            historyModal.style.display = 'flex';
        });
    }

    if (closeHistoryModal) closeHistoryModal.addEventListener('click', () => historyModal.style.display = 'none');
    if (closeHistoryBtn) closeHistoryBtn.addEventListener('click', () => historyModal.style.display = 'none');

    // End Task Modal triggers
    const endTaskTrigger = document.getElementById('end-task-trigger');
    const endTaskModal = document.getElementById('end-task-modal');
    const closeEndTaskModal = document.getElementById('close-end-task-modal');
    const endTaskCancelBtn = document.getElementById('end-task-cancel-btn');
    const endTaskSaveBtn = document.getElementById('end-task-save-btn');
    const endTaskDirectBtn = document.getElementById('end-task-direct-btn');

    if (endTaskTrigger) {
        endTaskTrigger.addEventListener('click', () => {
            document.getElementById('end-task-filename').innerText = originalFileName || '当前清单';
            endTaskModal.style.display = 'flex';
        });
    }

    if (closeEndTaskModal) closeEndTaskModal.addEventListener('click', () => endTaskModal.style.display = 'none');
    if (endTaskCancelBtn) endTaskCancelBtn.addEventListener('click', () => endTaskModal.style.display = 'none');
    
    if (endTaskDirectBtn) {
        endTaskDirectBtn.addEventListener('click', async () => {
            endTaskModal.style.display = 'none';
            await resetAndEndTask();
        });
    }

    if (endTaskSaveBtn) {
        endTaskSaveBtn.addEventListener('click', () => {
            endTaskModal.style.display = 'none';
            // Open save session modal
            let suggestedName = originalFileName ? originalFileName.substring(0, originalFileName.lastIndexOf('.')) : '清单进度';
            const now = new Date();
            const timeStr = `${now.getMonth()+1}-${now.getDate()}_${now.getHours()}-${now.getMinutes()}`;
            saveSessionNameInput.value = `${suggestedName}_备份_${timeStr}`;
            saveSessionModal.style.display = 'flex';
        });
    }

    // Upload Confirm Modal triggers
    const uploadConfirmModal = document.getElementById('upload-confirm-modal');
    const closeUploadConfirmModal = document.getElementById('close-upload-confirm-modal');
    const uploadConfirmCancelBtn = document.getElementById('upload-confirm-cancel-btn');
    const uploadConfirmSaveBtn = document.getElementById('upload-confirm-save-btn');
    const uploadConfirmOverrideBtn = document.getElementById('upload-confirm-override-btn');

    if (closeUploadConfirmModal) closeUploadConfirmModal.addEventListener('click', () => uploadConfirmModal.style.display = 'none');
    if (uploadConfirmCancelBtn) uploadConfirmCancelBtn.addEventListener('click', () => uploadConfirmModal.style.display = 'none');

    if (uploadConfirmOverrideBtn) {
        uploadConfirmOverrideBtn.addEventListener('click', async () => {
            uploadConfirmModal.style.display = 'none';
            await resetAndEndTask();
            fileInput.click();
        });
    }

    if (uploadConfirmSaveBtn) {
        uploadConfirmSaveBtn.addEventListener('click', () => {
            uploadConfirmModal.style.display = 'none';
            let suggestedName = originalFileName ? originalFileName.substring(0, originalFileName.lastIndexOf('.')) : '清单进度';
            const now = new Date();
            const timeStr = `${now.getMonth()+1}-${now.getDate()}_${now.getHours()}-${now.getMinutes()}`;
            saveSessionNameInput.value = `${suggestedName}_备份_${timeStr}`;
            saveSessionModal.style.display = 'flex';
        });
    }

    // Auto-refresh interval for logs (runs every 3 seconds if config is visible and checkbox is checked)
    setInterval(() => {
        const configDashboard = document.getElementById('config-dashboard');
        const autoRefresh = document.getElementById('auto-refresh-logs');
        if (configDashboard && configDashboard.style.display === 'flex' && autoRefresh && autoRefresh.checked) {
            loadAndRenderLogs();
        }
    }, 3000);
}

// 3.8 Jump to and Highlight a Row across Pages
function jumpToItem(item) {
    const filtered = items.filter(matchesWorkbenchFilters);
    const idx = filtered.findIndex(it => isSameWorkbenchRow(it, item));
    if (idx !== -1) currentPage = Math.floor(idx / pageSize) + 1;
    selectRow(item);
    renderTable();
}

// Show/Hide Spinner Overlay with dynamic timer and safety timeout guard
let loadingSafetyTimer = null;
let loadingIntervalTimer = null;

function showLoading(title, subtitle, timeoutMs = 30000) {
    document.getElementById('loading-title').innerText = title || "处理中...";
    const subtitleEl = document.getElementById('loading-subtitle');
    const timerEl = document.getElementById('loading-timer');
    const baseSubtitle = subtitle || "";
    subtitleEl.innerText = baseSubtitle;
    
    if (timerEl) {
        timerEl.innerText = "已用时 0 秒";
        timerEl.style.display = 'inline-block';
    }
    
    document.getElementById('loading-overlay').style.display = 'flex';
    
    // Hide progress bars container by default for non-upload loaders
    const segContainer = document.getElementById('segmented-progress-container');
    if (segContainer) segContainer.style.display = 'none';
    
    let secondsElapsed = 0;
    if (loadingIntervalTimer) clearInterval(loadingIntervalTimer);
    loadingIntervalTimer = setInterval(() => {
        secondsElapsed += 1;
        if (timerEl) {
            timerEl.innerText = `已用时 ${secondsElapsed} 秒`;
        }
    }, 1000);

    if (loadingSafetyTimer) clearTimeout(loadingSafetyTimer);
    if (timeoutMs === 0) {
        // Explicit long background task with active progress polling.
        // Lifecycle is fully driven by window.uploadPollInterval; no artificial timer popup.
        loadingSafetyTimer = null;
    } else {
        // P0-3: Default 30s safety timeout for quick actions, clamp between 5s and 60s max
        const requestedTimeout = (timeoutMs && timeoutMs > 0) ? timeoutMs : 30000;
        const effectiveTimeout = Math.min(Math.max(requestedTimeout, 5000), 60000);
        loadingSafetyTimer = setTimeout(() => {
            hideLoading();
            alert(`操作提示：后台处理超时（超过 ${effectiveTimeout / 1000} 秒），已自动中断界面挂起。请检查网络或刷新页面重试。`);
        }, effectiveTimeout);
    }
}

function hideLoading() {
    if (loadingIntervalTimer) {
        clearInterval(loadingIntervalTimer);
        loadingIntervalTimer = null;
    }
    if (loadingSafetyTimer) {
        clearTimeout(loadingSafetyTimer);
        loadingSafetyTimer = null;
    }
    if (window.uploadPollInterval) {
        clearInterval(window.uploadPollInterval);
        window.uploadPollInterval = null;
    }
    const timerEl = document.getElementById('loading-timer');
    if (timerEl) timerEl.style.display = 'none';
    document.getElementById('loading-overlay').style.display = 'none';
}

// Task lifecycle control: End and reset current workbench task
async function resetAndEndTask() {
    const endingDocumentId = currentDocumentId;
    items = [];
    originalFileName = '';
    activeSessionId = null;
    currentDocumentId = '';
    currentUploadTaskId = '';
    currentWorkSessionId = '';
    purgePendingEventsForDocument(endingDocumentId);
    renderLegacyIsolationBanner(0);
    searchQuery = '';
    currentFilter = 'all';
    currentPage = 1;
    selectedItem = null;
    professionals = ['全部'];
    currentProfessional = '全部';
    
    // Reset search DOM
    const listSearch = document.getElementById('list-search');
    if (listSearch) listSearch.value = '';
    
    // Reset filter tabs DOM
    const filterTabs = document.querySelectorAll('.filter-tab');
    filterTabs.forEach(t => {
        if (t.dataset.filter === 'all') t.classList.add('active');
        else t.classList.remove('active');
    });
    
    // Toggle UI views
    document.getElementById('welcome-slate').style.display = 'flex';
    document.getElementById('workbench').style.display = 'none';
    document.getElementById('export-btn').disabled = true;
    
    const saveTrigger = document.getElementById('save-trigger');
    if (saveTrigger) saveTrigger.style.display = 'none';
    
    const endTaskTrigger = document.getElementById('end-task-trigger');
    if (endTaskTrigger) endTaskTrigger.style.display = 'none';
    
    const uploadedFilenameEl = document.getElementById('uploaded-filename');
    if (uploadedFilenameEl) uploadedFilenameEl.innerText = '';
    
    if (autoSaveInterval) {
        clearInterval(autoSaveInterval);
        autoSaveInterval = null;
    }
    
    // Delete auto-saved draft on server
    try {
        await fetch(`${backendUrl}/api/session/draft`, { method: 'DELETE' });
    } catch (e) {
        console.error("Failed to clear backend draft:", e);
    }
}

// 1. File Upload Handler
async function handleFileUpload(file) {
    const fileNameLower = (file && file.name ? file.name : '').trim().toLowerCase();
    if (!fileNameLower.endsWith('.xlsx')) {
        const fileInput = document.getElementById('xlsx-file-input');
        if (fileInput) fileInput.value = '';
        alert('工程清单上传仅支持标准 .xlsx 文件，旧版 .xls 文件请先另存为 .xlsx 后再上传。');
        return;
    }

    showLoading("正在上传并分析清单...", "系统正在初始化后台比对任务...", 0);
    
    // Show and reset the segmented progress container
    const segContainer = document.getElementById('segmented-progress-container');
    if (segContainer) {
        segContainer.style.display = 'flex';
        document.getElementById('progress-bar-structuring').style.width = '0%';
        document.getElementById('progress-val-structuring').innerText = '0%';
        document.getElementById('progress-bar-vectorization').style.width = '0%';
        document.getElementById('progress-val-vectorization').innerText = '0%';
        document.getElementById('progress-bar-matching').style.width = '0%';
        document.getElementById('progress-val-matching').innerText = '0%';
    }
    
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        const response = await fetch(`${backendUrl}/api/upload`, {
            method: 'POST',
            body: formData,
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('token')}`
            }
        });
        
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "解析Excel清单时发生错误");
        }
        
        const uploadRes = await response.json();
        const taskId = uploadRes.task_id;
        
        // Start polling status
        if (window.uploadPollInterval) clearInterval(window.uploadPollInterval);
        window.uploadPollInterval = setInterval(async () => {
            try {
                const statusRes = await fetch(`${backendUrl}/api/upload/status?task_id=${taskId}`, {
                    headers: {
                        'Authorization': `Bearer ${localStorage.getItem('token')}`
                    }
                });
                
                if (!statusRes.ok) {
                    const err = await statusRes.json();
                    throw new Error(err.detail || "查询任务进度失败");
                }
                
                const taskState = await statusRes.json();
                
                // Update UI text and progress bars
                document.getElementById('loading-subtitle').innerText = "系统正在全速处理特征提取与算法比对，这可能需要一点时间...";
                const currentItemEl = document.getElementById('progress-current-item');
                if (currentItemEl) {
                    currentItemEl.innerText = taskState.current_step || "准备就绪...";
                }
                
                document.getElementById('progress-bar-structuring').style.width = `${taskState.structuring_progress}%`;
                document.getElementById('progress-val-structuring').innerText = `${taskState.structuring_progress}%`;
                
                document.getElementById('progress-bar-vectorization').style.width = `${taskState.vectorization_progress}%`;
                document.getElementById('progress-val-vectorization').innerText = `${taskState.vectorization_progress}%`;
                
                document.getElementById('progress-bar-matching').style.width = `${taskState.matching_progress}%`;
                document.getElementById('progress-val-matching').innerText = `${taskState.matching_progress}%`;
                
                if (taskState.status === 'completed') {
                    clearInterval(window.uploadPollInterval);
                    window.uploadPollInterval = null;
                    hideLoading();
                    processUploadResult(taskState.result, file.name, taskState);
                } else if (taskState.status === 'failed') {
                    clearInterval(window.uploadPollInterval);
                    window.uploadPollInterval = null;
                    hideLoading();
                    alert(`比对处理失败: ${taskState.error}`);
                }
            } catch (err) {
                console.error("Error polling task status:", err);
                clearInterval(window.uploadPollInterval);
                window.uploadPollInterval = null;
                hideLoading();
                alert(`轮询进度失败: ${err.message}`);
            }
        }, 800);
        
    } catch (error) {
        hideLoading();
        alert(`错误: ${error.message}`);
    } finally {
        // Reset file input value so re-selecting any file triggers 'change' event
        document.getElementById('xlsx-file-input').value = "";
    }
}

// Processing matching results helper
function processUploadResult(resultItems, fileName, taskState = null) {
    // 1. Reset all state and search filters to guarantee new file displays cleanly
    searchQuery = '';
    const listSearch = document.getElementById('list-search');
    if (listSearch) listSearch.value = '';
    
    currentFilter = 'all';
    const filterTabs = document.querySelectorAll('.filter-tab');
    filterTabs.forEach(t => {
        if (t.dataset.filter === 'all') t.classList.add('active');
        else t.classList.remove('active');
    });
    
    activeSessionId = null;
    currentPage = 1;
    selectedItem = null;

    items = resultItems;
    const firstItem = (items && items.length > 0) ? items[0] : null;
    currentDocumentId = (taskState && taskState.document_id) || (firstItem && firstItem.document_id) || '';
    currentUploadTaskId = (firstItem && firstItem.upload_task_id) || '';
    currentWorkSessionId = (taskState && taskState.work_session_id) || (firstItem && firstItem.work_session_id) || '';
    renderLegacyIsolationBanner(0);
    reconcilePendingFeedbackForDocument();

    // Cache initial matching codes and sequence counters (never overwrite server initial_auto_code)
    items.forEach(item => {
        if (item.initial_auto_code === undefined || item.initial_auto_code === null) {
            item.initial_auto_code = item.matched_code || '';
        }
        item._event_seq = Number(item.max_event_seq || 0);
        item._feedback_seq = Number(item.max_feedback_seq || 0);
        item.bindings ??= [];
    });
    
    // Initialize professional list
    const uniqueProfs = [...new Set(items.map(item => item.professional).filter(Boolean))];
    professionals = ['全部', ...uniqueProfs];
    currentProfessional = uniqueProfs.includes('暖通') ? '暖通' : uniqueProfs[0] || '全部';
    
    // Render professional tabs
    renderProfessionalTabs();
    
    // Hide welcome slate & show workbench
    originalFileName = fileName;
    const uploadedFilenameEl = document.getElementById('uploaded-filename');
    if (uploadedFilenameEl) {
        uploadedFilenameEl.innerText = ` - ${fileName}`;
        uploadedFilenameEl.title = fileName;
    }
    document.getElementById('welcome-slate').style.display = 'none';
    document.getElementById('workbench').style.display = 'flex';
    document.getElementById('export-btn').disabled = false;
    
    const saveTrigger = document.getElementById('save-trigger');
    if (saveTrigger) saveTrigger.style.display = 'inline-flex';
    
    const endTaskTrigger = document.getElementById('end-task-trigger');
    if (endTaskTrigger) endTaskTrigger.style.display = 'inline-flex';
    
    startAutoSave();
    saveDraft();
    
    // Clear selection UI
    document.getElementById('select-prompt').style.display = 'flex';
    document.getElementById('detail-editor').style.display = 'none';
    
    // Update table & status
    renderTable();
    updateProgress();
}

// 1.5 Render Professional Page tabs
function renderProfessionalTabs() {
    const wrapper = document.getElementById('prof-tabs-wrapper');
    const container = document.getElementById('prof-tabs');
    container.innerHTML = '';
    
    const uniqueProfs = [...new Set(items.map(item => item.professional).filter(Boolean))];
    
    if (uniqueProfs.length === 0) {
        wrapper.style.display = 'none';
        return;
    }
    
    wrapper.style.display = 'block';
    
    const tabs = ['全部', ...uniqueProfs];
    
    tabs.forEach(prof => {
        const btn = document.createElement('button');
        btn.className = `prof-tab ${currentProfessional === prof ? 'active' : ''}`;
        btn.innerText = prof;
        btn.addEventListener('click', () => {
            currentProfessional = prof;
            currentPage = 1;
            renderProfessionalTabs();
            renderTable();
            updateProgress();
        });
        container.appendChild(btn);
    });
}

// 2. Render Left Panel Items Table (Paginated V0.0.2)
function renderTable() {
    const tbody = document.getElementById('items-tbody');
    tbody.innerHTML = '';
    
    // Professional, status, and search use the same view for table and navigation.
    const filtered = items.filter(matchesWorkbenchFilters);

    document.getElementById('items-count').innerText = filtered.length;
    
    // Update pagination variables
    totalPages = Math.ceil(filtered.length / pageSize) || 1;
    if (currentPage > totalPages) currentPage = totalPages;
    
    document.getElementById('current-page-num').innerText = currentPage;
    document.getElementById('total-pages-num').innerText = totalPages;
    document.getElementById('prev-page-btn').disabled = currentPage === 1;
    document.getElementById('next-page-btn').disabled = currentPage === totalPages;
    
    if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--color-text-muted); padding: 30px;">无匹配记录</td></tr>`;
        return;
    }
    
    // Page slicing
    const startIdx = (currentPage - 1) * pageSize;
    const endIdx = startIdx + pageSize;
    const pageItems = filtered.slice(startIdx, endIdx);
    
    pageItems.forEach(item => {
        const tr = document.createElement('tr');
        if (selectedItem && isSameWorkbenchRow(selectedItem, item)) {
            tr.classList.add('selected');
        }
        
        const state = reviewState(item);
        const statusHtml = `<span class="row-status-pill" data-state="${state}"
            title="${escapeHtml(item.decision_reason || REVIEW_LABELS[state])}">${REVIEW_LABELS[state]}</span>`;

        let codeDisplay = '-';
        if (item.bindings && item.bindings.length > 0) {
            if (item.bindings.length === 1) {
                codeDisplay = item.bindings[0].code;
            } else {
                codeDisplay = `${item.bindings[0].code} (+${item.bindings.length - 1})`;
            }
        }

        tr.innerHTML = `
            <td>${escapeHtml(item.original_row_idx)}</td>
            <td>
                <div style="font-weight: 500;">${escapeHtml(item.original_name)}</div>
                <div style="font-size: 11px; color: var(--color-text-muted); max-width: 320px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                    ${escapeHtml((item.original_spec || '').replace(/\n/g, ' '))}
                </div>
            </td>
            <td><code class="row-code-lbl">${escapeHtml(codeDisplay)}</code></td>
            <td>${statusHtml}</td>
        `;
        
        tr.addEventListener('click', () => {
            selectRow(item);
            // Highlight row
            const rows = tbody.querySelectorAll('tr');
            rows.forEach(r => r.classList.remove('selected'));
            tr.classList.add('selected');
        });
        
        tbody.appendChild(tr);
    });
}

// 3. Selection Handler
function selectRow(item) {
    selectedItem = item;
    selectedItem.bindings ??= [];
    renderReviewDetail();

    renderBoundMaterials();
    
    // Hide prompt & show editor
    document.getElementById('select-prompt').style.display = 'none';
    document.getElementById('detail-editor').style.display = 'flex';
    
    // Reset scroll positions to top
    const fixedTop = document.querySelector('.detail-fixed-top');
    if (fixedTop) {
        fixedTop.scrollTop = 0;
    }
    const scrollBottom = document.querySelector('.detail-scroll-bottom');
    if (scrollBottom) {
        scrollBottom.scrollTop = 0;
    }
    
    // Fill original item metadata
    document.getElementById('lbl-row-idx').innerText = item.original_row_idx;
    document.getElementById('lbl-name').innerText = item.original_name;
    document.getElementById('lbl-qty').innerText = item.original_qty;
    document.getElementById('lbl-unit').innerText = item.original_unit;
    document.getElementById('lbl-category').innerText = item.original_category || '-';
    document.getElementById('lbl-spec').innerText = item.original_spec;
    
    // Render clickable parameter chips
    renderParamChips();
    
    // Render recommendation cards
    renderCandidates();
    
    // Clear manual search input and results
    document.getElementById('manual-search-input').value = '';
    document.getElementById('search-results-tbody').innerHTML = `
        <tr><td colspan="5" class="empty-search">请输入关键字并搜索</td></tr>
    `;
}

const REVIEW_LABELS = Object.freeze({
    auto_accepted: '自动接受',
    manual_confirmed: '人工确认',
    recommended: '推荐待复核',
    needs_parameters: '待补参',
    rejected: '拒绝'
});
const pendingReviewEdits = new WeakMap();

function reviewState(item) {
    if (item.decision_source !== 'manual_edit' && item.decision_status === 'recommended' &&
        item.acceptance_calibration?.outcome === 'needs_parameters') return 'needs_parameters';
    return Object.hasOwn(REVIEW_LABELS, item.decision_status) ? item.decision_status : 'recommended';
}

function hasReviewApproval(item) {
    const state = reviewState(item);
    return state === 'auto_accepted' || state === 'manual_confirmed';
}

function matchesWorkbenchFilters(item) {
    return (currentProfessional === '全部' || item.professional === currentProfessional)
        && (currentFilter === 'all' || currentFilter === reviewState(item))
        && (!searchQuery || [item.original_name, item.original_spec, item.matched_code]
            .some(value => String(value ?? '').toLowerCase().includes(searchQuery)));
}

function reviewPayload(item) {
    return {
        original_name: item.original_name,
        original_spec: item.original_spec,
        original_qty: item.original_qty,
        original_unit: item.original_unit || '',
        original_loss: item.original_loss || 0,
        original_category: item.original_category || '',
        original_brand: item.original_brand || '',
        original_code: item.original_code || '',
        original_row_idx: item.original_row_idx || 0,
        professional: item.professional || '暖通',
        scope_filename: originalFileName || ''
    };
}

async function postReview(action, payload) {
    const response = await fetch(`${backendUrl}/api/binding/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.code !== 0) {
        throw new Error(_parseFeedbackApiError(data, response.status).message);
    }
    return data;
}

function persistReviewEdit(item) {
    const pending = pendingReviewEdits.get(item);
    if (!pending) return Promise.resolve();
    if (!pending.request) {
        pending.request = postReview('invalidate', pending.payload)
            .then(() => pendingReviewEdits.delete(item))
            .catch(error => {
                pending.request = null;
                throw error;
            });
    }
    return pending.request;
}

// V015-M2：内容/绑定变更使旧确认失效，降为待复核（须重新显式确认）
function invalidateItemDecision(item, reason) {
    const approved = hasReviewApproval(item);
    item.decision_status = 'recommended';
    item.decision_source = 'manual_edit';
    item.decision_reason = reason || '内容已修改，请重新确认';
    item.decision_codes = [];
    item.row_fingerprint = '';
    item.binding_fingerprint = '';
    if (approved && !pendingReviewEdits.has(item)) {
        pendingReviewEdits.set(item, {payload: reviewPayload(item)});
        persistReviewEdit(item).catch(() => {
            item.decision_reason = '服务端失效登记失败。保存或确认时会重试。';
            if (selectedItem === item) renderReviewDetail();
        });
    }
    if (selectedItem === item) renderReviewDetail();
    renderTable();
    updateProgress();
}

function syncBindingDisplay(item) {
    const first = item.bindings[0];
    item.matched_code = item.bindings.map(binding => binding.code).join(',');
    item.matched_name = first?.name || '';
    item.matched_spec = first?.specification || '';
    item.matched_category = first?.category || '';
    item.matched_unit = first?.unit || '';
    item.matched_brand = first?.brand || '';
}

async function submitReview(action) {
    const item = selectedItem;
    if (!item || (action === 'submit' && !item.bindings.length)) return;
    showLoading(action === 'reject' ? '正在拒绝此行…' : '正在核验绑定…');
    try {
        await persistReviewEdit(item);
        const response = await postReview(action, {...reviewPayload(item), bindings: item.bindings});
        const status = action === 'reject' ? 'rejected' : 'manual_confirmed';
        if (response.decision?.decision_status !== status) throw new Error('服务端未返回有效复核状态');
        Object.assign(item, response.decision);
        item.bindings = action === 'reject' ? [] : response.bindings ?? item.bindings;
        syncBindingDisplay(item);
        if (selectedItem === item) {
            renderReviewDetail();
            renderBoundMaterials();
            renderCandidates();
        }
        renderTable();
        updateProgress();
        if (action === 'submit') {
            // Report to Data Flywheel (do not revoke binding_decisions/export status if flywheel delivery fails)
            await reportFlywheelAction(item, item.bindings.length > 1 ? 'BIND_MULTI' : 'CONFIRM_TOP1');
            // Find next unconfirmed item for auto flow (Task 4)
            if (selectedItem === item) {
                const index = items.findIndex(other => isSameWorkbenchRow(other, item));
                const next = [...items.slice(index + 1), ...items.slice(0, index)].find(other =>
                    matchesWorkbenchFilters(other) && ['recommended', 'needs_parameters'].includes(reviewState(other)));
                if (next) jumpToItem(next);
            }
        }
    } catch (error) {
        alert(error.message);
    } finally {
        hideLoading();
    }
}

function renderReviewDetail() {
    const item = selectedItem;
    if (!item) return;
    const state = reviewState(item);
    const badge = document.getElementById('review-status');
    badge.dataset.state = state;
    badge.textContent = REVIEW_LABELS[state];
    document.getElementById('review-reason').textContent = item.decision_reason || '';
    document.getElementById('confirm-match-finished-btn').disabled = !item.bindings?.length;
    document.getElementById('reject-match-btn').disabled = state === 'rejected';

    const differences = document.getElementById('review-differences');
    differences.replaceChildren();
    const evidence = item.acceptance_calibration;
    const add = (label, value) => {
        if (!value) return;
        const term = document.createElement('dt');
        const detail = document.createElement('dd');
        term.textContent = label;
        detail.textContent = value;
        differences.append(term, detail);
    };
    add('待补参数', evidence?.missing_fields?.join('、'));
    add('关键差异', evidence?.different_fields?.join('、'));
    add('硬冲突', evidence?.hard_conflict);
    const eligible = (item.top_candidates || []).filter(candidate => !candidate.hard_blocked && candidate.score > 0);
    const [top, second] = eligible;
    if (top) {
        const rival = evidence ? evidence.competitor_code : second?.code;
        const gap = evidence ? evidence.distinct_margin : second && Number(top.score) - Number(second.score);
        add('候选分差', Number.isFinite(gap) && rival
            ? `${top.code} 与 ${rival} 相差 ${gap.toFixed(2)} 分` : '缺少可比较的第二候选');
        add('清单与首选规格', `${item.original_spec || '未填写'} ／ ${top.specification || '未填写'}`);
        add('首选理由', top.match_reasons?.join('、'));
        const runnerUp = eligible.find(candidate => candidate.code === rival);
        add('次选理由', runnerUp?.match_reasons?.join('、'));
    }
    add('确认等价编码', evidence?.equivalent_codes?.join('、'));
}

// 3.5 Render Bound Materials List (V0.1.0 Compact Cards)
function renderBoundMaterials() {
    const container = document.getElementById('bound-materials-list');
    container.innerHTML = '';
    
    if (!selectedItem) return;
    
    if (!selectedItem.bindings) {
        selectedItem.bindings = [];
    }
    
    document.getElementById('bound-materials-count').innerText = selectedItem.bindings.length;
    
    if (selectedItem.bindings.length === 0) {
        container.innerHTML = `<div style="grid-column: 1 / -1; text-align: center; color: var(--color-text-muted); font-size: 12px; padding: 10px; border: 1px dashed var(--border-color); border-radius: var(--radius-sm);">暂无绑定物料，请点击下方推荐卡片或检索套用以绑定</div>`;
        return;
    }
    
    selectedItem.bindings.forEach((b, idx) => {
        const div = document.createElement('div');
        div.className = 'bound-material-item';
        
        div.innerHTML = `
            <div class="bound-item-top">
                <span class="bound-item-name" title="${escapeHtml(b.name)}">${escapeHtml(b.name)}</span>
                <span class="bound-item-code">${escapeHtml(b.code)}</span>
            </div>
            <div class="bound-item-desc" title="规格: ${escapeHtml(b.specification || '无')} | 品牌: ${escapeHtml(b.brand || '无')}">
                ${escapeHtml(b.specification || '无规格')}
            </div>
            <div class="bound-item-bottom">
                <div class="bound-item-qty-wrap">
                    <span>数量:</span>
                    <input type="number" step="any" aria-label="绑定数量" class="bound-qty-input" value="${escapeHtml(b.qty === undefined ? selectedItem.original_qty : b.qty ?? '')}" />
                    <span style="font-size: 11px; color: var(--color-text-muted);">${escapeHtml(b.unit || selectedItem.original_unit || '个')}</span>
                </div>
                <label class="bound-item-qty-wrap">比例
                    <input type="number" step="any" aria-label="绑定比例" class="bound-qty-input bound-ratio-input"
                        value="${escapeHtml(b.qty_ratio ?? '')}" ${Number(selectedItem.original_qty) > 0 ? '' : 'disabled'} />
                </label>
                <button class="btn btn-delete-binding" style="padding: 2px 6px; height: 22px; color: var(--color-danger); border-color: transparent; background: transparent; font-size: 12px;" title="删除绑定">
                    <i class="fa-solid fa-trash-can"></i>
                </button>
            </div>
        `;
        
        const qtyInput = div.querySelector('.bound-qty-input');
        qtyInput.addEventListener('input', (e) => {
            const val = parseFloat(e.target.value);
            const qty = Number.isFinite(val) ? val : null;
            if (qty === b.qty) return;
            b.qty = qty;
            b.qty_ratio = qty !== null && Number(selectedItem.original_qty) > 0
                ? qty / Number(selectedItem.original_qty) : null;
            const ratioInput = div.querySelector('.bound-ratio-input');
            ratioInput.value = b.qty_ratio ?? '';
            // V015-M2：数量变更使旧确认失效（绑定指纹变化），须重新确认
            invalidateItemDecision(selectedItem, '数量已修改，请重新确认');
        });

        div.querySelector('.bound-ratio-input').addEventListener('input', (e) => {
            const ratio = e.target.valueAsNumber;
            const qty = ratio * Number(selectedItem.original_qty);
            b.qty_ratio = Number.isFinite(qty) ? ratio : null;
            b.qty = Number.isFinite(qty) ? qty : null;
            qtyInput.value = b.qty ?? '';
            invalidateItemDecision(selectedItem, '绑定比例已修改，请重新确认');
        });

        const deleteBtn = div.querySelector('.btn-delete-binding');
        deleteBtn.addEventListener('click', () => {
            selectedItem.bindings.splice(idx, 1);
            syncBindingDisplay(selectedItem);
            // V015-M2：绑定集合变化使旧确认失效
            invalidateItemDecision(selectedItem, '绑定已修改，请重新确认');
            renderBoundMaterials();
            renderCandidates();
            renderTable();
            updateProgress();
        });
        
        container.appendChild(div);
    });
}

// 4. Render Top 10 Candidates List with coloured tags (V0.0.2)
function renderCandidates() {
    const container = document.getElementById('candidates-container');
    container.innerHTML = '';
    
    if (!selectedItem.top_candidates || selectedItem.top_candidates.length === 0) {
        container.innerHTML = `<div style="text-align: center; color: var(--color-text-muted); font-size: 13px; padding: 20px; border: 1px dashed var(--border-color); border-radius: var(--radius-md);">暂无推荐物料</div>`;
        return;
    }
    
    selectedItem.top_candidates.forEach((cand, idx) => {
        const card = document.createElement('div');
        card.className = 'candidate-card';
        card.dataset.code = cand.code;
        card.tabIndex = 0;
        card.setAttribute('role', 'button');
        card.setAttribute('aria-label', `${cand.name} ${cand.code}`);
        
        const isActive = selectedItem.bindings && selectedItem.bindings.some(b => b.code === cand.code);
        if (isActive) {
            card.classList.add('active');
        }
        
        // Color coding for score
        if (cand.score < 60) {
            card.classList.add('fuzzy');
        }
        
        // Render colored tags based on match reasons
        let tagsHtml = '';
        if (cand.match_reasons) {
            cand.match_reasons.forEach(reason => {
                let tagClass = 'brand'; // default grey/yellow
                if (reason.includes('型号')) tagClass = 'model';       // blue
                if (reason.includes('参数') || reason.includes('公差')) tagClass = 'attrs'; // green
                if (reason.includes('语义')) tagClass = 'semantic';    // purple
                if (reason.includes('品牌')) tagClass = 'brand';       // yellow
                
                tagsHtml += `<span class="match-reason-tag ${tagClass}">${escapeHtml(reason)}</span>`;
            });
        }
        
        const boundBadge = isActive ? `<span style="font-size: 11px; font-weight: 700; color: var(--color-success); background: var(--color-success-bg); border: 1px solid var(--color-success-border); padding: 1px 6px; border-radius: 4px;"><i class="fa-solid fa-check"></i> 已绑定</span>` : '';
        
        card.innerHTML = `
            <div class="candidate-header">
                <div class="candidate-title">
                    <span class="cand-name" title="${escapeHtml(cand.name)}">${escapeHtml(cand.name)}</span>
                    <span class="cand-code">${escapeHtml(cand.code)}</span>
                </div>
                <div style="display: flex; align-items: center; gap: 6px;">
                    ${boundBadge}
                    <div class="match-percentage-badge">${escapeHtml(cand.score)}% 相似</div>
                </div>
            </div>
            <div class="candidate-spec" title="${escapeHtml(cand.specification || '无规格描述')}">${escapeHtml(cand.specification || '无规格描述')}</div>
            ${tagsHtml ? `<div style="margin: 2px 0 4px 0; display: flex; flex-wrap: wrap; gap: 4px;">${tagsHtml}</div>` : ''}
            <div class="candidate-meta">
                <span>品牌: <strong>${escapeHtml(cand.brand || '未指定')}</strong></span>
                <span>品类: <strong>${escapeHtml(cand.category || '-')}</strong></span>
                <span>单位: <strong>${escapeHtml(cand.unit || '个')}</strong></span>
            </div>
        `;
        
        card.setAttribute('aria-pressed', String(isActive));
        card.addEventListener('click', () => applyMatchCandidate(cand, 'PICK_CANDIDATE'));
        card.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                card.click();
            }
        });
        
        container.appendChild(card);
    });
}

// 5. Apply selected candidate to matched item & submit feedback + flywheel record
async function applyMatchCandidate(cand, actionType = 'PICK_CANDIDATE') {
    if (!selectedItem) return;
    
    if (!selectedItem.bindings) {
        selectedItem.bindings = [];
    }
    
    // Check duplicates / Already bound: if so, directly unbind without prompt
    const existingIndex = selectedItem.bindings.findIndex(b => b.code === cand.code);
    if (existingIndex !== -1) {
        selectedItem.bindings.splice(existingIndex, 1);
        syncBindingDisplay(selectedItem);
        invalidateItemDecision(selectedItem, '绑定已修改，请重新确认');
        renderBoundMaterials();
        renderCandidates();
        renderTable();
        updateProgress();
        return;
    }
    
    const wasCorrected = selectedItem.initial_auto_code !== cand.code;
    const fbDeliveryPromise = submitFeedbackSample(selectedItem, selectedItem.initial_auto_code, cand.code, !wasCorrected);
    
    const newBound = {
        code: cand.code,
        name: cand.name,
        specification: cand.specification || "",
        unit: cand.unit || selectedItem.original_unit || "个",
        brand: cand.brand || "",
        category: cand.category || "",
        qty: selectedItem.original_qty,
        qty_ratio: 1.0
    };
    
    // Push new bound material
    selectedItem.bindings.push(newBound);
    syncBindingDisplay(selectedItem);
    invalidateItemDecision(selectedItem, '绑定已修改，请重新确认');
    
    // Report action to Data Flywheel
    const fwDeliveryPromise = reportFlywheelAction(selectedItem, actionType, [newBound]);
    
    // Update matched_code to be comma-separated list of bound codes
    syncBindingDisplay(selectedItem);
    
    // Render incremental updates instead of selectRow to keep scroll position and search results
    renderBoundMaterials();
    renderCandidates();
    renderTable();
    updateProgress();
    await Promise.all([fbDeliveryPromise, fwDeliveryPromise]);
}

function _parseFeedbackApiError(respJson, httpStatus) {
    const d = respJson && respJson.detail;
    if (d && typeof d === 'object' && !Array.isArray(d)) {
        return {
            code: String(d.error || d.code || `http_${httpStatus}`),
            message: String(d.message || JSON.stringify(d))
        };
    }
    if (Array.isArray(d) && d.length > 0) {
        return {
            code: `http_${httpStatus}`,
            message: d.map(x => (x && x.msg) ? x.msg : JSON.stringify(x)).join('; ')
        };
    }
    if (typeof d === 'string' && d) {
        return { code: `http_${httpStatus}`, message: d };
    }
    return { code: `http_${httpStatus}`, message: `HTTP ${httpStatus}` };
}

function _getRetryPolicy() {
    const custom = (typeof window !== 'undefined' && window.__M0_FEEDBACK_RETRY_CONFIG) || {};
    return {
        maxAutoRetries: Number.isInteger(custom.maxAutoRetries) ? Math.max(0, custom.maxAutoRetries) : 1,
        retryDelayMs: (typeof custom.retryDelayMs === 'number' && custom.retryDelayMs >= 0) ? custom.retryDelayMs : 120
    };
}

function _findWorkbenchItemForRecord(rec) {
    if (!rec || !Array.isArray(items)) return null;
    return items.find(it =>
        it && it.document_id === rec.document_id &&
        it.sheet_id === rec.sheet_id &&
        it.source_row_id === rec.source_row_id
    ) || null;
}

function _syncItemDeliveryState(item, rec, result) {
    const targetItem = item || _findWorkbenchItemForRecord(rec);
    if (!targetItem) return;
    const summary = {
        status: rec.status,
        event_id: rec.event_id,
        event_seq: rec.event_seq,
        http_status: rec.last_http_status,
        error_code: rec.last_error_code,
        error_message: rec.last_error_message,
        idempotent_replay: Boolean(result && result.idempotent_replay),
        attempts: rec.attempts
    };
    if (rec.kind === 'flywheel') {
        targetItem._last_flywheel_delivery = summary;
        targetItem.max_event_seq = Math.max(Number(targetItem.max_event_seq || 0), rec.status === 'delivered' ? rec.event_seq : 0);
    } else {
        targetItem._last_feedback_delivery = summary;
        targetItem.max_feedback_seq = Math.max(Number(targetItem.max_feedback_seq || 0), rec.status === 'delivered' ? rec.event_seq : 0);
    }
}

async function _dispatchSingleFeedbackRecord(rec, opts = {}) {
    if (!rec) return { ok: false, retryable: false, error_code: 'missing_record', error_message: '事件记录缺失' };
    if (!_actorMatchesRecord(rec)) {
        rec.status = 'blocked_invalid';
        rec.last_error_code = 'actor_mismatch';
        rec.last_error_message = '当前登录主体或会话代际已变更，已阻断旧主体待投递事件。';
        savePendingFeedbackEventsToStorage();
        renderFeedbackDeliveryBanner();
        return { ok: false, retryable: false, event_id: rec.event_id, event_seq: rec.event_seq, error_code: rec.last_error_code, error_message: rec.last_error_message };
    }
    const policy = _getRetryPolicy();
    const maxExtra = opts.skipAutoRetry ? 0 : policy.maxAutoRetries;
    let lastOutcome = null;

    for (let retryIdx = 0; retryIdx <= maxExtra; retryIdx++) {
        if (retryIdx > 0 && policy.retryDelayMs > 0) {
            await new Promise(resolve => setTimeout(resolve, policy.retryDelayMs));
        }
        rec.attempts = Number(rec.attempts || 0) + 1;
        rec.status = 'retrying';
        rec.updated_at = Date.now();
        try {
            const fetchOpts = {
                method: 'POST',
                headers: getAuthHeaders(),
                body: JSON.stringify(rec.payload)
            };
            if (rec.kind === 'flywheel') fetchOpts.keepalive = true;
            const res = await fetch(`${backendUrl}${rec.endpoint}`, fetchOpts);
            const respJson = await res.json().catch(() => ({}));
            if (res && res.ok) {
                rec.status = 'delivered';
                rec.last_http_status = res.status;
                rec.last_error_code = '';
                rec.last_error_message = '';
                rec.updated_at = Date.now();
                pendingFeedbackEvents = pendingFeedbackEvents.filter(x => x.event_id !== rec.event_id);
                savePendingFeedbackEventsToStorage();
                lastOutcome = {
                    ok: true,
                    retryable: false,
                    status: res.status,
                    event_id: rec.event_id,
                    event_seq: rec.event_seq,
                    idempotent_replay: Boolean(respJson && respJson.idempotent_replay),
                    data: respJson
                };
                _syncItemDeliveryState(opts.item, rec, lastOutcome);
                renderFeedbackDeliveryBanner();
                return lastOutcome;
            }
            const httpStatus = res ? res.status : 0;
            const parsedErr = _parseFeedbackApiError(respJson, httpStatus);
            rec.last_http_status = httpStatus;
            rec.last_error_code = parsedErr.code;
            rec.last_error_message = parsedErr.message;
            rec.updated_at = Date.now();

            const isRetryableStatus = (httpStatus === 0 || httpStatus >= 500 || httpStatus === 429 || httpStatus === 408);
            if (!isRetryableStatus) {
                rec.status = (httpStatus === 409) ? 'blocked_conflict' : 'blocked_invalid';
                savePendingFeedbackEventsToStorage();
                lastOutcome = {
                    ok: false,
                    retryable: false,
                    status: httpStatus,
                    event_id: rec.event_id,
                    event_seq: rec.event_seq,
                    error_code: parsedErr.code,
                    error_message: parsedErr.message
                };
                _syncItemDeliveryState(opts.item, rec, lastOutcome);
                renderFeedbackDeliveryBanner();
                return lastOutcome;
            }
            rec.status = 'failed_retryable';
            lastOutcome = {
                ok: false,
                retryable: true,
                status: httpStatus,
                event_id: rec.event_id,
                event_seq: rec.event_seq,
                error_code: parsedErr.code,
                error_message: parsedErr.message
            };
        } catch (networkErr) {
            rec.status = 'failed_retryable';
            rec.last_http_status = 0;
            rec.last_error_code = 'network_error';
            rec.last_error_message = String((networkErr && networkErr.message) || networkErr || '网络异常');
            rec.updated_at = Date.now();
            lastOutcome = {
                ok: false,
                retryable: true,
                status: 0,
                event_id: rec.event_id,
                event_seq: rec.event_seq,
                error_code: 'network_error',
                error_message: rec.last_error_message
            };
        }
    }
    savePendingFeedbackEventsToStorage();
    _syncItemDeliveryState(opts.item, rec, lastOutcome);
    renderFeedbackDeliveryBanner();
    return lastOutcome;
}

async function retryPendingFeedbackEvents() {
    await ensureVerifiedActorIdentity();
    const candidates = getActivePendingFeedbackEvents()
        .filter(r => ['pending', 'failed_retryable'].includes(r.status))
        .sort((a, b) => (Number(a.event_seq || 0) - Number(b.event_seq || 0)) || (Number(a.created_at || 0) - Number(b.created_at || 0)));
    const results = [];
    for (const rec of candidates) {
        const outcome = await _dispatchSingleFeedbackRecord(rec, { skipAutoRetry: true });
        results.push(outcome);
    }
    return results;
}

// Helper to create an immutable Flywheel event once and deliver with bounded same-event retry
async function reportFlywheelAction(item, actionType, customFinalTargets = null) {
    if (!item) return { ok: false, skipped: true, reason: 'missing_item' };
    if (item.feedback_eligible === false || !item.document_id || !item.sheet_id || !item.source_row_id) {
        console.info('Skip flywheel record for isolated legacy/unverified row.');
        return { ok: false, skipped: true, reason: 'legacy_untrusted' };
    }
    const actorCtx = await ensureVerifiedActorIdentity();
    const clientEventId = generateClientEventId('fwevent');
    item._event_seq = Number(item._event_seq || item.max_event_seq || 0) + 1;
    const eventSeq = item._event_seq;

    const targets = (customFinalTargets || item.bindings || []).map(b => ({
        code: b.code,
        name: b.name,
        spec: b.specification || b.spec || '',
        unit: b.unit || '',
        brand: b.brand || '',
        category: b.category || '',
        qty_ratio: b.qty_ratio !== undefined && b.qty_ratio !== null
            ? Number(b.qty_ratio)
            : ((item.original_qty && Number(item.original_qty) > 0) ? (Number(b.qty) / Number(item.original_qty)) : 1.0)
    }));

    const candidates = (item.candidates || item.top_candidates || []).slice(0, 3).map(c => ({
        code: c.code,
        name: c.name,
        score: c.score || 0.0
    }));

    const top1 = candidates && candidates.length > 0 ? candidates[0] : null;
    const prof = item.professional || '暖通';
    const recSnapId = item.recommendation_snapshot_id || item.initial_snapshot_id || '';

    const payload = Object.freeze({
        client_event_id: clientEventId,
        document_id: item.document_id,
        work_session_id: item.work_session_id || currentWorkSessionId || '',
        saved_session_id: activeSessionId || '',
        session_id: item.work_session_id || currentWorkSessionId || item.document_id,
        sheet_id: item.sheet_id,
        source_row_id: item.source_row_id,
        row_idx: item.original_row_idx || 0,
        event_seq: eventSeq,
        recommendation_snapshot_id: recSnapId,
        recommendation_revision: item.recommendation_revision || 1,
        discipline: prof,
        professional: prof,
        raw_name: item.original_name || item.raw_name || item.name || '',
        raw_spec: item.original_spec || item.spec || '',
        raw_unit: item.original_unit || item.unit || '',
        raw_category: item.original_category || item.category || '',
        parsed_spec: item.parsed_spec || {},
        extracted_params: item.extracted_params || item.parsed_spec || {},
        auto_top1_code: top1 ? top1.code : (item.initial_auto_code || ''),
        auto_top1_score: top1 ? top1.score : 0.0,
        auto_top1_name: top1 ? top1.name : '',
        auto_candidates: candidates,
        algo_version: item.algo_version || '',
        model_version: item.model_version || '',
        config_version: item.config_version || '',
        action_type: actionType,
        final_targets: targets
    });

    const record = {
        event_id: clientEventId,
        kind: 'flywheel',
        endpoint: '/api/flywheel/record',
        actor_user_id: actorCtx.actor_user_id,
        actor_account_epoch: actorCtx.actor_account_epoch,
        document_id: item.document_id,
        sheet_id: item.sheet_id,
        source_row_id: item.source_row_id,
        recommendation_snapshot_id: recSnapId,
        event_seq: eventSeq,
        payload: JSON.parse(JSON.stringify(payload)),
        status: 'pending',
        attempts: 0,
        last_http_status: null,
        last_error_code: '',
        last_error_message: '',
        created_at: Date.now(),
        updated_at: Date.now()
    };
    pendingFeedbackEvents.push(record);
    savePendingFeedbackEventsToStorage();
    return await _dispatchSingleFeedbackRecord(record, { item });
}

// Helper to create an immutable feedback sample event once and deliver with bounded same-event retry
async function submitFeedbackSample(item, autoCode, manualCode, isCorrect) {
    if (!item || item.feedback_eligible === false || !item.document_id || !item.sheet_id || !item.source_row_id) {
        return { ok: false, skipped: true, reason: 'legacy_untrusted' };
    }
    const actorCtx = await ensureVerifiedActorIdentity();
    const clientEventId = generateClientEventId('fbevent');
    item._feedback_seq = Number(item._feedback_seq || item.max_feedback_seq || 0) + 1;
    const eventSeq = item._feedback_seq;
    const recSnapId = item.recommendation_snapshot_id || item.initial_snapshot_id || '';

    const payload = Object.freeze({
        client_event_id: clientEventId,
        document_id: item.document_id,
        sheet_id: item.sheet_id,
        source_row_id: item.source_row_id,
        event_seq: eventSeq,
        recommendation_snapshot_id: recSnapId,
        row_id: item.original_row_idx || 0,
        raw_text: item.original_spec || '',
        auto_code: autoCode || '',
        manual_code: manualCode || '',
        is_correct: Boolean(isCorrect)
    });

    const record = {
        event_id: clientEventId,
        kind: 'feedback',
        endpoint: '/api/feedback/submit',
        actor_user_id: actorCtx.actor_user_id,
        actor_account_epoch: actorCtx.actor_account_epoch,
        document_id: item.document_id,
        sheet_id: item.sheet_id,
        source_row_id: item.source_row_id,
        recommendation_snapshot_id: recSnapId,
        event_seq: eventSeq,
        payload: JSON.parse(JSON.stringify(payload)),
        status: 'pending',
        attempts: 0,
        last_http_status: null,
        last_error_code: '',
        last_error_message: '',
        created_at: Date.now(),
        updated_at: Date.now()
    };
    pendingFeedbackEvents.push(record);
    savePendingFeedbackEventsToStorage();
    return await _dispatchSingleFeedbackRecord(record, { item });
}

if (typeof window !== 'undefined') {
    window.retryPendingFeedbackEvents = retryPendingFeedbackEvents;
    window.getActivePendingFeedbackEvents = getActivePendingFeedbackEvents;
    window.reportFlywheelAction = reportFlywheelAction;
    window.submitFeedbackSample = submitFeedbackSample;
    window.applyVerifiedActorIdentity = applyVerifiedActorIdentity;
    window.annotateRestoredItems = annotateRestoredItems;
    window.__m0SetWorkbenchItems = (newItems, docId, wsId) => {
        items = Array.isArray(newItems) ? newItems : [];
        if (docId !== undefined) currentDocumentId = docId;
        if (wsId !== undefined) currentWorkSessionId = wsId;
    };
}

// 6. Manual Global Database Search
async function runManualSearch() {
    const input = document.getElementById('manual-search-input');
    const keyword = input.value.trim();
    if (!keyword) {
        alert("请输入搜索关键词");
        return;
    }
    
    const tbody = document.getElementById('search-results-tbody');
    tbody.innerHTML = `<tr><td colspan="5" class="empty-search"><i class="fa-solid fa-spinner animate-spin"></i> 正在检索中...</td></tr>`;
    
    try {
        const response = await fetch(`${backendUrl}/api/search?keyword=${encodeURIComponent(keyword)}`);
        if (!response.ok) {
            throw new Error("全局检索失败");
        }
        
        const data = await response.json();
        const results = data.results;
        
        tbody.innerHTML = '';
        if (results.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" class="empty-search">没有匹配的物料记录</td></tr>`;
            return;
        }
        
        results.forEach(res => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><code>${escapeHtml(res.code)}</code></td>
                <td><strong>${escapeHtml(res.name)}</strong></td>
                <td><div style="font-size: 11.5px; max-width: 250px; max-height: 80px; overflow-y: auto; white-space: pre-wrap;">${escapeHtml(res.specification || '')}</div></td>
                <td>${escapeHtml(res.unit || '个')}</td>
                <td><button class="btn btn-primary" style="padding: 4px 8px; font-size: 11px;">套用</button></td>
            `;
            
            const btn = tr.querySelector('button');
            btn.addEventListener('click', () => {
                applyMatchCandidate({
                    code: res.code,
                    name: res.name,
                    category: res.category,
                    specification: res.specification,
                    unit: res.unit,
                    brand: res.brand
                }, 'MANUAL_REPLACE');
            });
            
            tbody.appendChild(tr);
        });
    } catch (error) {
        tbody.innerHTML = `<tr><td colspan="5" class="empty-search" style="color: var(--color-danger);">搜索出错: ${escapeHtml(error.message)}</td></tr>`;
    }
}

// 7. Manual Parameters Supplement Rematch (V0.0.2)
async function runParamRematch() {
    const item = selectedItem;
    if (!item) return;
    
    const model = document.getElementById('modal-param-model').value.trim();
    const volume = document.getElementById('modal-param-volume').value ? parseInt(document.getElementById('modal-param-volume').value) : null;
    const power = document.getElementById('modal-param-power').value ? parseFloat(document.getElementById('modal-param-power').value) : null;
    const dn = document.getElementById('modal-param-dn').value ? parseInt(document.getElementById('modal-param-dn').value) : null;
    
    document.getElementById('param-modal').style.display = 'none';
    showLoading("重新对齐特征参数...", "系统正在整合手动输入的指标并检索计算匹配得分...");
    
    invalidateItemDecision(item, '已重算推荐，请重新确认');
    try {
        await persistReviewEdit(item);
        const response = await fetch(`${backendUrl}/api/rematch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                row_id: item.original_row_idx,
                text: item.original_spec,
                professional: item.professional || "暖通",
                category: item.original_category || "",
                brand: item.original_brand || "",
                unit: item.original_unit || "",
                document_id: item.document_id || null,
                sheet_id: item.sheet_id || null,
                source_row_id: item.source_row_id || null,
                fixed_params: {
                    model: model || null,
                    air_volume: volume,
                    power: power,
                    dn: dn
                }
            })
        });
        
        if (!response.ok) {
            throw new Error("参数重匹配计算失败");
        }
        
        const data = await response.json();
        
        applyRematchResult(item, data);

        // Report param adjust to Flywheel
        item.extracted_params = {
            model: model || null,
            air_volume: volume,
            power: power,
            dn: dn
        };
        await reportFlywheelAction(item, 'PARAM_ADJUST');

        if (selectedItem === item) selectRow(item);
        renderTable();
        updateProgress();
        
    } catch (e) {
        alert(e.message);
    } finally {
        hideLoading();
    }
}

// 7.2 Render Structured Parameter Chips
function renderParamChips() {
    const container = document.getElementById('params-chips-container');
    if (!container) return;
    container.innerHTML = '';
    
    if (!selectedItem || !selectedItem.parsed_spec) {
        container.innerHTML = `<span style="font-size: 11px; color: var(--color-text-muted); padding: 8px 0;">未提取到特征维度</span>`;
        return;
    }
    
    const spec = selectedItem.parsed_spec;
    const model = spec.model;
    const attrs = spec.attrs || {};
    
    const hasParams = model || Object.keys(attrs).length > 0;
    if (!hasParams) {
        container.innerHTML = `<span style="font-size: 11px; color: var(--color-text-muted); padding: 8px 0;">未提取到特征维度</span>`;
        return;
    }
    
    // Initialize active_params if not present, and ensure all keys are initialized
    if (!selectedItem.active_params) {
        selectedItem.active_params = {};
    }
    if (model && selectedItem.active_params['model'] === undefined) {
        selectedItem.active_params['model'] = true;
    }
    for (const k in attrs) {
        if (selectedItem.active_params[k] === undefined) {
            selectedItem.active_params[k] = true;
        }
    }
    
    // 1. Model Chip
    if (model) {
        const chip = document.createElement('div');
        const isActive = selectedItem.active_params['model'];
        chip.className = `param-chip ${isActive ? 'active' : 'inactive'}`;
        chip.innerHTML = `<i class="fa-solid ${isActive ? 'fa-check' : 'fa-xmark'}"></i> 型号: ${escapeHtml(model)}`;
        chip.addEventListener('click', () => {
            selectedItem.active_params['model'] = !selectedItem.active_params['model'];
            renderParamChips();
        });
        container.appendChild(chip);
    }
    
    // 2. Attribute Chips
    for (const k in attrs) {
        const val = attrs[k];
        let valStr = val;
        if (Array.isArray(val)) {
            valStr = val.join('+');
        }
        
        const chip = document.createElement('div');
        const isActive = selectedItem.active_params[k];
        chip.className = `param-chip ${isActive ? 'active' : 'inactive'}`;
        chip.innerHTML = `<i class="fa-solid ${isActive ? 'fa-check' : 'fa-xmark'}"></i> ${escapeHtml(k)}: ${escapeHtml(valStr)}`;
        chip.addEventListener('click', () => {
            selectedItem.active_params[k] = !selectedItem.active_params[k];
            renderParamChips();
        });
        container.appendChild(chip);
    }
}

// 7.3 Rematch Single Item with Active Parameter Dimensions
async function rematchCurrentWithParams() {
    const item = selectedItem;
    if (!item?.parsed_spec) return;
    
    // Construct customized parsed_spec
    const customizedSpec = {
        model: (item.active_params || {})['model'] ? item.parsed_spec.model : "",
        attrs: {}
    };
    
    const attrs = item.parsed_spec.attrs || {};
    for (const k in attrs) {
        if ((item.active_params || {})[k]) {
            customizedSpec.attrs[k] = attrs[k];
        }
    }
    
    showLoading("重新对齐特征参数...", "系统正在使用选定的核心参数维度检索并重新匹配推荐项...");
    
    invalidateItemDecision(item, '已重算推荐，请重新确认');
    try {
        await persistReviewEdit(item);
        const response = await fetch(`${backendUrl}/api/match`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                text: item.original_spec,
                name: item.original_name,
                category: item.original_category || "",
                professional: item.professional || "暖通",
                brand: item.original_brand || "",
                unit: item.original_unit || "",
                document_id: item.document_id || null,
                sheet_id: item.sheet_id || null,
                source_row_id: item.source_row_id || null,
                parsed_spec: customizedSpec
            })
        });
        
        if (!response.ok) throw new Error("重新匹配失败");
        
        const data = await response.json();
        
        applyRematchResult(item, data);

        if (selectedItem === item) selectRow(item);
        renderTable();
        updateProgress();
        
    } catch (e) {
        alert(e.message);
    } finally {
        hideLoading();
    }
}

// 7.4 Batch Apply Parameter Dimensions and Rematch Similar Items
async function batchApplyParamsAndRematch() {
    if (!selectedItem) return;
    
    const targetName = selectedItem.original_name;
    const similarItems = items.filter(it => it.original_name === targetName);
    
    if (similarItems.length === 0) return;
    
    // Confirm with user
    if (!confirm(`确认要将当前的特征选择应用到所有同类设备（名称为 "${targetName}"，共 ${similarItems.length} 项）并批量重新匹配吗？`)) {
        return;
    }
    
    showLoading("批量对齐特征并重新匹配中...", `系统正在更新 ${similarItems.length} 个同类设备的过滤参数，并并发计算最佳套用项，请稍候...`);
    
    try {
        // Clone the source of truth to prevent it from being mutated during the map loop
        const sourceActiveParams = Object.assign({}, selectedItem.active_params || {});
        
        const promises = similarItems.map(async (it) => {
            // Synchronize active_params
            it.active_params = {};
            if (it.parsed_spec) {
                if (it.parsed_spec.model) {
                    if ('model' in sourceActiveParams) {
                        it.active_params['model'] = sourceActiveParams['model'];
                    } else {
                        it.active_params['model'] = true;
                    }
                }
                if (it.parsed_spec.attrs) {
                    for (const k in it.parsed_spec.attrs) {
                        if (k in sourceActiveParams) {
                            it.active_params[k] = sourceActiveParams[k];
                        } else {
                            it.active_params[k] = true;
                        }
                    }
                }
            }
            
            invalidateItemDecision(it, '已重算推荐，请重新确认');
            await persistReviewEdit(it);
            // Build customized spec
            const customizedSpec = {
                model: it.active_params['model'] ? it.parsed_spec?.model : "",
                attrs: {}
            };
            const attrs = it.parsed_spec?.attrs || {};
            for (const k in attrs) {
                if (it.active_params[k]) {
                    customizedSpec.attrs[k] = attrs[k];
                }
            }
            
            const response = await fetch(`${backendUrl}/api/match`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: it.original_spec,
                    name: it.original_name,
                    category: it.original_category || "",
                    professional: it.professional || "暖通",
                    brand: it.original_brand || "",
                    unit: it.original_unit || "",
                    document_id: it.document_id || null,
                    sheet_id: it.sheet_id || null,
                    source_row_id: it.source_row_id || null,
                    parsed_spec: customizedSpec
                })
            });
            
            if (!response.ok) throw new Error(`设备第 ${it.original_row_idx} 行重新匹配失败`);
            
            const data = await response.json();
            
            applyRematchResult(it, data);
        });
        
        await Promise.all(promises);
        
        // Refresh UI
        selectRow(selectedItem);
        renderTable();
        updateProgress();
        
        alert(`批量匹配完成！已成功重新校准并对齐了 ${similarItems.length} 个 "${targetName}" 设备。`);
        
    } catch (e) {
        alert(`批量重新匹配失败: ${e.message}`);
    } finally {
        hideLoading();
    }
}

function applyRematchResult(item, result) {
    // Preserve initial_auto_code and initial_snapshot_id for feedback provenance.
    item.bindings = [];
    item.top_candidates = result.top_candidates || [];
    item.acceptance_calibration = result.acceptance_calibration;
    for (const key of ['recommendation_snapshot_id', 'recommendation_revision', 'algo_version',
        'model_version', 'config_version', 'parsed_spec']) {
        if (result[key] !== undefined) item[key] = result[key];
    }
    syncBindingDisplay(item);
}

// 8. Update progress indicators
function updateProgress() {
    if (items.length === 0) return;
    
    // 1. Overall progress
    const matchedCount = items.filter(item => hasReviewApproval(item)).length;
    const progressPercent = Math.round((matchedCount / items.length) * 100) || 0;
    
    document.getElementById('match-progress-bar').style.width = `${progressPercent}%`;
    document.getElementById('progress-percent').innerText = `${progressPercent}%`;
    
    // 2. Current Professional progress
    let profItems = items;
    if (currentProfessional !== '全部') {
        profItems = items.filter(item => item.professional === currentProfessional);
    }
    
    const profMatchedCount = profItems.filter(item => hasReviewApproval(item)).length;
    const profProgressPercent = Math.round((profMatchedCount / profItems.length) * 100) || 0;
    
    document.getElementById('current-prof-progress-label').innerText = `${currentProfessional}可导出进度`;
    document.getElementById('current-prof-progress-bar').style.width = `${profProgressPercent}%`;
    document.getElementById('current-prof-progress-percent').innerText = `${profProgressPercent}%`;
}

// 9. Export standard sheet blob handler (V0.0.2 non-destructive)
async function handleExport() {
    if (items.length === 0) return;
    
    showLoading("正在精准写入模板并导出报表...", "系统正在执行非破坏性写入，自动填充价格列并重新生成序号，请稍候...", 0);
    
    try {
        await Promise.all(items.map(persistReviewEdit));
        const response = await fetch(`${backendUrl}/api/export`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: jsonPayloadData()
        });
        
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            // V015-M2：行级导出门禁原因展示
            const d = err.detail;
            if (d && d.row) {
                throw new Error(`第 ${d.row.original_row_idx} 行「${d.row.original_name}」：${d.row.reason}`);
            }
            throw new Error((typeof d === 'string' ? d : (d && d.message)) || "生成报表时出错");
        }
        
        const blob = await response.blob();
        
        // Trigger download
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        
        let downloadName = `标准清单_已匹配_${new Date().toISOString().slice(0,10)}.xlsx`;
        if (originalFileName) {
            const dotIdx = originalFileName.lastIndexOf('.');
            const baseName = dotIdx !== -1 ? originalFileName.substring(0, dotIdx) : originalFileName;
            downloadName = `${baseName}_已匹配.xlsx`;
        }
        a.download = downloadName;
        document.body.appendChild(a);
        a.click();
        
        window.URL.revokeObjectURL(url);
        a.remove();
        
        setTimeout(() => {
            if (confirm("报表导出成功！是否立即结束当前匹配任务并清空工作台？\n（点击【确定】将结束当前任务并返回首页；点击【取消】可保留当前工作台继续查看）")) {
                resetAndEndTask();
            }
        }, 300);
        
    } catch (error) {
        alert(`导出失败: ${error.message}`);
    } finally {
        hideLoading();
    }
}

function jsonPayloadData() {
    const mapped = items.map(item => {
        return {
            original_row_idx: item.original_row_idx,
            original_name: item.original_name,
            original_spec: item.original_spec,
            original_qty: item.original_qty,
            original_unit: item.original_unit,
            original_loss: item.original_loss,
            matched_code: item.matched_code,
            matched_name: item.matched_name,
            matched_category: item.matched_category,
            matched_spec: item.matched_spec,
            matched_unit: item.matched_unit,
            matched_brand: item.matched_brand,
            professional: item.professional || "暖通",
            original_code: item.original_code || "",
            // V015-M1 / F19：原始品类/品牌随导出 payload 贯通，缺失回退空串兼容旧会话
            original_category: item.original_category || "",
            original_brand: item.original_brand || "",
            bindings: item.bindings || null,
            // V015-M2：决策状态仅作展示上下文，导出端不信任，服务端逐项复核
            decision_status: item.decision_status || "",
            decision_source: item.decision_source || ""
        };
    });
    return JSON.stringify({ items: mapped, scope_filename: originalFileName || "" });
}

// 10. Configurations panel loading & rendering
async function loadAndRenderConfigs() {
    showLoading("正在加载匹配规则...", "系统正在从后端热加载行业词典、别名以及参数公差规则...");
    try {
        const response = await fetch(`${backendUrl}/api/config/load`);
        if (!response.ok) throw new Error("加载规则配置失败");
        
        configs = await response.json();
        
        // Render Alias table
        renderConfigTable('alias-tbody', configs.alias);
        
        // Render Units table
        renderConfigTable('units-tbody', configs.units);
        
        // Render Stop words
        renderStopWords(configs.stop_words);
        
        // Render Tolerances table
        renderTolerancesTable(configs.tolerances);
        
        // Render Self-learning recommendations
        loadAndRenderSuggestions();
        
        // Render Database Metadata and logs
        loadAndRenderDbMetadata();
        loadAndRenderLogs();
        
    } catch (e) {
        alert(e.message);
    } finally {
        hideLoading();
    }
}

function renderConfigTable(tbodyId, dataObj) {
    const tbody = document.getElementById(tbodyId);
    tbody.innerHTML = '';
    
    Object.entries(dataObj).forEach(([key, val]) => {
        addConfigTableRow(tbodyId, key, val);
    });
}

function addConfigTableRow(tbodyId, key = '', val = '') {
    const tbody = document.getElementById(tbodyId);
    const tr = document.createElement('tr');
    tr.innerHTML = `
        <td><input type="text" class="cfg-key" value="${key}" placeholder="原始描述..."/></td>
        <td><input type="text" class="cfg-val" value="${val}" placeholder="替换词..."/></td>
        <td><button class="btn btn-secondary" style="padding: 4px 8px; color: var(--color-danger); border-color: var(--color-danger-border);" onclick="this.closest('tr').remove()"><i class="fa-solid fa-trash"></i></button></td>
    `;
    tbody.appendChild(tr);
}

function renderStopWords(stopWordsList) {
    const container = document.getElementById('stopwords-container');
    container.innerHTML = '';
    
    stopWordsList.forEach(word => {
        const tag = document.createElement('span');
        tag.className = 'stopword-tag';
        tag.innerHTML = `
            <span>${word}</span>
            <i class="fa-solid fa-xmark remove-btn"></i>
        `;
        
        tag.querySelector('.remove-btn').addEventListener('click', () => {
            tag.remove();
        });
        container.appendChild(tag);
    });
}

function addStopWordFromInput() {
    const input = document.getElementById('new-stopword-input');
    const word = input.value.trim();
    if (!word) return;
    
    // Add to HTML directly
    const container = document.getElementById('stopwords-container');
    const tag = document.createElement('span');
    tag.className = 'stopword-tag';
    tag.innerHTML = `
        <span>${word}</span>
        <i class="fa-solid fa-xmark remove-btn"></i>
    `;
    tag.querySelector('.remove-btn').addEventListener('click', () => tag.remove());
    container.appendChild(tag);
    
    input.value = '';
}

function renderTolerancesTable(tolerancesObj) {
    const tbody = document.getElementById('tolerances-tbody');
    tbody.innerHTML = '';
    
    Object.entries(tolerancesObj).forEach(([attr, val]) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td style="font-weight: 500;">${attr}</td>
            <td><input type="number" step="0.01" class="cfg-tolerance-val" data-attr="${attr}" value="${val}" style="width: 100px;"/></td>
        `;
        tbody.appendChild(tr);
    });
}

async function loadAndRenderSuggestions() {
    const tbody = document.getElementById('suggestions-tbody');
    try {
        const response = await fetch(`${backendUrl}/api/feedback/suggestions`);
        if (!response.ok) return;
        const data = await response.json();
        const suggestions = data.suggestions;
        
        tbody.innerHTML = '';
        if (suggestions.length === 0) {
            tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--color-text-muted); padding: 20px;">暂无新建议，样本数据持续沉淀中...</td></tr>`;
        } else {
            suggestions.forEach(s => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><code style="font-size: 13px;">${s.suggested_typo}</code></td>
                    <td><strong>${s.suggested_correction}</strong></td>
                    <td><span class="badge" style="background-color: var(--color-success-bg); color: var(--color-success); font-weight: 700; padding: 2px 8px;">${s.confidence_count} 次</span></td>
                    <td><button class="btn btn-success" style="padding: 4px 8px; font-size: 11px;"><i class="fa-solid fa-check"></i> 采纳为别名</button></td>
                `;
                
                tr.querySelector('button').addEventListener('click', () => {
                    // Add to alias list
                    addConfigTableRow('alias-tbody', s.suggested_typo, s.suggested_correction);
                    // Remove suggestion row from HTML
                    tr.remove();
                    if (tbody.children.length === 0) {
                        tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--color-text-muted); padding: 20px;">建议已被处理</td></tr>`;
                    }
                });
                tbody.appendChild(tr);
            });
        }
        
        // Also load binding recommendations and confirmed rules
        await loadAndRenderBindingSuggestionsAndRules();
    } catch (e) {
        console.error("Failed to load matching suggestions", e);
    }
}

// 10.5 Load and Render auto-binding suggestions and active rules
async function loadAndRenderBindingSuggestionsAndRules() {
    const suggTbody = document.getElementById('binding-suggestions-tbody');
    const rulesTbody = document.getElementById('binding-rules-tbody');
    
    try {
        // 1. Suggestions
        const suggResp = await fetch(`${backendUrl}/api/binding/suggestions`);
        if (suggResp.ok) {
            const suggData = await suggResp.json();
            const suggestions = suggData.suggestions || [];
            suggTbody.innerHTML = '';
            if (suggestions.length === 0) {
                suggTbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--color-text-muted); padding: 20px;">暂无自动学习到的绑定组合推荐</td></tr>`;
            } else {
                suggestions.forEach(s => {
                    const tr = document.createElement('tr');
                    
                    const boundListHtml = s.bindings.map(b => `<div style="font-size: 11.5px; margin-bottom: 2px;"><code style="font-family: monospace; background: rgba(59, 130, 246, 0.1); color: var(--color-primary); padding: 1px 4px; border-radius: 3px; font-size: 11px;">${escapeHtml(b.code)}</code> <strong>${escapeHtml(b.name)}</strong> (比例: ${escapeHtml(b.qty_ratio.toFixed(2))})</div>`).join('');
                    
                    const suggKey = `${s.original_name}::${s.professional || ''}::${s.original_spec || ''}`;
                    window._bindingEvalCache = window._bindingEvalCache || {};

                    tr.innerHTML = `
                        <td><strong>${escapeHtml(s.original_name)}</strong>${s.professional ? `<div style="font-size:11px;color:var(--color-text-muted);">${escapeHtml(s.professional)} ${escapeHtml(s.original_spec || '')}</div>` : ''}</td>
                        <td><div style="display: flex; flex-direction: column; gap: 4px;">${boundListHtml}</div></td>
                        <td><span class="badge" style="background-color: var(--color-success-bg); color: var(--color-success); font-weight: 700; padding: 2px 8px; font-size: 11px;">${escapeHtml(s.confidence_count)} 次</span></td>
                        <td>
                            <div style="display:flex;gap:4px;flex-wrap:wrap;">
                                <button class="btn btn-secondary btn-bind-eval" style="padding: 4px 8px; font-size: 11px;">🧪 评测</button>
                                <button class="btn btn-success btn-bind-confirm" style="padding: 4px 8px; font-size: 11px;"><i class="fa-solid fa-check"></i> 采纳规则</button>
                            </div>
                        </td>
                    `;

                    tr.querySelector('.btn-bind-eval').addEventListener('click', async () => {
                        const prof = (s.professional || prompt('请输入该绑定规则的明确专业 (例如 暖通 / 给排水 / 强电 / 医气):', '暖通') || '').trim();
                        if (!prof) return;
                        s.professional = prof;
                        try {
                            const evRes = await fetch(`${backendUrl}/api/binding/rules/eval`, {
                                method: 'POST',
                                headers: getAuthHeaders(),
                                body: JSON.stringify({
                                    original_name: s.original_name,
                                    original_spec: s.original_spec || '',
                                    professional: prof,
                                    bindings: s.bindings
                                })
                            });
                            const evData = await evRes.json();
                            if (!evRes.ok) {
                                const msg = typeof evData.detail === 'object' ? (evData.detail.message || JSON.stringify(evData.detail)) : evData.detail;
                                throw new Error(msg || '启动评测失败');
                            }
                            const evalId = evData.eval_id || evData.task_id;
                            for (let attempt = 0; attempt < 90; attempt++) {
                                await new Promise(r => setTimeout(r, 1000));
                                const stRes = await fetch(`${backendUrl}/api/binding/rules/eval/${evalId}`, { headers: getAuthHeaders() });
                                if (!stRes.ok) continue;
                                const st = await stRes.json();
                                if (st.status === 'completed' || st.status === 'failed') {
                                    window._bindingEvalCache[suggKey] = {
                                        eval_id: evalId,
                                        benefit: st.benefit || 'invalid',
                                        safety_passed: Boolean(st.safety_passed ?? st.passed),
                                        professional: prof
                                    };
                                    alert(`沙箱评测完成 (${evalId})\n结论: safety=${Boolean(st.safety_passed ?? st.passed)}, benefit=${st.benefit || 'invalid'}`);
                                    return;
                                }
                            }
                            alert('沙箱评测超时，请稍后重试');
                        } catch (err) {
                            alert('绑定规则评测失败: ' + err.message);
                        }
                    });

                    tr.querySelector('.btn-bind-confirm').addEventListener('click', async () => {
                        const cached = window._bindingEvalCache[suggKey] || {};
                        const prof = cached.professional || s.professional || '';
                        let neutralReason = null;
                        if (cached.benefit === 'neutral') {
                            neutralReason = prompt('该规则沙箱评测为 neutral（无改善），确需启用请填写理由:');
                            if (neutralReason === null) return;
                        }
                        const confirmResp = await fetch(`${backendUrl}/api/binding/rules/confirm`, {
                            method: 'POST',
                            headers: getAuthHeaders(),
                            body: JSON.stringify({
                                original_name: s.original_name,
                                original_spec: s.original_spec || '',
                                professional: prof,
                                bindings: s.bindings,
                                eval_id: cached.eval_id || null,
                                neutral_reason: neutralReason
                            })
                        });
                        if (confirmResp.ok) {
                            alert(`已启用原始清单 "${s.original_name}" 对应的多物料自动绑定推荐规则！`);
                            loadAndRenderBindingSuggestionsAndRules();
                        } else {
                            const errBody = await confirmResp.json().catch(() => ({}));
                            const errMsg = typeof errBody.detail === 'object' ? (errBody.detail.message || JSON.stringify(errBody.detail)) : errBody.detail;
                            alert('采纳规则失败: ' + (errMsg || confirmResp.status));
                        }
                    });
                    
                    suggTbody.appendChild(tr);
                });
            }
        }
        
        // 2. Confirmed Rules
        const rulesResp = await fetch(`${backendUrl}/api/binding/rules`);
        if (rulesResp.ok) {
            const rulesData = await rulesResp.json();
            const rules = rulesData.rules || [];
            rulesTbody.innerHTML = '';
            if (rules.length === 0) {
                rulesTbody.innerHTML = `<tr><td colspan="3" style="text-align: center; color: var(--color-text-muted); padding: 20px;">当前暂无启用的绑定推荐规则</td></tr>`;
            } else {
                rules.forEach(r => {
                    const tr = document.createElement('tr');
                    
                    const boundListHtml = r.bound_materials.map(b => `<div style="font-size: 11.5px; margin-bottom: 2px;"><code style="font-family: monospace; background: rgba(59, 130, 246, 0.1); color: var(--color-primary); padding: 1px 4px; border-radius: 3px; font-size: 11px;">${escapeHtml(b.code)}</code> <strong>${escapeHtml(b.name)}</strong> (比例: ${escapeHtml(b.qty_ratio.toFixed(2))})</div>`).join('');
                    
                    tr.innerHTML = `
                        <td><strong>${escapeHtml(r.original_name)}</strong></td>
                        <td><div style="display: flex; flex-direction: column; gap: 4px;">${boundListHtml}</div></td>
                        <td><button class="btn btn-secondary" style="padding: 4px 8px; color: var(--color-danger); border-color: var(--color-danger-border); font-size: 11px;"><i class="fa-solid fa-trash"></i> 删除</button></td>
                    `;
                    
                    tr.querySelector('button').addEventListener('click', async () => {
                        if (confirm(`确定要删除针对 "${r.original_name}" 的自动绑定推荐规则吗？`)) {
                            const delResp = await fetch(`${backendUrl}/api/binding/rules/delete`, {
                                method: 'POST',
                                headers: getAuthHeaders(),
                                body: JSON.stringify({
                                    original_name: r.original_name,
                                    original_spec: r.original_spec || null,
                                    professional: r.professional || null
                                })
                            });
                            if (delResp.ok) {
                                loadAndRenderBindingSuggestionsAndRules();
                            } else {
                                alert('删除失败');
                            }
                        }
                    });
                    
                    rulesTbody.appendChild(tr);
                });
            }
        }
    } catch (e) {
        console.error("Failed to load binding suggestions and rules", e);
    }
}

// 11. Save all configs from page inputs
async function saveAllConfigurations() {
    showLoading("正在保存所有配置项...", "系统正在向后端保存新的别名对齐、公差等规则并实时热加载生效...");
    
    try {
        // 1. Gather Alias config
        const aliasData = {};
        document.querySelectorAll('#alias-tbody tr').forEach(tr => {
            const key = tr.querySelector('.cfg-key').value.trim();
            const val = tr.querySelector('.cfg-val').value.trim();
            if (key) aliasData[key] = val;
        });
        
        // 2. Gather Units config
        const unitsData = {};
        document.querySelectorAll('#units-tbody tr').forEach(tr => {
            const key = tr.querySelector('.cfg-key').value.trim();
            const val = tr.querySelector('.cfg-val').value.trim();
            if (key) unitsData[key] = val;
        });
        
        // 3. Gather Stop Words config
        const stopWordsData = [];
        document.querySelectorAll('#stopwords-container .stopword-tag span').forEach(el => {
            stopWordsData.push(el.innerText.trim());
        });
        
        // 4. Gather Tolerances config
        const tolerancesData = {};
        document.querySelectorAll('.cfg-tolerance-val').forEach(input => {
            const attr = input.dataset.attr;
            const val = parseFloat(input.value);
            if (attr) tolerancesData[attr] = isNaN(val) ? 0.15 : val;
        });
        
        // Submit saves sequentially
        const savePromises = [
            submitSaveConfig("alias", aliasData),
            submitSaveConfig("units", unitsData),
            submitSaveConfig("stop_words", stopWordsData),
            submitSaveConfig("tolerances", tolerancesData)
        ];
        
        await Promise.all(savePromises);
        alert("所有系统配置规则已保存，并实时热加载生效！");
        
    } catch (e) {
        alert(`保存配置失败: ${e.message}`);
    } finally {
        hideLoading();
    }
}

async function submitSaveConfig(configType, data) {
    const response = await fetch(`${backendUrl}/api/config/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            config_type: configType,
            data: data
        })
    });
    if (!response.ok) {
        throw new Error(`保存 ${configType} 配置出错`);
    }
    return response.json();
}

// Helper to render DB metadata stats
function renderDbMetadata(meta) {
    if (!meta) return;
    const totalRows = (meta.total_rows !== undefined ? meta.total_rows : meta.row_count) || 0;
    const importedRows = (meta.imported_rows !== undefined ? meta.imported_rows : meta.row_count) || 0;
    const disabledRows = meta.disabled_rows || 0;

    const totalEl = document.getElementById('db-meta-total-count');
    const importedEl = document.getElementById('db-meta-imported-count');
    const disabledEl = document.getElementById('db-meta-disabled-count');
    const rowCountEl = document.getElementById('db-meta-row-count');

    if (totalEl) totalEl.innerText = totalRows.toLocaleString();
    if (importedEl) importedEl.innerText = importedRows.toLocaleString();
    if (disabledEl) disabledEl.innerText = disabledRows.toLocaleString();
    if (rowCountEl) rowCountEl.innerText = importedRows.toLocaleString();

    document.getElementById('db-meta-last-date').innerText = meta.last_upload_date || '-';
    document.getElementById('db-meta-filename').innerText = meta.filename || '-';
}

// 10.1 Load & Render Database Metadata
async function loadAndRenderDbMetadata() {
    try {
        const response = await fetch(`${backendUrl}/api/db/metadata`);
        if (!response.ok) throw new Error("获取数据库指标失败");
        const meta = await response.json();
        renderDbMetadata(meta);
    } catch (e) {
        console.error("Error loading DB metadata:", e);
    }
}

// 10.2 Load & Render System Runtime Logs
async function loadAndRenderLogs() {
    const consoleEl = document.getElementById('log-console');
    if (!consoleEl) return;
    
    try {
        const response = await fetch(`${backendUrl}/api/logs?limit=100`);
        if (!response.ok) throw new Error("获取系统日志失败");
        const data = await response.json();
        
        if (data.logs && data.logs.length > 0) {
            consoleEl.textContent = data.logs.join('\n');
            // Auto scroll to bottom
            consoleEl.scrollTop = consoleEl.scrollHeight;
        } else {
            consoleEl.textContent = '暂无运行日志';
        }
    } catch (e) {
        consoleEl.textContent = `加载日志失败: ${e.message}`;
    }
}

// 10.3 Handle Database File Upload & Re-indexing
async function handleDbUploadAndImport() {
    const dbUploadInput = document.getElementById('db-upload-input');
    const startDbImportBtn = document.getElementById('btn-start-db-import');
    const dbFileLabel = document.getElementById('db-selected-file-label');
    
    if (dbUploadInput.files.length === 0) return;
    const dbCheckFile = dbUploadInput.files[0];
    const dbNameLower = (dbCheckFile && dbCheckFile.name ? dbCheckFile.name : '').trim().toLowerCase();
    if (!(dbNameLower.endsWith('.xlsx') || dbNameLower.endsWith('.xls'))) {
        dbUploadInput.value = '';
        if (dbFileLabel) dbFileLabel.innerText = '未选择文件';
        if (startDbImportBtn) startDbImportBtn.disabled = true;
        alert('物料库导入仅支持 .xlsx 或 .xls 格式文件。');
        return;
    }
    const file = dbUploadInput.files[0];
    
    const importMode = document.querySelector('input[name="db-import-mode"]:checked')?.value || 'replace';
    const disableMode = document.querySelector('input[name="db-disable-mode"]:checked')?.value || 'on_expire';
    const modeText = importMode === 'replace' ? '全量替换' : '增量追加';
    const disableText = disableMode === 'on_expire' ? '到期生效（保留未到期物料）' : '立即生效（严格全部停用）';
    
    if (!confirm(`确认要以【${modeText}】模式、禁用策略【${disableText}】，将文件 "${file.name}" 导入到全局物料明细库中吗？\n警告: 此操作会重建匹配向量索引，耗时大约数秒至数十秒。`)) {
        return;
    }
    
    showLoading("正在导入物料库并重建向量索引...", "系统正在初始化后台导入任务...", 0);
    
    // Show and reset the segmented progress container
    const segContainer = document.getElementById('segmented-progress-container');
    if (segContainer) {
        segContainer.style.display = 'flex';
        document.getElementById('progress-bar-structuring').style.width = '0%';
        document.getElementById('progress-val-structuring').innerText = '0%';
        document.getElementById('progress-bar-vectorization').style.width = '0%';
        document.getElementById('progress-val-vectorization').innerText = '0%';
        document.getElementById('progress-bar-matching').style.width = '0%';
        document.getElementById('progress-val-matching').innerText = '0%';
    }
    
    startDbImportBtn.disabled = true;
    
    const formData = new FormData();
    formData.append('file', file);
    formData.append('mode', importMode);
    formData.append('disable_mode', disableMode);

    try {
        const response = await fetch(`${backendUrl}/api/db/upload`, {
            method: 'POST',
            body: formData,
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('token')}`
            }
        });
        
        if (!response.ok) {
            const errData = await response.json();
            throw new Error(errData.detail || "导入失败");
        }
        
        const uploadRes = await response.json();
        const taskId = uploadRes.task_id;
        
        // Start polling status
        let consecutivePollErrors = 0;
        if (window.uploadPollInterval) clearInterval(window.uploadPollInterval);
        window.uploadPollInterval = setInterval(async () => {
            try {
                const statusRes = await fetch(`${backendUrl}/api/upload/status?task_id=${taskId}`, {
                    headers: {
                        'Authorization': `Bearer ${localStorage.getItem('token')}`
                    }
                });
                
                if (!statusRes.ok) {
                    const err = await statusRes.json();
                    throw new Error(err.detail || "查询任务进度失败");
                }
                
                const taskState = await statusRes.json();
                consecutivePollErrors = 0; // Reset error counter on successful response
                
                document.getElementById('loading-subtitle').innerText = "系统正在进行物料解析与向量索引重建，请勿刷新页面...";
                const currentItemEl = document.getElementById('progress-current-item');
                if (currentItemEl) {
                    currentItemEl.innerText = taskState.current_step || "导入中...";
                }
                
                document.getElementById('progress-bar-structuring').style.width = `${taskState.structuring_progress}%`;
                document.getElementById('progress-val-structuring').innerText = `${taskState.structuring_progress}%`;
                
                document.getElementById('progress-bar-vectorization').style.width = `${taskState.vectorization_progress}%`;
                document.getElementById('progress-val-vectorization').innerText = `${taskState.vectorization_progress}%`;
                
                document.getElementById('progress-bar-matching').style.width = `${taskState.matching_progress}%`;
                document.getElementById('progress-val-matching').innerText = `${taskState.matching_progress}%`;
                
                if (taskState.status === 'completed') {
                    clearInterval(window.uploadPollInterval);
                    window.uploadPollInterval = null;
                    hideLoading();
                    
                    // Reset file inputs
                    dbUploadInput.value = '';
                    dbFileLabel.innerText = '未选择文件';
                    
                    // Re-render fresh metadata and portrait
                    renderDbMetadata(taskState.result);
                    loadAndRenderDataPortrait();
                    
                    alert("物料明细库导入及向量索引重建已全部完成！");
                    loadAndRenderLogs();
                } else if (taskState.status === 'failed') {
                    clearInterval(window.uploadPollInterval);
                    window.uploadPollInterval = null;
                    hideLoading();
                    alert(`导入失败: ${taskState.error}`);
                    startDbImportBtn.disabled = false;
                }
            } catch (err) {
                consecutivePollErrors += 1;
                console.warn(`Polling error (${consecutivePollErrors}/10):`, err);
                if (consecutivePollErrors >= 10) {
                    clearInterval(window.uploadPollInterval);
                    window.uploadPollInterval = null;
                    hideLoading();
                    alert(`轮询进度连续失败中断: ${err.message}`);
                    startDbImportBtn.disabled = false;
                }
            }
        }, 1000);
        
    } catch (e) {
        hideLoading();
        alert(`导入出错: ${e.message}`);
        startDbImportBtn.disabled = false;
    }
}

async function loadAndRenderDataPortrait() {
    try {
        const response = await fetch(`${backendUrl}/api/db/portrait`);
        if (!response.ok) return;
        const data = await response.json();
        
        // Render Metric Cards
        const totalEl = document.getElementById('portrait-total-items');
        if (totalEl) totalEl.innerText = (data.total_items || 0).toLocaleString();
        
        const validEl = document.getElementById('portrait-valid-items');
        if (validEl) validEl.innerText = (data.valid_materials || 0).toLocaleString();
        
        const catEl = document.getElementById('portrait-cat-count');
        if (catEl) catEl.innerText = (data.category_count || 0).toLocaleString();
        
        const specRateEl = document.getElementById('portrait-spec-rate');
        if (specRateEl) specRateEl.innerText = `${data.spec_nonempty_rate || 0}%`;
        
        const specCountEl = document.getElementById('portrait-spec-count');
        if (specCountEl) specCountEl.innerText = (data.spec_nonempty_count || 0).toLocaleString();
        
        const modelRateEl = document.getElementById('portrait-model-rate');
        if (modelRateEl) modelRateEl.innerText = `${data.model_extracted_rate || 0}%`;
        
        const modelMissEl = document.getElementById('portrait-model-missing');
        if (modelMissEl) modelMissEl.innerText = (data.model_missing_count || 0).toLocaleString();
        
        // Render Bars
        const rawSpecRate = data.spec_nonempty_rate || 0;
        const modelRate = data.model_extracted_rate || 0;
        const attrRate = data.structured_attr_rate || 0;
        
        const barRawText = document.getElementById('bar-raw-spec-text');
        const barRaw = document.getElementById('bar-raw-spec');
        if (barRawText) barRawText.innerText = `${rawSpecRate}%`;
        if (barRaw) barRaw.style.width = `${Math.min(100, Math.max(0, rawSpecRate))}%`;
        
        const barModelText = document.getElementById('bar-model-text');
        const barModel = document.getElementById('bar-model');
        if (barModelText) barModelText.innerText = `${modelRate}% (${(data.model_extracted_count || 0).toLocaleString()}条)`;
        if (barModel) barModel.style.width = `${Math.min(100, Math.max(0, modelRate))}%`;
        
        const barAttrText = document.getElementById('bar-attr-text');
        const barAttr = document.getElementById('bar-attr');
        if (barAttrText) barAttrText.innerText = `${attrRate}% (${(data.structured_attr_count || 0).toLocaleString()}条)`;
        if (barAttr) barAttr.style.width = `${Math.min(100, Math.max(0, attrRate))}%`;
        
        // Render Top 5 Categories
        const catContainer = document.getElementById('portrait-categories-list');
        if (catContainer) {
            catContainer.innerHTML = '';
            (data.top_categories || []).forEach((c, idx) => {
                const item = document.createElement('div');
                item.style.cssText = 'display: flex; flex-direction: column; gap: 4px; font-size: 13px;';
                item.innerHTML = `
                    <div style="display: flex; justify-content: space-between;">
                        <span><strong style="color: var(--color-primary); font-family: monospace;">Top ${idx+1}.</strong> ${c.category}</span>
                        <span style="color: var(--color-text-secondary);">${c.count.toLocaleString()} 行 (${c.percent}%)</span>
                    </div>
                    <div style="height: 6px; background: var(--bg-subtle); border-radius: 3px; overflow: hidden;">
                        <div style="width: ${c.percent}%; height: 100%; background: var(--color-primary); border-radius: 3px;"></div>
                    </div>
                `;
                catContainer.appendChild(item);
            });
        }
        
        // Render Top 5 Units
        const unitContainer = document.getElementById('portrait-units-list');
        if (unitContainer) {
            unitContainer.innerHTML = '';
            (data.top_units || []).forEach((u, idx) => {
                const item = document.createElement('div');
                item.style.cssText = 'display: flex; flex-direction: column; gap: 4px; font-size: 13px;';
                item.innerHTML = `
                    <div style="display: flex; justify-content: space-between;">
                        <span><strong style="color: var(--color-chart-ram); font-family: monospace;">Top ${idx+1}.</strong> ${escapeHtml(u.unit)}</span>
                        <span style="color: var(--color-text-secondary);">${u.count.toLocaleString()} 行 (${u.percent}%)</span>
                    </div>
                    <div style="height: 6px; background: var(--bg-subtle); border-radius: 3px; overflow: hidden;">
                        <div style="width: ${u.percent}%; height: 100%; background: var(--color-chart-ram); border-radius: 3px;"></div>
                    </div>
                `;
                unitContainer.appendChild(item);
            });
        }
        
    } catch (e) {
        console.error("Failed to load data portrait:", e);
    }
}

async function handleSystemReset() {
    if (!confirm("⚠️ 高危操作确认！\n\n确定要一键清理系统所有运行期数据吗？\n（包含：所有暂存工作草稿、历史保存会话、上传的临时清单及反馈日志）\n\n注意：此操作不可逆！标准物料库和系统账号将被安全保留。")) {
        return;
    }
    
    showLoading("正在清理系统运行数据...", "系统正在清空临时会话、临时文件及历史日志，请稍候...");
    try {
        const response = await fetch(`${backendUrl}/api/system/reset`, {
            method: 'DELETE'
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "清理失败");
        
        await resetAndEndTask();
        alert(data.message || "系统运行数据已全量清理完毕，已恢复干净运行状态！");
    } catch (e) {
        alert(`清理失败: ${e.message}`);
    } finally {
        hideLoading();
    }
}

let currentDbPage = 1;
let currentDbTotalPages = 1;

async function loadAndRenderMaterialTable(page = 1) {
    currentDbPage = page;
    const categorySelect = document.getElementById('db-category-select');
    const searchInput = document.getElementById('db-search-input');
    const tbody = document.getElementById('db-materials-tbody');
    
    const category = categorySelect ? categorySelect.value : '';
    const keyword = searchInput ? searchInput.value.trim() : '';
    
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--color-text-muted); padding: 25px;"><i class="fa-solid fa-spinner fa-spin"></i> 正在检索全局物料数据库...</td></tr>`;
    }
    
    try {
        const url = `${backendUrl}/api/db/materials?page=${page}&page_size=20&category=${encodeURIComponent(category)}&keyword=${encodeURIComponent(keyword)}`;
        const response = await fetch(url);
        if (!response.ok) throw new Error("获取物料列表失败");
        
        const data = await response.json();
        
        // Populate category dropdown options if not populated yet
        if (categorySelect && categorySelect.options.length <= 1 && data.categories) {
            data.categories.forEach(cat => {
                const opt = document.createElement('option');
                opt.value = cat;
                opt.textContent = cat;
                categorySelect.appendChild(opt);
            });
        }
        
        // Update pagination labels
        currentDbTotalPages = data.total_pages || 1;
        const startItem = data.total > 0 ? (data.page - 1) * data.page_size + 1 : 0;
        const endItem = Math.min(data.total, data.page * data.page_size);
        
        const totalLbl = document.getElementById('db-table-total-label');
        if (totalLbl) totalLbl.innerText = `(共 ${(data.total || 0).toLocaleString()} 条记录)`;
        
        const countLbl = document.getElementById('db-total-count');
        if (countLbl) countLbl.innerText = (data.total || 0).toLocaleString();
        
        const startLbl = document.getElementById('db-page-start');
        if (startLbl) startLbl.innerText = startItem.toLocaleString();
        
        const endLbl = document.getElementById('db-page-end');
        if (endLbl) endLbl.innerText = endItem.toLocaleString();
        
        const curPageLbl = document.getElementById('db-current-page');
        if (curPageLbl) curPageLbl.innerText = data.page;
        
        const totalPagesLbl = document.getElementById('db-total-pages');
        if (totalPagesLbl) totalPagesLbl.innerText = currentDbTotalPages;
        
        const prevBtn = document.getElementById('db-prev-page-btn');
        if (prevBtn) prevBtn.disabled = (data.page <= 1);
        
        const nextBtn = document.getElementById('db-next-page-btn');
        if (nextBtn) nextBtn.disabled = (data.page >= currentDbTotalPages);
        
        // Render Table Rows
        if (!tbody) return;
        tbody.innerHTML = '';
        
        if (!data.items || data.items.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--color-text-muted); padding: 30px;">未查找到匹配条件的物料记录</td></tr>`;
            return;
        }
        
        data.items.forEach(item => {
            const tr = document.createElement('tr');
            const modelTag = item.model 
                ? `<span style="background: var(--color-success-bg); color: var(--color-success); padding: 2px 6px; border-radius: 4px; font-family: monospace; font-size: 12px; font-weight: 500;">${escapeHtml(item.model)}</span>` 
                : `<span style="color: var(--color-text-muted); font-size: 11px;">未提取</span>`;
                
            tr.innerHTML = `
                <td style="font-family: monospace; color: var(--color-text-secondary); font-size: 12px;">${escapeHtml(item.code)}</td>
                <td style="font-weight: 500; color: var(--color-text-primary);">${escapeHtml(item.name)}</td>
                <td style="color: var(--color-text-secondary); max-width: 300px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${escapeHtml(item.specification)}">${escapeHtml(item.specification || '-')}</td>
                <td>${modelTag}</td>
                <td><span style="background: var(--color-primary-glow); color: var(--color-primary); padding: 2px 8px; border-radius: 4px; font-size: 12px;">${escapeHtml(item.category || '未分类')}</span></td>
                <td>${escapeHtml(item.unit || '-')}</td>
                <td style="color: var(--color-text-secondary);">${escapeHtml(item.brand || '-')}</td>
            `;
            tbody.appendChild(tr);
        });
        
    } catch (e) {
        console.error("Error loading material table:", e);
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--color-danger); padding: 25px;">加载失败: ${escapeHtml(e.message)}</td></tr>`;
        }
    }
}

// ==========================================
// 工作暂存与保存 (Draft and Saved Sessions)
// ==========================================

let autoSaveInterval = null;
let activeSessionId = null;

function startAutoSave() {
    if (autoSaveInterval) clearInterval(autoSaveInterval);
    autoSaveInterval = setInterval(async () => {
        await saveDraft();
    }, 180000); // 3 minutes
}

async function saveDraft() {
    if (items.length === 0) return;
    try {
        await Promise.all(items.map(persistReviewEdit));
        const payload = {
            original_filename: originalFileName,
            professional: currentProfessional || '全部',
            items_json: JSON.stringify(items),
            document_id: currentDocumentId || '',
            work_session_id: currentWorkSessionId || ''
        };
        await fetch(`${backendUrl}/api/session/draft`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        console.log("Draft auto-saved successfully.");
    } catch (err) {
        console.error("Failed to auto-save draft:", err);
    }
}

// V015-M2：草稿/保存进度恢复后，请求服务端做权威状态注释与降级提示
async function annotateRestoredItems() {
    const downgradeUnverified = () => {
        let downgraded = 0;
        items.forEach(item => {
            if (hasReviewApproval(item)) {
                downgraded++;
            }
            if (!item.decision_status || ['auto_accepted', 'manual_confirmed'].includes(item.decision_status)) {
                item.decision_status = 'recommended';
                item.decision_source = 'legacy_draft';
                item.decision_reason = '服务端状态未能核验，请重新人工确认';
            }
        });
        if (downgraded > 0) {
            alert(`已载入进度：${downgraded} 条记录的服务端确认状态无法核验，已降为「待确认」，请复核后再导出。`);
        }
    };
    try {
        const res = await fetch(`${backendUrl}/api/session/annotate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                items: items,
                scope_filename: originalFileName || '',
                document_id: currentDocumentId || '',
                work_session_id: currentWorkSessionId || ''
            })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.items) {
            if (data.actor_user_id !== undefined && data.actor_user_id !== null) {
                applyVerifiedActorIdentity(data.actor_user_id, data.actor_account_epoch || '');
            }
            items = data.items;
            items.forEach(it => {
                it._event_seq = Math.max(Number(it._event_seq || 0), Number(it.max_event_seq || 0));
                it._feedback_seq = Math.max(Number(it._feedback_seq || 0), Number(it.max_feedback_seq || 0));
            });
            const firstVerified = items.find(it => it.identity_status === 'verified' && it.document_id);
            if (firstVerified) {
                currentDocumentId = firstVerified.document_id;
                currentWorkSessionId = firstVerified.work_session_id || currentWorkSessionId;
            }
            renderLegacyIsolationBanner(data.legacy_untrusted_count || 0);
            reconcilePendingFeedbackForDocument();
            if (data.downgraded > 0) {
                alert(`已载入进度：${data.downgraded} 条记录的旧绑定/确认状态无法核验或已失效，已统一降为「待确认」，请逐项复核后再导出。`);
            }
            if (data.legacy_untrusted_count > 0) {
                alert(`旧草稿/会话隔离提示：${data.legacy_untrusted_count} 条记录缺少可核验的服务端文档/源行身份，已隔离为只读复核，不会提交可信反馈；如需新采集反馈请重新上传清单。`);
            }
        } else {
            downgradeUnverified();
        }
    } catch (err) {
        downgradeUnverified();
        console.warn("草稿状态注释失败，已保守降级；导出仍受服务端门禁约束:", err);
    }
}

async function checkForDraft() {
    try {
        const res = await fetch(`${backendUrl}/api/session/draft`);
        const data = await res.json();
        if (res.ok && data.draft && items.length === 0) {
            const draft = data.draft;
            const draftTime = draft.updated_at;
            if (confirm(`系统检测到您有未保存的编辑草稿（${draft.original_filename}，最后编辑时间：${draftTime}，共 ${JSON.parse(draft.items_json).length} 条数据）。\n\n是否载入该草稿并继续工作？`)) {
                // Restore state
                originalFileName = draft.original_filename;
                currentProfessional = draft.professional || '全部';
                currentDocumentId = draft.document_id || '';
                currentWorkSessionId = draft.work_session_id || '';
                items = JSON.parse(draft.items_json);
                // V015-M2：草稿恢复经服务端注释，旧确认无凭据者降为待复核
                await annotateRestoredItems();

                document.getElementById('welcome-slate').style.display = 'none';
                document.getElementById('workbench').style.display = 'flex';
                document.getElementById('export-btn').disabled = false;
                
                const saveTrigger = document.getElementById('save-trigger');
                if (saveTrigger) saveTrigger.style.display = 'inline-flex';
                
                const endTaskTrigger = document.getElementById('end-task-trigger');
                if (endTaskTrigger) endTaskTrigger.style.display = 'inline-flex';
                
                const uploadedFilenameEl = document.getElementById('uploaded-filename');
                if (uploadedFilenameEl) {
                    uploadedFilenameEl.innerText = ` - ${draft.original_filename}`;
                    uploadedFilenameEl.title = draft.original_filename;
                }

                startAutoSave();
                
                // Recover professionals list
                const profSet = new Set(items.map(it => it.professional).filter(Boolean));
                professionals = ['全部', ...Array.from(profSet)];
                
                renderProfessionalTabs();
                currentPage = 1;
                renderTable();
                updateProgress();
            }
        }
    } catch (err) {
        console.error("Error checking for draft:", err);
    }
}

async function saveNamedSession(name) {
    if (items.length === 0) return;
    try {
        showLoading("正在保存工作进度...", "正在备份当前清单匹配工作状态，请稍候...");
        await Promise.all(items.map(persistReviewEdit));
        const payload = {
            session_name: name,
            original_filename: originalFileName,
            professional: currentProfessional || '全部',
            items_json: JSON.stringify(items),
            document_id: currentDocumentId || '',
            work_session_id: currentWorkSessionId || ''
        };
        
        let res;
        if (activeSessionId) {
            // Update existing named session
            res = await fetch(`${backendUrl}/api/session/${activeSessionId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    items_json: payload.items_json,
                    document_id: payload.document_id,
                    work_session_id: payload.work_session_id
                })
            });
        } else {
            // Create a new named session
            res = await fetch(`${backendUrl}/api/session/save`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        }
        
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "保存进度请求失败");
        
        if (data.session_id) {
            activeSessionId = data.session_id;
        }
        
        alert("进度保存成功！您可以在【历史记录】中随时重新加载或管理该记录。");
        document.getElementById('save-session-modal').style.display = 'none';
    } catch (err) {
        alert(`保存失败: ${err.message}`);
    } finally {
        hideLoading();
    }
}

async function fetchAndRenderHistorySessions() {
    const tbody = document.getElementById('history-sessions-tbody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--color-text-muted);">正在加载历史记录...</td></tr>';
    
    try {
        const res = await fetch(`${backendUrl}/api/session/list`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "加载历史列表失败");
        
        const sessions = data.sessions || [];
        if (sessions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--color-text-muted);">暂无在线保存记录</td></tr>';
            return;
        }
        
        tbody.innerHTML = '';
        sessions.forEach(sess => {
            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid var(--border-color)';
            tr.innerHTML = `
                <td style="padding: 10px;"><strong>${escapeHtml(sess.session_name)}</strong></td>
                <td style="padding: 10px; color: var(--color-text-secondary);">${escapeHtml(sess.original_filename)}</td>
                <td style="padding: 10px; color: var(--color-text-muted); font-size: 12px;">${escapeHtml(sess.updated_at)}</td>
                <td style="padding: 10px;">
                    <button class="btn btn-primary btn-restore-sess" style="padding: 4px 8px; font-size: 11px; margin-right: 4px;" data-id="${escapeHtml(sess.id)}">恢复</button>
                    <button class="btn btn-danger btn-delete-sess" style="padding: 4px 8px; font-size: 11px; background-color: var(--color-danger); border-color: var(--color-danger);" data-id="${escapeHtml(sess.id)}" data-name="${escapeHtml(sess.session_name)}">删除</button>
                </td>
            `;
            tr.querySelector('.btn-restore-sess').addEventListener('click', () => loadSession(sess.id));
            tr.querySelector('.btn-delete-sess').addEventListener('click', () => deleteSession(sess.id, sess.session_name));
            tbody.appendChild(tr);
        });
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--color-danger);">${escapeHtml(err.message)}</td></tr>`;
    }
}

async function loadSession(sessionId) {
    if (items.length > 0 && !confirm("恢复此保存进度将覆盖您当前的工作台，确定要继续吗？")) {
        return;
    }
    
    try {
        showLoading("正在恢复工作进度...", "正在从云端载入项目匹配数据并还原，请稍候...");
        const res = await fetch(`${backendUrl}/api/session/${sessionId}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "恢复失败");
        
        const session = data.session;
        originalFileName = session.original_filename;
        currentProfessional = session.professional || '全部';
        currentDocumentId = session.document_id || '';
        currentWorkSessionId = session.work_session_id || '';
        items = JSON.parse(session.items_json);
        // V015-M2：命名进度恢复经服务端注释，旧确认无凭据者降为待复核
        await annotateRestoredItems();
        
        // Render view
        document.getElementById('welcome-slate').style.display = 'none';
        document.getElementById('workbench').style.display = 'flex';
        document.getElementById('export-btn').disabled = false;
        
        const saveTrigger = document.getElementById('save-trigger');
        if (saveTrigger) saveTrigger.style.display = 'inline-flex';
        
        const endTaskTrigger = document.getElementById('end-task-trigger');
        if (endTaskTrigger) endTaskTrigger.style.display = 'inline-flex';
        
        startAutoSave();
        
        const uploadedFilenameEl = document.getElementById('uploaded-filename');
        if (uploadedFilenameEl) {
            uploadedFilenameEl.innerText = ` - ${session.original_filename}`;
            uploadedFilenameEl.title = session.original_filename;
        }
        
        // Recover professionals list
        const profSet = new Set(items.map(it => it.professional).filter(Boolean));
        professionals = ['全部', ...Array.from(profSet)];
        
        renderProfessionalTabs();
        selectedItem = null;
        document.getElementById('select-prompt').style.display = 'flex';
        document.getElementById('detail-editor').style.display = 'none';
        
        currentPage = 1;
        renderTable();
        updateProgress();
        
        activeSessionId = sessionId;
        document.getElementById('history-modal').style.display = 'none';
        alert("进度已成功恢复！");
    } catch (err) {
        alert(`加载失败: ${err.message}`);
    } finally {
        hideLoading();
    }
}

async function deleteSession(sessionId, name) {
    if (!confirm(`确定要彻底删除保存的进度 "${name}" 吗？此操作不可逆。`)) return;
    
    try {
        showLoading("正在删除...", "正在清理服务器上的保存记录...");
        const res = await fetch(`${backendUrl}/api/session/${sessionId}`, {
            method: 'DELETE'
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "删除失败");
        
        if (activeSessionId === sessionId) {
            activeSessionId = null;
        }
        
        alert("删除成功。");
        await fetchAndRenderHistorySessions();
    } catch (err) {
        alert(`删除失败: ${err.message}`);
    } finally {
        hideLoading();
    }
}

// Safe navigation alert on leave
window.addEventListener('beforeunload', (e) => {
    if (items.length > 0) {
        const msg = '您有未保存的清单匹配工作，刷新或离开可能导致最新的编辑内容丢失，确定离开吗？';
        e.preventDefault();
        e.returnValue = msg;
        return msg;
    }
});


// ==========================================
// V0.0.8 LLM Rule Engine Frontend Module
// ==========================================

let currentDraftRuleId = null;

function setupLlmRulesModule() {
    const tabStudio = document.getElementById('tab-rule-studio');
    const tabAudit = document.getElementById('tab-rule-audit');
    const tabLeaderboard = document.getElementById('tab-rule-leaderboard');

    const panelStudio = document.getElementById('rule-studio-panel');
    const panelAudit = document.getElementById('rule-audit-panel');
    const panelLeaderboard = document.getElementById('rule-leaderboard-panel');

    if (tabStudio && tabAudit && tabLeaderboard) {
        tabStudio.addEventListener('click', () => {
            tabStudio.style.background = 'var(--color-primary)';
            tabStudio.style.color = 'white';
            tabAudit.style.background = 'transparent';
            tabAudit.style.color = 'var(--color-text-secondary)';
            tabLeaderboard.style.background = 'transparent';
            tabLeaderboard.style.color = 'var(--color-text-secondary)';

            panelStudio.style.display = 'grid';
            panelAudit.style.display = 'none';
            panelLeaderboard.style.display = 'none';
        });

        tabAudit.addEventListener('click', () => {
            tabAudit.style.background = 'var(--color-primary)';
            tabAudit.style.color = 'white';
            tabStudio.style.background = 'transparent';
            tabStudio.style.color = 'var(--color-text-secondary)';
            tabLeaderboard.style.background = 'transparent';
            tabLeaderboard.style.color = 'var(--color-text-secondary)';

            panelStudio.style.display = 'none';
            panelAudit.style.display = 'flex';
            panelLeaderboard.style.display = 'none';

            fetchAndRenderPendingRules();
        });

        tabLeaderboard.addEventListener('click', () => {
            tabLeaderboard.style.background = 'var(--color-primary)';
            tabLeaderboard.style.color = 'white';
            tabStudio.style.background = 'transparent';
            tabStudio.style.color = 'var(--color-text-secondary)';
            tabAudit.style.background = 'transparent';
            tabAudit.style.color = 'var(--color-text-secondary)';

            panelStudio.style.display = 'none';
            panelAudit.style.display = 'none';
            panelLeaderboard.style.display = 'flex';

            loadRuleStats();
            fetchAndRenderActiveRules();
        });
    }

    const btnGenerate = document.getElementById('btn-generate-rule');
    if (btnGenerate) {
        btnGenerate.addEventListener('click', handleGenerateRule);
    }

    const btnTest = document.getElementById('btn-test-rule');
    if (btnTest) {
        btnTest.addEventListener('click', handleTestRule);
    }

    const btnSave = document.getElementById('btn-save-draft');
    if (btnSave) {
        btnSave.addEventListener('click', handleSaveDraft);
    }

    const btnSubmit = document.getElementById('btn-submit-audit');
    if (btnSubmit) {
        btnSubmit.addEventListener('click', handleSubmitAudit);
    }

    const btnRefresh = document.getElementById('refresh-rules-btn');
    if (btnRefresh) {
        btnRefresh.addEventListener('click', () => {
            loadRuleStats();
            fetchAndRenderPendingRules();
            fetchAndRenderActiveRules();
        });
    }

    const activeRuleRefreshBtn = document.getElementById('active-rule-refresh-btn');
    if (activeRuleRefreshBtn) {
        activeRuleRefreshBtn.addEventListener('click', fetchAndRenderActiveRules);
    }

    const activeRuleSearch = document.getElementById('active-rule-search');
    if (activeRuleSearch) {
        activeRuleSearch.addEventListener('input', () => {
            renderActiveRulesTable();
        });
    }

    const activeRuleFilter = document.getElementById('active-rule-status-filter');
    if (activeRuleFilter) {
        activeRuleFilter.addEventListener('change', () => {
            renderActiveRulesTable();
        });
    }
}

async function handleGenerateRule() {
    const scope_level = document.getElementById('rule-scope-level').value;
    const scope_target = document.getElementById('rule-scope-target').value.trim();
    const description = document.getElementById('rule-prompt-desc').value.trim();
    const sample_text = document.getElementById('rule-sample-text').value.trim();

    if (!description && !sample_text) {
        alert('请在上方输入原始清单描述或填写自然语言规则需求！');
        return;
    }

    const finalDescription = description || `请针对样本清单描述【${sample_text}】，自动分析提取核心设备名称与型号、提取关键规格属性、过滤施工杂质并建立标准匹配规则。`;

    const statusBadge = document.getElementById('rule-compiler-status');
    if (statusBadge) {
        statusBadge.innerText = '正在调用大模型...';
        statusBadge.style.background = 'var(--color-primary-glow)';
        statusBadge.style.color = 'var(--color-primary)';
    }

    showLoading("大模型智能编译中...", "系统正在通过优先容灾多模型路由器解析自然语言并生成 DSL 规则...");

    try {
        const res = await fetch(`${backendUrl}/api/llm-rules/generate`, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({
                scope_level,
                scope_target,
                discipline: scope_level === 'discipline' ? scope_target : '',
                description: finalDescription,
                sample_text
            })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.error || '规则编译生成失败');

        const dsl = data.dsl_json || {};
        document.getElementById('rule-name-input').value = dsl.rule_name || `${scope_target || '通用'}清洗比对规则`;
        document.getElementById('rule-json-editor').value = JSON.stringify(dsl, null, 2);

        currentDraftRuleId = `RULE-${Date.now()}`;

        if (statusBadge) {
            statusBadge.innerText = `编译成功 (${data.provider_name || 'LLM'}, ${data.latency_ms || 0}ms)`;
            statusBadge.style.background = 'var(--color-success-bg)';
            statusBadge.style.color = 'var(--color-success)';
        }

        if (sample_text) {
            handleTestRule();
        }
    } catch (err) {
        alert(`编译失败: ${err.message}`);
        if (statusBadge) {
            statusBadge.innerText = '编译失败';
            statusBadge.style.background = 'var(--color-danger-bg)';
            statusBadge.style.color = 'var(--color-danger)';
        }
    } finally {
        hideLoading();
    }
}

async function handleTestRule() {
    const rawJson = document.getElementById('rule-json-editor').value.trim();
    const sample_text = document.getElementById('rule-sample-text').value.trim();

    if (!rawJson) {
        alert('没有可运行的 Rule DSL JSON！');
        return;
    }
    if (!sample_text) {
        alert('请输入测试样本原文！');
        return;
    }

    let parsed_dsl;
    try {
        parsed_dsl = JSON.parse(rawJson);
    } catch (e) {
        alert(`JSON 语法错误: ${e.message}`);
        return;
    }

    try {
        const res = await fetch(`${backendUrl}/api/llm-rules/test`, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({
                parsed_dsl,
                sample_text
            })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '测试失败');

        const box = document.getElementById('rule-test-output-box');
        const content = document.getElementById('rule-test-output-content');
        if (box && content) {
            box.style.display = 'block';
            content.innerHTML = `
                <div><strong>清洗前：</strong> <code>${escapeHtml(data.original_text)}</code></div>
                <div><strong>清洗后：</strong> <code style="color: var(--color-info);">${escapeHtml(data.cleaned_text)}</code></div>
                <div><strong>提取属性：</strong> <code style="color: var(--color-warning);">${escapeHtml(JSON.stringify(data.extracted_params))}</code></div>
                <div><strong>清洗规则应用：</strong> ${escapeHtml(data.cleaning_logs.length ? data.cleaning_logs.join('; ') : '无修改')}</div>
            `;
        }
    } catch (err) {
        alert(`测试运行失败: ${err.message}`);
    }
}

async function handleSaveDraft() {
    const rawJson = document.getElementById('rule-json-editor').value.trim();
    const rule_name = document.getElementById('rule-name-input').value.trim();
    const scope_level = document.getElementById('rule-scope-level').value;
    const scope_target = document.getElementById('rule-scope-target').value;
    const description = document.getElementById('rule-prompt-desc').value.trim();

    if (!rawJson || !rule_name) {
        alert('规则名称和规则 JSON 不能为空！');
        return;
    }

    let parsed_dsl;
    try {
        parsed_dsl = JSON.parse(rawJson);
    } catch (e) {
        alert('规则 JSON 格式错误');
        return;
    }

    try {
        const res = await fetch(`${backendUrl}/api/llm-rules`, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({
                rule_id: currentDraftRuleId,
                rule_name,
                scope_level,
                scope_target,
                discipline: scope_level === 'discipline' ? scope_target : '',
                priority: 10,
                description,
                parsed_dsl
            })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '保存草稿失败');
        currentDraftRuleId = data.rule_id;
        alert('草稿保存成功！');
    } catch (err) {
        alert(`保存失败: ${err.message}`);
    }
}

async function handleSubmitAudit() {
    if (!currentDraftRuleId) {
        await handleSaveDraft();
        if (!currentDraftRuleId) return;
    }

    try {
        const res = await fetch(`${backendUrl}/api/llm-rules/${currentDraftRuleId}/submit`, {
            method: 'POST',
            headers: getAuthHeaders()
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.error || '提交审核失败');
        alert('规则已成功提交给上级经理/总监审核！');
        loadRuleStats();
    } catch (err) {
        alert(`提交失败: ${err.message}`);
    }
}

let cachedActiveRules = [];

async function fetchAndRenderActiveRules() {
    const tbody = document.getElementById('active-rules-tbody');
    if (!tbody) return;

    try {
        const [resActive, resApprovedPending] = await Promise.all([
            fetch(`${backendUrl}/api/llm-rules?status=all_active`, { headers: getAuthHeaders() }),
            fetch(`${backendUrl}/api/llm-rules?status=approved_pending_publish`, { headers: getAuthHeaders() })
        ]);
        if (!resActive.ok) return;

        const listActive = await resActive.json();
        const listApprovedPending = resApprovedPending.ok ? await resApprovedPending.json() : [];
        cachedActiveRules = [...listApprovedPending, ...listActive];
        renderActiveRulesTable();
    } catch (e) {
        console.error('Failed to fetch active rules:', e);
    }
}

function renderActiveRulesTable() {
    const tbody = document.getElementById('active-rules-tbody');
    if (!tbody) return;

    const query = (document.getElementById('active-rule-search')?.value || '').toLowerCase().trim();
    const filter = document.getElementById('active-rule-status-filter')?.value || 'all';

    let filtered = cachedActiveRules.filter(r => {
        if (filter !== 'all' && r.status !== filter) return false;
        if (!query) return true;
        const matchName = (r.rule_name || '').toLowerCase().includes(query);
        const matchId = (r.rule_id || '').toLowerCase().includes(query);
        const matchTarget = (r.scope_target || '').toLowerCase().includes(query);
        const matchAuthor = (r.author_name || '').toLowerCase().includes(query);
        return matchName || matchId || matchTarget || matchAuthor;
    });

    tbody.innerHTML = '';

    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--color-text-muted); padding: 24px;">暂无匹配的规则。可在“规则生成与调试 Studio”中生成并提交新规则。</td></tr>';
        return;
    }

    filtered.forEach(r => {
        const tr = document.createElement('tr');
        const isPublished = r.status === 'published';
        const isPendingPublish = r.status === 'approved_pending_publish';
        let statusBadge = '<span style="display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 4px; background: var(--bg-subtle); color: var(--color-text-muted); font-size: 11px; font-weight: 600;"><i class="fa-solid fa-circle-pause"></i> 已停用</span>';
        if (isPublished) {
            statusBadge = '<span style="display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 4px; background: var(--color-success-bg); color: var(--color-success); font-size: 11px; font-weight: 600;"><i class="fa-solid fa-circle-check"></i> 生效运行中</span>';
        } else if (isPendingPublish) {
            statusBadge = '<span style="display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 4px; background: rgba(245,158,11,0.16); color: #f59e0b; font-size: 11px; font-weight: 600;"><i class="fa-solid fa-clock"></i> 已审待发布</span>';
        }

        const primaryActionBtn = isPendingPublish
            ? `<button class="btn btn-success btn-rule-publish" style="padding: 3px 8px; font-size: 11px; height: 26px;" title="仅管理员可发布生效"><i class="fa-solid fa-rocket"></i> 发布生效</button>`
            : `<button class="btn ${isPublished ? 'btn-warning' : 'btn-success'} btn-rule-toggle" style="padding: 3px 8px; font-size: 11px; height: 26px; ${isPublished ? 'background-color: var(--color-warning); border-color: var(--color-warning); color: white;' : ''}" title="${isPublished ? '停用此规则' : '启用此规则'}">${isPublished ? '<i class="fa-solid fa-pause"></i> 停用' : '<i class="fa-solid fa-play"></i> 启用'}</button>`;

        tr.innerHTML = `
            <td><code>${escapeHtml(r.rule_id)}</code></td>
            <td><strong style="color: var(--color-text-primary);">${escapeHtml(r.rule_name)}</strong></td>
            <td><span style="font-size: 12px; color: var(--color-accent-text);">[${escapeHtml(r.scope_level)}]</span> ${escapeHtml(r.scope_target || '全域通用')}</td>
            <td><span style="font-size: 12px; color: var(--color-chart-ram);">${escapeHtml(r.discipline || '通用')}</span></td>
            <td style="text-align: center;">${statusBadge}</td>
            <td style="text-align: right;"><strong style="color: var(--color-success); font-size: 14px;">${escapeHtml(r.hit_count || 0)}</strong> 次</td>
            <td><span style="font-size: 12px;">${escapeHtml(r.author_name || '-')}</span> <span style="color: var(--color-text-muted); font-size: 11px;">/ ${escapeHtml(r.approver_name || '-')}</span></td>
            <td style="font-size: 12px; color: var(--color-text-secondary);">${escapeHtml(r.published_at || r.approved_at || r.updated_at || '-')}</td>
            <td style="text-align: center;">
                <div style="display: inline-flex; gap: 6px; align-items: center; justify-content: center;">
                    <button class="btn btn-secondary btn-rule-edit" style="padding: 3px 8px; font-size: 11px; height: 26px;" title="修改规则配置"><i class="fa-solid fa-pen-to-square"></i> 修改</button>
                    <button class="btn btn-secondary btn-rule-eval" style="padding: 3px 8px; font-size: 11px; height: 26px;" title="运行沙箱回归评测">🧪 评测</button>
                    ${primaryActionBtn}
                    <button class="btn btn-danger btn-rule-del" style="padding: 3px 8px; font-size: 11px; height: 26px; background: var(--color-danger); border-color: var(--color-danger); color: white;" title="删除未发布草稿"><i class="fa-solid fa-trash"></i> 删除</button>
                    <button class="btn btn-rule-dsl" style="padding: 3px 8px; font-size: 11px; height: 26px; background: var(--bg-subtle); border-color: var(--border-color); color: var(--color-info);" title="查看DSL代码"><i class="fa-solid fa-code"></i> DSL</button>
                </div>
            </td>
        `;
        tr.querySelector('.btn-rule-edit').addEventListener('click', () => openRuleEditModal(r.rule_id));
        tr.querySelector('.btn-rule-eval').addEventListener('click', () => startLlmRuleEval(r));
        const pubBtn = tr.querySelector('.btn-rule-publish');
        if (pubBtn) {
            pubBtn.addEventListener('click', () => publishActiveRule(r.rule_id, r.revision, r.content_digest, r.last_eval_id));
        }
        const togBtn = tr.querySelector('.btn-rule-toggle');
        if (togBtn) {
            togBtn.addEventListener('click', () => toggleActiveRule(r.rule_id, r.status, r.last_eval_id));
        }
        tr.querySelector('.btn-rule-del').addEventListener('click', () => deleteActiveRule(r.rule_id));
        tr.querySelector('.btn-rule-dsl').addEventListener('click', () => viewActiveRuleDsl(r.rule_id));
        tbody.appendChild(tr);
    });
}

let editingRuleId = null;

async function openRuleEditModal(ruleId) {
    editingRuleId = ruleId;
    document.getElementById('modal-rule-id-lbl').innerText = ruleId;

    try {
        const res = await fetch(`${backendUrl}/api/llm-rules/${ruleId}`, {
            headers: getAuthHeaders()
        });
        if (!res.ok) throw new Error('无法获取规则详情');

        const r = await res.json();
        document.getElementById('modal-rule-name').value = r.rule_name || '';
        document.getElementById('modal-rule-priority').value = r.priority || 10;
        document.getElementById('modal-rule-scope-level').value = r.scope_level || 'category';
        document.getElementById('modal-rule-scope-target').value = r.scope_target || '';
        document.getElementById('modal-rule-discipline').value = r.discipline || '';
        document.getElementById('modal-rule-desc').value = r.description || '';
        document.getElementById('modal-rule-json').value = JSON.stringify(r.parsed_dsl || {}, null, 2);

        const modal = document.getElementById('rule-edit-modal');
        if (modal) modal.style.display = 'flex';
    } catch (err) {
        alert(`打开编辑窗口失败: ${err.message}`);
    }
}

function closeRuleEditModal() {
    const modal = document.getElementById('rule-edit-modal');
    if (modal) modal.style.display = 'none';
    editingRuleId = null;
}

async function submitModalRuleUpdate() {
    if (!editingRuleId) return;

    const rule_name = document.getElementById('modal-rule-name').value.trim();
    const priority = parseInt(document.getElementById('modal-rule-priority').value) || 10;
    const scope_level = document.getElementById('modal-rule-scope-level').value;
    const scope_target = document.getElementById('modal-rule-scope-target').value.trim();
    const discipline = document.getElementById('modal-rule-discipline').value.trim();
    const description = document.getElementById('modal-rule-desc').value.trim();
    const rawJson = document.getElementById('modal-rule-json').value.trim();

    if (!rule_name) {
        alert('规则名称不能为空！');
        return;
    }

    let parsed_dsl;
    try {
        parsed_dsl = JSON.parse(rawJson);
    } catch (e) {
        alert(`Rule DSL JSON 语法错误: ${e.message}`);
        return;
    }

    try {
        const res = await fetch(`${backendUrl}/api/llm-rules/${editingRuleId}`, {
            method: 'PUT',
            headers: getAuthHeaders(),
            body: JSON.stringify({
                rule_name,
                priority,
                scope_level,
                scope_target,
                discipline,
                description,
                parsed_dsl
            })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.error || '保存规则修改失败');

        alert(data.detail || `规则 [${editingRuleId}] 修改已保存为草稿，需重新提交审核并由管理员发布后生效！`);
        closeRuleEditModal();
        fetchAndRenderActiveRules();
        loadRuleStats();
    } catch (err) {
        alert(`修改保存失败: ${err.message}`);
    }
}

window._llmEvalCache = window._llmEvalCache || {};

function _formatRuleError(data, fallback) {
    if (!data) return fallback;
    const d = data.detail;
    if (d && typeof d === 'object') return d.message || d.code || JSON.stringify(d);
    return d || data.error || fallback;
}

async function startLlmRuleEval(ruleObj) {
    const ruleId = ruleObj.rule_id;
    try {
        const res = await fetch(`${backendUrl}/api/llm-rules/${ruleId}/eval`, {
            method: 'POST',
            headers: getAuthHeaders()
        });
        const data = await res.json();
        if (!res.ok) throw new Error(_formatRuleError(data, '启动沙箱评测失败'));
        const evalId = data.eval_id || data.task_id;
        for (let attempt = 0; attempt < 90; attempt++) {
            await new Promise(r => setTimeout(r, 1000));
            const stRes = await fetch(`${backendUrl}/api/llm-rules/eval/${evalId}`, { headers: getAuthHeaders() });
            if (!stRes.ok) continue;
            const st = await stRes.json();
            if (st.status === 'completed' || st.status === 'failed') {
                window._llmEvalCache[ruleId] = {
                    eval_id: evalId,
                    benefit: st.benefit || 'invalid',
                    safety_passed: Boolean(st.safety_passed ?? st.passed),
                    revision: ruleObj.revision,
                    content_digest: ruleObj.content_digest
                };
                alert(`规则 [${ruleId}] 沙箱评测完成 (${evalId})\n结论: safety=${Boolean(st.safety_passed ?? st.passed)}, benefit=${st.benefit || 'invalid'}`);
                return;
            }
        }
        alert(`规则 [${ruleId}] 评测超时，请稍后查看。`);
    } catch (err) {
        alert(`沙箱评测失败: ${err.message}`);
    }
}

async function publishActiveRule(ruleId, expectedRevision, expectedDigest, dbEvalId = null) {
    const cached = window._llmEvalCache[ruleId];
    const evalId = (cached && cached.revision === expectedRevision && cached.eval_id) || dbEvalId || null;
    let neutralReason = null;
    if (cached && cached.benefit === 'neutral') {
        neutralReason = prompt('该规则沙箱评测收益为 neutral（无改善），确需发布请填写审批理由:');
        if (neutralReason === null) return;
    }
    try {
        const payload = {
            expected_revision: expectedRevision ?? null,
            expected_digest: expectedDigest ?? null,
            eval_id: evalId
        };
        if (neutralReason !== null) payload.neutral_reason = neutralReason;
        const res = await fetch(`${backendUrl}/api/llm-rules/${ruleId}/publish`, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok) throw new Error(_formatRuleError(data, '发布失败'));
        alert(data.detail || '规则发布生效成功！');
        fetchAndRenderActiveRules();
        loadRuleStats();
    } catch (err) {
        alert(`发布失败: ${err.message}`);
    }
}

async function toggleActiveRule(ruleId, currentStatus = '', dbEvalId = null) {
    const cached = window._llmEvalCache[ruleId];
    const evalId = (cached && cached.eval_id) || dbEvalId || null;
    let neutralReason = null;
    if (currentStatus === 'disabled' && cached && cached.benefit === 'neutral') {
        neutralReason = prompt('该停用规则沙箱评测收益为 neutral，确需重新启用请填写审批理由:');
        if (neutralReason === null) return;
    }
    try {
        const res = await fetch(`${backendUrl}/api/llm-rules/${ruleId}/toggle`, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({
                eval_id: evalId,
                neutral_reason: neutralReason
            })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(_formatRuleError(data, '状态切换失败'));

        alert(data.detail || '操作成功！');
        fetchAndRenderActiveRules();
        loadRuleStats();
    } catch (err) {
        alert(`操作失败: ${err.message}`);
    }
}

async function deleteActiveRule(ruleId) {
    if (!confirm(`⚠️ 确定要彻底删除规则 [${ruleId}] 吗？\n\n删除后将无法恢复，且该规则将立即停止在所有物料匹配任务中生效。`)) {
        return;
    }

    try {
        const res = await fetch(`${backendUrl}/api/llm-rules/${ruleId}`, {
            method: 'DELETE',
            headers: getAuthHeaders()
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.error || '删除失败');

        alert(`规则 [${ruleId}] 已成功删除！`);
        fetchAndRenderActiveRules();
        loadRuleStats();
    } catch (err) {
        alert(`删除失败: ${err.message}`);
    }
}

async function viewActiveRuleDsl(ruleId) {
    try {
        const res = await fetch(`${backendUrl}/api/llm-rules/${ruleId}`, {
            headers: getAuthHeaders()
        });
        if (!res.ok) throw new Error('无法获取规则详情');

        const r = await res.json();
        document.getElementById('view-modal-rule-id').innerText = `${ruleId} - ${r.rule_name}`;
        document.getElementById('view-modal-rule-json').value = JSON.stringify(r.parsed_dsl || {}, null, 2);

        const modal = document.getElementById('rule-view-modal');
        if (modal) modal.style.display = 'flex';
    } catch (err) {
        alert(`查看DSL失败: ${err.message}`);
    }
}

function closeRuleViewModal() {
    const modal = document.getElementById('rule-view-modal');
    if (modal) modal.style.display = 'none';
}

async function fetchAndRenderPendingRules() {
    const tbody = document.getElementById('pending-rules-tbody');
    if (!tbody) return;

    try {
        const res = await fetch(`${backendUrl}/api/llm-rules?status=pending_approval`, {
            headers: getAuthHeaders()
        });
        if (!res.ok) return;

        const rules = await res.json();
        tbody.innerHTML = '';

        const badge = document.getElementById('audit-pending-badge');
        if (badge) badge.innerText = rules.length;

        if (rules.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--color-text-muted); padding: 20px;">暂无待审核的规则。</td></tr>';
            return;
        }

        rules.forEach(r => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><code>${escapeHtml(r.rule_id)}</code></td>
                <td><strong>${escapeHtml(r.rule_name)}</strong></td>
                <td>${escapeHtml(r.scope_target || r.scope_level)}</td>
                <td>${escapeHtml(r.author_name)}</td>
                <td>${escapeHtml(r.created_at || '-')}</td>
                <td style="text-align: center;">
                    <button class="btn btn-success btn-audit-approve" style="padding: 4px 8px; font-size: 11px;"><i class="fa-solid fa-check"></i> 审核同意(待发布)</button>
                    <button class="btn btn-danger btn-audit-reject" style="padding: 4px 8px; font-size: 11px; background: var(--color-danger); border-color: var(--color-danger); color: white;"><i class="fa-solid fa-xmark"></i> 驳回</button>
                    <button class="btn btn-secondary btn-audit-withdraw" style="padding: 4px 8px; font-size: 11px;"><i class="fa-solid fa-rotate-left"></i> 撤回</button>
                </td>
            `;
            tr.querySelector('.btn-audit-approve').addEventListener('click', () => auditRule(r.rule_id, 'approve', r.revision, r.content_digest));
            tr.querySelector('.btn-audit-reject').addEventListener('click', () => auditRule(r.rule_id, 'reject', r.revision, r.content_digest));
            tr.querySelector('.btn-audit-withdraw').addEventListener('click', () => withdrawPendingRule(r.rule_id));
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error('Failed to fetch pending rules:', e);
    }
}

async function withdrawPendingRule(ruleId) {
    try {
        const res = await fetch(`${backendUrl}/api/llm-rules/${ruleId}/withdraw`, {
            method: 'POST',
            headers: getAuthHeaders()
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.error || '撤回失败');
        alert(data.detail || '已撤回为草稿');
        fetchAndRenderPendingRules();
        fetchAndRenderActiveRules();
        loadRuleStats();
    } catch (err) {
        alert(`撤回失败: ${err.message}`);
    }
}

async function auditRule(ruleId, action, expectedRevision, expectedDigest) {
    const comment = prompt(action === 'approve' ? '请输入审核说明（同意后需管理员发布生效）：' : '请输入驳回原因：');
    if (action === 'reject' && comment === null) return;

    try {
        const res = await fetch(`${backendUrl}/api/llm-rules/${ruleId}/audit`, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({
                action,
                comment: comment || '',
                expected_revision: expectedRevision ?? null,
                expected_digest: expectedDigest ?? null
            })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.error || '审核操作失败');
        alert(data.detail);
        fetchAndRenderPendingRules();
        fetchAndRenderActiveRules();
        loadRuleStats();
    } catch (err) {
        alert(`操作失败: ${err.message}`);
    }
}

async function loadRuleStats() {
    try {
        const res = await fetch(`${backendUrl}/api/llm-rules/stats`, {
            headers: getAuthHeaders()
        });
        if (!res.ok) return;

        const data = await res.json();
        if (document.getElementById('stat-approved-count')) {
            document.getElementById('stat-approved-count').innerText = data.approved_count || 0;
        }
        if (document.getElementById('stat-pending-count')) {
            document.getElementById('stat-pending-count').innerText = data.pending_count || 0;
        }
        if (document.getElementById('stat-total-hits')) {
            document.getElementById('stat-total-hits').innerText = data.total_hits || 0;
        }

        // Also count disabled
        if (document.getElementById('stat-disabled-count')) {
            const disabledCount = cachedActiveRules.filter(r => r.status === 'disabled').length;
            document.getElementById('stat-disabled-count').innerText = disabledCount;
        }

        const tbody = document.getElementById('leaderboard-tbody');
        if (tbody) {
            tbody.innerHTML = '';
            if (!data.top_rules || data.top_rules.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--color-text-muted); padding: 20px;">暂无命中记录。</td></tr>';
                return;
            }

            data.top_rules.forEach((r, index) => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><strong style="color: var(--color-warning);">#${index + 1}</strong></td>
                    <td><code>${escapeHtml(r.rule_id)}</code></td>
                    <td><strong>${escapeHtml(r.rule_name)}</strong></td>
                    <td>${escapeHtml(r.scope_target || '通用')}</td>
                    <td>${escapeHtml(r.author_name)}</td>
                    <td>${escapeHtml(r.approved_at || '-')}</td>
                    <td style="text-align: right;"><strong style="color: var(--color-success); font-size: 15px;">${escapeHtml(r.hit_count)}</strong> 次</td>
                `;
                tbody.appendChild(tr);
            });
        }
    } catch (e) {
        console.error('Failed to load rule stats:', e);
    }
}

// Call on startup
document.addEventListener('DOMContentLoaded', () => {
    setupLlmRulesModule();
});

