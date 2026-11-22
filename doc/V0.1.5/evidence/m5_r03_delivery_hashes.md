# V015-M5 R03 交付文件清单与精确 SHA256

- 采集时间：2026-11-16（R03 完工交付前，开发 AI，Python `hashlib.sha256` 实算并程序断言 64 位小写十六进制匹配）
- Git 基线：`e964fb16848de9dece8e729e801b61c7d81dd341`（未执行任何 `git commit/push/tag/checkout/reset`，全部成果仍为未提交工作区）
- 生产 Docker 镜像：`listtrans:m5-r03-prod`（`sha256:69c5a4a6cf1ba783c24399adc09b38432510efbddebecdc0fa8d7a3e8782b6b7`，11 层；R02 镜像 `listtrans:m5-r02-prod` `sha256:80d2583b5b6fbd702b0f48aea13854174e66cf2e59ec70c0ef2bdb8f47749a5d` 未覆写）
- 改前备件目录：`.local-archive/m5_r03_pre_backup/`（5 文件，哈希见 [R03 改前基线清单](m5_r03_pre_baseline_hashes.md)）；R02 备件 `.local-archive/m5_r02_pre_backup/` 未触碰
- 外层业务快照前后恒定：`5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e`；`app/materials.db` 恒定 `9b7be8c2bd8f420f25b12a5394e8470cc2910dd363da875eafd04c1385b78927`
- 本清单不含自身哈希。

## 实现与测试（3 份改动源文件 + 1 份新增专项测试 + 4 份零改动对照）

| 文件 | 大小（字节） | R03 当前 SHA256 | R03 改前基线 SHA256 | 状态 |
|---|---:|---|---|---|
| `app/server.py` | 161123 | `2b0e418504d70eaed2aaa39c06d8a7ab108a5edcb8e0e7f8cbf8cc23d9b4ca1c` | `5f478a5d730555e91ecb5eceafdd923eac4ee46208014dd50b8682dadf053ff9` | 已改（A-M5-01/02：ImportCommitArbiter 与七步编排/回滚） |
| `app/db_manager.py` | 64279 | `6c76cee35baedcb5eeb4a816189d3747286a0fc5b31644a98e1fd4293b74d53b` | `d50ae4847fee3b4875318c452faac72b213885235d6bcb51783ff3b85c124284` | 已改（A-M5-01：append staging、保留旧代与 reverse_retained_materials_swap） |
| `app/vector_index.py` | 32663 | `3e4df3066748906da7fe0f1ca5ff902fb2cc195fa09d5994d672c835dc03900a` | `30de26fbd63c0285a84839698da9a6ac8ca8599044cdac26e845753cc2a835f4` | 已改（A-M5-01：整套备份回滚、双失败写阻断 CacheRollbackError） |
| `tests/test_m5_r03_commit_arbitration.py` | 44018 | `bed12a55a5cb25206cc82a3bf8df723319606688e049c246221c432a55b7bd7f` | （R03 新增） | 新增（24 个确定性用例：Probe A1/A2/B/C/C-double/D 及 append/replace 对照） |
| `tests/test_m5_file_boundary_and_compat.py` | 70405 | `85570f190d8dc0a26215fe65f69152c76d22fc2061fa0a045a970de97dd9825a` | `85570f190d8dc0a26215fe65f69152c76d22fc2061fa0a045a970de97dd9825a` | 零改动（保留 R02 27 个用例与授权 FM5 断言） |
| `app/excel_boundary.py` | 31191 | `a37f1c98167b308fae218685b5da93bdba57cb59b7204056cf3436c0d86fff34` | `a37f1c98167b308fae218685b5da93bdba57cb59b7204056cf3436c0d86fff34` | 零改动（当前=R02 基线） |
| `app/auth_manager.py` | 49239 | `0f621d06bc78d4abbc569b221a354b391322dee285ab02ed2091e65ca47472d8` | `0f621d06bc78d4abbc569b221a354b391322dee285ab02ed2091e65ca47472d8` | 零改动（当前=R02 基线） |
| `tests/conftest.py` | 12485 | `207a7cf79afd6335170cc270e42302303057b59c1de88c9077711f96466b4033` | `207a7cf79afd6335170cc270e42302303057b59c1de88c9077711f96466b4033` | 零改动（当前=R02 基线） |

## 轮次小结与跨窗口交接文档

| 文件 | 大小（字节） | SHA256 |
|---|---:|---|
| `doc/V0.1.5/rounds/V015-M5_R03_开发小结.md` | 15813 | `f1bda98498137d622975aea9bb88092db48a31b901beba10295d58bac53ddfe2` |
| `doc/V0.1.5/rounds/V015-M5_R03_开发交接说明.md` | 11538 | `13a05dcfdb3e1137aa3f21941e33349b5d508eb8d49775a7a86e02978a86414e` |

