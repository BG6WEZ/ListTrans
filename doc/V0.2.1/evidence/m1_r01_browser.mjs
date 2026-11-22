import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {copyFile, readFile, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';

const [chrome, origin, root, out] = process.argv.slice(2);
const profile = join(root, 'chrome');
const browser = spawn(chrome, [
    '--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + profile,
    '--disable-background-networking', '--disable-component-update', '--disable-sync',
    '--disable-extensions', '--no-first-run', '--no-proxy-server', '--window-size=1440,1000',
    '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1', 'about:blank'
], {windowsHide: true, stdio: 'ignore'});
const result = {engine: null, fixed: {}, checks: [], requests: [], exceptions: [], dialogs: []};
let socket, approveDraft = false, openDialog = false;
const pending = new Map();
const requests = new Map();
let sequence = 0;

async function until(check, name, timeout = 25000) {
    const end = performance.now() + timeout;
    while (performance.now() < end) {
        const value = await check();
        if (value) return value;
        await delay(40);
    }
    throw new Error('Timed out waiting for ' + name);
}

function send(method, params = {}) {
    const id = ++sequence;
    return new Promise((resolve, reject) => {
        pending.set(id, {resolve, reject, method});
        socket.send(JSON.stringify({id, method, params}));
    });
}

async function evaluate(expression, awaitPromise = false) {
    const response = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise});
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || 'DOM evaluation failed');
    return response.result.value;
}

const query = selector => 'document.querySelector(' + JSON.stringify(selector) + ')';
const text = selector => evaluate(query(selector) + '?.textContent?.trim()');
const visible = selector => evaluate('Boolean(' + query(selector) + '?.getClientRects().length)');
const count = async expected => assert.equal(Number(await text('#items-count')), expected);
const summary = async (pre, total, ready) => {
    const rate = (100 * pre / total).toFixed(1);
    assert.equal(await text('#initial-prefilled-count'), '系统已自动预填 ' + pre + ' / ' + total + ' 条（' + rate + '%）');
    if (ready != null) assert.equal(await text('#initial-export-count'), '已确认可导出 ' + ready + ' / ' + total + ' 条');
};
const idle = () => until(() => evaluate("getComputedStyle(document.querySelector('#loading-overlay')).display === 'none'"), 'idle workbench');
const request = (after, path, method, status) => until(
    () => result.requests.slice(after).find(row => row.path === path && row.method === method && (status == null || row.status === status)),
    method + ' ' + path + ' ' + status,
);

async function click(selector) {
    const spot = await until(() => evaluate('(() => { const el = ' + query(selector)
        + '; if (!el || el.disabled || !el.getClientRects().length) return null; '
        + 'el.scrollIntoView({block:"nearest",inline:"nearest"}); const r = el.getBoundingClientRect(); '
        + 'return {x:r.left+r.width/2,y:r.top+r.height/2}; })()'), selector);
    for (const type of ['mousePressed', 'mouseReleased']) {
        await send('Input.dispatchMouseEvent', {...spot, type, button: 'left', clickCount: 1});
    }
}

async function fill(selector, value) {
    await click(selector);
    const key = {key: 'a', code: 'KeyA', modifiers: 2, windowsVirtualKeyCode: 65};
    await send('Input.dispatchKeyEvent', {type: 'keyDown', ...key});
    await send('Input.dispatchKeyEvent', {type: 'keyUp', ...key});
    if (value) await send('Input.insertText', {text: value});
    else {
        await send('Input.dispatchKeyEvent', {type: 'keyDown', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8});
        await send('Input.dispatchKeyEvent', {type: 'keyUp', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8});
    }
}

async function file(name) {
    const {root: dom} = await send('DOM.getDocument');
    const {nodeId} = await send('DOM.querySelector', {nodeId: dom.nodeId, selector: '#xlsx-file-input'});
    assert(nodeId);
    await send('DOM.setFileInputFiles', {nodeId, files: [join(root, name)]});
}

async function upload(name, total) {
    const from = result.requests.length;
    await file(name);
    await request(from, '/api/upload', 'POST', 200);
    await until(async () => await visible('#initial-overview') &&
        (await text('#uploaded-filename')).includes(name), name, 45000);
    await count(total);
    await idle();
}

