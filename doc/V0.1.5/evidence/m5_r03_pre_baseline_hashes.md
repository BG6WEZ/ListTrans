# V015-M5 R03 改动前基线哈希与备份清单

- 采集时间：2026-11-16（R03 开工前，开发 AI，程序采集）
- Git HEAD：`e964fb16848de9dece8e729e801b61c7d81dd341`
- 前置 `tests/verify_isolation.py --out doc/V0.1.5/evidence/m5_r03_isolation_verify_before.json`：verdict=PASS / exit_code=0 / business_hash_unchanged=true / hard_errors=[] / missing_deps=[]
- R03 改前备份目录：`.local-archive/m5_r03_pre_backup/`（5 个文件，与工作区双份哈希程序断言一致）
- 全部摘要均由本脚本程序校验为 64 位十六进制且与磁盘字节匹配；R02 清单 62 位抄录勘误不在本轮复发
- R02 改前备份 `.local-archive/m5_r02_pre_backup/` 保持未触碰

## A. R03 拟改文件基线（工作区与改前备份双份，程序断言一致）

| 文件路径 | 字节大小 | SHA256 |
|---|---:|---|
| app/server.py | 154385 | `5f478a5d730555e91ecb5eceafdd923eac4ee46208014dd50b8682dadf053ff9` |
| .local-archive/m5_r03_pre_backup/server.py | 154385 | `5f478a5d730555e91ecb5eceafdd923eac4ee46208014dd50b8682dadf053ff9` |
| app/db_manager.py | 57600 | `d50ae4847fee3b4875318c452faac72b213885235d6bcb51783ff3b85c124284` |
| .local-archive/m5_r03_pre_backup/db_manager.py | 57600 | `d50ae4847fee3b4875318c452faac72b213885235d6bcb51783ff3b85c124284` |
| app/vector_index.py | 25554 | `30de26fbd63c0285a84839698da9a6ac8ca8599044cdac26e845753cc2a835f4` |
| .local-archive/m5_r03_pre_backup/vector_index.py | 25554 | `30de26fbd63c0285a84839698da9a6ac8ca8599044cdac26e845753cc2a835f4` |
| app/excel_boundary.py | 31191 | `a37f1c98167b308fae218685b5da93bdba57cb59b7204056cf3436c0d86fff34` |
| .local-archive/m5_r03_pre_backup/excel_boundary.py | 31191 | `a37f1c98167b308fae218685b5da93bdba57cb59b7204056cf3436c0d86fff34` |
| tests/test_m5_file_boundary_and_compat.py | 70405 | `85570f190d8dc0a26215fe65f69152c76d22fc2061fa0a045a970de97dd9825a` |
| .local-archive/m5_r03_pre_backup/test_m5_file_boundary_and_compat.py | 70405 | `85570f190d8dc0a26215fe65f69152c76d22fc2061fa0a045a970de97dd9825a` |

## B. 关联不改文件基线（回归对照）

| 文件路径 | 字节大小 | SHA256 |
|---|---:|---|
| tests/conftest.py | 12485 | `207a7cf79afd6335170cc270e42302303057b59c1de88c9077711f96466b4033` |
| tests/verify_isolation.py | 14064 | `090ba89a25813f42910bd245b1bd2cd05bf580ffdc888ce3fe19ec523ad1eb62` |
| app/auth_manager.py | 49239 | `0f621d06bc78d4abbc569b221a354b391322dee285ab02ed2091e65ca47472d8` |
| web/index.html | 125941 | `bf6c68b08054c87e24e41cd1fc3ec038c730f9619268f3452e9d70f976ca6906` |
| web/app.js | 180731 | `2a0b197fa13df5e5b183e41b63c7f3f3070089cd74ae61ed0445149d05e2fb00` |
| Dockerfile | 1534 | `bc5e8d54fb8758fd4f0ebdbb7c586d3c6d68cf5386ff570b17b7c7154259413a` |
| requirements.txt | 280 | `6f3b73001ffec927671c5496684f203c3bb6c7682bc4da628238a7f1cb2cc15f` |
| pytest.ini | 435 | `4180e186629fc8daf4eac04b8de15081dd277571253f0733b3e5a7422868d62a` |

## C. R02 证据与轮次文档冻结基线（共 48 份，R03 不得覆写）

