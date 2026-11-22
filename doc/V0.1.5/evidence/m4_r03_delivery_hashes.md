# V015-M4 R03 开发交付 SHA256 核对清单

- 角色：**开发AI**（无 git commit/push/tag/checkout/reset）
- 基线：`HEAD` = `e964fb16848de9dece8e729e801b61c7d81dd341`
- 业务快照：`5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e`（前/后及相对 R01/R02 `diffs=[]`）
- 本清单按磁盘实算写入，不含本清单自身 SHA256。

## 1. 本轮实际修改文件（5 项：2 份代码/测试 + 3 份共享台账/索引）

| 文件 | 说明 | R03改动前 SHA256 | 当前交付 SHA256 | 大小(B) |
|---|---|---|---|---:|
| `app/auth_manager.py` | A-M4-05 移除Excel同步is_active=1覆写与old_active字段，保留角色变更并发递增token_version及完整回滚 | `4d774c0016e3ad7f91345275a05dbcf61f0fa2e40664da5aba04dc65ee11b711` | `3b8e9f97c12db0092396bb0fc4bee5bf2480759f3413e0ea9e75c30783da8485` | 48200 |
| `tests/test_m4_auth_lifecycle.py` | R03新增test_r03_a_m4_05回归锁定停用同步保留/跨角色撤销/权限门禁/回滚 | `17583fbde0666263fcffd38b3f157b7ff88add535af42e918541192acfff710e` | `c1aa6671bca63247e234e5f3b446e4f1f095864693b2f6bfb481552db92d250b` | 38281 |
| `doc/V0.1.5/02_任务台账.md` | 台账更新V015-M4状态并追加M4 R03开发进展留痕 | `9022899602de95f31604140ff1e431e1e137ec0b42ace33a8a9d5d9888d9ec8f` | `e5a13d4390d04fb2093ee84f13321ada31931dbfb3aec413dda25160d6f815c5` | 47907 |
| `doc/V0.1.5/rounds/README.md` | 轮次索引追加V015-M4 R03开发交付入口 | `2fe5e96159b85fc7d52b03b35309b7699fbc82b592fd4c5e72323c60768ee2de` | `d5e006a3eb0e1b893823972f496fa5d080ddbd83fa5789fdaf9d849a99ba1292` | 8300 |
| `doc/V0.1.5/evidence/README.md` | 证据索引更新当前状态并追加V015-M4(R03)实测清单 | `95e843d0decac986e6fab5bedaeacaf73cc378a0a584332ee4c809cfbf9ff49a` | `7f909225fd962603f0ae49e86e3eca106789ea0993597f78eaa366bd87d3d240` | 29161 |

## 2. R03 备份区内未变更文件（30 项）与范围外保护文件确认

