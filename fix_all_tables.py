#!/usr/bin/env python3
"""
COMPREHENSIVE DATABASE FIX SCRIPT
Checks ALL tables and adds ALL missing columns from migration 0013
Run this from your project directory: python fix_all_tables.py
"""

import sqlite3
import os
import sys
import shutil
from datetime import datetime

# Database configuration
DB_PATH = 'db.sqlite3'
BACKUP_PATH = f'db.sqlite3.backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

# Define all missing columns for each table (from migration 0013)
TABLES_TO_FIX = {
    'donations_surplusfoodrequest': {
        'posted_at': 'datetime',
        'restaurant_lat': 'real',
        'restaurant_lng': 'real',
        'donation_status': 'varchar(20) DEFAULT "posted" NOT NULL',
        'current_radius_km': 'integer DEFAULT 5 NOT NULL',
        'ngos_notified_at': 'datetime',
        'last_radius_expansion_at': 'datetime',
        'notified_ngo_ids': 'TEXT NOT NULL DEFAULT "[]"',
        'expiry_reason': 'varchar(50)',
        'archived_at': 'datetime',
    },
    'donations_ngoprofile': {
        'current_lat': 'real',
        'current_lng': 'real',
        'priority_score': 'integer DEFAULT 0 NOT NULL',
    }
}

# Indexes to create
INDEXES_TO_CREATE = [
    ("donations_s_donatio_b64344_idx", 
     "CREATE INDEX IF NOT EXISTS donations_s_donatio_b64344_idx ON donations_surplusfoodrequest (donation_status, expiry_at)"),
    ("donations_s_restaur_8dcc58_idx",
     "CREATE INDEX IF NOT EXISTS donations_s_restaur_8dcc58_idx ON donations_surplusfoodrequest (restaurant_id, donation_status)"),
    ("donations_d_donatio_67820a_idx",
     "CREATE INDEX IF NOT EXISTS donations_d_donatio_67820a_idx ON donations_donationnotificationlog (donation_id, status)"),
    ("donations_d_ngo_id_531e51_idx",
     "CREATE INDEX IF NOT EXISTS donations_d_ngo_id_531e51_idx ON donations_donationnotificationlog (ngo_id, status)"),
    ("donations_d_ngo_id_9af8c2_idx",
     "CREATE INDEX IF NOT EXISTS donations_d_ngo_id_9af8c2_idx ON donations_donationnotificationlog (ngo_id, is_active, is_read)"),
]

def print_header(text):
    print("\n" + "="*70)
    print(f"  {text}")
    print("="*70)

def print_section(text):
    print(f"\n{text}")
    print("-" * len(text))

def check_database_exists():
    if not os.path.exists(DB_PATH):
        print(f"❌ ERROR: Database file '{DB_PATH}' not found!")
        print("Make sure you're running this from the project root directory:")
        print("  D:\\project1\\HappyTummy-main-main\\")
        sys.exit(1)
    print(f"✓ Found database: {DB_PATH}")

def create_backup():
    print_section("Creating Backup")
    try:
        shutil.copy2(DB_PATH, BACKUP_PATH)
        file_size = os.path.getsize(BACKUP_PATH) / 1024  # KB
        print(f"✓ Backup created: {BACKUP_PATH} ({file_size:.1f} KB)")
        return True
    except Exception as e:
        print(f"❌ Backup failed: {e}")
        return False

def get_existing_columns(cursor, table_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1]: row[2] for row in cursor.fetchall()}

def check_table_exists(cursor, table_name):
    cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'")
    return cursor.fetchone() is not None

