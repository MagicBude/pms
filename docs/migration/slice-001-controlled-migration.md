# SLICE-001 受控迁移与对账

状态：技术流程已验证，业务样例待确认

## 1. 输入格式

`migrate_legacy_slice` 接受最大 2 MiB 的普通 UTF-8 JSON 文件，schema version 必须是
`pms-legacy-slice-v1` 或 `pms-legacy-slice-v2`。根对象只允许以下字段：

- `sample`：小写 slug、`synthetic`/`business_pending`/`business_confirmed` 类型和业务确认人；
- `master_data`：一个客户、单位、分类和物料；
- `project`：项目编号、客户引用、机型、负责人和日期；
- `bom`：版本和保留输入顺序的明细；
- `production`：正整数投产台数、单位和接单部门；
- `legacy_purchase_candidates`：旧系统基线的来源行、物料、请求数量和单位；
- `accepted_differences`：差异检查键、规则 ID、原因和接受人。

所有数量必须用 JSON 字符串保存十进制值，例如 `"2.000000"`，不能用 JSON 浮点数。完整的完全
虚构示例位于 `tests/fixtures/migration/legacy-slice-v1-synthetic.json`，只能作为格式参考，不能复制
其中“技术通过”结论作为业务验收。v2 额外保留 BOM 来源行、层级、部套和物料零件属性。

## 2. 执行步骤

先停止 PMS，创建并验证当前数据备份；在包含输入文件和报告目录的受控维护终端中执行：

```powershell
uv run python manage.py migrate_legacy_slice `
  --input "D:\PMS-Migration\confirmed-slice.json" `
  --report "D:\PMS-Migration\reports\slice-20260824.json"
```

报告目标必须位于已存在目录、使用 `.json` 扩展名且尚不存在，防止覆盖先前签收证据。默认操作者
是 `admin`；需要其他管理员时使用 `--actor-username`，该用户必须恰好有一个活动 membership 并
具备正式用例所需权限。

命令依次创建或复核主数据、活动项目、发布 BOM、发布投产和已提交请购。迁移生成的 BOM 来源
附件是由受控 JSON 重新构造的静态 `.xlsx`，不会打开或执行旧宏。再次运行相同包会复用一致的
项目编号、BOM 版本、投产批次和请购幂等键；任一既有字段冲突都会停止。

## 3. 对账报告

报告 schema 是 `pms-reconciliation-v1`，当前至少比较：

- 项目编号；
- 每条 BOM 的来源行和单台数量；
- 投产台数；
- 请购候选数量；
- 候选行的来源行、物料、请求数量和单位，从而显式核对不可请购物料排除及去重结果。

报告不保存输入/输出绝对路径、密码或附件正文。`DIFFERENCES_PENDING` 会在写出报告后让命令以
失败状态结束；不能手工改报告假装通过，应让业务人员补充输入包中的差异原因和接受人后，使用
新报告文件名重新执行。

自动映射的真实包固定为 `business_pending`，命令默认拒绝写库。仅可在独立数据目录中显式使用
`--allow-business-pending` 做技术复核；此时报告范围为 `BUSINESS_PENDING`，不能关闭业务验收。

## 4. 当前结论

完全虚构样例已通过首次迁移、重复执行不重复、未签收差异失败、完整业务链备份和空目录恢复。
真实 10 行切片已完成 v2 映射和隔离库逐项一致对账，但客户关联、投产单位语义、加工件/采购件
分流和逐行内容仍待业务人员复核。因此 `AC-S001-043` 仍为“待执行”。详见
[真实项目切片映射与复核](real-slice-review.md)。