## R03 开发侧证据与脚本（`doc/V0.1.5/evidence/`，全部为新建 `m5_r03_*`，R01/R02 证据未覆写）

| 文件 | 大小（字节） | SHA256 |
|---|---:|---|
| `m5_r03_3rule_gate.log` | 1368 | `3af637f2547efcf9b233d31c9c803b84f0d2553421c9d104bd79de92db1d649f` |
| `m5_r03_3rule_gate.py` | 9689 | `1d0c58f2a38ad7b96a043940ef5bb4d41530f14edde85e741edce33a092149a6` |
| `m5_r03_browser_e2e.log` | 1369 | `6e1699b6b458274ea9f011e99afc1e30a9f250a052c2d1d3b5c50f9460f78403` |
| `m5_r03_browser_e2e.py` | 46818 | `7c60827e7a4b0279666e144873dba65b512217b35c7d78cd75ceba04f805771f` |
| `m5_r03_browser_e2e_report.json` | 11568 | `2526dd4dfca71372557ff74f7ed9b0a4a5a916c6304ab465049838081ffddef9` |
| `m5_r03_full_pytest.log` | 2557 | `94e487f8746a6dac798251dbc0170be9bdee648c3feb97ead374cbb7a28f45de` |
| `m5_r03_gate_reports.json` | 52080 | `26f717c304735dabd129b0d1fadfe6fa5e6165017311e85e9b424bd72a8aa1d7` |
| `m5_r03_isolation_verify_before.json` | 12188 | `dad70b2d948874427cc12bf38c416253d62810e04a5199c65b739fe74daf944d` |
| `m5_r03_m4_m5_regression_pytest.log` | 8349 | `6db312d63d86cffddca84122e45656935c09493a25799217ac46817f080ec68f` |
| `m5_r03_outer_snapshot_after.json` | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| `m5_r03_outer_snapshot_before.json` | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| `m5_r03_pre_baseline_hashes.md` | 10630 | `a0d676113b46bf942c6da106288d2b15f3bcd91ddcc41d6dbaf714f81622e0ad` |
| `m5_r03_prod_image_build.log` | 25150 | `7e27b501997812d5fa6e7b962bd6241a6febedbcd79423b29b074a88f5de6092` |
| `m5_r03_prod_image_verify.json` | 91246 | `d7f72bfb9cf8b068c9eef9ffe6db0326704df4dfbc6159672cf8eb0affaf9aef` |
| `m5_r03_prod_image_verify.log` | 3450 | `998cff8bf587f3ab55f57550d026901e726cfb7a2c066791ba25186de448f093` |
| `m5_r03_prod_image_verify.py` | 64698 | `dabdfe19a3beb8242793914371d3a8d52ca4d77b2e207b9e4e56c9d7c6de8458` |
| `m5_r03_ui_export_done.png` | 179357 | `710ead4ca2e1b499c65224360d2876881fe751cec427991e97d268248f12129f` |
| `m5_r03_ui_rejected_restored.png` | 92713 | `4b811f087667df449b383521631544374e3ced374d9efb3c555a36f7dac8556d` |
| `m5_r03_ui_workbench.png` | 179014 | `dfe564726791a8265fc7052bf077554658db05a2961f5c520d74d91969e3a7b9` |
| `m5_r03_venv_verify_after.json` | 12188 | `ac0a22e03696f0533f859288df09df374b0e2844d783b7f0d432612ca4948f2d` |
| `m5_r03_venv_verify_after.log` | 680 | `4487bea84b39673fd3883556ac9f75a4dc9e29fc31f8cb47f4f41e5a9c229f0d` |

## 统筹 R03 下发及业务冻结对象对照（非开发产出，列入仅作审计完整）

| 文件 | 大小（字节） | SHA256 |
|---|---:|---|
| `doc/V0.1.5/evidence/m5_r03_coordinator_before.json` | 17412 | `91db60e72b6a59ce9ef0d6b0814f12333dba2180ceea088f76c49909a0058def` |
| `doc/V0.1.5/evidence/m5_r03_coordinator_check.py` | 5794 | `3b60a0837b52a292c46d0038b70c52ee7682f986ff5741f7a3ba5b612a8f8a37` |
| `doc/V0.1.5/evidence/m5_r03_coordinator_closure.json` | 700 | `20fdbe4e65daec98fb1f4d3cc45425f22ba65d61c8469ff3960706eca477e180` |
| `doc/V0.1.5/evidence/m5_r03_coordinator_delivery_hashes.md` | 1731 | `b2bc03260a8129263ee510e9137dc626790a556888f2c07c66c522309c66e9e2` |
| `app/materials.db`（业务冻结库） | 19759104 | `9b7be8c2bd8f420f25b12a5394e8470cc2910dd363da875eafd04c1385b78927` |
