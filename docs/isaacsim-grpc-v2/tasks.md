# Isaac Sim gRPC v2 实施任务

按顺序执行；每步完成后运行对应验证，全部完成跑第 12 步。

1. **proto 与 stub**
   - 新增 `src/capabilities/isaac_sim/grpc/simulation.proto`（v2 全量契约）。
   - 生成 `simulation_pb2.py`、`simulation_pb2_grpc.py`，修正导入。
   - 删除 `simulator.proto`、`simulator_pb2.py`、`simulator_pb2_grpc.py`、`protocol.json`。
   - 验证：`python -c "import capabilities.isaac_sim.grpc.simulation_pb2"`。

2. **gRPC 客户端**
   - 重写 `grpc/transport.py` 为 `SimulationClient` + `document()`。
   - 重写 `grpc/tasks.py` 为 `DurableJobs`（submit/wait/download，保留 `request_id`）。
   - 验证：单测中的 mock server 可完成 Health/Submit/Get/Download/Ack/Cancel。

3. **场景映射**
   - 新增 `scene_mapping.py`：第 3 节映射、`simulation_plan`、相机方位角/距离。
   - `mesh` 仅在 `settings.mesh_assets[asset_id]` 存在时映射为 `BodySpec.asset`；
     `cylinder`、`native`、`generated`、内建原生资产一律拒绝。
   - 验证：`tests/test_simulation_request.py`。

4. **契约与准入**
   - 重写 `capture_contract.py`（静态契约 + Health）。
   - 修改 `scene_requirements.py`（白名单、删除 asset_sources、native/generated 与
     未绑定 mesh 拒绝）。
   - 修改 `capture_scope.py`（`export_scene` 默认 False）。
   - 验证：`tests/test_capture_service_contract.py`、`tests/test_capture_preflight.py`。

5. **证据生成**
   - 重写 `evidence.py`：`frame_evidence`、`entities_from_job`、`physics_summary`。
   - 验证：`tests/test_isaac_sim_evidence.py`。

6. **采集编排（过程帧全采）**
   - 重写 `capture_stage.py`：计划 → 每相机 Submit（`CaptureSpec.every` +
     `frames=[steps]`）/轮询/下载 → 逐帧证据 → manifest → publish → Ack。
   - 默认 `capture_target_frames=24`，`every=max(1, ceil(steps/target))`；
     `capture_every_steps` 非空时用固定间隔；`capture_options.frames`/`every` 最高优先。
     最终帧写规范名 `view_{i}.*`，过程帧写 `process_view_{i}_step_{step:06d}.*`。
   - 限制 1..steps、最多 1200 项。
   - 修改 `pipelines/scene_production/review.py`：复核提示词每相机只取最终帧 +
     最多 3 张均匀抽样的过程帧；数据帧不删。
   - 更新 `recovery.py` 的协议文案与状态兼容。
   - 删除 `authoring.py`、`usd_materials.py`、`mesh_materials.py`、`native_geometry.py`、
     `upload_access.py`、`resource_access.py`。
   - 验证：mock 服务完整采集测试。

7. **工具动作**
   - 重写 `tool_api.py` 与 `guide.py`：guide/health/submit/status/wait/cancel/download。
   - 修改 `store.py`、`observation.py`、`migrate_capture.py` 的 protocol 文案。
   - 验证：`tests/test_operation_outputs.py` 中下载用例。

8. **配置**
   - 修改 `service_access/configuration.py`：允许并校验 simulation/render/artifacts/
     mesh_assets，删除 asset_sources。
   - 修改 `config/benchforge.local.json` 与 `config/benchforge.example.json`：
     port 60051，新增 simulation/render/artifacts，用 `mesh_assets` 替代 `asset_sources`。
   - 验证：`tests/test_runtime_config.py`（如涉及）。

9. **DSH 契约**
   - 修改 `contracts.mjs`、`review_policy.mjs`、`tool-api.md`、`SKILL.md`、
     `preset_definition.py`、`cordis.patch.yml`、`artifacts.test.mjs`。
   - 验证：`node --test src/dsh/production/review_policy.test.mjs`（若环境可用）。

10. **测试替换**
    - 重写/删除第 7 节列出的测试文件。
    - 验证：替换后的单测全绿。

11. **清理**
    - `grep -R "a100sim\|simulator_pb2\|GetCapabilities\|UploadAsset\|DurableTasks\|A100Client" src tests config`
      应为空（历史文档与 example 产物除外）。
    - `python -m compileall -q src`。

12. **总体验证**
    - 运行 spec 第 8 节命令。
    - 汇总：改动文件、关键决策、未覆盖风险（FOV、静态刚体、语义标签格式）。
