from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent / "travelagent.db"

if not DB.exists():
    raise SystemExit(f"Database not found: {DB}")

backup = DB.with_suffix(DB.suffix + ".bak")
shutil.copy2(DB, backup)
print(f"Backup created: {backup}")

with sqlite3.connect(DB) as conn:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(trip_memories)")}
    if "conversation" not in columns:
        conn.execute(
            "ALTER TABLE trip_memories "
            "ADD COLUMN conversation JSON NOT NULL DEFAULT '[]'"
        )
        conn.commit()
        print("Added column: trip_memories.conversation")
    else:
        print("Column already exists: trip_memories.conversation")

print("Migration complete.")
