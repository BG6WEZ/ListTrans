# V017-M3 R01 交付源码 SHA256

基线为 Git HEAD e964fb16848de9dece8e729e801b61c7d81dd341 加 [M2 R01 放行工作区](m2_r01_coord_release.json)。
本表覆盖本轮修改与新增的所有应用、测试、依赖声明和复验脚本文件。测试源码最后一次更改后重新哈希。

| 文件 | SHA256 | 本轮范围 |
|---|---|---|
| `app/admin_api.py` | `e34bea10db3a6150739c5abc82f877ca60642ff67527c39fa9520c24fa842d2c` | 管理同步操作卸载，健康查询由独立线程处理。 |
| `app/db_manager.py` | `b3ca00f1eed7a06052e0378a1d726b0c27371d791d211e124b260f6106959aeb` | 重解析检查点及有界异步补参，保留目录事务。 |
| `app/excel_boundary.py` | `8e552f21aa52747ade205620ca07337d6697d176078e8093c352ba231d1fef7c` | 上传临时文件 I/O 卸载。 |
| `app/feedback_miner.py` | `5c820e42ad7dc9d65d9e70822087648dd0c5b52e73d2bb73220da246ff2d68c9` | 矿工循环和事务提交前协作检查。 |
| `app/flywheel_sandbox.py` | `5eef56584df5630f505e7fac29cea5a4df293fe406207c562e980aaf4ae35ce0` | 限额评测、终态过期、取消子进程回收及隔离目录。 |
| `app/rule_api.py` | `ecac520f7f01b7968e63b310530993d044f3ffafd0a7a4958b2e927eeaa92bcd` | 规则重操作卸载和本人的评测取消。 |
| `app/runtime_jobs.py` | `6ac3d9cdd3432c50f0c1a3e1ed7c456c6788c03d160b3891b42754b0aec1e0da` | 有界线程池、任务取消、超时、终态清理及队列采样。 |
| `app/runtime_retention.py` | `d7452100cf69cb813ed2c26b6687fce56333e60453c0f17de2357b0324d03ee5` | 保留策略、根路径保护、活跃租约及干跑。 |
| `app/server.py` | `e2f6b20774742ce01cd79afe768688c440e31ae0fe039fb460240c5ed5f588f3` | 重操作调度、导入仲裁衔接、清理与取消 API、日志轮转。 |
| `app/vector_index.py` | `8a19336d09de7a8535f7a3b86667ffac91a556bd6e356bbf71812c1bf2f058fe` | 编码批次和磁盘发布前协作取消。 |
| `requirements.txt` | `84b1857f9ef5916ff8e8de25aad3ccad9f3fa36340fb92152b9d4acec7360861` | 声明实际直接使用的 Starlette、Pydantic 和 bcrypt。 |
| `tests/test_flywheel_sandbox.py` | `0d6bbddeaed8f7b8050ccd9e26548488dbb05928239653dbc8f2b2c42f085c98` | 故障注入改用生产沙箱子进程入口。 |
| `tests/test_m4_permissions_and_tasks.py` | `d4bfcda8d66930227ce7ac7c90bf1d6d6750ee040973aaba62786fcadc3bc0de` | 权限测试只替换评测执行，保留真实调度和权限断言。 |
| `tests/test_m5_file_boundary_and_compat.py` | `957458bb61d105e198647a83c6c747fe68cb2b1859d16ea84eeb3f30abf4ff91` | 增加管理员清理和默认干跑的 HTTP 核验。 |
| `tests/test_v016_m2_publish_gate.py` | `bfb056909057813f94343df503702efb5df64bd3c0b58c936274eab27a909a0a` | 发布门禁故障注入改用生产沙箱子进程入口。 |
| `tests/test_v016_m4_lifecycle.py` | `a3e250d3678fc55953509380619e5b958fc14934212ce4203693318a9cb897bc` | 保留生产子进程超时与回收实测。 |
| `tests/test_v017_m3_runtime_jobs.py` | `312530aa5ca59d13803ef7874a234b8fad7b5c86eecbc68290320dcd62862e58` | 容量、取消、清理、隔离和终态回归。 |
| `doc/V0.1.7/evidence/m3_r01_load.py` | `596c8a17d355113ea3da9717151fa2c0a112d7ed5b8aeff98d97a82f1233881a` | 隔离真实 HTTP 负载和队列时延采样。 |
