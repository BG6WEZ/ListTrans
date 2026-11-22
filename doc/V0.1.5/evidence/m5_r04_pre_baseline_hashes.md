# V015-M5 R04 改动前基线哈希与备份清单

- 采集时间：2026-11-16（R04 开工前，开发 AI，程序采集）
- Git HEAD：`e964fb16848de9dece8e729e801b61c7d81dd341`
- 前置 `tests/verify_isolation.py --out doc/V0.1.5/evidence/m5_r04_isolation_verify_before.json`：verdict=PASS / exit_code=0 / business_hash_unchanged=true / hard_errors=[] / missing_deps=[]
- 前置外层快照 `doc/V0.1.5/evidence/m5_r04_outer_snapshot_before.json`：`5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e`
- R04 改前备份目录：`.local-archive/m5_r04_pre_backup/`（5 个文件，工作区与备份双份一致性由程序校验）
- R01/R02/R03 既有证据及 `.local-archive/m5_r01~03_pre_backup/` 保持未触碰

## A. R04 拟改/回归相关文件基线（工作区 vs 改前备份）

| 文件路径 | 字节大小 | SHA256 |
|---|---:|---|
| app/server.py | 161123 | `2b0e418504d70eaed2aaa39c06d8a7ab108a5edcb8e0e7f8cbf8cc23d9b4ca1c` |
| .local-archive/m5_r04_pre_backup/server.py | 161123 | `2b0e418504d70eaed2aaa39c06d8a7ab108a5edcb8e0e7f8cbf8cc23d9b4ca1c` |
| app/db_manager.py | 64279 | `6c76cee35baedcb5eeb4a816189d3747286a0fc5b31644a98e1fd4293b74d53b` |
| .local-archive/m5_r04_pre_backup/db_manager.py | 64279 | `6c76cee35baedcb5eeb4a816189d3747286a0fc5b31644a98e1fd4293b74d53b` |
| app/vector_index.py | 32663 | `3e4df3066748906da7fe0f1ca5ff902fb2cc195fa09d5994d672c835dc03900a` |
| .local-archive/m5_r04_pre_backup/vector_index.py | 32663 | `3e4df3066748906da7fe0f1ca5ff902fb2cc195fa09d5994d672c835dc03900a` |
| tests/test_m5_r03_commit_arbitration.py | 44018 | `bed12a55a5cb25206cc82a3bf8df723319606688e049c246221c432a55b7bd7f` |
| .local-archive/m5_r04_pre_backup/test_m5_r03_commit_arbitration.py | 44018 | `bed12a55a5cb25206cc82a3bf8df723319606688e049c246221c432a55b7bd7f` |
| tests/test_m5_file_boundary_and_compat.py | 70405 | `85570f190d8dc0a26215fe65f69152c76d22fc2061fa0a045a970de97dd9825a` |
| .local-archive/m5_r04_pre_backup/test_m5_file_boundary_and_compat.py | 70405 | `85570f190d8dc0a26215fe65f69152c76d22fc2061fa0a045a970de97dd9825a` |

## B. R03 冻结证据基线（共 33 份，R04 不得覆写）

