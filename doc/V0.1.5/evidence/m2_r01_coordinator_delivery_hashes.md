# V015-M2 R01 统筹放行交付哈希

- 日期：2026-11-14（Asia/Shanghai）；基线：`4cbbbe5bd6e1bee21ad908ed1f631a919920ea8f`；未提交、未push。
- 本轮精确范围：下表12个文件及本清单自身，共13个文件；本清单不记录自指哈希。
- 只维护管理文档并新增统筹核验证据，未修改业务/测试实现、旧开发/审核小结、旧实测证据、原交付清单或金标。
- 抽查时R01交付清单35/38匹配，差异仅为审核后维护的台账和两个README；其余稳定内容与开发小结匹配，历史38/38记录保留。
- 本轮V8解析当前web/app.js语法PASS，未运行浏览器DOM/HTTP/模型/全量；146P等运行结果引用独立审核。业务快照与审核末次一致，两个非隔离金标明细均不存在。
- M2正式放行，仅下发M3 R01；UI-M5-01与兼容/依赖项由M5最迟版本终审前收口。M4～M5待下发，后续版本锁定，版本终审未开展。

| SHA256 | 文件 |
|---|---|
| c8dee4f976cade0809f8c883bcbc5e382f15d913689e9da1832da86085b0839d | doc/README.md |
| 86e4e4d1760da0d724a154e3aab8b03b18a5dbe46060aa4faeb91eafbd609116 | doc/00_项目管理/进度总表.md |
| 11ae35fc7660706a8e5c5b0a3c9c7c40223a655fc5e98ca1e71e98349bf078a6 | doc/00_项目管理/版本路线图.md |
| e018009a6eef10370a15240e789f69c680522b7a5c67be2ee66932606f2ccf24 | doc/00_项目管理/问题与版本归属总台账.md |
| fc529cd960956941deee55063261216323c8ab7ad68aae8a1e0dcd33331163f2 | doc/V0.1.5/01_实施方案与任务包.md |
| b237b39bc96fd76b5332a2f09075b79383ef70a1fe19c7a61b22ecb212a2ae47 | doc/V0.1.5/02_任务台账.md |
| 1b3af4a557d896e5c838ad6477e617c0941704378448c35beba2ca0c93e9805f | doc/V0.1.5/03_终审与版本准入.md |
| a28b18b80304ec72c32274f552419dd56800495ebdbfe9020c5211f9da4397b9 | doc/V0.1.5/rounds/V015-M2_R01_统筹裁决.md |
| a132cc65769af869b818da5cfc44217fd2c58310631c680be539464b0f5b81c5 | doc/V0.1.5/evidence/README.md |
| b77ad30e28b2714d63476e4d6db94116d403d8920d3ae7f9d242f8957d4f9995 | doc/V0.1.5/rounds/README.md |
| cd045e85f2041a528139fca24d9326b5f8ef2d4d8dfec1c8957031a9020a9e29 | doc/V0.1.5/evidence/m2_r01_coordinator_check.json |
| 2923962d84a5e6eb975767fa288363df60d093e8c4b29aa6663be687a3259099 | doc/V0.1.5/evidence/m2_r01_coordinator_js_syntax.json |