| 文件 | 当前 SHA256 | 大小(B) | 核对状态 |
|---|---|---:|---|
| `.dockerignore` | `4c40a6755581f7b0feb276d27dc25c8b9118304f55df7c47e2edc0f86dd243e1` | 1677 | 与 R02 交付备件一致 |
| `.env.example` | `2cf00504fd013eee4f357d7081625b1d151b59e4bddc621e83a99ebc3742342d` | 934 | 与 R02 交付备件一致 |
| `app/admin_api.py` | `ea4cd0998e6a812a18282004e9d45931b63b079bc081b96924ef3b6f7f2245bc` | 15058 | 与 R02 交付备件一致 |
| `app/auth_middleware.py` | `3c09aa6b339743581d5d6e6c38f2318b737f7caae6e6f18b0cd8b388c206eef3` | 3541 | 与 R02 交付备件一致 |
| `app/flywheel_sandbox.py` | `29f3152a2b0029a014e7b173743433a1e60321abf490305fcfc2a58b650ea843` | 27031 | 与 R02 交付备件一致 |
| `app/llm_rule_engine.py` | `bcf17254b356d87d433536171b5e55c861f23c32aa1e6db4cccb62648398d813` | 81023 | 与 R02 交付备件一致 |
| `app/rule_api.py` | `f83c22b3368e948267ceb0e7b4507457d5b872d4ddcbec6b4bf4259cd0e47c9c` | 6787 | 与 R02 交付备件一致 |
| `app/server.py` | `aaeba6ff6981d4f57b279fe78d70c277a130a11c1e3a8f76a2eb240ada25e891` | 140507 | 与 R02 交付备件一致 |
| `deploy_guide.md` | `c35df24850a940bb13c4e100324495c4ec708b0e544073b5360cb736b335276b` | 6183 | 与 R02 交付备件一致 |
| `docker-compose.yml` | `dc74a02b00e64e598ce675d0a1fe9214ef803d1022551e775d47e4eef557efc7` | 636 | 与 R02 交付备件一致 |
| `Dockerfile` | `bc5e8d54fb8758fd4f0ebdbb7c586d3c6d68cf5386ff570b17b7c7154259413a` | 1534 | 与 R02 交付备件一致 |
| `scripts/_deploy_env.py` | `a9aa895e54885242d53fc1398a95bc91acd28c82062f55ad983eb8770d260f6d` | 3927 | 与 R02 交付备件一致 |
| `scripts/check_cloud_status.py` | `78819b5b8e157b491521a383b9ac2051bfed6c9303b35d9c811980e6d1d5179c` | 1534 | 与 R02 交付备件一致 |
| `scripts/deploy_cloud.py` | `f1a7580878839eb84a84c49704fefe99b3d8eb5493c8caf29efe19f906f239fd` | 5307 | 与 R02 交付备件一致 |
| `scripts/deploy_to_cloud_safe.py` | `81b7d84d0ff05621da63f723facf1d9a39d17f742618b9a0acca584ceb4976b8` | 7274 | 与 R02 交付备件一致 |
| `scripts/probe_servers.py` | `c1de9c53ffcc7aaf11382a9d16743ce67c67cfe9b51ea15a2fe2315fd12c6d54` | 1382 | 与 R02 交付备件一致 |
| `tests/test_admin_dashboard.py` | `64d599e0645c75c8660d160df8845344fa9399846328d815646cc7e8421ae872` | 5692 | 与 R02 交付备件一致 |
| `tests/test_auth_security_fix.py` | `664a36b0a2a614bec22fdc7c18ce1fdb836cd0fee7e257c67b6de1bfcc150e6b` | 2651 | 与 R02 交付备件一致 |
| `tests/test_auto_binding.py` | `d86af2d39f30aa91ab33d67f5da7749b2a3aa084385aaf39f3748099e4556b58` | 15091 | 与 R02 交付备件一致 |
| `tests/test_binding_decisions.py` | `9e9f18061ab8ce2840e378dcbc3660ff0fdeae2027f6ca069d2b2f6f3970026d` | 30474 | 与 R02 交付备件一致 |
| `tests/test_flywheel_end2end.py` | `e14c024960314eb47ab633d9a9b2c91d57109e6e1e0f111a2422e7b2ae58a84b` | 9579 | 与 R02 交付备件一致 |
| `tests/test_m3_hard_constraints.py` | `dac079439b10592e5713af4319b0063ddfb511aaca294a9d1196b26fba1546c4` | 81614 | 与 R02 交付备件一致 |
| `tests/test_m3_hard_constraints_http.py` | `5af49d4c85f01901f5573a9aeb73c42197cc9873cf42ff83f667ce580e8ddc8d` | 83406 | 与 R02 交付备件一致 |
| `tests/test_m4_permissions_and_tasks.py` | `5f258a8ac742f73830a94d93c40d2389157c6cd3947e91be6740eca6a4587abf` | 30001 | 与 R02 交付备件一致 |
| `tests/test_phase0_phase1.py` | `1cdf4e055183990d14990007f339098a8a718fb827e8e0ceccca41eba8af1d34` | 3926 | 与 R02 交付备件一致 |
| `tests/test_v0_0_8_llm_rules.py` | `558efc9a9876f0662b0d2fbef4819497aa226a77b0ab1dce217de0e837664a5a` | 5168 | 与 R02 交付备件一致 |
| `web/admin.html` | `ed1a3eeb9c6ef09683c16924a0c436966fa91de7020767b7cb198dedacea0669` | 154682 | 与 R02 交付备件一致 |
| `web/app.js` | `3b37776865e1328f85158c4d138ab799ada290c13675c7330e79ae650860f35c` | 179435 | 与 R02 交付备件一致 |
| `web/index.html` | `9794bf2c45aa4441ecf29510542e27ed50dd46703324f82629ea55a9a95f5cae` | 125949 | 与 R02 交付备件一致 |
| `web/login.html` | `f8fe49b0a1315ca5bdf86c79c85d7a78a4111af42880cec1cd3520c8a5af9cb5` | 7400 | 与 R02 交付备件一致 |

说明：R02 审核/统筹交付 `m4_r02_audit_delivery_hashes.md` 中除 3 份共享台账/索引外的 **50 项**、R01 审核独立留存 **38 项** 以及 R02 开发交付产物 **39 项** 的 SHA256 均 100% 保持不变。

## 3. 本轮新增开发小结、脚本与证据产物（共 38 项，不含本清单）

