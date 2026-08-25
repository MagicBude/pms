"""由已接受角色权限矩阵定义的稳定权限与角色代码。"""

from enum import StrEnum


class PermissionCode(StrEnum):
    """应用服务检查的稳定能力代码，禁止用角色名称替代。"""

    CONFIGURATION_MANAGE = "configuration.manage"
    MEMBERSHIP_MANAGE = "membership.manage"
    CUSTOMER_VIEW = "customer.view"
    CUSTOMER_MANAGE = "customer.manage"
    SUPPLIER_VIEW = "supplier.view"
    SUPPLIER_MANAGE = "supplier.manage"
    MATERIAL_VIEW = "material.view"
    MATERIAL_MANAGE = "material.manage"
    PROJECT_VIEW = "project.view"
    PROJECT_CREATE = "project.create"
    PROJECT_EDIT = "project.edit"
    PROJECT_ACTIVATE = "project.activate"
    PROJECT_CLOSE = "project.close"
    PROJECT_CANCEL = "project.cancel"
    BOM_VIEW = "bom.view"
    BOM_IMPORT = "bom.import"
    BOM_EDIT = "bom.edit"
    BOM_PUBLISH = "bom.publish"
    BOM_CANCEL = "bom.cancel"
    PRODUCTION_RELEASE_VIEW = "production_release.view"
    PRODUCTION_RELEASE_CREATE = "production_release.create"
    PRODUCTION_RELEASE_RELEASE = "production_release.release"
    PRODUCTION_RELEASE_CANCEL = "production_release.cancel"
    PURCHASE_REQUEST_VIEW = "purchase_request.view"
    PURCHASE_REQUEST_CREATE = "purchase_request.create"
    PURCHASE_REQUEST_SUBMIT = "purchase_request.submit"
    PURCHASE_REQUEST_CANCEL = "purchase_request.cancel"
    PURCHASE_QUOTE_VIEW = "purchase_quote.view"
    PURCHASE_QUOTE_MANAGE = "purchase_quote.manage"
    PURCHASE_ORDER_VIEW = "purchase_order.view"
    PURCHASE_ORDER_MANAGE = "purchase_order.manage"
    ATTACHMENT_DOWNLOAD = "attachment.download"
    AUDIT_VIEW_RELATED = "audit.view_related"
    AUDIT_VIEW_ALL = "audit.view_all"


class RoleCode(StrEnum):
    """首版默认角色代码；角色只是权限组合。"""

    TENANT_ADMIN = "tenant_admin"
    PROJECT_MANAGER = "project_manager"
    BOM_ENGINEER = "bom_engineer"
    REQUESTER = "requester"
    AUDITOR = "auditor"


class PermissionScope(StrEnum):
    """权限在当前租户内的对象范围。"""

    TENANT = "tenant"
    RELATED = "related"
