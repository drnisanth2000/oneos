"""New lifecycle boundary failures remain visible and safe to render."""
from app.console_errors import describe
from app.lifecycle_shape import LifecycleShapeError
from app.lifecycle_journal import JournalError
from app.git_transaction import GitTransactionCommittedError, TransactionResult


def test_shape_failure_is_configuration_refusal():
    result=describe(LifecycleShapeError('untrusted detail'))
    assert result.code=='E-CONFIG' and result.committed=='no'
    assert 'untrusted detail' not in result.message


def test_journal_failure_is_unavailable_before_commit():
    result=describe(JournalError('untrusted detail'))
    assert result.code=='E-UNAVAILABLE' and result.committed=='no'
    assert 'untrusted detail' not in result.message


def test_failed_postcommit_evidence_is_committed_not_retryable():
    cause=JournalError('untrusted detail')
    exc=GitTransactionCommittedError(TransactionResult('a'*40,()),cause)
    exc.__cause__=cause
    result=describe(exc)
    assert result.committed=='yes' and result.retry=='stop'