async function openWorkbench(loadDraft = false) {
    approveDraft = loadDraft;
    const from = result.requests.length;
    await send('Page.navigate', {url: origin + '/'});
    await request(from, '/api/session/draft', 'GET', 200);
    await until(() => !openDialog && evaluate("document.readyState === 'complete' && !!document.querySelector('#items-count')"),
        'workbench ready');
    if (loadDraft) await until(() => visible('#initial-overview'), 'annotated draft');
}

const group = (name) => click('[data-initial-filter="' + name + '"]');
const currentState = (name) => click('[data-filter="' + name + '"]');
const selectFirst = () => click('#items-tbody tr[data-source-row]');

async function screenshot(name, theme) {
    await send('Input.dispatchMouseEvent', {type: 'mouseMoved', x: 1, y: 1});
    await delay(350);
    const geometry = await evaluate('(() => { const overview=document.querySelector("#initial-overview"); '
        + 'const card=overview.getBoundingClientRect(); const buttons=[...overview.querySelectorAll("button")]; '
        + 'return {theme:document.documentElement.dataset.theme, overflow:document.documentElement.scrollWidth>innerWidth, '
        + 'cardsClipped:buttons.some(b=>b.getClientRects().length && (b.scrollWidth>b.clientWidth+1 || '
        + 'b.getBoundingClientRect().right>card.right+1)), '
        + 'navClipped:document.querySelector(".nav-links").scrollWidth>document.querySelector(".nav-links").clientWidth+1, '
        + 'listHeight:document.querySelector(".list-scroll").clientHeight}; })()');
    assert.equal(geometry.theme, theme);
    assert(!geometry.overflow && !geometry.cardsClipped && !geometry.navClipped && geometry.listHeight > 40, JSON.stringify(geometry));
    const image = await send('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
    await writeFile(join(out, name + '.png'), Buffer.from(image.data, 'base64'));
    return geometry;
}

async function fixed(theme) {
    await openWorkbench();
    if (await evaluate('document.documentElement.dataset.theme') !== theme) await click('#themeToggleBtn');
    const filename = theme === 'dark' ? 'fixed-100.xlsx' : 'fixed-100-light.xlsx';
    await upload(filename, 100);
    await summary(63, 100, 1);
    assert.equal(await text('#initial-scope'), '全单 · 100 条');
    assert.equal(await text('[data-initial-count="unprefilled"]'), '37');
    assert.equal(await text('#initial-source-counts'), '组合匹配 1 条');
    const checks = {A: 21, B: 32, C: 20, D: 27, low_score_prefill: 10, safety_missing: 10, unprefilled: 37};
    for (const [name, expected] of Object.entries(checks)) {
        assert.equal(Number(await text('[data-initial-count="' + name + '"]')), expected);
        await group(name);
        await count(expected);
        await group(name);
    }
    await group('safety_missing');
    assert.match(await text('#items-tbody tr .current-row-result'), /待补参/);
    assert.equal(await evaluate('document.querySelectorAll("#items-tbody .current-row-result .row-code-lbl").length'), 0);
    await selectFirst();
    assert.equal(await text('#bound-materials-count'), '0');
    assert.equal(await evaluate('document.querySelector("#confirm-match-finished-btn").disabled'), true);
    assert.match(await text('#review-differences'), /防爆.*清单缺失.*安全参数待补/s);
    assert.match(await text('#candidates-container'), /匹配评分 97 分/);
    assert.equal(await evaluate('document.querySelector("#candidates-container .candidate-card").getAttribute("role")'), 'group');
    assert(await visible('#rematch-selected-btn'));
    const blocked = result.requests.length;
    await click('#export-btn');
    const exportRequest = await request(blocked, '/api/export', 'POST', 422);
    assert(exportRequest.emptySafety && exportRequest.firstSafetyRows === 10);
    await idle();
    assert(result.dialogs.some(message => message.includes('核对轴流风机')));
    await group('safety_missing');

    await click('#prof-tabs .prof-tab:nth-child(2)');
    assert.equal(await text('#initial-scope'), '暖通 · 37 条');
    await summary(0, 37, 0);
    await click('#prof-tabs .prof-tab:nth-child(3)');
    await summary(63, 63, 1);
    await click('#prof-tabs .prof-tab:first-child');
    await click('#next-page-btn');
    assert.equal(await text('#current-page-num'), '2');
    await summary(63, 100, 1);
    await click('#prev-page-btn');
    await fill('#list-search', 'FIX-A-0019');
    await count(1);
    await summary(63, 100, 1);
    await fill('#list-search', '');
    await click('#initial-score-filter');
    await count(30);
    await click('#initial-score-filter');
    await group('A');
    await currentState('auto_accepted');
    await count(1);
    await selectFirst();
    assert.equal(await text('#bound-materials-count'), '3');
    await currentState('recommended');
    await count(20);
    await selectFirst();
    assert.match(await text('#review-differences'), /功率.*目录缺失.*清单明确要求，目录待核验.*40-50w/s);
    assert.match(await text('#review-differences'), /CDP-B.*14\.00 分/);
    const before = performance.now();
    const from = result.requests.length;
    for (let i = 0; i < 10; i++) {
        await selectFirst();
        const since = result.requests.length;
        await click('#confirm-match-finished-btn');
        await request(since, '/api/binding/submit', 'POST', 200);
        await idle();
        await count(19 - i);
    }
    await summary(63, 100, 11);
    const duration = performance.now() - before;
    const reviewed = await evaluate('(() => { const revisited=items.filter(i=>i.initial_match?.actual_prefilled '
        + '&& i.decision_status==="manual_confirmed"); const retained=revisited.filter(i=>JSON.stringify(i.bindings.map(b=>[b.code,b.unit,b.qty,b.qty_ratio]).sort())'
        + '===JSON.stringify(i.initial_match.effective_bindings.map(b=>[b.code,b.unit,b.qty,b.qty_ratio]).sort())); '
        + 'return {reviewed:revisited.length, retained:retained.length, unreviewed:63-revisited.length}; })()');
    assert.deepEqual(reviewed, {reviewed: 10, retained: 10, unreviewed: 53});
    assert.equal(result.requests.slice(from).filter(r => r.path === '/api/binding/submit' && r.status === 200).length, 10);
    await currentState('all');
    await group('A');
    await click('#prof-tabs .prof-tab:first-child');
    const layout = await screenshot('workbench-' + theme, theme);
    result.fixed[theme] = {prefilled: 63, total: 100, exportable_before: 1, exportable_after: 11, checks, layout,
        elapsed_ms: Math.round(duration), ...reviewed, adoption: reviewed.retained / reviewed.reviewed, safety_export: 422};
}

async function seedLegacyDraft(mixed) {
    const status = await evaluate('(async () => { const verified=items.find(i=>i.initial_match); '
        + 'const legacy={...verified,original_code:"LEGACY-M1", original_name:"旧草稿物料", '
        + 'document_id:"",sheet_id:"",source_row_id:"",initial_match:null,'
        + 'workbench_bindings_initialized:false,decision_status:"recommended",bindings:[]}; '
        + 'const target=' + (mixed ? '[verified,legacy]' : '[legacy]') + '; '
        + 'return (await fetch("/api/session/draft",{method:"POST",headers:{"Content-Type":"application/json"},'
        + 'body:JSON.stringify({original_filename:"legacy-fixture.xlsx",professional:"全部",'
        + 'items_json:JSON.stringify(target),document_id:currentDocumentId,work_session_id:currentWorkSessionId})})).status; })()', true);
    assert.equal(status, 200);
    const from = result.requests.length;
    await openWorkbench(true);
    await request(from, '/api/session/annotate', 'POST', 200);
    await count(mixed ? 2 : 1);
}

async function advanced() {
    await group('A');
    await currentState('manual_confirmed');
    await selectFirst();
    const first = await text('#initial-review-binding');
    const id = await evaluate('selectedItem.source_row_id');
    const frozen = await evaluate('JSON.stringify(selectedItem.initial_match)');
    assert(await evaluate('Object.isFrozen(selectedItem.initial_match.effective_bindings[0])'));
    let from = result.requests.length;
    await click('#rematch-selected-btn');
    await request(from, '/api/match', 'POST', 200);
    await idle();
    assert.equal(await text('#initial-review-binding'), first);
    assert.equal(await text('#bound-materials-count'), '0');
    from = result.requests.length;
    await click('#batch-apply-params-btn');
    await request(from, '/api/match', 'POST', 200);
    await until(() => result.dialogs.some(msg => msg.includes('批量匹配完成')), 'batch rematch dialog');
    await idle();
    assert.equal(await evaluate('JSON.stringify(selectedItem.initial_match)'), frozen);
    const rematches = result.requests.slice(from).filter(r => r.path === '/api/match' && r.status === 200).length;
    assert.equal(rematches, 3);
    from = result.requests.length;
    await fill('#manual-search-input', '核对配电箱');
    await click('#manual-search-btn');
    await request(from, '/api/search', 'GET', 200);
    const alternative = await until(() => evaluate(
        '[...document.querySelectorAll("#search-results-tbody tr")].findIndex(tr=>tr.cells[0]?.textContent==="CDP-B")+1'
    ), 'alternate material in global search');
    await click('#search-results-tbody tr:nth-child(' + alternative + ') button');
    assert.match(await text('#current-review-binding'), /CDP-B/);
    assert.equal(await text('#initial-review-binding'), first);
    const prior = await evaluate('selectedItem.bindings.map(b=>b.code)');
    assert.deepEqual(prior, ['CDP-B']);
    const autosave = result.requests.length;
    await evaluate('saveDraft()', true);
    await request(autosave, '/api/session/draft', 'POST', 200);
    await openWorkbench(true);
    await summary(63, 100, null);
    assert.deepEqual(await evaluate('items.find(i=>i.source_row_id===' + JSON.stringify(id) + ').bindings.map(b=>b.code)'), prior);
    assert.equal(await evaluate('JSON.stringify(items.find(i=>i.source_row_id===' + JSON.stringify(id) + ').initial_match)'), frozen);
    result.checks.push({name: 'single and batch rematch, changed current binding, draft round trip', rematches, current: prior});

    await group('B');
    await selectFirst();
    assert.match(await text('#review-differences'), /优先核对评估不可用/);
    from = result.requests.length;
    await click('#reject-match-btn');
    await request(from, '/api/binding/reject', 'POST', 200);
    await idle();
    assert.equal(await text('#review-status'), '拒绝');
    assert.equal(await text('#bound-materials-count'), '0');
    await summary(63, 100, null);
    result.checks.push({name: 'native reject preserves frozen first-match totals'});

    await seedLegacyDraft(true);
    assert.match(await text('#initial-prefilled-count'), /（—）/);
    assert.equal(await text('#initial-summary-message'), '已知 1 条 · 未记录 1 条');
    await seedLegacyDraft(false);
    assert.equal(await text('#initial-summary-message'), '首次统计未记录');
    assert.equal(await visible('#initial-prefilled-count'), false);
    result.checks.push({name: 'legacy and partial recovery via authoritative annotate'});

    await upload('native-bom.xlsx', 1);
    await summary(1, 1, 1);
    assert.equal(await text('#initial-source-counts'), '组合匹配 1 条');
    await selectFirst();
    assert.equal(await text('#bound-materials-count'), '2');
    result.checks.push({name: 'native BOM v1', components: 2});

    await upload('keyword-1060.xlsx', 1060);
    await summary(510, 1060, 0);
    for (const [tier, expected] of Object.entries({A: 34, B: 205, C: 344, D: 477})) {
        await group(tier);
        await count(expected);
        await group(tier);
    }
    result.checks.push({name: 'keyword-scale workbench', prefilled: 510, total: 1060, counts: [34, 205, 344, 477]});
    await send('Emulation.setDeviceMetricsOverride', {width: 1024, height: 900, deviceScaleFactor: 1, mobile: false});
    result.checks.push({name: 'narrow desktop layout', ...await screenshot('workbench-1024', 'light')});
    await click('#themeToggleBtn');
    result.checks.push({name: 'narrow desktop dark layout', ...await screenshot('workbench-1024-dark', 'dark')});
    await send('Emulation.clearDeviceMetricsOverride');

    // The parser rejects empty workbooks. Exercise the empty completed DTO at its browser entry point.
    await evaluate('processUploadResult([], "empty-DTO.xlsx")');
    assert.equal(await text('#initial-summary-message'), '无可匹配物料');
    assert(await evaluate('document.querySelector("#export-btn").disabled'));
    assert.equal(await visible('#initial-prefilled-count'), false);
    from = result.requests.length;
    await file('invalid.xlsx');
    const failure = await request(from, '/api/upload', 'POST');
    assert(failure.status >= 400);
    await idle();
    assert.equal(await visible('#initial-overview'), false);
    from = result.requests.length;
    await file('background-failure.xlsx');
    await request(from, '/api/upload', 'POST', 200);
    await until(() => result.dialogs.some(message => message.includes('隔离后台失败验收')), 'failed task');
    await idle();
    assert.equal(await visible('#initial-overview'), false);
    result.checks.push({name: 'zero DTO, rejected XLSX, failed async task', precheckStatus: failure.status});
}

async function main() {
    const port = await until(async () => {
        try { return (await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split(/\r?\n/)[0]; }
        catch (err) { if (err.code !== 'ENOENT') throw err; return null; }
    }, 'Chrome');
    const debuggerAddress = 'http://127.0.0.1:' + port;
    result.engine = (await (await fetch(debuggerAddress + '/json/version')).json()).Browser;
    socket = new WebSocket((await (await fetch(debuggerAddress + '/json/list')).json()).find(p => p.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
        socket.addEventListener('open', resolve, {once: true});
        socket.addEventListener('error', reject, {once: true});
    });
    socket.addEventListener('message', async ({data}) => {
        const message = JSON.parse(data);
        const {id, method, params} = message;
        if (pending.has(id)) {
            const {resolve, reject, method: call} = pending.get(id);
            pending.delete(id);
            if (message.error) reject(new Error(call + ' ' + message.error.message));
            else resolve(message.result);
            return;
        }
        if (method === 'Page.javascriptDialogOpening') {
            openDialog = true;
            result.dialogs.push(params.message);
            try {
                await send('Page.handleJavaScriptDialog', {accept: !params.message.includes('未保存的编辑草稿') || approveDraft});
            } finally {
                openDialog = false;
            }
        } else if (method === 'Network.requestWillBeSent' && params.request.url.startsWith(origin + '/api/')) {
            const path = new URL(params.request.url).pathname;
            const entry = {path, method: params.request.method};
            if (path === '/api/export') {
                const sent = JSON.parse(params.request.postData || '{}').items || [];
                const safety = sent.filter(row => row.original_code?.includes('safety_missing'));
                entry.firstSafetyRows = safety.length;
                entry.emptySafety = safety.every(row => !row.bindings?.length && !row.matched_code);
            }
            requests.set(params.requestId, entry);
        } else if (method === 'Network.responseReceived' && requests.has(params.requestId)) {
            result.requests.push({...requests.get(params.requestId), status: params.response.status});
            requests.delete(params.requestId);
        } else if (method === 'Runtime.exceptionThrown') {
            result.exceptions.push(params.exceptionDetails.exception?.description || params.exceptionDetails.text);
        }
    });
    for (const name of ['Page', 'DOM', 'Runtime']) await send(name + '.enable');
    await send('Network.enable', {maxPostDataSize: 1024 * 1024});
    await send('Network.setBlockedURLs', {urls: ['https://*']});
    await send('Page.addScriptToEvaluateOnNewDocument', {source: 'if(location.origin===' + JSON.stringify(origin)
        + '){localStorage.setItem("token",' + JSON.stringify(process.env.V021_M1_TOKEN) + ')}'});
    await copyFile(join(root, 'fixed-100.xlsx'), join(root, 'fixed-100-light.xlsx'));
    await fixed('dark');
    await fixed('light');
    await advanced();
    assert.deepEqual(result.exceptions, []);
}

try {
    await main();
    result.exit_code = 0;
} catch (error) {
    result.exit_code = 1;
    result.failure = error.message;
    console.error(error.stack);
    process.exitCode = 1;
} finally {
    await writeFile(join(out, 'result.json'), JSON.stringify(result, null, 2) + '\n');
    socket?.close();
    browser.kill();
}
