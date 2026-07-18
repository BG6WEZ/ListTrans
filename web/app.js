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

const backendUrl = ''; // Relative paths since hosted on same server

document.addEventListener('DOMContentLoaded', () => {
    initEventListeners();
    setupNavigation();
});

function setupNavigation() {
    const navWorkbench = document.getElementById('nav-workbench');
    const navConfig = document.getElementById('nav-config');
    const workbenchEl = document.getElementById('workbench');
    const configDashboardEl = document.getElementById('config-dashboard');
    const welcomeSlate = document.getElementById('welcome-slate');

    navWorkbench.addEventListener('click', () => {
        navWorkbench.classList.add('active');
        navConfig.classList.remove('active');
        
        if (items.length > 0) {
            workbenchEl.style.display = 'flex';
            welcomeSlate.style.display = 'none';
        } else {
            welcomeSlate.style.display = 'flex';
            workbenchEl.style.display = 'none';
        }
        configDashboardEl.style.display = 'none';
    });

    navConfig.addEventListener('click', () => {
        navConfig.classList.add('active');
        navWorkbench.classList.remove('active');
        
        workbenchEl.style.display = 'none';
        welcomeSlate.style.display = 'none';
        configDashboardEl.style.display = 'flex';
        
        loadAndRenderConfigs();
    });
}

function initEventListeners() {
    // File inputs
    const fileInput = document.getElementById('xlsx-file-input');
    const uploadTrigger = document.getElementById('upload-trigger');
    const welcomeUploadBtn = document.getElementById('welcome-upload-btn');
    
    uploadTrigger.addEventListener('click', () => fileInput.click());
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

// Show/Hide Spinner Overlay
function showLoading(title, subtitle) {
    document.getElementById('loading-title').innerText = title || "处理中...";
    document.getElementById('loading-subtitle').innerText = subtitle || "";
    document.getElementById('loading-overlay').style.display = 'flex';
}

function hideLoading() {
    document.getElementById('loading-overlay').style.display = 'none';
}

// 1. File Upload Handler
async function handleFileUpload(file) {
    showLoading("正在上传并分析清单...", "系统正在提取规格参数并执行高性能多线程语义匹配，请稍候...");
    
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        const response = await fetch(`${backendUrl}/api/upload`, {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "解析Excel清单时发生错误");
        }
        
        const data = await response.json();
        items = data.items;
        
        // Cache initial matching codes to check if user manual overrides later (for learning feedback loop)
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
        originalFileName = file.name;
        const uploadedFilenameEl = document.getElementById('uploaded-filename');
        if (uploadedFilenameEl) {
            uploadedFilenameEl.innerText = ` - ${file.name}`;
            uploadedFilenameEl.title = file.name;
        }
        document.getElementById('welcome-slate').style.display = 'none';
        document.getElementById('workbench').style.display = 'flex';
        document.getElementById('export-btn').disabled = false;
        
        // Clear selection
        selectedItem = null;
        document.getElementById('select-prompt').style.display = 'flex';
        document.getElementById('detail-editor').style.display = 'none';
        
        // Reset pagination
        currentPage = 1;
        
        // Update table & status
        renderTable();
        updateProgress();
        
    } catch (error) {
        alert(`错误: ${error.message}`);
    } finally {
        hideLoading();
        // Reset file input value
        document.getElementById('xlsx-file-input').value = "";
    }
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
    
    // Render decision status card
    const statusCard = document.getElementById('match-status-card');
    const statusTitle = document.getElementById('status-title');
    const statusDesc = document.getElementById('status-desc');
    
    statusCard.className = 'match-status-card';
    if (item.is_exact) {
        statusCard.classList.add('exact');
        statusTitle.innerText = "已完成精确匹配 / 人工确认";
        statusDesc.innerHTML = `
            <div style="margin-bottom: 6px;">当前已绑定以下物料的完整信息：</div>
            <div class="matched-info-grid">
                <div class="matched-info-item">
                    <span class="matched-info-label">物料编码:</span>
                    <span class="matched-info-value code-badge">${item.matched_code || '-'}</span>
                </div>
                <div class="matched-info-item">
                    <span class="matched-info-label">物料名称:</span>
                    <span class="matched-info-value">${item.matched_name || '-'}</span>
                </div>
                <div class="matched-info-item full-width">
                    <span class="matched-info-label">规格型号:</span>
                    <span class="matched-info-value">${item.matched_spec || '-'}</span>
                </div>
                <div class="matched-info-item">
                    <span class="matched-info-label">品牌:</span>
                    <span class="matched-info-value">${item.matched_brand || '-'}</span>
                </div>
                <div class="matched-info-item">
                    <span class="matched-info-label">品类:</span>
                    <span class="matched-info-value">${item.matched_category || '-'}</span>
                </div>
                <div class="matched-info-item">
                    <span class="matched-info-label">单位:</span>
                    <span class="matched-info-value">${item.matched_unit || '个'}</span>
                </div>
            </div>
        `;
    } else if (item.matched_code) {
        statusCard.classList.add('fuzzy');
        statusTitle.innerText = "自动匹配相似度低于阈值，请人工校准";
        statusDesc.innerText = `系统分析了物理量与文本指标，推荐了相似度最高的前10个选项，请套用最符合标准的卡片。`;
    } else {
        statusCard.classList.add('unmatched');
        statusTitle.innerText = "未在物料库中检索到相近设备";
        statusDesc.innerText = `参数差异可能过大。请点击顶部“补全参数”补充完整物理量重试，或在下方手动全局模糊检索。`;
    }
    
    // Render recommendation cards
    renderCandidates();
    
    // Clear manual search input and results
    document.getElementById('manual-search-input').value = '';
    document.getElementById('search-results-tbody').innerHTML = `
        <tr><td colspan="5" class="empty-search">请输入关键字并搜索</td></tr>
    `;
}

