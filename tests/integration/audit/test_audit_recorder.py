"""审计追加、敏感字段拒绝与不可变行为测试。"""

import pytest
from django.contrib.auth import get_user_model

from pms.audit.domain.events import AuditEvent, AuditResult
from pms.audit.infrastructure.django.models import AuditLog, AuditLogImmutableError
from pms.audit.infrastructure.django.recorder import DjangoAuditRecorder, SensitiveAuditDataError
from pms.tenancy.domain.context import MembershipId, TenantId, UserId
from pms.tenancy.infrastructure.django.models import Membership, Tenant


def build_audit_event(
    *,
    result: AuditResult = AuditResult.SUCCESS,
    summary: dict[str, object] | None = None,
) -> AuditEvent:
    user = get_user_model().objects.create_user(username="audit-user")
    tenant = Tenant.objects.create(code="audit-tenant", name="Audit Factory")
    membership = Membership.objects.create(user=user, tenant=tenant)
    return AuditEvent(
        tenant_id=TenantId(tenant.id),
        actor_id=UserId(user.id),
        membership_id=MembershipId(membership.id),
        action="project.activate",
        object_type="project",
        object_id="PROJECT-001",
        result=result,
        summary=(
            summary if summary is not None else {"from_status": "draft", "to_status": "active"}
        ),
    )


@pytest.mark.django_db
@pytest.mark.parametrize("result", list(AuditResult))
def test_recorder_appends_complete_audit_event(result: AuditResult) -> None:
    event = build_audit_event(result=result)

    audit_id = DjangoAuditRecorder().record(event)

    saved = AuditLog.objects.get(id=audit_id)
    assert saved.tenant_id == event.tenant_id
    assert saved.actor_id == event.actor_id
    assert saved.membership_id == event.membership_id
    assert saved.action == "project.activate"
    assert saved.result == result


@pytest.mark.django_db
@pytest.mark.parametrize(
    "sensitive_key",
    ["password", "access_token", "session-cookie", "attachment_body", "file_content"],
)
def test_recorder_rejects_sensitive_fields_at_any_depth(sensitive_key: str) -> None:
    event = build_audit_event(
        summary={"changes": [{"details": {sensitive_key: "must-not-be-stored"}}]}
    )

    with pytest.raises(SensitiveAuditDataError) as exception_info:
        DjangoAuditRecorder().record(event)

    assert "must-not-be-stored" not in str(exception_info.value)
    assert AuditLog.objects.count() == 0


@pytest.mark.django_db
def test_existing_audit_cannot_be_updated_or_deleted_through_normal_orm() -> None:
    audit_id = DjangoAuditRecorder().record(build_audit_event())
    saved = AuditLog.objects.get(id=audit_id)

    saved.action = "tampered"
    with pytest.raises(AuditLogImmutableError):
        saved.save()
    with pytest.raises(AuditLogImmutableError):
        saved.delete()
    with pytest.raises(AuditLogImmutableError):
        AuditLog.objects.filter(id=audit_id).update(action="tampered")
    with pytest.raises(AuditLogImmutableError):
        AuditLog.objects.filter(id=audit_id).delete()
