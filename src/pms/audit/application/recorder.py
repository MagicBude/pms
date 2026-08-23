"""应用层依赖的审计记录抽象。"""

from typing import Protocol
from uuid import UUID

from pms.audit.domain.events import AuditEvent


class AuditRecorder(Protocol):
    """追加一条审计事件并返回持久化标识。"""

    def record(self, event: AuditEvent) -> UUID:
        """记录事件；实现不得更新或覆盖既有审计。"""
