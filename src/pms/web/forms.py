"""工作台边界表单；只验证输入格式，不复制领域状态规则。"""

from typing import Any, cast

from django import forms

from pms.web.queries import Option


class StyledForm(forms.Form):
    """为服务端表单统一可访问控件样式。"""

    def apply_styles(self) -> None:
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"field-control {existing}".strip()


class LoginForm(StyledForm):
    username = forms.CharField(
        label="用户名",
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "username"}),
    )
    password = forms.CharField(
        label="密码",
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.apply_styles()


class CodeNameForm(StyledForm):
    code = forms.CharField(label="代码", max_length=64)
    name = forms.CharField(label="名称", max_length=200)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.apply_styles()


class CustomerForm(CodeNameForm):
    """客户组织资料表单；可选税务和银行字段只交给应用服务处理。"""

    short_name = forms.CharField(label="客户简称", max_length=100, required=False)
    tax_identifier = forms.CharField(label="客户税号", max_length=64, required=False)
    address = forms.CharField(label="客户地址", max_length=300, required=False)
    phone = forms.CharField(label="联系电话", max_length=64, required=False)
    bank_name = forms.CharField(label="开户行", max_length=200, required=False)
    bank_account = forms.CharField(label="银行账号", max_length=64, required=False)
    bank_routing_number = forms.CharField(label="银行行号", max_length=64, required=False)


class SupplierForm(CodeNameForm):
    """供应商完整档案表单；列表页不会展示税号和银行字段。"""

    short_name = forms.CharField(label="供应商简称", max_length=100, required=False)
    contact_person = forms.CharField(label="联系人", max_length=100, required=False)
    phone = forms.CharField(label="联系电话", max_length=64, required=False)
    address = forms.CharField(label="地址", max_length=300, required=False)
    tax_identifier = forms.CharField(label="税号", max_length=64, required=False)
    bank_routing_number = forms.CharField(label="银行行号", max_length=64, required=False)
    bank_name = forms.CharField(label="开户银行", max_length=200, required=False)
    bank_account = forms.CharField(label="银行账号", max_length=64, required=False)
    service_description = forms.CharField(label="服务说明", max_length=200, required=False)
    english_name = forms.CharField(label="英文名称", max_length=200, required=False)
    english_address = forms.CharField(label="英文地址", max_length=300, required=False)


class MaterialForm(StyledForm):
    code = forms.CharField(label="物料编码", max_length=64)
    name = forms.CharField(label="物料名称", max_length=200)
    specification = forms.CharField(label="规格型号", max_length=200, required=False)
    brand = forms.CharField(label="品牌", max_length=100, required=False)
    unit_id = forms.ChoiceField(label="单位")
    category_id = forms.ChoiceField(label="物料分类")
    procurement_required = forms.BooleanField(label="需要采购", required=False, initial=True)

    def __init__(
        self,
        *args: Any,
        units: tuple[Option, ...] = (),
        categories: tuple[Option, ...] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["unit_id"]).choices = [
            (str(item.id), item.label) for item in units
        ]
        cast(forms.ChoiceField, self.fields["category_id"]).choices = [
            (str(item.id), item.label) for item in categories
        ]
        self.apply_styles()


class ProjectForm(StyledForm):
    number = forms.CharField(label="项目编号", max_length=64)
    customer_id = forms.ChoiceField(label="客户")
    device_model = forms.CharField(label="设备机型", max_length=200)
    owner_membership_id = forms.ChoiceField(label="项目负责人")
    start_date = forms.DateField(
        label="开始日期", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    planned_completion_date = forms.DateField(
        label="计划完成日期", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )

    def __init__(
        self,
        *args: Any,
        customers: tuple[Option, ...] = (),
        memberships: tuple[Option, ...] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["customer_id"]).choices = [
            (str(item.id), item.label) for item in customers
        ]
        cast(forms.ChoiceField, self.fields["owner_membership_id"]).choices = [
            (str(item.id), item.label) for item in memberships
        ]
        self.apply_styles()


class BomImportForm(StyledForm):
    version_number = forms.IntegerField(label="版本号", min_value=1, initial=1)
    source_file = forms.FileField(
        label="BOM 文件",
        help_text="仅支持 .xlsx / .xlsm，最大 25 MiB；宏和公式不会执行。",
    )
    header_material_code = forms.CharField(label="物料编码列", required=False, initial="物料编码")
    header_material_name = forms.CharField(label="物料名称列", initial="物料名称")
    header_specification = forms.CharField(label="规格型号列", required=False, initial="规格型号")
    header_brand = forms.CharField(label="品牌列", required=False, initial="品牌")
    header_quantity = forms.CharField(label="单台数量列", initial="单台数量")
    header_unit = forms.CharField(label="单位列", initial="单位")
    header_level = forms.CharField(label="层级列", required=False, initial="层级")
    header_remark = forms.CharField(label="备注列", required=False, initial="备注")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.apply_styles()

    def mapping(self) -> dict[str, str]:
        """只把使用者实际填写的表头转换为稳定字段映射。"""
        field_map = {
            "material_code": "header_material_code",
            "material_name": "header_material_name",
            "specification": "header_specification",
            "brand": "header_brand",
            "quantity_per_unit": "header_quantity",
            "unit": "header_unit",
            "level_path": "header_level",
            "remark": "header_remark",
        }
        result: dict[str, str] = {}
        for stable_field, form_field in field_map.items():
            value = self.cleaned_data.get(form_field)
            if isinstance(value, str) and value.strip():
                result[stable_field] = value.strip()
        return result


class MaterialAssignmentForm(StyledForm):
    material_id = forms.ChoiceField(label="确认系统物料")

    def __init__(self, *args: Any, materials: tuple[Option, ...] = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["material_id"]).choices = [
            (str(item.id), item.label) for item in materials
        ]
        self.apply_styles()


class ProductionForm(StyledForm):
    production_units = forms.IntegerField(label="投产台数", min_value=1)
    production_unit = forms.CharField(label="投产单位", max_length=64, initial="台")
    receiving_department = forms.CharField(label="接单部门", max_length=100)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.apply_styles()


class CancelForm(StyledForm):
    reason = forms.CharField(
        label="取消原因", max_length=500, widget=forms.Textarea(attrs={"rows": 3})
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.apply_styles()
