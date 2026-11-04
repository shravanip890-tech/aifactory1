"""Explicit demo-data seeding:  python -m backend.seed
Demo projects are flagged is_demo=1, left in 'Queued' (never faked as Completed)."""
from . import database as db
from .schemas import ProjectCreate
from .services import orchestrator as orch

DEMO = [("Demo Corner Store Billing", "Billing", "DEMO DATA: sample billing project."),
        ("Demo GST Invoicing", "GST", "DEMO DATA: sample GST invoice project."),
        ("Demo Warehouse Stock", "Inventory", "DEMO DATA: sample inventory project."),
        ("Demo Client Contacts", "CRM", "DEMO DATA: sample CRM project.")]


def seed() -> int:
    db.init_db()
    with db.connect() as conn:
        if conn.execute("SELECT COUNT(*) FROM projects WHERE is_demo=1").fetchone()[0]:
            return 0
    for name, cat, desc in DEMO:
        orch.create_project(ProjectCreate(name=name, category=cat, description=desc), is_demo=True)
    return len(DEMO)


if __name__ == "__main__":
    print(f"Seeded {seed()} demo projects (flagged as demo data).")
