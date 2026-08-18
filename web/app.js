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
            alert("您的登录会话已失效，请重新登录。");
            localStorage.clear();
            window.location.href = "login.html";
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

document.addEventListener('DOMContentLoaded', () => {
    // Process User profile info: show Position (岗位) and Real Name (姓名)
    const username = localStorage.getItem('username') || '-';
    let displayName = localStorage.getItem('name') || (username === 'admin' ? '系统管理员' : username);
    let position = localStorage.getItem('position') || (username === 'admin' ? '系统管理员' : '');
    let role = localStorage.getItem('role') || 'employee';
    
    const usernameLbl = document.getElementById('username-lbl');
    const roleLbl = document.getElementById('user-role-lbl');
    const adminLink = document.getElementById('admin-panel-link');
    const logoutBtn = document.getElementById('logout-btn');
    
    function updateUserUI() {
        if (usernameLbl) usernameLbl.innerText = displayName;
        if (roleLbl) {
            let positionText = position;
            if (!positionText) {
                if (role === 'admin' || username === 'admin') positionText = '系统管理员';
                else if (role === 'director') positionText = '总监';
                else if (role === 'manager') positionText = '经理';
                else positionText = '员工';
            }
            roleLbl.innerText = positionText;
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
                if (userData.name) {
                    displayName = userData.name;
                    localStorage.setItem('name', userData.name);
                }
                if (userData.position) {
                    position = userData.position;
                    localStorage.setItem('position', userData.position);
                }
                if (userData.role) {
                    role = userData.role;
                    localStorage.setItem('role', userData.role);
                }
                updateUserUI();
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
    
    if (logoutBtn) {
        logoutBtn.addEventListener('click', () => {
            if (confirm('确认退出系统吗？')) {
                localStorage.clear();
                window.location.href = 'login.html';
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
            filterTabs.forEach(t => t.classList.remove('active'));
            const clickedTab = e.currentTarget;
            clickedTab.classList.add('active');
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
    document.getElementById('confirm-match-finished-btn').addEventListener('click', async () => {
        if (!selectedItem) return;
        
        if (!selectedItem.bindings || selectedItem.bindings.length === 0) {
            if (!confirm("当前清单行未绑定任何物料，确定要标记为已确认吗？")) {
                return;
            }
        }
        
        showLoading("正在保存绑定记录...", "正在记录当前绑定关系以更新统计推荐值，并自动流转至下一行，请稍候...");
        
        try {
            const response = await fetch(`${backendUrl}/api/binding/submit`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    original_name: selectedItem.original_name,
                    original_spec: selectedItem.original_spec,
                    original_qty: selectedItem.original_qty,
                    bindings: selectedItem.bindings || []
                })
            });
            
            if (!response.ok) throw new Error("提交绑定日志失败");
            
            selectedItem.is_exact = true;
            
            // Find next unconfirmed item for auto flow (Task 4)
            const curIdx = items.findIndex(it => it.original_row_idx === selectedItem.original_row_idx);
            let nextItem = null;
            for (let i = curIdx + 1; i < items.length; i++) {
                if (!items[i].is_exact) {
                    nextItem = items[i];
                    break;
                }
            }
            if (!nextItem) {
                for (let i = 0; i < curIdx; i++) {
                    if (!items[i].is_exact) {
                        nextItem = items[i];
                        break;
                    }
                }
            }
            
            renderTable();
            updateProgress();
            
            if (nextItem) {
                jumpToItem(nextItem);
            } else {
                selectRow(selectedItem);
            }
            
        } catch (e) {
            alert(e.message);
        } finally {
            hideLoading();
        }
    });

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
    let filtered = items.filter(it => {
        if (currentProfessional !== '全部' && it.professional !== currentProfessional) return false;
        if (currentFilter === 'exact') return it.is_exact;
        else if (currentFilter === 'fuzzy') return !it.is_exact && it.matched_code !== "";
        else if (currentFilter === 'unmatched') return it.matched_code === "";
        return true;
    });
    if (searchQuery) {
        filtered = filtered.filter(it => {
            return it.original_name.toLowerCase().includes(searchQuery) || 
                   it.original_spec.toLowerCase().includes(searchQuery) ||
                   it.matched_code.toLowerCase().includes(searchQuery);
        });
    }
    
    const idx = filtered.findIndex(it => it.original_row_idx === item.original_row_idx);
    if (idx !== -1) {
        currentPage = Math.floor(idx / pageSize) + 1;
    }
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
    items = [];
    originalFileName = '';
    activeSessionId = null;
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
                    processUploadResult(taskState.result, file.name);
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
function processUploadResult(resultItems, fileName) {
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
    
    // Cache initial matching codes
    items.forEach(item => {
        item.initial_auto_code = item.matched_code;
        if (!item.bindings) {
            item.bindings = [];
            if (item.matched_code) {
                item.bindings.push({
                    code: item.matched_code,
                    name: item.matched_name || "",
                    specification: item.matched_spec || "",
                    unit: item.matched_unit || "",
                    brand: item.matched_brand || "",
                    category: item.matched_category || "",
                    qty: item.original_qty
                });
            }
        }
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
    
    // Filter items
    let filtered = items.filter(item => {
        // Professional filter
        if (currentProfessional !== '全部' && item.professional !== currentProfessional) {
            return false;
        }
        
        // Status filter
        if (currentFilter === 'exact') {
            return item.is_exact;
        } else if (currentFilter === 'fuzzy') {
            return !item.is_exact && item.matched_code !== "";
        } else if (currentFilter === 'unmatched') {
            return item.matched_code === "";
        }
        return true; // 'all'
    });
    
    // Search query filter
    if (searchQuery) {
        filtered = filtered.filter(item => {
            return item.original_name.toLowerCase().includes(searchQuery) || 
                   item.original_spec.toLowerCase().includes(searchQuery) ||
                   item.matched_code.toLowerCase().includes(searchQuery);
        });
    }
    
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
        if (selectedItem && selectedItem.original_row_idx === item.original_row_idx) {
            tr.classList.add('selected');
        }
        
        let statusHtml = '';
        if (item.is_exact) {
            statusHtml = `<span class="row-status-pill exact"><i class="fa-solid fa-circle-check"></i> 精确</span>`;
        } else if (item.matched_code) {
            statusHtml = `<span class="row-status-pill fuzzy"><i class="fa-solid fa-circle-question"></i> 待确认</span>`;
        } else {
            statusHtml = `<span class="row-status-pill unmatched"><i class="fa-solid fa-triangle-exclamation"></i> 未匹配</span>`;
        }
        
        let codeDisplay = '-';
        if (item.bindings && item.bindings.length > 0) {
            if (item.bindings.length === 1) {
                codeDisplay = item.bindings[0].code;
            } else {
                codeDisplay = `${item.bindings[0].code} (+${item.bindings.length - 1})`;
            }
        } else if (item.matched_code) {
            codeDisplay = item.matched_code;
        }

        tr.innerHTML = `
            <td>${item.original_row_idx}</td>
            <td>
                <div style="font-weight: 500;">${item.original_name}</div>
                <div style="font-size: 11px; color: var(--color-text-muted); max-width: 320px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                    ${item.original_spec.replace(/\n/g, ' ')}
                </div>
            </td>
            <td><code class="row-code-lbl">${codeDisplay}</code></td>
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
    if (!selectedItem.bindings) {
        selectedItem.bindings = [];
        if (selectedItem.matched_code) {
            const codes = selectedItem.matched_code.split(",");
            if (codes.length === 1) {
                selectedItem.bindings.push({
                    code: selectedItem.matched_code,
                    name: selectedItem.matched_name || "",
                    specification: selectedItem.matched_spec || "",
                    unit: selectedItem.matched_unit || "",
                    brand: selectedItem.matched_brand || "",
                    category: selectedItem.matched_category || "",
                    qty: selectedItem.original_qty
                });
            } else {
                codes.forEach(c => {
                    selectedItem.bindings.push({
                        code: c.trim(),
                        name: "已关联物料",
                        specification: "",
                        unit: selectedItem.original_unit,
                        brand: "",
                        category: "",
                        qty: selectedItem.original_qty
                    });
                });
            }
        }
    }
    
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
                <span class="bound-item-name" title="${b.name}">${b.name}</span>
                <span class="bound-item-code">${b.code}</span>
            </div>
            <div class="bound-item-desc" title="规格: ${b.specification || '无'} | 品牌: ${b.brand || '无'}">
                ${b.specification || '无规格'}
            </div>
            <div class="bound-item-bottom">
                <div class="bound-item-qty-wrap">
                    <span>数量:</span>
                    <input type="number" class="bound-qty-input" value="${b.qty !== undefined ? b.qty : selectedItem.original_qty}" />
                    <span style="font-size: 11px; color: var(--color-text-muted);">${b.unit || selectedItem.original_unit || '个'}</span>
                </div>
                <button class="btn btn-delete-binding" style="padding: 2px 6px; height: 22px; color: var(--color-danger); border-color: transparent; background: transparent; font-size: 12px;" title="删除绑定">
                    <i class="fa-solid fa-trash-can"></i>
                </button>
            </div>
        `;
        
        const qtyInput = div.querySelector('.bound-qty-input');
        qtyInput.addEventListener('input', (e) => {
            const val = parseFloat(e.target.value);
            b.qty = isNaN(val) ? 0.0 : val;
        });
        
        const deleteBtn = div.querySelector('.btn-delete-binding');
        deleteBtn.addEventListener('click', () => {
            selectedItem.bindings.splice(idx, 1);
            selectedItem.matched_code = selectedItem.bindings.map(x => x.code).join(",");
            if (selectedItem.bindings.length === 0) {
                selectedItem.matched_code = "";
            }
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
                
                tagsHtml += `<span class="match-reason-tag ${tagClass}">${reason}</span>`;
            });
        }
        
        const boundBadge = isActive ? `<span style="font-size: 11px; font-weight: 700; color: var(--color-success); background: var(--color-success-bg); border: 1px solid var(--color-success-border); padding: 1px 6px; border-radius: 4px;"><i class="fa-solid fa-check"></i> 已绑定</span>` : '';
        
        card.innerHTML = `
            <div class="candidate-header">
                <div class="candidate-title">
                    <span class="cand-name" title="${cand.name}">${cand.name}</span>
                    <span class="cand-code">${cand.code}</span>
                </div>
                <div style="display: flex; align-items: center; gap: 6px;">
                    ${boundBadge}
                    <div class="match-percentage-badge">${cand.score}% 相似</div>
                </div>
            </div>
            <div class="candidate-spec" title="${cand.specification || '无规格描述'}">${cand.specification || '无规格描述'}</div>
            ${tagsHtml ? `<div style="margin: 2px 0 4px 0; display: flex; flex-wrap: wrap; gap: 4px;">${tagsHtml}</div>` : ''}
            <div class="candidate-meta">
                <span>品牌: <strong>${cand.brand || '未指定'}</strong></span>
                <span>品类: <strong>${cand.category || '-'}</strong></span>
                <span>单位: <strong>${cand.unit || '个'}</strong></span>
            </div>
        `;
        
        card.addEventListener('click', () => {
            applyMatchCandidate(cand);
        });
        
        container.appendChild(card);
    });
}

// 5. Apply selected candidate to matched item & submit feedback
async function applyMatchCandidate(cand) {
    if (!selectedItem) return;
    
    if (!selectedItem.bindings) {
        selectedItem.bindings = [];
    }
    
    // Check duplicates / Already bound: if so, directly unbind without prompt
    const existingIndex = selectedItem.bindings.findIndex(b => b.code === cand.code);
    if (existingIndex !== -1) {
        selectedItem.bindings.splice(existingIndex, 1);
        selectedItem.matched_code = selectedItem.bindings.map(x => x.code).join(",");
        if (selectedItem.bindings.length === 0) {
            selectedItem.matched_code = "";
        }
        renderBoundMaterials();
        renderCandidates();
        renderTable();
        updateProgress();
        return;
    }
    
    const wasCorrected = selectedItem.initial_auto_code !== cand.code;
    if (wasCorrected) {
        submitFeedbackSample(selectedItem.original_row_idx, selectedItem.original_spec, selectedItem.initial_auto_code, cand.code, false);
    } else {
        submitFeedbackSample(selectedItem.original_row_idx, selectedItem.original_spec, selectedItem.initial_auto_code, cand.code, true);
    }
    
    // Push new bound material
    selectedItem.bindings.push({
        code: cand.code,
        name: cand.name,
        specification: cand.specification || "",
        unit: cand.unit || selectedItem.original_unit || "个",
        brand: cand.brand || "",
        category: cand.category || "",
        qty: selectedItem.original_qty
    });
    
    // Update matched_code to be comma-separated list of bound codes
    selectedItem.matched_code = selectedItem.bindings.map(x => x.code).join(",");
    
    // Render incremental updates instead of selectRow to keep scroll position and search results
    renderBoundMaterials();
    renderCandidates();
    renderTable();
    updateProgress();
}

// Helper to submit feedback logs
async function submitFeedbackSample(rowId, text, autoCode, manualCode, isCorrect) {
    try {
        await fetch(`${backendUrl}/api/feedback/submit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                row_id: rowId,
                raw_text: text,
                auto_code: autoCode,
                manual_code: manualCode,
                is_correct: isCorrect
            })
        });
    } catch (e) {
        console.error("Failed to submit matching feedback log", e);
    }
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
                <td><code>${res.code}</code></td>
                <td><strong>${res.name}</strong></td>
                <td><div style="font-size: 11.5px; max-width: 250px; max-height: 80px; overflow-y: auto; white-space: pre-wrap;">${res.specification || ''}</div></td>
                <td>${res.unit || '个'}</td>
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
                });
            });
            
            tbody.appendChild(tr);
        });
    } catch (error) {
        tbody.innerHTML = `<tr><td colspan="5" class="empty-search" style="color: var(--color-danger);">搜索出错: ${error.message}</td></tr>`;
    }
}

// 7. Manual Parameters Supplement Rematch (V0.0.2)
async function runParamRematch() {
    if (!selectedItem) return;
    
    const model = document.getElementById('modal-param-model').value.trim();
    const volume = document.getElementById('modal-param-volume').value ? parseInt(document.getElementById('modal-param-volume').value) : null;
    const power = document.getElementById('modal-param-power').value ? parseFloat(document.getElementById('modal-param-power').value) : null;
    const dn = document.getElementById('modal-param-dn').value ? parseInt(document.getElementById('modal-param-dn').value) : null;
    
    document.getElementById('param-modal').style.display = 'none';
    showLoading("重新对齐特征参数...", "系统正在整合手动输入的指标并检索计算匹配得分...");
    
    try {
        const response = await fetch(`${backendUrl}/api/rematch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                row_id: selectedItem.original_row_idx,
                text: selectedItem.original_spec,
                professional: selectedItem.professional || "暖通",
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
        
        // Update current selectedItem matching options and candidate ranks
        selectedItem.is_exact = data.is_exact;
        selectedItem.matched_code = data.matched_code || "";
        selectedItem.top_candidates = data.top_candidates;
        
        if (data.top_candidates && data.top_candidates.length > 0) {
            const top = data.top_candidates[0];
            selectedItem.matched_name = top.name;
            selectedItem.matched_category = top.category || "";
            selectedItem.matched_spec = top.specification || "";
            selectedItem.matched_unit = top.unit || "";
            selectedItem.matched_brand = top.brand || "";
            
            if (data.is_exact) {
                selectedItem.bindings = [{
                    code: top.code,
                    name: top.name,
                    specification: top.specification || "",
                    unit: top.unit || selectedItem.original_unit || "个",
                    brand: top.brand || "",
                    category: top.category || "",
                    qty: selectedItem.original_qty
                }];
                selectedItem.matched_code = top.code;
            }
        }
        
        selectRow(selectedItem);
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
        chip.innerHTML = `<i class="fa-solid ${isActive ? 'fa-check' : 'fa-xmark'}"></i> 型号: ${model}`;
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
        chip.innerHTML = `<i class="fa-solid ${isActive ? 'fa-check' : 'fa-xmark'}"></i> ${k}: ${valStr}`;
        chip.addEventListener('click', () => {
            selectedItem.active_params[k] = !selectedItem.active_params[k];
            renderParamChips();
        });
        container.appendChild(chip);
    }
}

// 7.3 Rematch Single Item with Active Parameter Dimensions
async function rematchCurrentWithParams() {
    if (!selectedItem) return;
    
    // Construct customized parsed_spec
    const customizedSpec = {
        model: selectedItem.active_params['model'] ? selectedItem.parsed_spec.model : "",
        attrs: {}
    };
    
    const attrs = selectedItem.parsed_spec.attrs || {};
    for (const k in attrs) {
        if (selectedItem.active_params[k]) {
            customizedSpec.attrs[k] = attrs[k];
        }
    }
    
    showLoading("重新对齐特征参数...", "系统正在使用选定的核心参数维度检索并重新匹配推荐项...");
    
    try {
        const response = await fetch(`${backendUrl}/api/match`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                text: selectedItem.original_spec,
                name: selectedItem.original_name,
                category: selectedItem.original_category || "",
                professional: selectedItem.professional || "暖通",
                parsed_spec: customizedSpec
            })
        });
        
        if (!response.ok) throw new Error("重新匹配失败");
        
        const data = await response.json();
        
        // Update item matching details
        selectedItem.is_exact = data.is_exact;
        selectedItem.matched_code = data.matched_code || "";
        selectedItem.top_candidates = data.top_candidates;
        
        if (data.top_candidates && data.top_candidates.length > 0) {
            const top = data.top_candidates[0];
            selectedItem.matched_name = top.name;
            selectedItem.matched_category = top.category || "";
            selectedItem.matched_spec = top.specification || "";
            selectedItem.matched_unit = top.unit || "";
            selectedItem.matched_brand = top.brand || "";
            
            if (data.is_exact) {
                selectedItem.bindings = [{
                    code: top.code,
                    name: top.name,
                    specification: top.specification || "",
                    unit: top.unit || selectedItem.original_unit || "个",
                    brand: top.brand || "",
                    category: top.category || "",
                    qty: selectedItem.original_qty
                }];
                selectedItem.matched_code = top.code;
            }
        }
        
        selectRow(selectedItem);
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
            
            // Build customized spec
            const customizedSpec = {
                model: it.active_params['model'] ? it.parsed_spec.model : "",
                attrs: {}
            };
            const attrs = it.parsed_spec.attrs || {};
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
                    parsed_spec: customizedSpec
                })
            });
            
            if (!response.ok) throw new Error(`设备第 ${it.original_row_idx} 行重新匹配失败`);
            
            const data = await response.json();
            
            // Update similar item details
            it.is_exact = data.is_exact;
            it.matched_code = data.matched_code || "";
            it.top_candidates = data.top_candidates;
            
            if (data.top_candidates && data.top_candidates.length > 0) {
                const top = data.top_candidates[0];
                it.matched_name = top.name;
                it.matched_category = top.category || "";
                it.matched_spec = top.specification || "";
                it.matched_unit = top.unit || "";
                it.matched_brand = top.brand || "";
                
                if (data.is_exact) {
                    it.bindings = [{
                        code: top.code,
                        name: top.name,
                        specification: top.specification || "",
                        unit: top.unit || it.original_unit || "个",
                        brand: top.brand || "",
                        category: top.category || "",
                        qty: it.original_qty
                    }];
                    it.matched_code = top.code;
                }
            }
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

