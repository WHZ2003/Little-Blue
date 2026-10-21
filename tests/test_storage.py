from little_blue.storage import Store
from little_blue.errors import BudgetExceeded
import pytest


def test_memory_and_state_survive_restart(tmp_path):
    path = tmp_path / 'pet.db'
    store = Store(path)
    store.remember('用户喜欢蓝莓茶', 'semantic')
    store.remember('We explored the desktop together')
    store.put('life', {'energy': 42})
    store.close()
    store = Store(path)
    assert store.get('life')['energy'] == 42
    assert store.search('蓝莓')[0]['kind'] == 'semantic'
    assert 'explored' in store.search('explored')[0]['text']
    store.close()


def test_fts_special_input_never_executes_sql(tmp_path):
    store = Store(tmp_path / 'pet.db')
    store.remember('hello world')
    for query in ['" OR * NEAR(', "'; DROP TABLE memories;--", '', '*']:
        store.search(query)
    assert store.search('hello')
    store.close()


def test_interrupted_tasks_are_not_resumed(tmp_path):
    path = tmp_path / 'pet.db'
    store = Store(path)
    store.task('old', 'approval', 'delete something')
    store.task('done', 'succeeded', 'finished')
    store.close()
    store = Store(path)
    statuses = {row['id']: row['status'] for row in store.tasks()}
    assert statuses == {'old': 'interrupted', 'done': 'succeeded'}
    store.close()


def test_budget_persists_and_counts_failed_reservations(tmp_path):
    path = tmp_path / 'pet.db'
    store = Store(path)
    store.reserve(2, 1000, 300)
    store.close()
    store = Store(path)
    store.reserve(2, 1000, 300)
    with pytest.raises(RuntimeError):
        store.reserve(2, 1000, 1)
    store.close()


def test_reminders_deliver_once_after_restart(tmp_path):
    path = tmp_path / 'pet.db'
    store = Store(path)
    store.add_reminder('tea', 100)
    store.close()
    store = Store(path)
    assert store.due_reminders(99) == []
    assert store.due_reminders(101)[0]['text'] == 'tea'
    assert store.due_reminders(102) == []
    store.close()


def test_usage_settlement_persists_and_is_idempotent(tmp_path):
    path = tmp_path / 'budget.db'
    store = Store(path)
    ticket = store.reserve(10, 1000, 800)
    with pytest.raises(BudgetExceeded):
        store.reserve(10, 1000, 300)
    assert store.settle_budget(ticket, {'prompt_tokens': 80, 'completion_tokens': 20, 'total_tokens': 100})
    assert store.budget_usage() == {'requests': 1, 'reserved': 100}
    store.close()
    store = Store(path)
    assert not store.settle_budget(ticket, {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2})
    store.reserve(10, 1000, 300)
    assert store.budget_usage() == {'requests': 2, 'reserved': 400}
    store.close()


@pytest.mark.parametrize('usage', [None, {}, [], {'total_tokens': 3},
    {'prompt_tokens': True, 'completion_tokens': 1, 'total_tokens': 2},
    {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 29},
    {'prompt_tokens': -1, 'completion_tokens': 3, 'total_tokens': 2},
    {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}])
def test_missing_or_invalid_usage_keeps_reservation(tmp_path, usage):
    store = Store(tmp_path / 'budget.db')
    ticket = store.reserve(10, 1000, 800)
    assert not store.settle_budget(ticket, usage)
    assert store.budget_usage() == {'requests': 1, 'reserved': 800}
    store.close()


def test_settlement_charges_original_day_and_records_actual_overrun(tmp_path):
    store = Store(tmp_path / 'budget.db')
    # An old request completes after midnight; today's ledger must not be credited.
    with store.db:
        store.db.execute("INSERT INTO budget VALUES('2000-01-01',1,100)")
        store.db.execute("INSERT INTO budget_reservations VALUES('old','2000-01-01',100,NULL)")
    store.reserve(10, 1000, 500)
    assert store.settle_budget('old', {'prompt_tokens': 110, 'completion_tokens': 10, 'total_tokens': 120})
    assert store.db.execute("SELECT reserved FROM budget WHERE day='2000-01-01'").fetchone()[0] == 120
    assert store.budget_usage()['reserved'] == 500
    assert not store.settle_budget('unknown', {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2})
    store.close()