def fix_tables(conn):
    cursor = conn.cursor()
    total_added = 0
    tables_fixed = []
    
    print_section("Analyzing Tables")
    
    for table_name, required_columns in TABLES_TO_FIX.items():
        print(f"\n📋 Checking: {table_name}")
        
        # Check if table exists
        if not check_table_exists(cursor, table_name):
            print(f"   ⚠️  Table doesn't exist - will be created by migration")
            continue
        
        # Get existing columns
        existing_columns = get_existing_columns(cursor, table_name)
        print(f"   Current columns: {len(existing_columns)}")
        
        # Find missing columns
        missing_columns = {
            col: dtype for col, dtype in required_columns.items() 
            if col not in existing_columns
        }
        
        if not missing_columns:
            print(f"   ✓ All columns exist")
            continue
        
        print(f"   ⚠️  Missing {len(missing_columns)} columns:")
        for col in missing_columns:
            print(f"      - {col}")
        
        # Add missing columns
        try:
            for column_name, column_type in missing_columns.items():
                alter_query = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
                cursor.execute(alter_query)
                total_added += 1
                print(f"   ✓ Added: {column_name}")
            
            tables_fixed.append(table_name)
            conn.commit()
            
        except Exception as e:
            print(f"   ❌ Error adding columns: {e}")
            conn.rollback()
            raise
    
    return total_added, tables_fixed

def create_indexes(conn):
    cursor = conn.cursor()
    created = 0
    
    print_section("Creating Indexes")
    
    for idx_name, idx_query in INDEXES_TO_CREATE:
        try:
            cursor.execute(idx_query)
            print(f"✓ {idx_name}")
            created += 1
        except Exception as e:
            print(f"⚠️  {idx_name}: {e}")
    
    conn.commit()
    return created

def verify_fix(conn):
    cursor = conn.cursor()
    print_section("Verification")
    
    all_good = True
    for table_name, required_columns in TABLES_TO_FIX.items():
        if not check_table_exists(cursor, table_name):
            continue
            
        existing_columns = get_existing_columns(cursor, table_name)
        missing = [col for col in required_columns if col not in existing_columns]
        
        if missing:
            print(f"❌ {table_name}: Still missing {missing}")
            all_good = False
        else:
            print(f"✓ {table_name}: All {len(required_columns)} required columns present ({len(existing_columns)} total)")
    
    return all_good

def main():
    print_header("COMPREHENSIVE DATABASE FIX SCRIPT")
    print("This script will add all missing columns from migration 0013")
    
    # Check database exists
    check_database_exists()
    
    # Create backup
    if not create_backup():
        print("\n❌ Cannot proceed without backup!")
        sys.exit(1)
    
    # Connect to database
    try:
        conn = sqlite3.connect(DB_PATH)
        print("\n✓ Connected to database")
    except Exception as e:
        print(f"\n❌ Cannot connect to database: {e}")
        sys.exit(1)
    
    try:
        # Fix tables
        total_added, tables_fixed = fix_tables(conn)
        
        # Create indexes
        indexes_created = create_indexes(conn)
        
        # Verify
        if verify_fix(conn):
            print_header("✓ SUCCESS!")
            print(f"""
Summary:
  • {total_added} columns added
  • {len(tables_fixed)} tables fixed: {', '.join(tables_fixed) if tables_fixed else 'none'}
  • {indexes_created} indexes created
  • Backup saved: {BACKUP_PATH}

Your database is now fixed! You can:
  1. Run your Django server: python manage.py runserver
  2. Access: http://127.0.0.1:8000/dashboard/restaurant/

If something goes wrong, restore from backup:
  copy {BACKUP_PATH} {DB_PATH}
""")
        else:
            print_header("⚠️  PARTIAL SUCCESS")
            print("""
Some columns are still missing. Try these steps:

1. Run Django migrations:
   python manage.py migrate donations 0012
   python manage.py migrate donations 0013

2. If that fails, try:
   python manage.py migrate donations 0013 --fake
   python manage.py migrate donations

3. If still broken, restore backup and start fresh:
   copy {BACKUP_PATH} {DB_PATH}
   del db.sqlite3
   python manage.py migrate
""")
            sys.exit(1)
            
    except Exception as e:
        print_header("❌ ERROR OCCURRED")
        print(f"\nError: {e}")
        print(f"\nRestoring from backup...")
        conn.close()
        try:
            shutil.copy2(BACKUP_PATH, DB_PATH)
            print(f"✓ Database restored from backup: {BACKUP_PATH}")
        except Exception as restore_error:
            print(f"❌ Restore failed: {restore_error}")
            print(f"Manually restore: copy {BACKUP_PATH} {DB_PATH}")
        sys.exit(1)
    
    finally:
        conn.close()

if __name__ == "__main__":
    main()