// 8. Update progress indicators
function updateProgress() {
    if (items.length === 0) return;
    
    // 1. Overall progress
    const matchedCount = items.filter(item => item.is_exact || item.matched_code !== "").length;
    const progressPercent = Math.round((matchedCount / items.length) * 100) || 0;
    
    document.getElementById('match-progress-bar').style.width = `${progressPercent}%`;
    document.getElementById('progress-percent').innerText = `${progressPercent}%`;
    
    // 2. Current Professional progress
    let profItems = items;
    if (currentProfessional !== '全部') {
        profItems = items.filter(item => item.professional === currentProfessional);
    }
    
    const profMatchedCount = profItems.filter(item => item.is_exact || item.matched_code !== "").length;
    const profProgressPercent = Math.round((profMatchedCount / profItems.length) * 100) || 0;
    
    document.getElementById('current-prof-progress-label').innerText = `${currentProfessional}匹配进度:`;
    document.getElementById('current-prof-progress-bar').style.width = `${profProgressPercent}%`;
    document.getElementById('current-prof-progress-percent').innerText = `${profProgressPercent}%`;
}

// 9. Export standard sheet blob handler (V0.0.2 non-destructive)
async function handleExport() {
    if (items.length === 0) return;
    
    showLoading("正在精准写入模板并导出报表...", "系统正在执行非破坏性写入，自动填充价格列并重新生成序号，请稍候...", 0);
    
    try {
        const response = await fetch(`${backendUrl}/api/export`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: jsonPayloadData()
        });
        
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "生成报表时出错");
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
            bindings: item.bindings || null
        };
    });
    return JSON.stringify({ items: mapped });
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
                    
                    const boundListHtml = s.bindings.map(b => `<div style="font-size: 11.5px; margin-bottom: 2px;"><code style="font-family: monospace; background: rgba(59, 130, 246, 0.1); color: var(--color-primary); padding: 1px 4px; border-radius: 3px; font-size: 11px;">${b.code}</code> <strong>${b.name}</strong> (比例: ${b.qty_ratio.toFixed(2)})</div>`).join('');
                    
                    tr.innerHTML = `
                        <td><strong>${s.original_name}</strong></td>
                        <td><div style="display: flex; flex-direction: column; gap: 4px;">${boundListHtml}</div></td>
                        <td><span class="badge" style="background-color: var(--color-success-bg); color: var(--color-success); font-weight: 700; padding: 2px 8px; font-size: 11px;">${s.confidence_count} 次</span></td>
                        <td><button class="btn btn-success" style="padding: 4px 8px; font-size: 11px;"><i class="fa-solid fa-check"></i> 采纳规则</button></td>
                    `;
                    
                    tr.querySelector('button').addEventListener('click', async () => {
                        const confirmResp = await fetch(`${backendUrl}/api/binding/rules/confirm`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                original_name: s.original_name,
                                bindings: s.bindings
                            })
                        });
                        if (confirmResp.ok) {
                            alert(`已启用原始清单 "${s.original_name}" 对应的多物料自动绑定推荐规则！`);
                            loadAndRenderBindingSuggestionsAndRules();
                        } else {
                            alert('采纳规则失败');
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
                    
                    const boundListHtml = r.bound_materials.map(b => `<div style="font-size: 11.5px; margin-bottom: 2px;"><code style="font-family: monospace; background: rgba(59, 130, 246, 0.1); color: var(--color-primary); padding: 1px 4px; border-radius: 3px; font-size: 11px;">${b.code}</code> <strong>${b.name}</strong> (比例: ${b.qty_ratio.toFixed(2)})</div>`).join('');
                    
                    tr.innerHTML = `
                        <td><strong>${r.original_name}</strong></td>
                        <td><div style="display: flex; flex-direction: column; gap: 4px;">${boundListHtml}</div></td>
                        <td><button class="btn btn-secondary" style="padding: 4px 8px; color: var(--color-danger); border-color: var(--color-danger-border); font-size: 11px;"><i class="fa-solid fa-trash"></i> 删除</button></td>
                    `;
                    
                    tr.querySelector('button').addEventListener('click', async () => {
                        if (confirm(`确定要删除针对 "${r.original_name}" 的自动绑定推荐规则吗？`)) {
                            const delResp = await fetch(`${backendUrl}/api/binding/rules/delete`, {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ original_name: r.original_name })
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
                    <div style="height: 6px; background: rgba(255,255,255,0.05); border-radius: 3px; overflow: hidden;">
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
                        <span><strong style="color: #a855f7; font-family: monospace;">Top ${idx+1}.</strong> ${u.unit}</span>
                        <span style="color: var(--color-text-secondary);">${u.count.toLocaleString()} 行 (${u.percent}%)</span>
                    </div>
                    <div style="height: 6px; background: rgba(255,255,255,0.05); border-radius: 3px; overflow: hidden;">
                        <div style="width: ${u.percent}%; height: 100%; background: #a855f7; border-radius: 3px;"></div>
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
                ? `<span style="background: rgba(16, 185, 129, 0.15); color: #34d399; padding: 2px 6px; border-radius: 4px; font-family: monospace; font-size: 12px; font-weight: 500;">${item.model}</span>` 
                : `<span style="color: var(--color-text-muted); font-size: 11px;">未提取</span>`;
                
            tr.innerHTML = `
                <td style="font-family: monospace; color: var(--color-text-secondary); font-size: 12px;">${item.code}</td>
                <td style="font-weight: 500; color: var(--color-text-primary);">${item.name}</td>
                <td style="color: var(--color-text-secondary); max-width: 300px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${item.specification}">${item.specification || '-'}</td>
                <td>${modelTag}</td>
                <td><span style="background: rgba(59, 130, 246, 0.1); color: var(--color-primary); padding: 2px 8px; border-radius: 4px; font-size: 12px;">${item.category || '未分类'}</span></td>
                <td>${item.unit || '-'}</td>
                <td style="color: var(--color-text-secondary);">${item.brand || '-'}</td>
            `;
            tbody.appendChild(tr);
        });
        
    } catch (e) {
        console.error("Error loading material table:", e);
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: #f87171; padding: 25px;">加载失败: ${e.message}</td></tr>`;
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
        const payload = {
            original_filename: originalFileName,
            professional: currentProfessional || '全部',
            items_json: JSON.stringify(items)
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
                items = JSON.parse(draft.items_json);
                
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
        const payload = {
            session_name: name,
            original_filename: originalFileName,
            professional: currentProfessional || '全部',
            items_json: JSON.stringify(items)
        };
        
        let res;
        if (activeSessionId) {
            // Update existing named session
            res = await fetch(`${backendUrl}/api/session/${activeSessionId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ items_json: payload.items_json })
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
            tr.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
            tr.innerHTML = `
                <td style="padding: 10px;"><strong>${sess.session_name}</strong></td>
                <td style="padding: 10px; color: var(--color-text-secondary);">${sess.original_filename}</td>
                <td style="padding: 10px; color: var(--color-text-muted); font-size: 12px;">${sess.updated_at}</td>
                <td style="padding: 10px;">
                    <button class="btn btn-primary" style="padding: 4px 8px; font-size: 11px; margin-right: 4px;" onclick="loadSession('${sess.id}')">恢复</button>
                    <button class="btn btn-danger" style="padding: 4px 8px; font-size: 11px; background-color: #dc2626; border-color: #dc2626;" onclick="deleteSession('${sess.id}', '${sess.session_name}')">删除</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--color-danger);">${err.message}</td></tr>`;
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
        items = JSON.parse(session.items_json);
        
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
            tabStudio.style.background = '#3b82f6';
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
            tabAudit.style.background = '#3b82f6';
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
            tabLeaderboard.style.background = '#3b82f6';
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
        statusBadge.style.background = 'rgba(59, 130, 246, 0.2)';
        statusBadge.style.color = '#3b82f6';
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
            statusBadge.style.background = 'rgba(16, 185, 129, 0.2)';
            statusBadge.style.color = '#10b981';
        }

        if (sample_text) {
            handleTestRule();
        }
    } catch (err) {
        alert(`编译失败: ${err.message}`);
        if (statusBadge) {
            statusBadge.innerText = '编译失败';
            statusBadge.style.background = 'rgba(239, 68, 68, 0.2)';
            statusBadge.style.color = '#ef4444';
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
                <div><strong>清洗前：</strong> <code>${data.original_text}</code></div>
                <div><strong>清洗后：</strong> <code style="color: #38bdf8;">${data.cleaned_text}</code></div>
                <div><strong>提取属性：</strong> <code style="color: #f59e0b;">${JSON.stringify(data.extracted_params)}</code></div>
                <div><strong>清洗规则应用：</strong> ${data.cleaning_logs.length ? data.cleaning_logs.join('; ') : '无修改'}</div>
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
        const res = await fetch(`${backendUrl}/api/llm-rules?status=all_active`, {
            headers: getAuthHeaders()
        });
        if (!res.ok) return;

        cachedActiveRules = await res.json();
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
        const isApproved = r.status === 'approved';
        const statusBadge = isApproved 
            ? '<span style="display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 4px; background: rgba(16, 185, 129, 0.15); color: #10b981; font-size: 11px; font-weight: 600;"><i class="fa-solid fa-circle-check"></i> 生效运行中</span>'
            : '<span style="display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 4px; background: rgba(148, 163, 184, 0.15); color: #94a3b8; font-size: 11px; font-weight: 600;"><i class="fa-solid fa-circle-pause"></i> 已停用</span>';

        tr.innerHTML = `
            <td><code>${r.rule_id}</code></td>
            <td><strong style="color: var(--color-text-primary);">${r.rule_name}</strong></td>
            <td><span style="font-size: 12px; color: #60a5fa;">[${r.scope_level}]</span> ${r.scope_target || '全域通用'}</td>
            <td><span style="font-size: 12px; color: #a78bfa;">${r.discipline || '通用'}</span></td>
            <td style="text-align: center;">${statusBadge}</td>
            <td style="text-align: right;"><strong style="color: #10b981; font-size: 14px;">${r.hit_count || 0}</strong> 次</td>
            <td><span style="font-size: 12px;">${r.author_name || '-'}</span> <span style="color: var(--color-text-muted); font-size: 11px;">/ ${r.approver_name || '-'}</span></td>
            <td style="font-size: 12px; color: var(--color-text-secondary);">${r.approved_at || r.updated_at || '-'}</td>
            <td style="text-align: center;">
                <div style="display: inline-flex; gap: 6px; align-items: center; justify-content: center;">
                    <button class="btn btn-secondary" onclick="openRuleEditModal('${r.rule_id}')" style="padding: 3px 8px; font-size: 11px; height: 26px;" title="修改规则配置"><i class="fa-solid fa-pen-to-square"></i> 修改</button>
                    <button class="btn ${isApproved ? 'btn-warning' : 'btn-success'}" onclick="toggleActiveRule('${r.rule_id}')" style="padding: 3px 8px; font-size: 11px; height: 26px; ${isApproved ? 'background-color: #d97706; border-color: #d97706; color: white;' : ''}" title="${isApproved ? '停用此规则' : '启用此规则'}">
                        ${isApproved ? '<i class="fa-solid fa-pause"></i> 停用' : '<i class="fa-solid fa-play"></i> 启用'}
                    </button>
                    <button class="btn btn-danger" onclick="deleteActiveRule('${r.rule_id}')" style="padding: 3px 8px; font-size: 11px; height: 26px; background-color: #dc2626; border-color: #dc2626; color: white;" title="彻底删除此规则"><i class="fa-solid fa-trash"></i> 删除</button>
                    <button class="btn" onclick="viewActiveRuleDsl('${r.rule_id}')" style="padding: 3px 8px; font-size: 11px; height: 26px; background: rgba(255,255,255,0.08); border-color: var(--border-color); color: #38bdf8;" title="查看DSL代码"><i class="fa-solid fa-code"></i> DSL</button>
                </div>
            </td>
        `;
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

        alert(`规则 [${editingRuleId}] 修改成功并已即时生效！`);
        closeRuleEditModal();
        fetchAndRenderActiveRules();
        loadRuleStats();
    } catch (err) {
        alert(`修改保存失败: ${err.message}`);
    }
}

async function toggleActiveRule(ruleId) {
    try {
        const res = await fetch(`${backendUrl}/api/llm-rules/${ruleId}/toggle`, {
            method: 'POST',
            headers: getAuthHeaders()
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.error || '状态切换失败');

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
                <td><code>${r.rule_id}</code></td>
                <td><strong>${r.rule_name}</strong></td>
                <td>${r.scope_target || r.scope_level}</td>
                <td>${r.author_name}</td>
                <td>${r.created_at || '-'}</td>
                <td style="text-align: center;">
                    <button class="btn btn-success" style="padding: 4px 8px; font-size: 11px;" onclick="auditRule('${r.rule_id}', 'approve')"><i class="fa-solid fa-check"></i> 批准上线</button>
                    <button class="btn btn-danger" style="padding: 4px 8px; font-size: 11px; background: #dc2626; border-color: #dc2626; color: white;" onclick="auditRule('${r.rule_id}', 'reject')"><i class="fa-solid fa-xmark"></i> 驳回</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error('Failed to fetch pending rules:', e);
    }
}

async function auditRule(ruleId, action) {
    const comment = prompt(action === 'approve' ? '请输入批准说明（可选）：' : '请输入驳回原因：');
    if (action === 'reject' && comment === null) return;

    try {
        const res = await fetch(`${backendUrl}/api/llm-rules/${ruleId}/audit`, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({ action, comment: comment || '' })
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
                    <td><strong style="color: #eab308;">#${index + 1}</strong></td>
                    <td><code>${r.rule_id}</code></td>
                    <td><strong>${r.rule_name}</strong></td>
                    <td>${r.scope_target || '通用'}</td>
                    <td>${r.author_name}</td>
                    <td>${r.approved_at || '-'}</td>
                    <td style="text-align: right;"><strong style="color: #10b981; font-size: 15px;">${r.hit_count}</strong> 次</td>
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