// 3.5 Render Bound Materials List
function renderBoundMaterials() {
    const container = document.getElementById('bound-materials-list');
    container.innerHTML = '';
    
    if (!selectedItem) return;
    
    if (!selectedItem.bindings) {
        selectedItem.bindings = [];
    }
    
    document.getElementById('bound-materials-count').innerText = selectedItem.bindings.length;
    
    if (selectedItem.bindings.length === 0) {
        container.innerHTML = `<div style="text-align: center; color: var(--color-text-muted); font-size: 13px; padding: 15px; border: 1px dashed var(--border-color); border-radius: var(--radius-sm);">暂无绑定物料，请点击下方推荐卡片或检索套用以绑定</div>`;
        return;
    }
    
    selectedItem.bindings.forEach((b, idx) => {
        const div = document.createElement('div');
        div.className = 'bound-material-item';
        div.style = 'display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: var(--radius-sm); gap: 16px;';
        
        div.innerHTML = `
            <div style="flex: 1; display: flex; flex-direction: column; gap: 4px; min-width: 0;">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span style="font-weight: 600; font-size: 13.5px; color: var(--color-text-primary); text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">${b.name}</span>
                    <span class="code-badge" style="font-family: monospace; font-size: 11px; padding: 1px 6px; background: rgba(59, 130, 246, 0.15); color: var(--color-primary); border-radius: 3px;">${b.code}</span>
                </div>
                <div style="font-size: 11px; color: var(--color-text-muted); text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">
                    规格: ${b.specification || '无'} | 品牌: ${b.brand || '无'} | 分类: ${b.category || '无'}
                </div>
            </div>
            <div style="display: flex; align-items: center; gap: 10px; flex-shrink: 0;">
                <div style="display: flex; align-items: center; gap: 4px;">
                    <span style="font-size: 12px; color: var(--color-text-secondary);">工程量:</span>
                    <input type="number" class="bound-qty-input" value="${b.qty !== undefined ? b.qty : selectedItem.original_qty}" style="width: 80px; padding: 4px 6px; background: var(--bg-base); border: 1px solid var(--border-color); border-radius: 4px; color: var(--color-text-primary); font-size: 12.5px; font-weight: 500;" />
                    <span style="font-size: 12px; color: var(--color-text-muted); min-width: 16px;">${b.unit || selectedItem.original_unit || '个'}</span>
                </div>
                <button class="btn btn-delete-binding" style="padding: 4px 8px; color: var(--color-danger); border-color: transparent; background: transparent; font-size: 13px;" title="删除绑定">
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
        
        card.innerHTML = `
            <div class="candidate-header">
                <div class="candidate-title">
                    <span class="cand-name">${cand.name}</span>
                    <span class="cand-code">${cand.code}</span>
                </div>
                <div class="match-percentage-badge">${cand.score}% 相似</div>
            </div>
            <div class="candidate-spec">${cand.specification || '无规格描述'}</div>
            <div style="margin: 4px 0 8px 0;">${tagsHtml}</div>
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
    
    // Check duplicates
    if (selectedItem.bindings.some(b => b.code === cand.code)) {
        alert(`物料 ${cand.code} (${cand.name}) 已经绑定了！`);
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
    const block = document.getElementById('parsed-params-block');
    container.innerHTML = '';
    
    if (!selectedItem || !selectedItem.parsed_spec) {
        block.style.display = 'none';
        return;
    }
    
    const spec = selectedItem.parsed_spec;
    const model = spec.model;
    const attrs = spec.attrs || {};
    
    const hasParams = model || Object.keys(attrs).length > 0;
    if (!hasParams) {
        block.style.display = 'none';
        return;
    }
    
    block.style.display = 'block';
    
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
    
    showLoading("正在精准写入模板并导出报表...", "系统正在执行非破坏性写入，自动填充价格列并重新生成序号，请稍候...");
    
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
    
    const importMode = document.querySelector('input[name="db-import-mode"]:checked').value;
    const modeText = importMode === 'replace' ? '全量替换' : '增量追加';
    
    if (!confirm(`确认要以【${modeText}】模式，将文件 "${file.name}" 导入到全局物料明细库中吗？\n警告: 此操作会重建匹配向量索引，耗时大约数秒至数十秒。`)) {
        return;
    }
    
    showLoading("正在导入物料库并重建向量索引...", "系统正在导入 Excel 并重新训练生成 FAISS 特征向量，请勿刷新页面...");
    startDbImportBtn.disabled = true;
    
    const formData = new FormData();
    formData.append('file', file);
    formData.append('mode', importMode);
    
    try {
        const response = await fetch(`${backendUrl}/api/db/upload`, {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            const errData = await response.json();
            throw new Error(errData.detail || "导入失败");
        }
        
        const data = await response.json();
        
        // Reset file inputs
        dbUploadInput.value = '';
        dbFileLabel.innerText = '未选择文件';
        
        // Re-render fresh metadata
        renderDbMetadata(data.metadata);
        
        alert("物料明细库导入及向量索引重建已全部完成！");
        
        // Refresh logs immediately
        loadAndRenderLogs();
        
    } catch (e) {
        alert(`导入出错: ${e.message}`);
        startDbImportBtn.disabled = false;
    } finally {
        hideLoading();
    }
}
