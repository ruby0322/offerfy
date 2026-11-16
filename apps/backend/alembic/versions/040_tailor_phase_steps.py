"""allow the tailor pass's four stage names in tailor_runs.phase

The pass used to report three phases, of which only `reading` was ever set at
all (by `start_run`) -- `assessing` came from any narration and `writing` from
an edit, so a 60-90 second pass showed one unchanging line and then a wall of
prose. It now has four stages, each entered by the tool that does it: read the
resume, read the posting, map the posting's requirements against the resume,
propose changes. The card draws them as a checklist, with `phase` as the
cursor. See app.services.tailor_run.PHASES.

Widening only: every value that was legal stays legal, so no row can violate
the new constraint and there is nothing to backfill. The three old names are
kept legal on purpose rather than tidied away -- a pass can outlive this
deploy. Only `finish`/`fail` clear `phase`, a **stale** run keeps its last
phase forever, and the runner in the other uvicorn worker goes on writing the
old names until its task ends, so narrowing here could fail on live data.

sqlite is skipped the same way 015/016/034/035 skip it: it cannot ALTER a
constraint, and the test suite builds its schema from the models with
create_all rather than from alembic. No test runs alembic; this is checked by
eye against the models and against a throwaway Postgres.
"""

from alembic import op

revision = "040_tailor_phase_steps"
down_revision = "039_line_source"
branch_labels = None
depends_on = None

_OLD = "phase IS NULL OR phase IN ('reading', 'assessing', 'writing')"
_NEW = (
    "phase IS NULL OR phase IN ("
    "'read_resume', 'read_job', 'mapping', 'proposing', "
    "'reading', 'assessing', 'writing')"
)


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    op.drop_constraint("tailor_runs_phase_values", "tailor_runs", type_="check")
    op.create_check_constraint("tailor_runs_phase_values", "tailor_runs", _NEW)


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    # Fails while any row holds one of the four new names, which is correct:
    # the narrower constraint cannot describe that data. A run still `running`
    # is the likely holder, so end it (or clear its phase) before going back.
    op.drop_constraint("tailor_runs_phase_values", "tailor_runs", type_="check")
    op.create_check_constraint("tailor_runs_phase_values", "tailor_runs", _OLD)
