from uuid import uuid4

from call_intelligence_api.deletion import thread_uses_call


def test_only_threads_that_touched_the_deleted_call_are_purged() -> None:
    deleted, other = uuid4(), uuid4()
    assert thread_uses_call(deleted, None, [], deleted)  # the call's own chat
    assert thread_uses_call(None, [str(other), str(deleted)], [], deleted)  # selected it
    answered = [[{"call_id": str(other)}, {"call_id": str(deleted)}]]
    assert thread_uses_call(None, None, answered, deleted)  # org-wide answer drew on it
    assert not thread_uses_call(None, [str(other)], [[{"call_id": str(other)}]], deleted)
    assert not thread_uses_call(None, None, [None, []], deleted)  # org-wide thread that never used it
