/*
 * V016-M0 R02 审核自有探针：沿用 R01 前端事件 ID / event_seq / 重试语义探针，在 R02 代码上复验
 * 原则：不修改 web/app.js；从真实文件按括号配平提取三个函数，在 vm 沙箱中运行，
 * fetch/crypto/console 全部 mock，无真实网络、无业务写入。
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..', '..', '..', '..');
const APP_JS = path.join(ROOT, 'web', 'app.js');
const src = fs.readFileSync(APP_JS, 'utf8');
const sha256 = crypto.createHash('sha256').update(src).digest('hex');
const stat = fs.statSync(APP_JS);

function extractFunction(source, marker) {
    const start = source.indexOf(marker);
    if (start < 0) throw new Error('marker not found: ' + marker);
    const braceStart = source.indexOf('{', start);
    let depth = 0;
    for (let i = braceStart; i < source.length; i++) {
        const ch = source[i];
        if (ch === '{') depth++;
        else if (ch === '}') {
            depth--;
            if (depth === 0) return source.slice(start, i + 1);
        }
    }
    throw new Error('unbalanced function: ' + marker);
}

const fnGenId = extractFunction(src, 'function generateClientEventId');
const fnFly = extractFunction(src, 'async function reportFlywheelAction');
const fnFb = extractFunction(src, 'async function submitFeedbackSample');

// 记录行号（1-based）
function lineOf(marker) {
    return src.slice(0, src.indexOf(marker)).split('\n').length;
}
const lineMap = {
    generateClientEventId: lineOf('function generateClientEventId'),
    reportFlywheelAction: lineOf('async function reportFlywheelAction'),
    submitFeedbackSample: lineOf('async function submitFeedbackSample'),
};

function makeSandbox() {
    const calls = [];
    const sandbox = {
        console: { log() {}, info() {}, warn() {}, error() {} },
        crypto: { randomUUID: () => crypto.randomUUID() },
        backendUrl: 'http://audit-stub.invalid',
        currentWorkSessionId: '',
        activeSessionId: '',
        fetch: async (url, opts) => {
            const mode = sandbox.__mode;
            calls.push({
                url,
                client_event_id: JSON.parse(opts.body).client_event_id,
                event_seq: JSON.parse(opts.body).event_seq,
                mode,
            });
            if (mode === 'network_error') {
                throw new TypeError('injected_network_failure');
            }
            return { ok: mode === 'ok', status: mode === 'http_409' ? 409 : (mode === 'http_500' ? 500 : 200) };
        },
        __mode: 'ok',
    };
    vm.createContext(sandbox);
    vm.runInContext(`${fnGenId}\n${fnFly}\n${fnFb}`, sandbox);
    return { sandbox, calls };
}

function baseItem() {
    return {
        feedback_eligible: true,
        document_id: 'doc_audit',
        sheet_id: 'sht_audit',
        source_row_id: 'row_audit',
        work_session_id: 'ws_audit',
        original_row_idx: 7,
        original_name: '排风机',
        original_spec: '风量:1000m3/h',
        bindings: [{ code: '3.50.29.1369', name: '离心排风机', specification: 's', qty: 1 }],
        candidates: [{ code: '3.50.29.1369', name: '离心排风机', score: 0.91 }],
        recommendation_snapshot_id: 'snap_audit',
        professional: '暖通',
    };
}

async function runFlywheel(mode) {
    const { sandbox, calls } = makeSandbox();
    sandbox.__mode = mode;
    const item = baseItem();
    const errors = [];
    for (let i = 0; i < 2; i++) {
        try {
            await sandbox.reportFlywheelAction(item, 'bind_confirm');
        } catch (e) {
            errors.push(String(e));
        }
    }
    return {
        mode,
        item_event_seq_after: item._event_seq,
        post_attempts: calls.length,
        event_ids: calls.map(c => c.client_event_id),
        event_seqs: calls.map(c => c.event_seq),
        ids_unique: new Set(calls.map(c => c.client_event_id)).size === calls.length,
        ids_same_as_retry_key: calls.length === 2 ? calls[0].client_event_id === calls[1].client_event_id : null,
        thrown_errors: errors,
    };
}

async function runFeedback(mode) {
    const { sandbox, calls } = makeSandbox();
    sandbox.__mode = mode;
    const item = baseItem();
    const errors = [];
    for (let i = 0; i < 2; i++) {
        try {
            await sandbox.submitFeedbackSample(item, 'A', '3.50.29.1369', false);
        } catch (e) {
            errors.push(String(e));
        }
    }
    return {
        mode,
        item_feedback_seq_after: item._feedback_seq,
        post_attempts: calls.length,
        event_ids: calls.map(c => c.client_event_id),
        event_seqs: calls.map(c => c.event_seq),
        ids_unique: new Set(calls.map(c => c.client_event_id)).size === calls.length,
        ids_same_as_retry_key: calls.length === 2 ? calls[0].client_event_id === calls[1].client_event_id : null,
        thrown_errors: errors,
    };
}

(async () => {
    const modes = ['network_error', 'http_409', 'http_500', 'ok'];
    const flywheel = [];
    const feedback = [];
    for (const m of modes) {
        flywheel.push(await runFlywheel(m));
        feedback.push(await runFeedback(m));
    }
    const report = {
        probe: 'frontend_event_id_seq_retry_semantics',
        app_js: { path: 'web/app.js', bytes: stat.size, sha256 },
        extracted_lines: lineMap,
        semantics_assertions: {
            every_call_generates_new_uuid: flywheel.every(r => r.ids_unique) && feedback.every(r => r.ids_unique),
            seq_increments_before_response_and_is_consumed_on_failure:
                flywheel.slice(0, 3).every(r => r.item_event_seq_after === 2 && r.event_seqs.join(',') === '1,2')
                && feedback.slice(0, 3).every(r => r.item_feedback_seq_after === 2 && r.event_seqs.join(',') === '1,2'),
            no_in_built_retry_same_event_id: flywheel.every(r => r.ids_same_as_retry_key === false)
                && feedback.every(r => r.ids_same_as_retry_key === false),
            failures_swallowed_no_throw: flywheel.every(r => r.thrown_errors.length === 0)
                && feedback.every(r => r.thrown_errors.length === 0),
        },
        flywheel,
        feedback,
    };
    const out = path.join(__dirname, 'frontend_probe_report.json');
    fs.writeFileSync(out, JSON.stringify(report, null, 2), 'utf8');
    console.log('REPORT=' + out);
    console.log(JSON.stringify(report.semantics_assertions, null, 2));
})().catch(e => { console.error(e); process.exit(1); });
