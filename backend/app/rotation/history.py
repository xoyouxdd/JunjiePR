"""Read historical corrections written before line undo was retired."""
from bisect import bisect_right
import json


def reverted_legacy_push7(db, attraction_id, events, restored):
    """Find old successful events from before executions carried explicit IDs."""
    from app.rotation.namespace import tables_for

    days = {event.work_date for event, detail in events if event.line and not detail.get('push7_execution')}
    days.intersection_update(restored)
    if not days:
        return set()
    model = tables_for(db).RotationEvent
    checkpoints = {day: [] for day in days}
    ids = {day: [] for day in days}
    for row in db.query(model.id, model.work_date, model.minute, model.detail_json).filter(
            model.attraction_id == attraction_id, model.work_date.in_(days),
            model.event_type == 'workflow_checkpoint').order_by(model.id):
        ids[row.work_date].append(row.id)
        checkpoints[row.work_date].append(row)
    reverted = set()
    for event, detail in events:
        if event.work_date not in days or detail.get('push7_execution'):
            continue
        # The checkpoint follows the action's event in the same transaction.
        i = bisect_right(ids[event.work_date], event.id)
        rows = checkpoints[event.work_date]
        if i == len(rows) or rows[i].id not in restored[event.work_date] or rows[i].minute != event.minute:
            continue
        target = detail.get('target') or next((m['pid'] for m in detail.get('moves', []) if m['to'] is None), None)
        entry = json.loads(rows[i].detail_json)
        people = entry['beforePeople']
        before = people.get(target) or {}
        if (event.line in entry['lines'] and event.employee_no in people and before.get('state') == 'onpost'
                and not before.get('sevenReplaced')):
            reverted.add(event.id)
    return reverted
