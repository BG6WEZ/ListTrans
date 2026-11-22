async function priorityScenario(theme) {
    await openWorkbench();
    if (await evaluate('document.documentElement.dataset.theme') !== theme) await click('#themeToggleBtn');
    assert(await evaluate("!!document.querySelector('script[src$=\"app.js?v=v021_m2\"]')"
        + " && !!document.querySelector('link[href$=\"index.css?v=v021_m2\"]')"));
    await upload('priority-mixed-' + theme + '.xlsx', 2);
    let from = result.requests.length;
    await click('#open-priority-review');
    await request(from, '/api/binding/priority', 'POST', 200);
    await until(async () => (await text('#priority-review-rows')).includes('可逐行勾选（1）'), 'eligible rows');
    const panel = '#priority-review';
    const checkbox = panel + ' input[type="checkbox"]';
    assert.match(await text(panel), /仅查看（1）.*清单明确要求的参数缺少目录依据/s);
    assert.match(await text(panel), /专业（目录缺失）/);
    assert.equal(await evaluate('document.querySelectorAll("#priority-review input:checked").length'), 0);
    assert(await evaluate(query(checkbox) + '.disabled'));
    assert(await evaluate(query('#submit-priority-review') + '.disabled'));
    await click('#priority-review-rows section:first-child summary');
    await until(() => evaluate('!' + query(checkbox) + '.disabled'), 'ordinary missing comparison opens');
    assert.match(await text(panel), /清单原文.*AZ-421.*目录规格.*AZ-421/s);
    const image = await send('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
    await writeFile(join(out, 'priority-' + theme + '.png'), Buffer.from(image.data, 'base64'));
    await click(checkbox);
    assert.equal(await text('#submit-priority-review'), '确认所选行（1）');
    await click('#close-priority-review');

    await upload('priority-export-' + theme + '.xlsx', 1);
    await summary(1, 1, 0);
    from = result.requests.length;
    await click('#open-priority-review');
    await request(from, '/api/binding/priority', 'POST', 200);
    await click('#priority-review-rows summary');
    await click(checkbox);
    await click('#submit-priority-review');
    await request(from, '/api/binding/priority/submit', 'POST', 200);
    await until(async () => (await text('#priority-review-status')).includes('已确认 1 行'), 'confirmed result');
    await summary(1, 1, 1);
    assert.match(await text('#priority-review-status'), /已确认 1 行/);
    await click('#close-priority-review');
    await click('#open-priority-review');
    assert.match(await text('#priority-review-status'), /没有可勾选的行/);
    await click('#priority-review-rows article button');
    assert.equal(await visible('#priority-review'), false);
    await send('Page.setDownloadBehavior', {behavior: 'allow', downloadPath: root});
    from = result.requests.length;
    const beforeDialogs = result.dialogs.length;
    await click('#export-btn');
    await request(from, '/api/export', 'POST', 200);
    await until(() => result.dialogs.slice(beforeDialogs).some(message => message.includes('报表导出成功')), 'export dialog');
    await fill('.bound-qty-input', '4');
    await request(from, '/api/binding/invalidate', 'POST', 200);
    await summary(1, 1, 0);
    const afterEdit = result.requests.length;
    await click('#export-btn');
    await request(afterEdit, '/api/export', 'POST', 422);
    await idle();
    result.checks.push({name: 'M2 priority review and export', theme, defaultSelected: 0,
        eligible: 1, viewOnly: 1, requiresExpandedComparison: true, export: 200, editExport: 422});
}
