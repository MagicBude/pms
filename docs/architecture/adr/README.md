# Architecture Decision Records

状态：持续维护

ADR 记录不可轻易逆转或影响多个模块的技术决定。状态为“提议”的 ADR 不能作为创建工程骨架的最终授权；“已接受”的 ADR 是后续实现约束。

| ADR | 状态 | 决策主题 |
| --- | --- | --- |
| [ADR-0001](ADR-0001-technology-stack-and-modular-monolith.md) | 已接受 | Python、Django、服务端渲染和模块化单体 |
| [ADR-0002](ADR-0002-database-and-multi-tenancy.md) | 已接受 | SQLite/PostgreSQL、共享表多租户和事务 |
| [ADR-0003](ADR-0003-attachment-storage.md) | 已接受 | 附件元数据、存储适配器和上传安全 |
| [ADR-0004](ADR-0004-deployment-profiles.md) | 已接受 | 本机、内网和云端部署形态 |
| [ADR-0005](ADR-0005-data-portability-and-legacy-extraction.md) | 已接受 | 备份恢复、旧数据只读提取和租户导出边界 |
| [ADR-0006](ADR-0006-procurement-pricing-and-supplier-decision.md) | 已接受 | 采购报价、价格快照和追加式供应商确定 |
| [ADR-0007](ADR-0007-purchase-orders-and-versioned-documents.md) | 已接受 | 正式订单、稳定编号和版本化单据 |

## 状态规则

- `提议`：已形成推荐方案，等待用户或架构评审。
- `已接受`：后续实现必须遵守。
- `已替代`：由另一 ADR 取代，并保留历史原因。
- `已废弃`：不再使用，但仍保留历史记录。

ADR 接受后不重写原始决策背景。实质变化使用新 ADR 替代。
