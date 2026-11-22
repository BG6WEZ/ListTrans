/* Coordinator counterexample: execute the real three frontend functions.
 * fetch is an IPC transport to Python TestClient, not a simulated database.
 * No application implementation or previous evidence is modified.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');
const readline = require('readline');
const root = path.resolve(__dirname, '../../../..');
const source = fs.readFileSync(path.join(root, 'web/app.js'), 'utf8');
const item = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
function extract(marker) {
    const start = source.indexOf(marker);
    if (start < 0) throw Error('Missing function: ' + marker);
    const brace = source.indexOf('{', start);
    let depth = 0;
    for (let i = brace; i < source.length; i++) {
        if (source[i] === '{') depth++;
        if (source[i] === '}' && --depth === 0) return source.slice(start, i + 1);
    }
    throw Error('Unbalanced function: ' + marker);
}
const pending = new Map();
const calls = [];
let requestId = 0;
let scenario = '';
const input = readline.createInterface({ input: process.stdin });
input.on('line', line => {
    const response = JSON.parse(line);
    const task = pending.get(response.id);
    if (!task) throw Error('Unexpected response');
    pending.delete(response.id);
    if (response.network_error) task.reject(TypeError('injected_response_lost_after_commit'));
    else task.resolve({ ok: response.status >= 200 && response.status < 300,
                        status: response.status, json: async () => response.body });
});
const sandbox = {
    crypto: { randomUUID: () => crypto.randomUUID() },
    console: { info() {}, warn() {}, error() {} },
    backendUrl: '', currentWorkSessionId: '', activeSessionId: '',
    fetch: (url, options) => new Promise((resolve, reject) => {
        const id = ++requestId;
        const body = JSON.parse(options.body);
        calls.push({ id, scenario, url, body });
        pending.set(id, { resolve, reject });
        process.stdout.write(JSON.stringify({ type: 'request', id, scenario, url, body }) + '\n');
    })
};
vm.createContext(sandbox);
vm.runInContext(['function generateClientEventId', 'async function reportFlywheelAction',
                 'async function submitFeedbackSample'].map(extract).join('\n'), sandbox);
async function settle() {
    const deadline = Date.now() + 15000;
    while (pending.size) {
        if (Date.now() > deadline) throw Error('IPC timeout');
        await new Promise(resolve => setTimeout(resolve, 5));
    }
    await new Promise(resolve => setTimeout(resolve, 20));
}
(async () => {
    const result = {};
    scenario = 'flywheel_response_lost';
    const flyItem = JSON.parse(JSON.stringify(item));
    await sandbox.reportFlywheelAction(flyItem, 'PICK_CANDIDATE');
    result.flywheel_returned_before_response = pending.size > 0;
    await settle();
    result.flywheel_calls_after_first_failure = calls.filter(c => c.scenario === scenario).length;
    await sandbox.reportFlywheelAction(flyItem, 'PICK_CANDIDATE');
    await settle();
    scenario = 'feedback_json_sync_500';
    const fbItem = JSON.parse(JSON.stringify(item));
    let threw = false;
    try { await sandbox.submitFeedbackSample(fbItem, '', item.bindings[0].code, false); }
    catch (_) { threw = true; }
    result.feedback_500_thrown_to_caller = threw;
    await settle();
    result.feedback_calls_after_first_failure = calls.filter(c => c.scenario === scenario).length;
    await sandbox.submitFeedbackSample(fbItem, '', item.bindings[0].code, false);
    await settle();
    result.calls = calls;
    result.app_js_sha256 = crypto.createHash('sha256').update(source).digest('hex');
    process.stdout.write(JSON.stringify({ type: 'result', result }) + '\n');
    input.close();
})().catch(error => { process.stderr.write(String(error)); process.exit(1); });