| 文件 | 说明 | 当前交付 SHA256 | 大小(B) |
|---|---|---|---:|
| `doc/V0.1.5/rounds/V015-M4_R03_开发小结.md` | R03开发小结 | `11ed8ecde42534df89041f6015284b218b65027e1b4b82bb67e6428ec60b663a` | 11486 |
| `doc/V0.1.5/evidence/m4_r03_01_forced_pwd_change.png` | R03验证脚本/证据/截图/日志 | `99e85bdc51b7749094c7204a78563673c8c042448327978e436e42ace82df6af` | 100044 |
| `doc/V0.1.5/evidence/m4_r03_02_employee_forbidden.png` | R03验证脚本/证据/截图/日志 | `a50ab4fd64161ddec0101a19206b16522c10bc3dc43cc164bd3eeb0b7b3974e9` | 95323 |
| `doc/V0.1.5/evidence/m4_r03_03_dual_approval_and_isolation.png` | R03验证脚本/证据/截图/日志 | `6d045f6d57102f3909d7958e0de876f55e3c63b58158af0f4febae4c4e62a5e6` | 162986 |
| `doc/V0.1.5/evidence/m4_r03_3rule_check.json` | R03验证脚本/证据/截图/日志 | `8f7054b812d461b43ec73815f2bf90237b81f9647da693d960176103e48e6340` | 233 |
| `doc/V0.1.5/evidence/m4_r03_3rule_gate.log` | R03验证脚本/证据/截图/日志 | `2072fe976852d256048f3962b13b3eaa0d72fa2f36c13eb17c35a8290d16defe` | 484 |
| `doc/V0.1.5/evidence/m4_r03_assert_check.log` | R03验证脚本/证据/截图/日志 | `36cc4f0ee33522f77ef6728e60433c4f96c6c749c2ffa90631c043431da21b51` | 439 |
| `doc/V0.1.5/evidence/m4_r03_assert_check.py` | R03验证脚本/证据/截图/日志 | `71d2c9cd98c76160e8f8b48dd0f22afa27abd6976064da32a9c34706f963925c` | 2254 |
| `doc/V0.1.5/evidence/m4_r03_browser_check.py` | R03验证脚本/证据/截图/日志 | `6228430235ceb19afd038ee13cc5da1bb324b5c8c044055f4cc8af5cc4df4455` | 21698 |
| `doc/V0.1.5/evidence/m4_r03_browser_e2e.json` | R03验证脚本/证据/截图/日志 | `585a3dbc189819e1a62e2e55652db557b20117a9bc275d9f175b6cb4de802b31` | 334 |
| `doc/V0.1.5/evidence/m4_r03_browser_e2e.log` | R03验证脚本/证据/截图/日志 | `4d17c8417a31de2a0585949709a8df9ed0b41ced7a6e219ecac7978a776d95ef` | 1069 |
| `doc/V0.1.5/evidence/m4_r03_capture_evidence.py` | R03验证脚本/证据/截图/日志 | `182436285c9509df041b0854221dd7b5e2e97be76239d0dcc3235d6ee7722d29` | 6806 |
| `doc/V0.1.5/evidence/m4_r03_docker_check.log` | R03验证脚本/证据/截图/日志 | `03e85eff7f23d3df17d16e889a0cf853265d3d0e6fc1384dbe76de1dfeac7ad7` | 216 |
| `doc/V0.1.5/evidence/m4_r03_docker_probe.py` | R03验证脚本/证据/截图/日志 | `8b0ebde1c1c8d202e8205ae0038b08c44cc864bade7200f2d2b53e109387f8f8` | 8508 |
| `doc/V0.1.5/evidence/m4_r03_docker_probe_stdout.log` | R03验证脚本/证据/截图/日志 | `7432c5027cd2e54658703b19c78e266eed7a7f6901d6e9073033da58082e9e50` | 287 |
| `doc/V0.1.5/evidence/m4_r03_docker_probes.json` | R03验证脚本/证据/截图/日志 | `1a0275c79b48a7c4f786d705b1d1f8c349d874e10b6d8a64960a6533baad3c26` | 6898 |
| `doc/V0.1.5/evidence/m4_r03_full_pytest.log` | R03验证脚本/证据/截图/日志 | `f93826f424ecb51f55143f34ba232b6f67821f9c25acc4910b2fb4a37a08fd91` | 2725 |
| `doc/V0.1.5/evidence/m4_r03_gate_reports.json` | R03验证脚本/证据/截图/日志 | `2d76ea1f891960aa5337e53ba4d034ecb4c23fc1de5f2dc63b55a2410730ea74` | 51096 |
| `doc/V0.1.5/evidence/m4_r03_isolation_verify.json` | R03验证脚本/证据/截图/日志 | `d8af2e3daf6b5bac493176acc955eff352429c63d3df390f997654bd0548dfe3` | 12188 |
| `doc/V0.1.5/evidence/m4_r03_isolation_verify.log` | R03验证脚本/证据/截图/日志 | `bf66c7b144d5abf784bbaedd9df1fbcb55ad4205ffac0de2b02bb7275fe1d792` | 381 |
| `doc/V0.1.5/evidence/m4_r03_isolation_verify_before.json` | R03验证脚本/证据/截图/日志 | `6c56c1721f7102b0181f3ea9243bf7dadf34ab48079a3810414005325da6881c` | 12188 |
| `doc/V0.1.5/evidence/m4_r03_isolation_verify_before.log` | R03验证脚本/证据/截图/日志 | `78c463b652fe662f365fd701b0b7ec9ec2d19739b6f20fab9bd862ee266ae6dc` | 389 |
| `doc/V0.1.5/evidence/m4_r03_m0_m2_regression.log` | R03验证脚本/证据/截图/日志 | `455391831a678007ee38ad7c76654e64414b169f95fbc6ac7c7dbf7c79f5a4c7` | 1623 |
| `doc/V0.1.5/evidence/m4_r03_m3_regression.log` | R03验证脚本/证据/截图/日志 | `3182b494662d52e3cd716609462b25dee923549b49943d1b8349f5171eeba479` | 1510 |
| `doc/V0.1.5/evidence/m4_r03_pre_baseline_hashes.md` | R03改动前基线哈希 | `db72e0ea43bfb1d2c098fbb4524ec9052d42a78cf7b411ae5bbf0396dbf9f6dd` | 5788 |
| `doc/V0.1.5/evidence/m4_r03_probes.json` | R03验证脚本/证据/截图/日志 | `309e66c69277cb319fad078db5d17abe411d580124aef584aa5dc204ec414153` | 4200 |
| `doc/V0.1.5/evidence/m4_r03_probes.log` | R03验证脚本/证据/截图/日志 | `8987a2776c205e1e6a650f9a6c74136c936ec17d44c830e85095b8043f1c5ef0` | 1525 |
| `doc/V0.1.5/evidence/m4_r03_probes.py` | R03验证脚本/证据/截图/日志 | `fc34cc39abebd55e63a3a74e1ba731b1b7e8f44699b3467a84459070ec1a2aa4` | 28454 |
| `doc/V0.1.5/evidence/m4_r03_py_compile.log` | R03验证脚本/证据/截图/日志 | `a9abbb587941c550e4a5f4a97c7748af9f139b1a47a0786022ec458c34ce1b9b` | 41 |
| `doc/V0.1.5/evidence/m4_r03_pytest.log` | R03验证脚本/证据/截图/日志 | `bea3461c3863c0aa6b088e5055800979ab5797055c4479a5da83974427a2a97b` | 2922 |
| `doc/V0.1.5/evidence/m4_r03_rerun.ps1` | R03验证脚本/证据/截图/日志 | `39e824161897008fd4784ebe159bd26d6c12f2292a6cfc5b5c4a655d75f79c8c` | 3286 |
| `doc/V0.1.5/evidence/m4_r03_sandbox_regression.log` | R03验证脚本/证据/截图/日志 | `2d38255f67d36423011ca6d387a59f93ee4584d0690d3dd701472d93f09f6c6a` | 1557 |
| `doc/V0.1.5/evidence/m4_r03_snapshot_after.json` | R03验证脚本/证据/截图/日志 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` | 3496 |
| `doc/V0.1.5/evidence/m4_r03_snapshot_after.log` | R03验证脚本/证据/截图/日志 | `234142a543f6cdbe9523ce909b9387cb6e1b6a478dfe309a09a461f17e5e0234` | 118 |
| `doc/V0.1.5/evidence/m4_r03_snapshot_before.json` | R03验证脚本/证据/截图/日志 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` | 3496 |
| `doc/V0.1.5/evidence/m4_r03_snapshot_before.log` | R03验证脚本/证据/截图/日志 | `bb9b3ae513711c6045ae052871312410eaf0130ed1be7b661fe0a18137f4dd05` | 120 |
| `doc/V0.1.5/evidence/m4_r03_snapshot_diff.txt` | R03验证脚本/证据/截图/日志 | `8b2b1ad97e55cdeb2b134f024b901067991e9d6a4288fc285d69aff923ce20ad` | 403 |
| `doc/V0.1.5/evidence/m4_r03_test_assertions_check.json` | R03验证脚本/证据/截图/日志 | `6c077250024f53a188f9de3e82d821f6d81cc4cd967c19f24cec9d31d7f0bcc6` | 1152 |
