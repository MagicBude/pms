"""PMS 工作台 URL；所有状态变化只接受 POST。"""

from django.urls import path

from pms.web import views

urlpatterns = [
    path("", views.dashboard_view, name="web-dashboard"),
    path("login/", views.login_view, name="web-login"),
    path("logout/", views.logout_view, name="web-logout"),
    path("customers/", views.customer_list_view, name="web-customer-list"),
    path("customers/new/", views.customer_create_view, name="web-customer-create"),
    path("suppliers/", views.supplier_list_view, name="web-supplier-list"),
    path("suppliers/new/", views.supplier_create_view, name="web-supplier-create"),
    path("materials/", views.material_list_view, name="web-material-list"),
    path("units/new/", views.unit_create_view, name="web-unit-create"),
    path("categories/new/", views.category_create_view, name="web-category-create"),
    path("materials/new/", views.material_create_view, name="web-material-create"),
    path("projects/", views.project_list_view, name="web-project-list"),
    path("projects/new/", views.project_create_view, name="web-project-create"),
    path("projects/<uuid:project_id>/", views.project_detail_view, name="web-project-detail"),
    path("projects/<uuid:project_id>/bom/import/", views.bom_import_view, name="web-bom-import"),
    path(
        "projects/<uuid:project_id>/<str:action>/",
        views.project_action_view,
        name="web-project-action",
    ),
    path("boms/<uuid:bom_id>/", views.bom_detail_view, name="web-bom-detail"),
    path("boms/<uuid:bom_id>/publish/", views.bom_publish_view, name="web-bom-publish"),
    path("boms/<uuid:bom_id>/cancel/", views.bom_cancel_view, name="web-bom-cancel"),
    path(
        "boms/<uuid:bom_id>/lines/<uuid:line_id>/material/",
        views.bom_assign_material_view,
        name="web-bom-assign-material",
    ),
    path(
        "boms/<uuid:bom_id>/lines/<uuid:line_id>/confirm-duplicate/",
        views.bom_confirm_duplicate_view,
        name="web-bom-confirm-duplicate",
    ),
    path(
        "projects/<uuid:project_id>/boms/<uuid:bom_id>/production/new/",
        views.production_create_view,
        name="web-production-create",
    ),
    path(
        "production/<uuid:production_id>/",
        views.production_detail_view,
        name="web-production-detail",
    ),
    path(
        "production/<uuid:production_id>/release/",
        views.production_release_view,
        name="web-production-release",
    ),
    path(
        "production/<uuid:production_id>/cancel/",
        views.production_cancel_view,
        name="web-production-cancel",
    ),
    path(
        "production/<uuid:production_id>/requests/new/",
        views.request_create_view,
        name="web-request-create",
    ),
    path("requests/<uuid:request_id>/", views.request_detail_view, name="web-request-detail"),
    path(
        "requests/<uuid:request_id>/submit/",
        views.request_submit_view,
        name="web-request-submit",
    ),
    path(
        "requests/<uuid:request_id>/cancel/",
        views.request_cancel_view,
        name="web-request-cancel",
    ),
    path(
        "requests/<uuid:request_id>/lines/<uuid:line_id>/quotes/new/",
        views.quote_create_view,
        name="web-quote-create",
    ),
    path(
        "requests/<uuid:request_id>/quotes/<uuid:quote_id>/withdraw/",
        views.quote_withdraw_view,
        name="web-quote-withdraw",
    ),
    path(
        "requests/<uuid:request_id>/quotes/<uuid:quote_id>/select/",
        views.quote_select_view,
        name="web-quote-select",
    ),
    path("orders/", views.purchase_order_list_view, name="web-purchase-order-list"),
    path(
        "orders/<uuid:order_id>/",
        views.purchase_order_detail_view,
        name="web-purchase-order-detail",
    ),
    path(
        "requests/<uuid:request_id>/orders/new/",
        views.purchase_order_create_view,
        name="web-purchase-order-create",
    ),
    path(
        "orders/<uuid:order_id>/issue/",
        views.purchase_order_issue_view,
        name="web-purchase-order-issue",
    ),
    path(
        "orders/<uuid:order_id>/cancel/",
        views.purchase_order_cancel_view,
        name="web-purchase-order-cancel",
    ),
    path(
        "orders/<uuid:order_id>/documents/new/",
        views.purchase_order_document_generate_view,
        name="web-purchase-order-document-generate",
    ),
    path(
        "order-documents/<uuid:attachment_id>/download/",
        views.purchase_order_document_download_view,
        name="web-purchase-order-document-download",
    ),
    path(
        "attachments/<uuid:attachment_id>/download/",
        views.attachment_download_view,
        name="web-attachment-download",
    ),
]