| 文件路径 | 字节大小 | SHA256 |
|---|---:|---|
| doc/V0.1.5/evidence/m5_r02_3rule_gate.log | 1360 | `0bdca479492b4ce898608d4d066b2793b4db6b76f6afe776ed40462a513c5d12` |
| doc/V0.1.5/evidence/m5_r02_3rule_gate.py | 9689 | `8e06aba2c0a8bc67ea35a6b347f77be47653c94aa2aa2e1923c6ce5f6c05e3ce` |
| doc/V0.1.5/evidence/m5_r02_audit_3rule.log | 2648 | `628e9388af9109a396df4b7f425c4850c90bd57c1e0aa6c55720bd0a777f8658` |
| doc/V0.1.5/evidence/m5_r02_audit_3rule.py | 597 | `8e3063780335ee94d7e8a28b34af086cd0f185b2b67af68da74796e19e58486b` |
| doc/V0.1.5/evidence/m5_r02_audit_browser.log | 2650 | `2d62156124d430b7c571065c41239656771290f43c63b606e7c6fabbff9dbdef` |
| doc/V0.1.5/evidence/m5_r02_audit_browser_e2e.py | 826 | `fb61b32dc5ada34b2ca2fd77021ac3db79f394b271b93f7bd8007cd417c2c8e5` |
| doc/V0.1.5/evidence/m5_r02_audit_browser_e2e_report.json | 10936 | `498f2bdf086aee2aa6c5a0de9a26d35a2f79c41928e1e268fbae692f108538d1` |
| doc/V0.1.5/evidence/m5_r02_audit_closure.json | 2045 | `a18ee2f26c9d742fa0dbeb5cc36a731f44216340d1ccd45c736386d7731be1dd` |
| doc/V0.1.5/evidence/m5_r02_audit_closure.py | 7095 | `b18e13f701482961c7bc11a6130f34c3084dbc42c93c162fe251d2384c039279` |
| doc/V0.1.5/evidence/m5_r02_audit_delivery_hashes.md | 3418 | `f71dee61b01bb94acd13b4937a27c1632e434264c58abd0f43b82e33a4b4110d` |
| doc/V0.1.5/evidence/m5_r02_audit_docker_check.json | 927 | `eabb1694e5cbc26e90848e180cf8fc5b6b5e77f9c154606a3d3acc81e48252b4` |
| doc/V0.1.5/evidence/m5_r02_audit_docker_check.py | 4879 | `9222b11f73d27c56ed60ace85e3d28d681c9a52a41984880a6ce1c2995e92250` |
| doc/V0.1.5/evidence/m5_r02_audit_full_pytest.log | 66378 | `1008cbb9e782e1866f52ddf608b97e67c79ef9d69f5f9f507f8f470a6e7461cb` |
| doc/V0.1.5/evidence/m5_r02_audit_gate_reports.json | 52080 | `d5748715f7048c7fdeb9981ae4a11991b3de8be5a9a1b64c3d7ad98262dadba0` |
| doc/V0.1.5/evidence/m5_r02_audit_m5_pytest.log | 9434 | `9eb09cd2a4c41f3a3dd314971db80adf68674523b1ddf288498cd70eac67123d` |
| doc/V0.1.5/evidence/m5_r02_audit_probes.json | 6094 | `01fd6b78af3416bb4b38a24a31f884e701869dcfa7fb3b9e444d23e72f8b99eb` |
| doc/V0.1.5/evidence/m5_r02_audit_probes.py | 21111 | `7b2c6a1cf91d5af6513c9cffc10564b31d58e4096330e9bdbbfd37d01743cd94` |
| doc/V0.1.5/evidence/m5_r02_audit_r01_to_r02.diff | 77226 | `5feb534f9028b09131ef4be6fa4c6ac042bf0cbc6455e1160ce5680de8111132` |
| doc/V0.1.5/evidence/m5_r02_audit_snapshot_after.json | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| doc/V0.1.5/evidence/m5_r02_audit_snapshot_before.json | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| doc/V0.1.5/evidence/m5_r02_audit_ui_export_done.png | 179317 | `ec1edf86c82c3d0a5b9db8af6d40c444870e78abfa1c420a003039e6fe34bb63` |
| doc/V0.1.5/evidence/m5_r02_audit_ui_rejected_restored.png | 92713 | `4b811f087667df449b383521631544374e3ced374d9efb3c555a36f7dac8556d` |
| doc/V0.1.5/evidence/m5_r02_audit_ui_workbench.png | 179289 | `6ab6b39c653576bf10739e4a18882cf0332f827cf77b2b5111688ddb1151c891` |
| doc/V0.1.5/evidence/m5_r02_audit_verify_after.json | 12188 | `bf62698f9555b48d697ab4683c9eeaf1a00964f8c8d19df21ca9914b22b5c796` |
| doc/V0.1.5/evidence/m5_r02_audit_verify_before.json | 12188 | `3c6f98d93d3e4a1554650d0ba1af75dde86610c0d662820a7eaaefc4447347e9` |
| doc/V0.1.5/evidence/m5_r02_browser_e2e.py | 45536 | `54b97068e057b4ec5b16ba3cba2040cc366f1ae8bb775eb04e63ff73ca062633` |
| doc/V0.1.5/evidence/m5_r02_browser_e2e_report.json | 10936 | `84ddde9de4ce9ef59a4eafc3740d9da239ace65b9874b7ea0ed337940598d8a9` |
| doc/V0.1.5/evidence/m5_r02_coordinator_handoff_hashes.md | 821 | `b7c05ba0964814c98f78bfe917116c5b1fd49d9f8f7ea68b446d3047cc312390` |
| doc/V0.1.5/evidence/m5_r02_delivery_hashes.md | 5596 | `5fd0fb830ea3385c5cf5fc37bb10bf233506e9e463cca7800ed593e490b7412d` |
| doc/V0.1.5/evidence/m5_r02_full_pytest.log | 33259 | `53a03182a0c6a8ea566da4ca3c9143c741494d5477c81b396f3457bf67d388d0` |
| doc/V0.1.5/evidence/m5_r02_gate_reports.json | 52080 | `540138598be5647b24a9d0b31d4585f9c0460b1c69d43bdc51bba4ddd00b4022` |
| doc/V0.1.5/evidence/m5_r02_m0_m4_regression_pytest.log | 15917 | `892a21f3be37d7d898f4876bfd3498a9b2b2635d2a84994ce47d09bac610f5f2` |
| doc/V0.1.5/evidence/m5_r02_m5_special_pytest.log | 4787 | `dfe0456d722feb7af759c5d1ef43f11087368d560e9778dcef215082d86f9394` |
| doc/V0.1.5/evidence/m5_r02_outer_snapshot_after.json | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| doc/V0.1.5/evidence/m5_r02_outer_snapshot_before.json | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| doc/V0.1.5/evidence/m5_r02_pre_baseline_hashes.md | 2577 | `ebe3aa69db41140b0f9667a51da0e211e67fad336ecb5725453532adeff5a0d0` |
| doc/V0.1.5/evidence/m5_r02_prod_image_build.log | 25382 | `819583ec2c2b753392bbf43b9531e20d7371d90673c94be40b22c1048ea209d4` |
| doc/V0.1.5/evidence/m5_r02_prod_image_verify.json | 90412 | `f295b9e5e058600dd3601bb64036d3d80dcf00e3ed1b568b8e5fc424494c2941` |
| doc/V0.1.5/evidence/m5_r02_prod_image_verify.py | 63429 | `2b2a56574238848a0940b7f2168fd6364ee52be7780eb3aed91c92972776eeab` |
| doc/V0.1.5/evidence/m5_r02_ui_export_done.png | 179317 | `ec1edf86c82c3d0a5b9db8af6d40c444870e78abfa1c420a003039e6fe34bb63` |
| doc/V0.1.5/evidence/m5_r02_ui_rejected_restored.png | 92713 | `4b811f087667df449b383521631544374e3ced374d9efb3c555a36f7dac8556d` |
| doc/V0.1.5/evidence/m5_r02_ui_workbench.png | 178977 | `a39d2ab03b856970e05c3df1db917e4a37c5643adff35163f79377b2b07465ba` |
| doc/V0.1.5/evidence/m5_r02_venv_verify_after.json | 12188 | `5c7538a3159c350e6efea7505fa4516d906d20e03802b57b5cb8ac1469f449e9` |
| doc/V0.1.5/evidence/m5_r02_venv_verify_after.log | 342 | `b5de95bf773d912844532f5119630513e406395a0e4370a7c0e1f07a6bc90b9c` |
| doc/V0.1.5/rounds/V015-M5_R02_开发小结.md | 17271 | `f6c933197a7d494117746a30e4706bb348a8140ff71a6d3b05b936da86d60f00` |
| doc/V0.1.5/rounds/V015-M5_R02_审核小结.md | 11764 | `d75261cd8c0b94700c60f47d97bd131b30f79f3b0490ded8dbda7cbd2164a6d3` |
| doc/V0.1.5/rounds/V015-M5_R02_统筹裁决.md | 9063 | `c92d57faf3e095662f85c6328d35a0a08b1a2b381d6f73ebea38e5a6c697e452` |
| doc/V0.1.5/rounds/V015-M5_R02_统筹接续.md | 1573 | `550cc848ce237fd110b04d2ce5bf3f5a112a03ca45b1841b3543bbe4775c51db` |

