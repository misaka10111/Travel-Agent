from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.planning import PlanningCommand, PlanningEvent, PlanningMigration, PlanningSession


def migrate_planning(engine):
    """P1 creates independent tables; never rewrites or drops legacy trip data."""
    for model in (PlanningMigration, PlanningSession, PlanningCommand, PlanningEvent):
        model.__table__.create(engine, checkfirst=True)
    with Session(engine) as db:
        if not db.scalar(select(PlanningMigration).where(PlanningMigration.version == "p1_001")):
            db.add(PlanningMigration(version="p1_001"))
            db.commit()
