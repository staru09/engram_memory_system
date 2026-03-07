import os
import subprocess
import sys
import time
import requests

PG_CONTAINER = "evermemos_postgres"
PG_USER = "evermemos"
PG_DB = "evermemos"
QDRANT_URL = "http://localhost:6333"
BACKUP_DIR = "db_backup"


def status():
    """Show row/point counts across all stores."""
    print("=== PostgreSQL ===")
    query = (
        "SELECT 'memcells' AS tbl, COUNT(*) FROM memcells "
        "UNION ALL SELECT 'atomic_facts', COUNT(*) FROM atomic_facts "
        "UNION ALL SELECT 'memscenes', COUNT(*) FROM memscenes "
        "UNION ALL SELECT 'conflicts', COUNT(*) FROM conflicts "
        "UNION ALL SELECT 'foresight', COUNT(*) FROM foresight "
        "UNION ALL SELECT 'user_profile', COUNT(*) FROM user_profile;"
    )
    result = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB, "-c", query],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  Error: {result.stderr.strip()}")
    else:
        print(result.stdout.strip())

    print("\n=== Qdrant ===")
    for collection in ("facts", "scenes"):
        try:
            resp = requests.get(f"{QDRANT_URL}/collections/{collection}", timeout=5)
            if resp.status_code == 404 or "not found" in resp.text.lower():
                print(f"  {collection}: does not exist")
            else:
                data = resp.json()
                count = data["result"]["points_count"]
                print(f"  {collection}: {count} points")
        except Exception as e:
            print(f"  {collection}: Error — {e}")


def backup():
    """Backup PostgreSQL + Qdrant to db_backup/ directory."""
    os.makedirs(BACKUP_DIR, exist_ok=True)

    # Step 1: PostgreSQL pg_dump
    print("=== Step 1: Backing up PostgreSQL ===")
    pg_path = f"{BACKUP_DIR}/pg_backup.sql"
    result = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "pg_dump", "-U", PG_USER, "-d", PG_DB],
        capture_output=True,
    )
    if result.returncode != 0:
        print(f"  Error: {result.stderr.decode('utf-8', errors='replace').strip()}")
        return
    with open(pg_path, "wb") as f:
        f.write(result.stdout)
    size_kb = len(result.stdout) / 1024
    print(f"  Saved {pg_path} ({size_kb:.0f} KB)")

    # Step 2: Qdrant snapshots
    print("\n=== Step 2: Backing up Qdrant ===")
    for collection in ("facts", "scenes"):
        snapshot_path = f"{BACKUP_DIR}/{collection}_snapshot.snapshot"
        try:
            # Create snapshot
            resp = requests.post(
                f"{QDRANT_URL}/collections/{collection}/snapshots", timeout=120
            )
            if resp.status_code != 200:
                print(f"  {collection}: Error creating snapshot — {resp.status_code} {resp.text}")
                continue
            snapshot_name = resp.json()["result"]["name"]

            # Download snapshot
            dl_resp = requests.get(
                f"{QDRANT_URL}/collections/{collection}/snapshots/{snapshot_name}",
                stream=True, timeout=120,
            )
            if dl_resp.status_code != 200:
                print(f"  {collection}: Error downloading snapshot — {dl_resp.status_code}")
                continue
            with open(snapshot_path, "wb") as f:
                for chunk in dl_resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            size_kb = os.path.getsize(snapshot_path) / 1024
            print(f"  Saved {snapshot_path} ({size_kb:.0f} KB)")

            # Clean up remote snapshot
            requests.delete(
                f"{QDRANT_URL}/collections/{collection}/snapshots/{snapshot_name}",
                timeout=30,
            )
        except Exception as e:
            print(f"  {collection}: Error — {e}")

    print("\n=== Backup complete ===")
    status()


def reset():
    """Wipe all data: docker compose down -v + up -d."""
    print("Stopping containers and removing volumes...")
    subprocess.run(["docker", "compose", "down", "-v"])
    print("Starting fresh containers...")
    subprocess.run(["docker", "compose", "up", "-d"])
    print("Done. Containers running with empty volumes.")


