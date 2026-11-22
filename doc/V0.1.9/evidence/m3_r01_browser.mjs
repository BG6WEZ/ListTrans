import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {copyFile, mkdir, readFile, readdir, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {performance} from 'node:perf_hooks';
import {setTimeout as delay} from 'node:timers/promises';

const [chrome, origin, root, output] = process.argv.slice(2);
const downloads = join(root, 'downloads');
const profile = join(root, 'chrome');
await mkdir(downloads);
const browser = spawn(chrome, [
    '--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
    '--disable-background-networking', '--disable-component-update', '--disable-sync',
    '--no-first-run', '--no-proxy-server', '--disable-extensions', '--window-size=1440,1000',
    '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1', 'about:blank'
], {windowsHide: true, stdio: 'ignore'});

async function until(check, label, timeout = 20000) {
    const end = performance.now() + timeout;
    while (performance.now() < end) {
        const result = await check();
        if (result) return result;
        await delay(35);
    }
    throw new Error(`Timed out waiting for ${label}`);
}

class CDP {
    constructor(socket) {
        this.socket = socket;
        this.sequence = 0;
        this.pending = new Map();
        this.handlers = new Map();
        socket.addEventListener('message', ({data}) => {
            const msg = JSON.parse(data);
            if (msg.id && this.pending.has(msg.id)) {
                const {resolve, reject, method} = this.pending.get(msg.id);
                this.pending.delete(msg.id);
                if (msg.error) reject(new Error(`${method}: ${msg.error.code} ${msg.error.message}`));
                else resolve(msg.result);
            }
            this.handlers.get(msg.method)?.(msg.params);
        });
    }
    command(method, params = {}) {
        return new Promise((resolve, reject) => {
            const id = ++this.sequence;
            this.pending.set(id, {resolve, reject, method});
            this.socket.send(JSON.stringify({id, method, params}));
        });
    }
}

const requests = [];
const dialogs = [];
const exceptions = [];
const exportsChecked = [];
let acceptDraft = false;
let dialogOpen = false;
let cdp;
try {
    const port = await until(async () => {
        try {
            return (await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split(/\r?\n/)[0];
        } catch (error) {
            if (error.code !== 'ENOENT') throw error;
            return null;
        }
    }, 'Chrome CDP');
    const version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
    const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    const socket = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
        socket.addEventListener('open', resolve, {once: true});
        socket.addEventListener('error', reject, {once: true});
    });
    cdp = new CDP(socket);
    const send = (name, data) => cdp.command(name, data);
    for (const domain of ['Page', 'DOM', 'Runtime', 'Network']) await send(`${domain}.enable`);
    await send('Network.setBlockedURLs', {urls: ['https://*', 'http://localhost:*']});
    await send('Browser.setDownloadBehavior', {behavior: 'allow', downloadPath: downloads});
    const active = new Map();
    cdp.handlers.set('Network.requestWillBeSent', ({requestId, request}) => {
        if (request.url.startsWith(`${origin}/api/`)) {
            active.set(requestId, {path: new URL(request.url).pathname, method: request.method});
        }
    });
    cdp.handlers.set('Network.responseReceived', ({requestId, response}) => {
        if (active.has(requestId)) requests.push({...active.get(requestId), status: response.status});
    });
    cdp.handlers.set('Page.javascriptDialogOpening', ({message}) => {
        dialogOpen = true;
        dialogs.push(message);
        const accept = message.includes('未保存的编辑草稿') ? acceptDraft :
            !message.includes('是否立即结束当前匹配任务');
        send('Page.handleJavaScriptDialog', {accept}).catch(() => {}).finally(() => {
            dialogOpen = false;
        });
    });
    cdp.handlers.set('Runtime.exceptionThrown', ({exceptionDetails}) => {
        exceptions.push(exceptionDetails.text || 'uncaught browser exception');
    });

    async function readDom(expression) {
        const msg = await send('Runtime.evaluate', {expression, returnByValue: true});
        if (msg.exceptionDetails) throw new Error('DOM inspection failed');
        return msg.result.value;
    }
    async function nodeId(selector, visible) {
        const result = await send('Runtime.evaluate', {expression: `(() => {
            const element = document.querySelector(${JSON.stringify(selector)});
            return element && (!${visible} || (element.getClientRects().length && !element.disabled)) ? element : null;
        })()`});
        if (!result.result.objectId) return null;
        const objectId = result.result.objectId;
        try {
            return (await send('DOM.describeNode', {objectId})).node.backendNodeId;
        } finally {
            await send('Runtime.releaseObject', {objectId}).catch(() => {});
        }
    }
    async function click(selector) {
        const [x, y] = await until(async () => {
            try {
                const backendNodeId = await nodeId(selector, true);
                if (!backendNodeId) return null;
                await send('DOM.scrollIntoViewIfNeeded', {backendNodeId});
                const {model} = await send('DOM.getBoxModel', {backendNodeId});
                const [x1, y1, , , x2, y2] = model.border;
                return [(x1 + x2) / 2, (y1 + y2) / 2];
            } catch (error) {
                if (error.message.includes('-32000')) return null;
                throw error;
            }
        }, selector);
        const position = {x, y, button: 'left', clickCount: 1};
        await send('Input.dispatchMouseEvent', {type: 'mousePressed', ...position});
        await send('Input.dispatchMouseEvent', {type: 'mouseReleased', ...position});
    }
    async function fill(selector, value) {
        await click(selector);
        const key = {key: 'a', code: 'KeyA', modifiers: 2, windowsVirtualKeyCode: 65};
        await send('Input.dispatchKeyEvent', {type: 'keyDown', ...key});
        await send('Input.dispatchKeyEvent', {type: 'keyUp', ...key});
        await send('Input.insertText', {text: String(value)});
    }
    const response = (after, path, method, status) => until(
        () => requests.slice(after).find(v => v.path === path && v.method === method && v.status === status),
        `${method} ${path} ${status}`,
    );
    const textOf = selector => readDom(`document.querySelector(${JSON.stringify(selector)})?.textContent?.trim()`);
    const attr = (selector, name) => readDom(
        `document.querySelector(${JSON.stringify(selector)})?.getAttribute(${JSON.stringify(name)})`,
    );
    const ready = () => until(() => readDom("document.querySelector('#loading-overlay')?.style.display === 'none'"), 'workbench idle');
    async function clickText(selector, token) {
        const rank = await until(() => readDom(
            `Array.from(document.querySelectorAll(${JSON.stringify(selector)}))
                .findIndex(element => element.textContent.includes(${JSON.stringify(token)})) + 1`,
        ), token);
        const child = await readDom(
            `Array.from(document.querySelectorAll(${JSON.stringify(selector)}))[${rank - 1}]?.previousElementSibling !== undefined`,
        );
        assert(child);
        await click(`${selector}:nth-child(${rank})`);
    }
    async function login(prefix) {
        acceptDraft = false;
        await send('Storage.clearDataForOrigin', {origin, storageTypes: 'all'});
        await send('Page.navigate', {url: `${origin}${prefix}/login.html`});
        await until(async () => await readDom('location.pathname') === `${prefix}/login.html`, 'login navigation');
        await fill('#username', 'review-cdp');
        await fill('#password', process.env.M3_CDP_PASSWORD);
        const from = requests.length;
        await click('#submit-btn');
        await response(from, '/api/auth/login', 'POST', 200);
        await response(from, '/api/session/draft', 'GET', 200);
        await until(() => !dialogOpen && readDom(
            "document.readyState === 'complete' && Boolean(document.querySelector('#xlsx-file-input'))",
        ), 'initialized workbench');
        await ready();
    }
    async function upload(name, count) {
        const from = requests.length;
        const backendNodeId = await nodeId('#xlsx-file-input', false);
        assert(backendNodeId);
        await send('DOM.setFileInputFiles', {backendNodeId, files: [join(root, name)]});
        await response(from, '/api/upload', 'POST', 200);
        await until(async () => Number(await textOf('#items-count')) > 0, 'uploaded table');
        await ready();
        await clickText('#prof-tabs .prof-tab', '全部');
        assert.equal(Number(await textOf('#items-count')), count);
    }
    const row = name => clickText('#items-tbody tr', name);
    const candidate = code => clickText('#candidates-container .candidate-card', code);
    async function confirm() {
        const from = requests.length;
        await click('#confirm-match-finished-btn');
        await response(from, '/api/binding/submit', 'POST', 200);
        await ready();
    }
    async function exportSheet(name, bindings) {
        const from = requests.length;
        await click('#export-btn');
        await response(from, '/api/export', 'POST', 200);
        const file = name.replace(/\.xlsx$/, '_已匹配.xlsx');
        await until(async () => (await readdir(downloads)).includes(file), 'downloaded workbook');
        await ready();
        await until(() => dialogs.some(v => v.includes('报表导出成功')), 'export completion dialog');
        exportsChecked.push({file, bindings});
    }

    await login('');
    await upload('native-flow.xlsx', 3);
    console.log('Native upload and login completed');
    await click('[data-filter="auto_accepted"]');
    assert.equal(Number(await textOf('#items-count')), 1);
    await row('复核配电箱');
    assert.equal(await attr('#review-status', 'data-state'), 'auto_accepted');
    await click('[data-filter="needs_parameters"]');
    await row('复核轴流风机');
    assert.equal(await attr('#review-status', 'data-state'), 'needs_parameters');
    assert.match(await textOf('#review-differences'), /待补参数.*防爆/s);
    assert.match(await textOf('#review-differences'), /候选分差/);
    let from = requests.length;
    await click('#reject-match-btn');
    await response(from, '/api/binding/reject', 'POST', 200);
    await ready();
    assert.equal(await attr('#review-status', 'data-state'), 'rejected');
    await click('[data-filter="rejected"]');
    assert.equal(Number(await textOf('#items-count')), 1);
    await click('[data-filter="all"]');
    await row('复核组合配电箱');
    await candidate('CDP-ONE');
    await candidate('CDP-TWO');
    await fill('.bound-material-item:nth-child(1) input[aria-label="绑定数量"]', 3);
    await fill('.bound-material-item:nth-child(2) input[aria-label="绑定比例"]', 2.25);
    await confirm();
    await click('[data-filter="manual_confirmed"]');
    assert.equal(Number(await textOf('#items-count')), 1);
    await row('复核组合配电箱');
    assert.equal(await attr('#review-status', 'data-state'), 'manual_confirmed');
    await click('[data-filter="all"]');
    from = requests.length;
    await click('#export-btn');
    await response(from, '/api/export', 'POST', 422);
    await ready();
    assert(dialogs.some(message => message.includes('人工拒绝')));

    // Edit and revert to the same quantity. A persisted confirmation must stay revoked.
    from = requests.length;
    await fill('.bound-material-item:nth-child(1) input[aria-label="绑定数量"]', 5);
    await fill('.bound-material-item:nth-child(1) input[aria-label="绑定数量"]', 3);
    await response(from, '/api/binding/invalidate', 'POST', 200);
    assert.equal(await attr('#review-status', 'data-state'), 'recommended');
    await click('[data-filter="recommended"]');
    assert.equal(Number(await textOf('#items-count')), 1);
    await click('[data-filter="all"]');
    await confirm();
    await row('复核组合配电箱');
    from = requests.length;
    await click('#open-param-modal-btn');
    await fill('#modal-param-model', 'AL-422');
    await click('#confirm-param-btn');
    await response(from, '/api/rematch', 'POST', 200);
    await ready();
    assert.equal(await attr('#review-status', 'data-state'), 'recommended');
    assert.equal(Number(await textOf('#bound-materials-count')), 0);
    await candidate('CDP-TWO');
    await candidate('CDP-ONE');
    await fill('.bound-material-item:nth-child(1) input[aria-label="绑定比例"]', 2.25);
    await fill('.bound-material-item:nth-child(2) input[aria-label="绑定数量"]', 3);
    await confirm();
    await row('复核轴流风机');
    await candidate('CDP-FAN');
    await confirm();
    await row('复核组合配电箱');
    await candidate('CDP-ONE');
    assert.equal(await attr('#review-status', 'data-state'), 'recommended');
    await candidate('CDP-ONE');
    await fill('.bound-material-item:nth-child(2) input[aria-label="绑定数量"]', 3);
    await confirm();

    from = requests.length;
    await click('#save-trigger');
    await fill('#save-session-name', '隔离真实 DOM 复核');
    await click('#confirm-save-session-btn');
    await response(from, '/api/session/save', 'POST', 200);
    await ready();
    from = requests.length;
    await click('#history-trigger');
    await click('#history-sessions-tbody .btn-restore-sess');
    await response(from, '/api/session/annotate', 'POST', 200);
    await ready();
    await row('复核组合配电箱');
    assert.equal(await attr('#review-status', 'data-state'), 'manual_confirmed');
    assert.match(await textOf('#bound-materials-list'), /CDP-TWO/);
    await exportSheet('native-flow.xlsx', {'CDP-AUTO': 2, 'CDP-ONE': 3, 'CDP-TWO': 4.5, 'CDP-FAN': 2});
    console.log('Native review and XLSX download completed. Waiting for the normal draft timer.');

    // Named restore restarts the existing autosave timer. Let the browser
    // reach it at its native interval, then reload and accept the draft.
    from = requests.length;
    await until(() => requests.slice(from).some(x => x.path === '/api/session/draft' &&
        x.method === 'POST' && x.status === 200), 'native autosave', 195000);
    from = requests.length;
    acceptDraft = true;
    await send('Page.reload', {});
    await response(from, '/api/session/annotate', 'POST', 200);
    await ready();
    await row('复核组合配电箱');
    assert.equal(await attr('#review-status', 'data-state'), 'manual_confirmed');
    assert.equal(await readDom("Array.from(document.querySelectorAll('.bound-material-item input[aria-label=绑定数量]')).map(input => +input.value).sort().join(',')"), '3,4.5');
    const nativeCalls = requests.splice(0);
    const nativeDialogs = dialogs.splice(0);
    const pairs = [];
    for (let i = 1; i <= 3; i++) {
        const pair = {};
        for (const arm of i % 2 ? ['baseline', 'candidate'] : ['candidate', 'baseline']) {
            const name = `pair-${i}-${arm}.xlsx`;
            await copyFile(join(root, 'paired.xlsx'), join(root, name));
            await login(arm === 'baseline' ? '/baseline' : '');
            await upload(name, 2);
            const start = performance.now();
            await row('复核配电箱');        // Inspect the automatic row.
            await row('复核组合配电箱');    // Apply the same correction in each arm.
            await candidate('CDP-ONE');
            const q = arm === 'baseline' ? '.bound-qty-input' : 'input[aria-label="绑定数量"]';
            await fill(q, 3);
            await confirm();
            await exportSheet(name, {'CDP-AUTO': 2, 'CDP-ONE': 3});
            pair[arm] = {reviewed_rows: ['INPUT-A', 'INPUT-B'], corrected_rows: ['INPUT-B'],
                         review_through_download_ms: Math.round(performance.now() - start)};
            console.log(`Pair ${i} ${arm} completed`);
            dialogs.splice(0);
        }
        pairs.push(pair);
    }
    assert.deepEqual(exceptions, []);
    function stats(arm) {
        const rows = pairs.map(pair => pair[arm]);
        const minutes = rows.map(row => row.review_through_download_ms).sort((a, b) => a - b);
        const corrected = rows.reduce((sum, row) => sum + new Set(row.corrected_rows).size, 0);
        const reviewed = rows.reduce((sum, row) => sum + new Set(row.reviewed_rows).size, 0);
        return {corrected, reviewed, percentage: 100 * corrected / reviewed,
                p50_ms: minutes[Math.ceil(minutes.length / 2) - 1], p95_ms: minutes[Math.ceil(.95 * minutes.length) - 1]};
    }
    const baseline = stats('baseline'), candidateArm = stats('candidate');
    await writeFile(output, JSON.stringify({
        browser: version.Browser, transport: 'Chrome CDP and native inputs',
        paired_operator: 'scripted repeatable review actions', pairs, baseline, candidate: candidateArm,
        manual_change_rate_delta_pp: candidateArm.percentage - baseline.percentage,
        native_flow: {five_filters: true, missing_explanation: true, reject_and_export_422: true,
                      rematch_edit_invalidation: true, candidate_edit_invalidation: true,
                      quantity_revert_invalidation: true, named_restore: true, actual_autosave_reload: true,
                      route_trace: nativeCalls, dialog_count: nativeDialogs.length},
        exports: exportsChecked, exit_code: 0
    }, null, 2), {flag: 'wx'});
} finally {
    if (cdp) await Promise.race([cdp.command('Browser.close').catch(() => {}), delay(1000)]);
    if (!browser.killed && browser.exitCode === null) browser.kill();
}
