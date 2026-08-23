"""受控迁移 JSON 与差异签收的反向边界。"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pms.legacy_migration.reconciliation import ReconciliationBuilder
from pms.legacy_migration.schema import (
    AcceptedDifference,
    LegacyPackageError,
    SampleMetadata,
    load_legacy_slice_package,
    parse_legacy_slice_package,
)

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "migration"
    / "legacy-slice-v1-synthetic.json"
)


@pytest.mark.unit
def test_synthetic_fixture_parses_decimal_without_float() -> None:
    package = load_legacy_slice_package(FIXTURE)

    assert package.sample.kind == "synthetic"
    assert str(package.bom.rows[0].quantity_per_unit) == "2.000000"
    assert package.materials[1].procurement_required is False


@pytest.mark.unit
def test_unknown_or_secret_like_field_is_rejected() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["password"] = "must-not-enter-migration-package"

    with pytest.raises(LegacyPackageError, match="多余=password"):
        parse_legacy_slice_package(payload)


@pytest.mark.unit
def test_quantity_must_be_decimal_string() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["bom"]["rows"][0]["quantity_per_unit"] = 2.0

    with pytest.raises(LegacyPackageError, match="JSON 字符串"):
        parse_legacy_slice_package(payload)


@pytest.mark.unit
def test_synthetic_sample_cannot_accept_a_business_difference() -> None:
    builder = ReconciliationBuilder(
        sample=SampleMetadata(id="synthetic-test", kind="synthetic", confirmed_by=None),
        accepted_differences=(
            AcceptedDifference(
                check_key="purchase.candidates",
                rule_id="BR-PUR-001",
                reason="业务说明",
                accepted_by="业务接受人",
            ),
        ),
    )

    with pytest.raises(LegacyPackageError, match="虚构技术样例"):
        builder.compare(
            check_key="purchase.candidates",
            rule_id="BR-PUR-001",
            legacy_value=1,
            new_value=2,
        )


@pytest.mark.unit
def test_business_sample_records_an_explicit_accepted_difference() -> None:
    builder = ReconciliationBuilder(
        sample=SampleMetadata(
            id="confirmed-test", kind="business_confirmed", confirmed_by="样例确认人"
        ),
        accepted_differences=(
            AcceptedDifference(
                check_key="purchase.candidates",
                rule_id="BR-PUR-001",
                reason="新系统按已接受规则排除不可请购物料",
                accepted_by="差异接受人",
            ),
        ),
        clock=lambda: datetime(2026, 8, 24, tzinfo=UTC),
    )
    builder.compare(
        check_key="purchase.candidates",
        rule_id="BR-PUR-001",
        legacy_value=2,
        new_value=1,
    )

    report = builder.build()

    assert report.overall_status == "ACCEPTED_DIFFERENCES"
    assert report.acceptance_scope == "BUSINESS_CONFIRMED"
    assert report.checks[0].accepted_by == "差异接受人"