def restore():
    """Full restore: reset containers, then restore PG + Qdrant from backups."""
    # Step 1: Fresh containers
    print("=== Step 1: Resetting containers ===")
    subprocess.run(["docker", "compose", "down", "-v"])
    subprocess.run(["docker", "compose", "up", "-d"])

    # Wait for PostgreSQL to be ready
    print("\nWaiting for PostgreSQL to be ready...")
    for i in range(30):
        result = subprocess.run(
            ["docker", "exec", PG_CONTAINER, "pg_isready", "-U", PG_USER],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print("  PostgreSQL is ready.")
            break
        time.sleep(1)
    else:
        print("  Error: PostgreSQL did not start in time.")
        return

    # Step 2: Restore PostgreSQL (convert UTF-16 → UTF-8 if needed, via temp file)
    print("\n=== Step 2: Restoring PostgreSQL ===")
    with open(f"{BACKUP_DIR}/pg_backup.sql", "rb") as f:
        raw = f.read()
    # Detect and convert UTF-16 to UTF-8
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff') or b'\x00' in raw[:100]:
        sql_bytes = raw.decode("utf-16").encode("utf-8")
    else:
        sql_bytes = raw
    # Pipe bytes directly to avoid Windows encoding issues
    result = subprocess.run(
        ["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB],
        input=sql_bytes, capture_output=True,
    )
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    if result.returncode != 0 and stderr:
        errors = [l for l in stderr.split("\n") if "ERROR" in l]
        if errors:
            print(f"  Errors: {chr(10).join(errors)}")
        else:
            print("  PostgreSQL restored (with warnings).")
    else:
        print("  PostgreSQL restored.")

    # Step 3: Restore Qdrant
    print("\n=== Step 3: Restoring Qdrant ===")
    for collection in ("facts", "scenes"):
        snapshot_path = f"{BACKUP_DIR}/{collection}_snapshot.snapshot"
        try:
            with open(snapshot_path, "rb") as f:
                resp = requests.post(
                    f"{QDRANT_URL}/collections/{collection}/snapshots/upload",
                    files={"snapshot": f},
                    timeout=120,
                )
            if resp.status_code == 200:
                print(f"  {collection}: restored.")
            else:
                print(f"  {collection}: Error — {resp.status_code} {resp.text}")
        except Exception as e:
            print(f"  {collection}: Error — {e}")

    print("\n=== Restore complete ===")
    status()


def scenes():
    """Print the current state of the memscenes table with assigned MemCell counts."""
    query = (
        "SELECT s.id, s.theme_label, s.summary, "
        "COUNT(m.id) AS memcell_count, "
        "s.created_at, s.updated_at "
        "FROM memscenes s "
        "LEFT JOIN memcells m ON m.scene_id = s.id "
        "GROUP BY s.id, s.theme_label, s.summary, s.created_at, s.updated_at "
        "ORDER BY s.id;"
    )
    result = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB, "-c", query],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"Error: {result.stderr.strip()}")
    else:
        print("=== MemScenes ===")
        print(result.stdout.strip())


def foresight():
    """Print the full contents of the foresight table."""
    query = "SELECT * FROM foresight ORDER BY valid_from ASC;"
    result = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB, "-c", query],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"Error: {result.stderr.strip()}")
    else:
        print("=== Foresight ===")
        print(result.stdout.strip())


def main():
    if len(sys.argv) < 2:
        print("Usage: python db_manage.py <command>")
        print("  status    — Show row/point counts")
        print("  backup    — Backup PG + Qdrant to db_backup/")
        print("  reset     — Wipe all data (docker compose down -v + up -d)")
        print("  restore   — Reset + restore from db_backup/")
        print("  scenes    — Show all MemScenes with assigned MemCell counts")
        print("  foresight — Show all contents of the foresight table")
        return

    cmd = sys.argv[1]
    if cmd == "status":
        status()
    elif cmd == "backup":
        backup()
    elif cmd == "reset":
        reset()
    elif cmd == "restore":
        restore()
    elif cmd == "scenes":
        scenes()
    elif cmd == "foresight":
        foresight()
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
