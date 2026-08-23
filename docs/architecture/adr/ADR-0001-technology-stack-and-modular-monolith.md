# ADR-0001 技术栈与模块化单体

状态：提议
日期：2026-08-23

## 背景

PMS 要从本机单用户逐步演进到内网多人和商业云端，同时承担软件工程学习目的。首版必须易于在 Windows 本机启动，又要具备认证、权限、ORM、迁移、表单、文件上传和服务端渲染等常见能力。

如果后端 API、SPA、认证和管理界面分别选型，会在业务规则尚未稳定时引入过多工程成本。项目更适合从功能完整的服务端框架和模块化单体开始。

截至 2026-08-23，Django 5.2 是 LTS，官方支持到 2028 年 4 月，并在 5.2.8 起支持 Python 3.14。Python 3.14 处于稳定 bugfix 支持阶段。

## 决策

### 1. 语言与运行时

- 使用 CPython 3.14 的最新补丁版本。
- `pyproject.toml` 声明 `requires-python`，`.python-version` 固定开发默认版本。
- 不启用 free-threaded 实验运行模式；使用标准 CPython 构建。
- 内部主键可以使用 Python 3.14 标准库提供的 UUIDv7，业务编号与内部主键分离。

### 2. Web 框架

- 使用 Django 5.2 LTS 的最新 5.2.x 补丁版本。
- 使用 Django ORM、迁移、表单、模板、认证、会话和安全中间件。
- 从第一次迁移起定义自有用户模型，基于 `AbstractUser` 扩展；不在已有生产数据后更换用户模型。
- Django Admin 仅用于受控的内部运维和数据诊断，不作为主要业务界面，也不绕过应用服务修改核心状态。

### 3. 前端策略

- 首版使用 Django Templates 服务端渲染 HTML。
- 使用 HTMX 2.x 做局部刷新、联动表单和渐进增强；生产环境自托管固定版本，不依赖公共 CDN。
- 少量浏览器交互使用原生 JavaScript；P0 不引入 React、Vue、独立 SPA 或 Node 构建链。
- 普通链接和表单优先在没有 HTMX 时仍可完成基础流程。
- 只有外部集成、移动客户端或明显复杂的交互需求出现后，才通过新 ADR 引入正式 JSON API 或 SPA。

### 4. 依赖与工程工具

- 使用 uv 管理 Python、虚拟环境、依赖和跨平台 `uv.lock`；锁文件提交 Git。
- 使用 Ruff 同时执行格式化和 Lint。
- 使用 mypy 做静态类型检查；Django 动态部分采用兼容的类型插件或边界封装。
- 使用 pytest 和 pytest-django；测试标记必须注册并启用严格检查。
- 开发、测试和生产依赖在 `pyproject.toml` 中分组，禁止手工维护多个互相漂移的 requirements 文件。
- 所有依赖在创建骨架时再次检查 Python 3.14 和 Django 5.2 兼容性，并锁定确切版本。

### 5. Excel BOM 解析

- P0 使用 openpyxl 只读解析 `.xlsx` 和 `.xlsm`。
- 使用只读模式并显式关闭工作簿；不保留、不调用也不执行 VBA。
- 不执行 Excel 公式、外部链接或嵌入对象。公式相关数值必须使用可信缓存值或要求用户转为普通值，具体行为由导入规范定义。
- Excel 解析属于 `bom` 模块的基础设施适配器，领域规则只接收规范化行数据。

### 6. 应用结构

- 采用 [模块化单体边界](../module-boundaries.md)。
- 一个进程、一个部署单元、一个主数据库，但模块拥有自己的应用服务、迁移和数据写入权。
- 领域层使用纯 Python，不导入 Django、ORM、HTTP 或存储 SDK。
- 不为每个模型机械创建 repository；只在领域与基础设施确有隔离价值时定义端口。
- 核心流程通过命名用例和显式事务实现，不隐藏在 signal 或模型保存副作用中。

### 7. 运行接口

- 项目提供 ASGI 应用，使用 Uvicorn 作为首版应用服务器。
- 开发环境可以使用 Django 开发服务器；可交付本机版不得使用 `runserver`。
- P0 的 Excel 解析同步执行，单文件限制 25 MiB；当实际耗时或并发证明需要时，再通过 ADR 引入后台任务队列。

## 备选方案

### FastAPI + SQLAlchemy + 独立前端

优点是 API 边界明确、异步生态丰富。缺点是认证、会话、权限、表单、管理界面和前端工程需要更多拼装，不适合当前以业务提炼和学习为主的阶段。

### Django REST Framework + React/Vue SPA

适合复杂独立前端或多客户端产品，但首版会同时维护 JSON 契约、前端状态、Node 工具链和两套验证逻辑。当前收益不足。

### Node.js/NestJS 全 TypeScript

可以统一前后端语言，但旧系统分析、Excel 处理和本机 Python 生态已有较强关联；切换不会减少业务复杂度。

### 桌面 GUI

本机体验直接，但不利于平滑升级到浏览器内网多人和云端版本，故不采用。

### Django 6.0

版本更新，但当前常规支持期短于 5.2 LTS。项目优先稳定与学习资料寿命，等待未来 LTS 再评估升级。

## 后果

### 正面

- 单一语言和框架覆盖认证、页面、数据、迁移和管理能力。
- 服务端渲染减少首版工具链和接口重复。
- Django 同时官方支持 SQLite 与 PostgreSQL，符合部署演进目标。
- LTS 降低项目早期频繁升级成本。
- 纯 Python 领域层保留测试和未来基础设施替换能力。

### 代价

- Django ORM 模型与纯领域对象之间需要明确映射，不能随意混用。
- HTMX 适合表单型系统，但极复杂表格编辑以后可能需要专门前端组件。
- Python 3.14 较新，创建骨架前必须确认所有第三方依赖兼容。
- 同步 Excel 导入受到 25 MiB 和请求时长限制。

## 实施约束

- 创建工程骨架前本 ADR 必须转为“已接受”。
- `uv.lock`、Python 版本、Django 补丁版本和 HTMX 静态文件必须进入版本控制。
- CI 至少运行 Ruff、mypy、pytest 和 Django system checks。
- 不因 Django 提供 Active Record API 就允许视图直接保存核心业务状态。
- 依赖新增必须遵守项目依赖评审规范。

## 复审条件

- Django 5.2 接近支持结束，或目标升级 LTS 已稳定。
- 业务界面需要离线、复杂画布、大规模客户端状态或独立移动端。
- 同步导入无法满足经过测量的性能目标。
- 模块边界长期无法在单体中保持，且有明确独立扩缩容或团队边界证据。

## 官方资料

- [Django 支持版本](https://www.djangoproject.com/download/)
- [Django 5.2 发布说明](https://docs.djangoproject.com/en/5.2/releases/5.2/)
- [Python 版本状态](https://devguide.python.org/versions/)
- [Django 认证系统](https://docs.djangoproject.com/en/5.2/topics/auth/default/)
- [uv 项目与锁文件](https://docs.astral.sh/uv/guides/projects/)
- [Ruff](https://docs.astral.sh/ruff/)
- [HTMX 文档](https://htmx.org/docs/)
- [openpyxl 只读模式](https://openpyxl.readthedocs.io/en/stable/optimized.html)
- [Django 使用 Uvicorn](https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/uvicorn/)
