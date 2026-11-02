"""The keepsake reuses only the existing self dashboard on an isolated database."""
import pytest
from test_monthly_self_ranks import MONTH, system  # noqa: F401
from test_self_honors import recognition


@pytest.mark.parametrize("base,duty", [("CM", None), ("TR", None), ("CM", "TA_SUPERVISOR"), ("SUPERVISOR", None)])
def test_memory_dashboard_source_is_self_only_and_keeps_zero_credit(system, base, duty):
    db, client, circles, _, make, switch, _, admin = system
    person = make("memory-" + (duty or base), base, points=12)
    other = make("memory-other", points=12)
    own = recognition(db, person, admin, circles[0], "主动协助伙伴", credit=0)
    foreign = recognition(db, other, admin, circles[0], "其他员工的认可")
    previous = recognition(db, person, admin, circles[0], "其他月份的认可", month="2026-09")
    pending = recognition(db, person, admin, circles[0], "尚未确认", status="pending")
    switch(person, base=base, duty=duty)
    response = client.get("/api/dashboard", params={"month": MONTH, "employee_id": other.id})
    assert response.status_code == 200, response.text
    data = response.json()
    rows = data["records"]
    assert all(row["employee_id"] == person.id for row in rows)
    assert foreign.id not in {row["id"] for row in rows}
    assert previous.id not in {row["id"] for row in rows}
    confirmed = [row for row in rows if row["status"] == "confirmed"]
    assert [row["id"] for row in confirmed] == [own.id]
    assert confirmed[0]["credited_fraction"] == 0
    assert confirmed[0]["content"] == "主动协助伙伴"
    assert pending.id in {row["id"] for row in rows}
    assert not data["month_closed"]  # A keepsake does not depend on a medal or month close.
