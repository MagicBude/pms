"""旧基线与新系统结果的逐项、可签收对账报告。"""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from pms.legacy_migration.schema import AcceptedDifference, LegacyPackageError, SampleMetadata

REPORT_SCHEMA_VERSION = "pms-reconciliation-v1"


@dataclass(frozen=True, slots=True)
class ReconciliationCheck:
    """一个稳定检查键的旧值、新值和差异签收状态。"""

    check_key: str
    rule_id: str
    legacy_value: object
    new_value: object
    status: str
    reason: str | None
    accepted_by: str | None


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """既供人阅读也供后续自动化消费的版本化对账结果。"""

    schema_version: str
    sample_id: str
    sample_kind: str
    sample_confirmed_by: str | None
    acceptance_scope: str
    overall_status: str
    generated_at: str
    checks: tuple[ReconciliationCheck, ...]

    def to_bytes(self) -> bytes:
        """使用稳定顺序输出 UTF-8 JSON，不包含输入/输出绝对路径。"""
        payload = asdict(self)
        return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )


class ReconciliationBuilder:
    """收集检查并强制差异签收元数据与实际差异一一对应。"""

    def __init__(
        self,
        *,
        sample: SampleMetadata,
        accepted_differences: tuple[AcceptedDifference, ...],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sample = sample
        self._accepted = {item.check_key: item for item in accepted_differences}
        self._used_acceptances: set[str] = set()
        self._checks: list[ReconciliationCheck] = []
        self._clock = clock

    def compare(
        self,
        *,
        check_key: str,
        rule_id: str,
        legacy_value: object,
        new_value: object,
    ) -> None:
        """比较一个稳定业务口径；不自动容忍字符串、数量或顺序差异。"""
        if legacy_value == new_value:
            status = "MATCHED"
            reason = None
            accepted_by = None
        else:
            acceptance = self._accepted.get(check_key)
            if acceptance is None:
                status = "DIFFERENCE_PENDING"
                reason = None
                accepted_by = None
            else:
                if acceptance.rule_id != rule_id:
                    raise LegacyPackageError(f"差异 {check_key} 的 rule_id 与检查规则不一致。")
                if self._sample.kind != "business_confirmed":
                    raise LegacyPackageError("虚构技术样例不能签收业务差异。")
                self._used_acceptances.add(check_key)
                status = "ACCEPTED_DIFFERENCE"
                reason = acceptance.reason
                accepted_by = acceptance.accepted_by
        self._checks.append(
            ReconciliationCheck(
                check_key=check_key,
                rule_id=rule_id,
                legacy_value=legacy_value,
                new_value=new_value,
                status=status,
                reason=reason,
                accepted_by=accepted_by,
            )
        )

    def build(self) -> ReconciliationReport:
        """完成报告；未命中实际差异的旧签收条目被视为过期配置。"""
        unused = set(self._accepted) - self._used_acceptances
        if unused:
            raise LegacyPackageError(
                "accepted_differences 包含未发生的检查：" + ", ".join(sorted(unused))
            )
        statuses = {item.status for item in self._checks}
        if "DIFFERENCE_PENDING" in statuses:
            overall = "DIFFERENCES_PENDING"
        elif "ACCEPTED_DIFFERENCE" in statuses:
            overall = "ACCEPTED_DIFFERENCES"
        else:
            overall = "MATCHED"
        return ReconciliationReport(
            schema_version=REPORT_SCHEMA_VERSION,
            sample_id=self._sample.id,
            sample_kind=self._sample.kind,
            sample_confirmed_by=self._sample.confirmed_by,
            acceptance_scope={
                "synthetic": "TECHNICAL_ONLY",
                "business_pending": "BUSINESS_PENDING",
                "business_confirmed": "BUSINESS_CONFIRMED",
            }[self._sample.kind],
            overall_status=overall,
            generated_at=self._clock().isoformat(),
            checks=tuple(self._checks),
        )


def write_reconciliation_report(report: ReconciliationReport, path: Path) -> None:
    """以独占创建写报告，防止覆盖先前人工签收证据。"""
    if path.suffix.lower() != ".json" or path.is_symlink() or not path.parent.is_dir():
        raise LegacyPackageError("报告目标必须位于现有目录且使用 .json 扩展名。")
    try:
        with path.open("xb") as output:
            output.write(report.to_bytes())
    except FileExistsError as error:
        raise LegacyPackageError("报告目标已存在；为本次执行选择新的文件名。") from error
    except OSError as error:
        raise LegacyPackageError("无法写入对账报告。") from error
