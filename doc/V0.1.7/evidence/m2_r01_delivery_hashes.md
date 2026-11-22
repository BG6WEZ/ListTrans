# V017-M2 R01 交付文件哈希

基线为 Git HEAD e964fb16848de9dece8e729e801b61c7d81dd341 与
[M1 R02 放行快照](m1_r02_coord_release.json)。本轮交付位于工作区，未执行 Git 写操作。
下表覆盖本轮实施与测试涉及的全部源码文件，包含最后一次测试文件修改后的实际字节 SHA256。

| 路径 | SHA256 | 本轮范围 |
|---|---|---|
| `app/binding_decisions.py` | `ed3ff9dc4b337f60110b623cca8630c0c2e5e570fcb9c5a7f062212dbc3756fd` | 将实时绑定校验的物料读路径纳入目录发布读屏障。 |
| `app/db_manager.py` | `d9a67877c35c80cea11ef1287ed5a08b41de47799900b895d4b58758a8f79df1` | 持久化目录版本，隔离查询缓存，协调导入、补参、重解析和备份恢复。 |
| `app/flywheel_sandbox.py` | `427e00720021c31d077de95e75bb11a5ecff7e7a37378b64b9c7dda0b1550d74` | 沙箱复制目录版本绑定的完整索引快照。 |
| `app/matcher.py` | `78a7a472d8ee08f8d2e9b25a5cdc97b27fe7f7590042d294f286990091c2c662` | 候选召回与物料补齐共享读屏障，校验向量版本和模型。 |
| `app/server.py` | `06d70bc0dc8dbf716b611796b5c8d74c0fe53021284a03ce1162be9a729c5ac2` | 将导入发布、分类和恢复核验绑定到目录版本，保留既有仲裁。 |
| `app/vector_index.py` | `76fd306782d7951e9bd4677adbb8269a83bf640056d67ba6ba2bbd7a4bd7285f` | 单引用快照、原子磁盘包、兼容旧资产和失败回退。 |
| `scripts/batch_llm_structure.py` | `271844176c06bbbd778e7027835c13017782d52740667320136c611893dc472c` | 离线批量补参使用统一版本事务。 |
| `tests/test_flywheel_sandbox.py` | `2f93e30e12c3411cd68616c7e1f2b261ebf85da93735743df5fbebadb30208c3` | 词法别名探针显式固定关键词通道并还原隔离配置。 |
| `tests/test_m5_file_boundary_and_compat.py` | `e78b110b5c1f039aea129b247d3481c237dc2953d54d13da35c0dba0b0241e7c` | 用不可变快照播种旧代索引，保留既有断言。 |
| `tests/test_m5_r03_commit_arbitration.py` | `d8a1a085ec7759c346ae0c975fe7a7b69593103541b5df730a1498ee24b682bd` | 通过整套快照播种仲裁测试旧代，保留既有断言。 |
| `tests/test_m5_r04_recovery_isolation.py` | `ade8909dc0a264cae2f8e4b27af6884b5d3d78b5a1a34ab61e7d752a960ca918` | 通过整套快照播种恢复隔离旧代，保留既有断言。 |
| `tests/test_v016_m4_diagnostics.py` | `f1cd7b3bf8f046b74e2e77d075c568671bc7e56eb322ba3b8560930d37e0821d` | 诊断专用向量替身实现当前索引检查接口。 |
| `tests/test_v017_m2_catalog_revision.py` | `745027e6297ec19bde8410cf86effcfbc132433eee7079462e26a2bbed61b967` | 新增十项版本、FTS、并发、维度和故障回滚检查。 |
