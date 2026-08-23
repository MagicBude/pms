# 角色与权限矩阵

状态：已接受（SLICE-001 范围）

## 1. 权限原则

- 所有权限判断在服务端应用服务执行，界面隐藏按钮只是辅助体验。
- 默认拒绝；没有明确权限就不能执行动作。
- 权限属于租户成员，不直接挂在全局用户上。
- 查询也要做租户和对象范围过滤，不能只保护写操作。
- 本机版可以由默认管理员承担全部角色，但请求仍必须携带成员身份并经过权限检查。

## 2. 首版角色

| 角色代码 | 中文名称 | 职责 |
| --- | --- | --- |
| `tenant_admin` | 系统管理员 | 管理本租户配置、成员和全部首版业务；查看完整审计 |
| `project_manager` | 项目负责人 | 管理项目生命周期，查看项目下 BOM、投产和请购 |
| `bom_engineer` | BOM 工程人员 | 导入、修正、验证并发布 BOM 版本 |
| `requester` | 生产/采购申请人员 | 发布投产批次，创建、提交和按规则取消生产请购 |
| `auditor` | 只读/审计人员 | 查看授权范围内的业务对象和审计，不得改变状态 |

角色是默认权限组合，不是硬编码判断。业务代码检查权限代码，不能写成“如果角色名称等于管理员”。

## 3. 权限代码

| 权限代码 | 说明 |
| --- | --- |
| `configuration.manage` | 管理编号、时区和基础字典 |
| `membership.manage` | 管理本租户成员与角色 |
| `customer.view` / `customer.manage` | 查看或维护客户 |
| `material.view` / `material.manage` | 查看或维护物料、单位和分类 |
| `project.view` / `project.create` / `project.edit` | 查看、创建或编辑项目草稿 |
| `project.activate` / `project.close` / `project.cancel` | 执行项目状态迁移 |
| `bom.view` / `bom.import` / `bom.edit` / `bom.publish` / `bom.cancel` | 查看、导入、修正、发布或取消 BOM 版本 |
| `production_release.view` / `production_release.create` / `production_release.release` / `production_release.cancel` | 管理投产批次 |
| `purchase_request.view` / `purchase_request.create` / `purchase_request.submit` / `purchase_request.cancel` | 管理生产请购 |
| `attachment.download` | 下载授权业务对象的附件 |
| `audit.view_related` | 查看与自己授权业务范围相关的审计 |
| `audit.view_all` | 查看本租户全部审计 |

## 4. 默认矩阵

符号：`✓` 允许，`R` 仅限授权项目或相关对象，`—` 默认拒绝。

| 权限 | 管理员 | 项目负责人 | BOM 工程人员 | 申请人员 | 审计人员 |
| --- | :---: | :---: | :---: | :---: | :---: |
| `configuration.manage` | ✓ | — | — | — | — |
| `membership.manage` | ✓ | — | — | — | — |
| `customer.view` | ✓ | ✓ | R | R | R |
| `customer.manage` | ✓ | ✓ | — | — | — |
| `material.view` | ✓ | ✓ | ✓ | ✓ | R |
| `material.manage` | ✓ | — | ✓ | — | — |
| `project.view` | ✓ | R | R | R | R |
| `project.create` / `project.edit` | ✓ | ✓ | — | — | — |
| `project.activate` / `project.close` / `project.cancel` | ✓ | ✓ | — | — | — |
| `bom.view` | ✓ | R | R | R | R |
| `bom.import` / `bom.edit` | ✓ | — | ✓ | — | — |
| `bom.publish` / `bom.cancel` | ✓ | — | ✓ | — | — |
| `production_release.view` | ✓ | R | R | R | R |
| `production_release.create` / `production_release.release` / `production_release.cancel` | ✓ | R | — | ✓ | — |
| `purchase_request.view` | ✓ | R | R | R | R |
| `purchase_request.create` / `purchase_request.submit` / `purchase_request.cancel` | ✓ | — | — | ✓ | — |
| `attachment.download` | ✓ | R | R | R | R |
| `audit.view_related` | ✓ | R | R | R | R |
| `audit.view_all` | ✓ | — | — | — | — |

## 5. 对象范围

`R` 不是“前端只显示少一点”，而是服务端查询范围：

- 项目负责人只能访问自己负责或被明确授权的项目。
- BOM 工程人员只能访问分配给自己的项目或 BOM。
- 申请人员只能访问允许其投产和请购的项目。
- 审计人员的查看范围由成员授权决定，默认不等于全租户。
- 附件权限继承业务对象权限；知道附件 ID 或下载地址不能绕过检查。

P0 本机版只有一个默认管理员时，上述范围仍通过同一授权接口计算，测试中必须创建第二租户和低权限成员验证反向场景。

## 6. 强制审计动作

以下动作无论成功或失败都要记录必要审计结果：

- 登录成功、登录失败和退出；
- 项目启用、关闭、取消；
- BOM 导入、发布、取消；
- 投产批次发布、取消；
- 请购提交、取消和重复/冲突提交；
- 权限拒绝和跨租户访问尝试；
- 配置、成员和角色变更；
- 受保护附件下载。

## 7. 验收要求

- 每个写权限至少有“允许角色成功”和“无权限角色失败”两个测试。
- 每个租户级资源至少有跨租户不可见、不可直接 ID 读取、不可修改三个反向测试。
- 管理员不能越过当前租户边界；跨租户平台运营权限不属于 P0。
- 删除角色或停用成员后，已有会话的权限必须按安全策略及时失效。

