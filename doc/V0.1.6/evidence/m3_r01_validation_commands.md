# V016-M3 R01 验证命令

Windows 主机使用项目 `venv/Scripts/python.exe`。`-u` 让日志及时写出。主机测试按 `tests/conftest.py` 写入独立临时根，`LLM_ENABLED=false`。独立入口在导入应用前断言 10 项可写路径均属于本轮临时目录。现有门禁报告、真实数据库和真实配置均为只读核对对象。

| 验证 | 命令或关键参数 | 日志前缀 |
|---|---|---|
| 前后隔离核验 | `python tests/verify_isolation.py --out doc/V0.1.6/evidence/m3_r01_isolation_before.json`，收尾用 `m3_r01_isolation_after.json` | `m3_r01_isolation_before` / `after` |
| 受影响测试 | `python -m pytest tests/test_flywheel_sandbox.py tests/test_v016_m2_publish_gate.py tests/test_query_contract.py tests/test_auto_binding.py tests/test_m4_permissions_and_tasks.py -q`，`LISTTRANS_TEST_ROOT` 指向独立固定根 | `m3_r01_targeted_pytest` |
| M3 专项 | `python -u -m pytest tests/test_v016_m3_runtime_assets.py -v`，固定隔离根独立于其他会话 | `m3_r01_assets_pytest` |
| 全量固定根 | `python -u -m pytest tests -v -p no:cacheprovider --basetemp <固定根专属 pytest 临时目录>`，另设固定 `LISTTRANS_TEST_ROOT` | `m3_r01_full_fixed` |
| 全量标准根 | 与固定根相同的 pytest 参数，`--basetemp` 使用独立目录，清除 `LISTTRANS_TEST_ROOT` 使应用沙箱按 conftest 的 `mkdtemp` 分配 | `m3_r01_full_standard` |
| 三规则门禁 | `python -u doc/V0.1.6/evidence/m3_r01_3rule_gate.py`，只读取 M0 R03、V015 M3 R04/M4 R03/M5 R04、V016 M2 R01 报告 | `m3_r01_3rule_gate` |
| 独立主机树 | `python -u doc/V0.1.6/evidence/m3_r01_runtime_host.py`，从白名单创建无 `tests` 和 `doc` 的临时运行根 | `m3_r01_runtime_host_verified` |
| 新镜像构建 | `docker desktop start --timeout 45` 恢复引擎后，`docker build --pull=false --progress=plain -t listtrans:v016-m3-r01-local .`，复用未变化的依赖层 | `m3_r01_docker_build_retry` |
| 全层审计 | `python -u doc/V0.1.6/evidence/m3_r01_image_audit.py listtrans:v016-m3-r01-local`，流式读 `docker image save`，每层核对项目文件路径和 SHA256 | `m3_r01_image_layers` |
| 无模型容器 | 用 stdin 传入 `m3_r01_runtime_smoke.py`，`docker run --rm --init --network none -i listtrans:v016-m3-r01-local python -u -` | `m3_r01_docker_keyword_delivery` |
| 向量模型容器 | 同一命令外加 `--mount type=bind,source=<本机 model_offline>,target=/app/model_offline,readonly -e M3_EXPECT_VECTOR=1` | `m3_r01_docker_vector_delivery` |
| 交付冻结 | `python -u doc/V0.1.6/evidence/m3_r01_freeze.py` | `m3_r01_outer_snapshot_after.json`、`m3_r01_delivery_hashes.md` |

容器用例仅用环回地址。所有账户和凭据为本次随机生成的临时合成数据。镜像分层比对限于本项目工作目录，其余文件由基础镜像和公共依赖层提供。实际部署配置与真实业务库迁移归后续授权。

三规则脚本的输出入口在开发复跑后补上 `--out <新报告>` 与独占创建。审核请指定审核目录中的新路径。默认已存在时脚本在加载应用前退出，规则、样本及比较逻辑保持一致。