## D. 统筹 R03 下发物与前置隔离校验基线

| 文件路径 | 字节大小 | SHA256 |
|---|---:|---|
| doc/V0.1.5/evidence/m5_r03_coordinator_before.json | 17412 | `91db60e72b6a59ce9ef0d6b0814f12333dba2180ceea088f76c49909a0058def` |
| doc/V0.1.5/evidence/m5_r03_coordinator_check.py | 5794 | `3b60a0837b52a292c46d0038b70c52ee7682f986ff5741f7a3ba5b612a8f8a37` |
| doc/V0.1.5/evidence/m5_r03_coordinator_closure.json | 700 | `20fdbe4e65daec98fb1f4d3cc45425f22ba65d61c8469ff3960706eca477e180` |
| doc/V0.1.5/evidence/m5_r03_coordinator_delivery_hashes.md | 1731 | `b2bc03260a8129263ee510e9137dc626790a556888f2c07c66c522309c66e9e2` |
| doc/V0.1.5/evidence/m5_r03_isolation_verify_before.json | 12188 | `dad70b2d948874427cc12bf38c416253d62810e04a5199c65b739fe74daf944d` |

## E. 业务冻结对象（程序断言与历史恒定值一致）

| 文件路径 | 字节大小 | SHA256 |
|---|---:|---|
| app/materials.db | 19759104 | `9b7be8c2bd8f420f25b12a5394e8470cc2910dd363da875eafd04c1385b78927` |
| doc/V0.1.5/evidence/m5_r02_outer_snapshot_before.json | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| doc/V0.1.5/evidence/m5_r02_outer_snapshot_after.json | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
