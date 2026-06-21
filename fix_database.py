#!/usr/bin/env python3
"""
Fix script for missing columns in donations_surplusfoodrequest table
Run this from your project directory: python fix_database.py
"""

import sqlite3
import os
import sys

# Find db.sqlite3
db_path = 'db.sqlite3'
if not os.path.exists(db_path):
    print(f"ERROR: Database file '{db_path}' not found!")
    print("Make sure you're running this from the project root directory.")
    sys.exit(1)

print(f"Found database: {db_path}")
print("Backing up database...")

# Backup the database
import shutil
backup_path = 'db.sqlite3.backup'
shutil.copy2(db_path, backup_path)
print(f"✓ Backup created: {backup_path}")

# Connect to database
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("\nChecking current table structure...")

# Get current columns
cursor.execute("PRAGMA table_info(donations_surplusfoodrequest)")
existing_columns = {row[1] for row in cursor.fetchall()}
print(f"Found {len(existing_columns)} existing columns")

# Define all columns that should exist (from migration 0013)
required_columns = {
    'posted_at': 'datetime',
    'restaurant_lat': 'real',
    'restaurant_lng': 'real',
    'donation_status': 'varchar(20) DEFAULT "posted"',
    'current_radius_km': 'integer DEFAULT 5',
    'ngos_notified_at': 'datetime',
    'last_radius_expansion_at': 'datetime',
    'notified_ngo_ids': 'TEXT DEFAULT "[]"',  # JSON field stored as TEXT
    'expiry_reason': 'varchar(50)',
    'archived_at': 'datetime',
}

# Find missing columns
missing_columns = {col: dtype for col, dtype in required_columns.items() if col not in existing_columns}

if not missing_columns:
    print("\n✓ All required columns already exist!")
    conn.close()
    sys.exit(0)

print(f"\n⚠ Missing {len(missing_columns)} columns:")
for col in missing_columns:
    print(f"  - {col}")

print("\nAdding missing columns...")

try:
    for column_name, column_type in missing_columns.items():
        alter_query = f"ALTER TABLE donations_surplusfoodrequest ADD COLUMN {column_name} {column_type}"
        print(f"  Adding: {column_name}...")
        cursor.execute(alter_query)
    
    conn.commit()
    print("\n✓ All missing columns added successfully!")
    
    # Verify
    cursor.execute("PRAGMA table_info(donations_surplusfoodrequest)")
    new_columns = {row[1] for row in cursor.fetchall()}
    print(f"\n✓ Table now has {len(new_columns)} columns")
    
    # Check indexes
    print("\nChecking indexes...")
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='donations_surplusfoodrequest'")
    indexes = cursor.fetchall()
    
    # Add indexes if they don't exist
    index_queries = [
        ("donations_s_donatio_b64344_idx", 
         "CREATE INDEX IF NOT EXISTS donations_s_donatio_b64344_idx ON donations_surplusfoodrequest (donation_status, expiry_at)"),
        ("donations_s_restaur_8dcc58_idx",
         "CREATE INDEX IF NOT EXISTS donations_s_restaur_8dcc58_idx ON donations_surplusfoodrequest (restaurant_id, donation_status)")
    ]
    
    for idx_name, idx_query in index_queries:
        cursor.execute(idx_query)
        print(f"  ✓ Index: {idx_name}")
    
    conn.commit()
    
    print("\n" + "="*60)
    print("✓ DATABASE FIXED SUCCESSFULLY!")
    print("="*60)
    print("\nYou can now run your Django server:")
    print("  python manage.py runserver")
    print(f"\nIf something goes wrong, restore from backup:")
    print(f"  copy {backup_path} {db_path}")
    
except Exception as e:
    conn.rollback()
    print(f"\n✗ ERROR: {e}")
    print(f"\nRestoring from backup...")
    conn.close()
    shutil.copy2(backup_path, db_path)
    print("✓ Database restored from backup")
    sys.exit(1)

finally:
    conn.close()
