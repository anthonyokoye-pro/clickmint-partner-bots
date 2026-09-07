"""SQLite backup and integrity verification helpers."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def backup_sqlite(source: str | Path, destination: str | Path) -> dict:
    source = str(source)
    destination = str(destination)
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
        integrity = destination_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"backup integrity check failed: {integrity}")
        tables = destination_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return {"destination": destination, "integrity": integrity,
                "tables": [row[0] for row in tables]}
    finally:
        destination_conn.close()
        source_conn.close()


def verify_backup_set(manifest_path: str | Path) -> dict:
    """Reopen every manifest backup and run integrity checks for restore rehearsal."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    verified = {}
    for name, record in manifest.get("databases", {}).items():
        backup = record["backup"]
        with sqlite3.connect(backup, factory=_ClosingConnection) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        if integrity != "ok":
            raise RuntimeError(f"restore verification failed for {name}: {integrity}")
        verified[name] = {"integrity": integrity, "tables": tables}
    return {"manifest": str(manifest_path), "verified": verified, "restore_verified": True}


def backup_database_set(sources: dict[str, str | Path], destination_dir: str | Path) -> dict:
    """Back up all named SQLite stores and return a portable manifest."""
    destination_dir = Path(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"created_at": int(time.time()), "databases": {}}
    for name, source in sources.items():
        destination = destination_dir / f"{name}.sqlite3"
        result = backup_sqlite(source, destination)
        manifest["databases"][name] = {
            "source": str(source), "backup": str(destination),
            "integrity": result["integrity"], "tables": result["tables"],
            "size_bytes": destination.stat().st_size,
        }
    manifest_path = destination_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest
