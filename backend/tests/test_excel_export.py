from __future__ import annotations

from datetime import date

from app.excel_export import build_employee_import_template, ordered_statistics_score_rows


def test_ordered_statistics_score_rows_follow_hierarchy_then_fallback() -> None:
    data = {
        "hierarchy": [
            {"node_type": "attraction", "employee_id": None},
            {"node_type": "employee", "employee_id": 2},
            {"node_type": "employee", "employee_id": 1},
        ],
        "scores": [
            {"employee_id": 1, "attraction_name": "A", "employee_name": "甲", "employee_no": "1000001"},
            {"employee_id": 2, "attraction_name": "A", "employee_name": "乙", "employee_no": "1000002"},
            {"employee_id": 3, "attraction_name": "B", "employee_name": "丙", "employee_no": "1000003"},
        ],
    }
    ordered = ordered_statistics_score_rows(data)
    assert [row["employee_id"] for row in ordered] == [2, 1, 3]


def test_employee_import_template_sheets_and_headers() -> None:
    wb = build_employee_import_template()
    assert wb.sheetnames == ["员工导入", "填写说明"]
    headers = [cell.value for cell in wb["员工导入"][1]]
    assert headers == ["员工号", "姓名", "角色代码", "景点圈", "初始密码", "在职", "账号启用", "任职开始日", "任职结束日", "到期恢复角色代码"]
    sample = [cell.value for cell in wb["员工导入"][2]]
    assert sample[0] == "1000001"
    assert sample[7] == date.today().isoformat()
    assert "可用角色代码" in [cell.value for cell in wb["填写说明"][1]]
