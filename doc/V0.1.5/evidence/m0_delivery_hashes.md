# V015-M0 R01 交付哈希清单

- 生成时间：2026-11-12（Asia/Shanghai）
- 基线 commit：`4cbbbe5bd6e1bee21ad908ed1f631a919920ea8f`（`git rev-parse HEAD` 实测一致，未提交、未 push）
- 算法：SHA256（PowerShell `Get-FileHash -Algorithm SHA256`，小写十六进制）
- 说明：本清单仅覆盖 V015-M0 本包新增/修改文件；仓库中其他未提交差异（目录治理、scripts/、doc 其他版本目录等）为用户/前序工作，本包未改动。

## 应用侧改造（env 可注入，默认路径行为不变）

| SHA256 | 文件 |
|---|---|
| dc469bb5c449f79b341a303510cf1745e6d9ab69c6910e467e65e5d79befba93 | app/server.py |
| 8b5caeba98abc398c32f22e60adef2e31761ce9b4da9bc9a3d32f72aefa7dd78 | app/auth_manager.py |
| f7e626bfc67dbe5af0212a3890e4fea721dbb743f30e611fe0e842d7d4d79324 | app/llm_rule_engine.py |
| 58cc76e35dbae01d3354f58684d5f7b74be72c279ab0fcf72c9cb47a4f95748d | app/vector_index.py |
| 47ed0b50957b8fdcf2d9d58a0ad377447bd05f2e81949c26b3202e940d3dec2c | app/db_manager.py |
| b13ac9eff30ac0143cad1e51c02a6c893e79f4298f3dedaec0fe07e1df736cbf | app/feedback_train.py |

## 测试隔离设施与证据

| SHA256 | 文件 |
|---|---|
| c66be0f8f82ba984282a3be4c3b85baa7ca1fce92ac140b430a8c80db61e9337 | tests/conftest.py |
| 246461b3fdb92ef86307efaa94f54b19fbebc5dca4a598875691915f23666403 | tests/test_m0_isolation.py |
| c4768ca5e7d299e621278c90a8253d7634ed1892b608749a743435e2dec9e003 | tests/verify_isolation.py |
| 4a536ef8c105769f20cb534d75c7ae9467feba744ac09c5a515d06c42ba55c37 | tests/README.md |
| 4180e186629fc8daf4eac04b8de15081dd277571253f0733b3e5a7422868d62a | pytest.ini |
| 2d68105768def529c5900332eb73f6d465a9ae2a00d6fc052ad1b3b3abe7fdd4 | requirements-test.txt |

## evidence 实测记录

| SHA256 | 文件 |
|---|---|
| 4d2ef46b5d1de9c2c7e7ed9679793496be92b9cbe8051adb2861e1e5f76459ba | doc/V0.1.5/evidence/m0_legacy_test_inventory.md |
| 85f46ae337f232b935a8f8d1bdbf9f970ccc5969a7844d1ad2490144133be881 | doc/V0.1.5/evidence/m0_isolation_verify.json |
| f88a8e16ba031dc14f68043973513c7cecad66cdacdd475b42697ec2510d0ffc | doc/V0.1.5/evidence/m0_smoke_pytest.log |
| c08fe39aa27d0cfd7ed64cf4f03399c22754dabe363795725e27fcc5140e8101 | doc/V0.1.5/evidence/m0_full_pytest.log |

## 管理与小结文档

| SHA256 | 文件 |
|---|---|
| db36a6dcf40788f6f9b265a0bf0afdb0ec0ca60c5c75eeac8b200bbaf238fef8 | doc/V0.1.5/rounds/V015-M0_R01_开发小结.md |
| a37d223dbdec20bbe6b0de8b3ac646120c8b496936887a381a05a149a7d1432f | doc/V0.1.5/02_任务台账.md（仅 M0 行进展与小结链接） |

注：本清单自身哈希自指无意义，以文件系统实测为准（生成时实测 `87eb19fe4058b97d716af49e819ed59933516152ea6cd52e942b1ac3c2bb2b28`，追加本节后内容已变化，审核以重算结果为准）。

## 业务资产零改动佐证

- 两轮 pytest 与 verify 脚本运行后，`git status --short -- app config logs data` 中无业务库/配置/缓存/日志/CSV 的新增或修改条目（仅有上表 6 个 app 源文件的本包修改与前序已存在的 `app/test_engine.py` 删除标记）。
- 业务文件级与目录树哈希前后快照见 m0_isolation_verify.json：`business_hash_unchanged=true`、`business_changes=[]`、`verdict=PASS`。
