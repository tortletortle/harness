"""Plan API must project the latest clone, not stale historical failures."""
from contextlib import closing
import pytest
from database import Database, DatabaseError
from test_rollback_branching import _three_step_plan, _rollback, _branch_rows


@pytest.mark.parametrize('depth',[1,3])
def test_current_clone_projects_all_status_metadata(tmp_path,depth):
    db=Database(str(tmp_path/'projection.db'))
    pid,ids=_three_step_plan(db,tmp_path)
    for sid in ids[:2]:db.update_step(sid,'Success')
    db.update_step(ids[-1],'Failed')
    with closing(db._get_connection()) as conn,conn:
        conn.execute('UPDATE steps SET failure_reason=?, duration_ms=? WHERE step_id=?',('historical failure',999,ids[-1]))
    for _ in range(depth):branch=_rollback(db,pid,ids[1])
    clone=_branch_rows(db,pid,branch)[0]
    db.update_step(clone['step_id'],'Success',outputs={'result':'latest clone'})
    steps=[s for segment in db.get_plan(str(pid)).segments for s in segment.steps]
    last=next(s for s in steps if str(s.step_id)==ids[-1])
    assert last.status.value=='Success'
    assert last.outputs=={'result':'latest clone'}
    assert last.failure_reason is None
    assert last.duration_ms is None
    assert last.branch_version==branch and not last.is_obsolete
    with closing(db._get_connection()) as conn:
        old=conn.execute('SELECT status,failure_reason FROM steps WHERE step_id=?',(ids[-1],)).fetchone()
    assert tuple(old)==('Failed','historical failure')


def test_cyclic_clone_ancestry_fails_closed(tmp_path):
    db=Database(str(tmp_path/'cycle.db'))
    pid,ids=_three_step_plan(db,tmp_path)
    branch=_rollback(db,pid,ids[1])
    clone=_branch_rows(db,pid,branch)[0]
    with closing(db._get_connection()) as conn,conn:
        conn.execute('UPDATE steps SET parent_step_id=step_id WHERE step_id=?',(clone['step_id'],))
    with pytest.raises(DatabaseError):db.get_plan(str(pid))