| 文件路径 | 字节大小 | SHA256 |
|---|---:|---|
| doc/V0.1.5/evidence/m5_r03_3rule_gate.log | 1368 | `3af637f2547efcf9b233d31c9c803b84f0d2553421c9d104bd79de92db1d649f` |
| doc/V0.1.5/evidence/m5_r03_3rule_gate.py | 9689 | `1d0c58f2a38ad7b96a043940ef5bb4d41530f14edde85e741edce33a092149a6` |
| doc/V0.1.5/evidence/m5_r03_browser_e2e.log | 1369 | `6e1699b6b458274ea9f011e99afc1e30a9f250a052c2d1d3b5c50f9460f78403` |
| doc/V0.1.5/evidence/m5_r03_browser_e2e.py | 46818 | `7c60827e7a4b0279666e144873dba65b512217b35c7d78cd75ceba04f805771f` |
| doc/V0.1.5/evidence/m5_r03_browser_e2e_report.json | 11568 | `2526dd4dfca71372557ff74f7ed9b0a4a5a916c6304ab465049838081ffddef9` |
| doc/V0.1.5/evidence/m5_r03_coordinator_before.json | 17412 | `91db60e72b6a59ce9ef0d6b0814f12333dba2180ceea088f76c49909a0058def` |
| doc/V0.1.5/evidence/m5_r03_coordinator_check.py | 5794 | `3b60a0837b52a292c46d0038b70c52ee7682f986ff5741f7a3ba5b612a8f8a37` |
| doc/V0.1.5/evidence/m5_r03_coordinator_closure.json | 700 | `20fdbe4e65daec98fb1f4d3cc45425f22ba65d61c8469ff3960706eca477e180` |
| doc/V0.1.5/evidence/m5_r03_coordinator_delivery_hashes.md | 1731 | `b2bc03260a8129263ee510e9137dc626790a556888f2c07c66c522309c66e9e2` |
| doc/V0.1.5/evidence/m5_r03_coordinator_final_before.json | 14596 | `872a8c4073f075197dca037803c8b1fea044e321e1f17f44e98d1b103e3e22a2` |
| doc/V0.1.5/evidence/m5_r03_coordinator_final_check.py | 10511 | `35b6c45123f16b003ddedc06ec86c42826e26f05f3b813ac5f80b0b6cd3e5309` |
| doc/V0.1.5/evidence/m5_r03_coordinator_final_closure.json | 1040 | `443780767d351c0a25bfc336304a8673a745946a0084f050cdd2bf56fd42036d` |
| doc/V0.1.5/evidence/m5_r03_coordinator_final_delivery_hashes.md | 2211 | `cb441ce2adee18595f4c111ae1ef0266fc708d2b03f4ad18b67b79f9c959b762` |
| doc/V0.1.5/evidence/m5_r03_coordinator_final_probes.json | 2623 | `4be8527140c5b0a4945d5597c293885534611f9ab2d2658e808deb8aee73e158` |
| doc/V0.1.5/evidence/m5_r03_coordinator_final_probes_attempt1.json | 2889 | `db01a2e646f0d368ee9a10ec345d3e47e6f709b26e0050e1c3c1955415050a30` |
| doc/V0.1.5/evidence/m5_r03_coordinator_final_snapshot_after.json | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| doc/V0.1.5/evidence/m5_r03_delivery_hashes.md | 6843 | `67856b06f6865ed1d77c46d2d4896e9425c529056dc43a296dcfad059a0bec0e` |
| doc/V0.1.5/evidence/m5_r03_full_pytest.log | 2557 | `94e487f8746a6dac798251dbc0170be9bdee648c3feb97ead374cbb7a28f45de` |
| doc/V0.1.5/evidence/m5_r03_gate_reports.json | 52080 | `26f717c304735dabd129b0d1fadfe6fa5e6165017311e85e9b424bd72a8aa1d7` |
| doc/V0.1.5/evidence/m5_r03_isolation_verify_before.json | 12188 | `dad70b2d948874427cc12bf38c416253d62810e04a5199c65b739fe74daf944d` |
| doc/V0.1.5/evidence/m5_r03_m4_m5_regression_pytest.log | 8349 | `6db312d63d86cffddca84122e45656935c09493a25799217ac46817f080ec68f` |
| doc/V0.1.5/evidence/m5_r03_outer_snapshot_after.json | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| doc/V0.1.5/evidence/m5_r03_outer_snapshot_before.json | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| doc/V0.1.5/evidence/m5_r03_pre_baseline_hashes.md | 10630 | `a0d676113b46bf942c6da106288d2b15f3bcd91ddcc41d6dbaf714f81622e0ad` |
| doc/V0.1.5/evidence/m5_r03_prod_image_build.log | 25150 | `7e27b501997812d5fa6e7b962bd6241a6febedbcd79423b29b074a88f5de6092` |
| doc/V0.1.5/evidence/m5_r03_prod_image_verify.json | 91246 | `d7f72bfb9cf8b068c9eef9ffe6db0326704df4dfbc6159672cf8eb0affaf9aef` |
| doc/V0.1.5/evidence/m5_r03_prod_image_verify.log | 3450 | `998cff8bf587f3ab55f57550d026901e726cfb7a2c066791ba25186de448f093` |
| doc/V0.1.5/evidence/m5_r03_prod_image_verify.py | 64698 | `dabdfe19a3beb8242793914371d3a8d52ca4d77b2e207b9e4e56c9d7c6de8458` |
| doc/V0.1.5/evidence/m5_r03_ui_export_done.png | 179357 | `710ead4ca2e1b499c65224360d2876881fe751cec427991e97d268248f12129f` |
| doc/V0.1.5/evidence/m5_r03_ui_rejected_restored.png | 92713 | `4b811f087667df449b383521631544374e3ced374d9efb3c555a36f7dac8556d` |
| doc/V0.1.5/evidence/m5_r03_ui_workbench.png | 179014 | `dfe564726791a8265fc7052bf077554658db05a2961f5c520d74d91969e3a7b9` |
| doc/V0.1.5/evidence/m5_r03_venv_verify_after.json | 12188 | `ac0a22e03696f0533f859288df09df374b0e2844d783b7f0d432612ca4948f2d` |
| doc/V0.1.5/evidence/m5_r03_venv_verify_after.log | 680 | `4487bea84b39673fd3883556ac9f75a4dc9e29fc31f8cb47f4f41e5a9c229f0d` |
| doc/V0.1.5/rounds/V015-M5_R03_开发小结.md | 15813 | `f1bda98498137d622975aea9bb88092db48a31b901beba10295d58bac53ddfe2` |
| doc/V0.1.5/rounds/V015-M5_R03_审核小结.md | 11510 | `8a1e722e5865f43f551e84dffdbb179dee2382731cb75181182d51c77d7a908f` |
| doc/V0.1.5/rounds/V015-M5_R03_统筹裁决.md | 7558 | `6c76bc536223324063cd54839e9d1a7a94856f83d891639d2243d8eb90e26dda` |

## C. 业务冻结库基线

| 文件路径 | 字节大小 | SHA256 |
|---|---:|---|
| app/materials.db | 19759104 | `9b7be8c2bd8f420f25b12a5394e8470cc2910dd363da875eafd04c1385b78927` |
