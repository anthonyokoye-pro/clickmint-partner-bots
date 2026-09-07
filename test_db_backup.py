import sqlite3
import tempfile
from pathlib import Path
from db_backup import backup_database_set, backup_sqlite, verify_backup_set


def test_database_set_manifest():
    directory = tempfile.TemporaryDirectory(prefix="clickmint-backup-set-")
    try:
        source = Path(directory.name) / "tasks.sqlite3"
        with sqlite3.connect(source) as conn:
            conn.execute("CREATE TABLE tasks (id INTEGER)")
        manifest = backup_database_set({"tasks": source}, Path(directory.name) / "backups")
        assert manifest["databases"]["tasks"]["integrity"] == "ok"
        manifest_path = Path(directory.name) / "backups" / "manifest.json"
        assert manifest_path.is_file()
        assert verify_backup_set(manifest_path)["restore_verified"]
    finally:
        directory.cleanup()


def test_sqlite_backup_integrity():
    directory = tempfile.TemporaryDirectory(prefix="clickmint-backup-")
    try:
        source = Path(directory.name) / "source.sqlite3"
        destination = Path(directory.name) / "backup.sqlite3"
        with sqlite3.connect(source) as conn:
            conn.execute("CREATE TABLE sample (value TEXT)")
            conn.execute("INSERT INTO sample VALUES ('ok')")
        result = backup_sqlite(source, destination)
        assert result["integrity"] == "ok"
        with sqlite3.connect(destination) as conn:
            assert conn.execute("SELECT value FROM sample").fetchone()[0] == "ok"
    finally:
        directory.cleanup()


if __name__ == "__main__":
    test_database_set_manifest()
    test_sqlite_backup_integrity()
    print("PASS test_database_set_manifest")
    print("PASS test_sqlite_backup_integrity")
