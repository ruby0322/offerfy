import pytest
from sqlalchemy.exc import IntegrityError

from app.models import GuestSession, Resume, ResumeImport, ResumeImportStep


@pytest.fixture
def resume(db_session):
    guest = GuestSession(key_hash="deadbeef-import-models", locale="en")
    db_session.add(guest)
    db_session.commit()
    resume = Resume(
        title="R",
        typst_source="= R",
        source="upload",
        guest_session_id=guest.id,
    )
    db_session.add(resume)
    db_session.commit()
    return resume


def test_import_run_defaults(db_session, resume):
    run = ResumeImport(resume_id=resume.id, status="running", file_key="uploads/k")
    db_session.add(run)
    db_session.flush()
    assert run.version == 0 and run.applied == 0 and run.id


def test_import_status_check_constraint(db_session, resume):
    db_session.add(ResumeImport(resume_id=resume.id, status="bogus"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_step_key_unique_per_import(db_session, resume):
    run = ResumeImport(resume_id=resume.id, status="running")
    db_session.add(run)
    db_session.commit()
    db_session.add(
        ResumeImportStep(
            import_id=run.id, seq=0, key="education", label="Education", status="pending"
        )
    )
    db_session.commit()
    db_session.add(
        ResumeImportStep(
            import_id=run.id, seq=1, key="education", label="Education again", status="pending"
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_step_status_check_constraint(db_session, resume):
    run = ResumeImport(resume_id=resume.id, status="running")
    db_session.add(run)
    db_session.commit()
    db_session.add(
        ResumeImportStep(
            import_id=run.id, seq=0, key="education", label="Education", status="bogus"
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_resume_imports_relationship(db_session, resume):
    run = ResumeImport(resume_id=resume.id, status="running")
    db_session.add(run)
    db_session.commit()
    db_session.refresh(resume)
    assert [r.id for r in resume.imports] == [run.id]
