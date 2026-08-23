"""项目聚合的租户级 ORM 映射。"""

import uuid

from django.db import models

from pms.master_data.infrastructure.django.models import Customer
from pms.projects.domain.lifecycle import ProjectStatus
from pms.tenancy.infrastructure.django.models import Membership, Tenant


class Project(models.Model):
    """从草稿到关闭/取消的项目聚合根。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="projects")
    number = models.CharField(max_length=64)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="projects")
    device_model = models.CharField(max_length=200)
    start_date = models.DateField(null=True, blank=True)
    planned_completion_date = models.DateField(null=True, blank=True)
    owner_membership = models.ForeignKey(
        Membership, on_delete=models.PROTECT, related_name="owned_projects"
    )
    created_by_membership = models.ForeignKey(
        Membership, on_delete=models.PROTECT, related_name="created_projects"
    )
    status = models.CharField(
        max_length=16,
        choices=[(status.value, status.value) for status in ProjectStatus],
        default=ProjectStatus.DRAFT.value,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "projects_project"
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(fields=("tenant", "number"), name="uq_project_tenant_number"),
            models.CheckConstraint(
                condition=models.Q(status__in=[status.value for status in ProjectStatus]),
                name="ck_project_status_valid",
            ),
        ]
