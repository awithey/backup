#!/usr/bin/env python3
"""
Backup script using rsync with hardlink support for incremental backups.
Supports local and remote (SSH) sources with Yearly/Monthly/Daily retention.
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Backup local or remote directories using rsync with hardlinks."
    )
    parser.add_argument(
        "source",
        help="Source directory (local path or user@host:/path for remote)"
    )
    parser.add_argument(
        "target",
        help="Local target directory for backups"
    )
    parser.add_argument(
        "--type",
        choices=["daily", "monthly", "yearly"],
        default="daily",
        help="Backup type for retention policy (default: daily)"
    )
    parser.add_argument(
        "--compress",
        action="store_true",
        help="Enable rsync compression (-z) for remote transfers"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without actually running rsync"
    )
    parser.add_argument(
        "--keep-daily",
        type=int,
        default=7,
        help="Number of daily backups to keep (default: 7)"
    )
    parser.add_argument(
        "--keep-monthly",
        type=int,
        default=12,
        help="Number of monthly backups to keep (default: 12)"
    )
    parser.add_argument(
        "--keep-yearly",
        type=int,
        default=5,
        help="Number of yearly backups to keep (default: 5)"
    )
    return parser.parse_args()


def get_timestamp():
    """Generate timestamp for backup directory name."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def get_backup_dirname(backup_type):
    """Generate backup directory name based on type."""
    now = datetime.now()
    if backup_type == "yearly":
        return now.strftime("%Y")
    elif backup_type == "monthly":
        return now.strftime("%Y_%m")
    else:  # daily
        return f"{now.strftime('%Y%m%d')}_{get_timestamp().split('_')[1]}"


def find_latest_backup(target_dir, backup_type):
    """Find the most recent backup directory for hardlink reference."""
    if not target_dir.exists():
        return None
    
    prefix_map = {
        "yearly": lambda name: name.isdigit() and len(name) == 4,
        "monthly": lambda name: len(name.split("_")) == 2 and name.split("_")[0].isdigit() and len(name.split("_")[0]) == 4,
        "daily": lambda name: name.startswith(datetime.now().strftime("%Y%m%d")[:8])
    }
    
    backups = []
    for item in target_dir.iterdir():
        if item.is_dir() and prefix_map[backup_type](item.name):
            backups.append(item)
    
    if not backups:
        # If no matching type found, try any backup dir
        for item in target_dir.iterdir():
            if item.is_dir() and item.name.replace("_", "").isdigit():
                backups.append(item)
    
    if backups:
        return max(backups, key=lambda p: p.stat().st_mtime)
    return None


def build_rsync_command(source, target, latest_backup, compress, dry_run):
    """Build rsync command with appropriate options."""
    cmd = ["rsync"]
    
    # Archive mode, verbose, one filesystem, delete excluded files in destination
    cmd.extend(["-av", "--one-file-system", "--delete-excluded"])
    
    # Hardlink with previous backup if available
    if latest_backup:
        cmd.extend(["--link-dest", str(latest_backup)])
    
    # Compression for remote transfers
    if compress:
        cmd.append("-z")
    
    # SSH options for minimal traffic and existing keys
    cmd.extend([
        "-e", "ssh -o StrictHostKeyChecking=no -o BatchMode=yes"
    ])
    
    # Exclude patterns to minimize traffic (common temporary/cache files)
    excludes = [
        "*.tmp", "*.temp", "*.swp", "*~",
        ".git", ".svn", "__pycache__", "*.pyc",
        "node_modules", ".cache", "Thumbs.db"
    ]
    for pattern in excludes:
        cmd.extend(["--exclude", pattern])
    
    if dry_run:
        cmd.append("--dry-run")
    
    # Ensure target ends with /
    target_str = str(target)
    if not target_str.endswith("/"):
        target_str += "/"
    
    cmd.extend([source, target_str])
    return cmd


def cleanup_old_backups(target_dir, backup_type, keep_counts):
    """Remove old backups based on retention policy."""
    if not target_dir.exists():
        return
    
    backups_by_type = {"daily": [], "monthly": [], "yearly": []}
    
    for item in target_dir.iterdir():
        if not item.is_dir():
            continue
        
        name = item.name
        mtime = item.stat().st_mtime
        
        # Classify backup by name pattern
        if len(name) == 4 and name.isdigit():
            backups_by_type["yearly"].append((mtime, item))
        elif "_" in name:
            parts = name.split("_")
            if len(parts) >= 2 and parts[0].isdigit() and len(parts[0]) == 4:
                if len(parts) == 2:
                    backups_by_type["monthly"].append((mtime, item))
                else:
                    backups_by_type["daily"].append((mtime, item))
    
    # Cleanup each type
    for btype, keep_count in [("daily", keep_counts["daily"]), 
                               ("monthly", keep_counts["monthly"]), 
                               ("yearly", keep_counts["yearly"])]:
        backups = sorted(backups_by_type[btype], reverse=True)
        for _, backup_path in backups[keep_count:]:
            print(f"Removing old {btype} backup: {backup_path}")
            try:
                subprocess.run(["rm", "-rf", str(backup_path)], check=True)
            except subprocess.CalledProcessError as e:
                print(f"Warning: Failed to remove {backup_path}: {e}", file=sys.stderr)


def main():
    args = parse_args()
    
    source = args.source
    target = Path(args.target)
    backup_type = args.type
    
    # Create target directory if it doesn't exist
    target.mkdir(parents=True, exist_ok=True)
    
    # Generate backup directory name
    backup_dirname = get_backup_dirname(backup_type)
    backup_path = target / backup_dirname
    
    # Find latest backup for hardlinks
    latest_backup = find_latest_backup(target, backup_type)
    
    if latest_backup:
        print(f"Using latest backup for hardlinks: {latest_backup}")
    else:
        print("No previous backup found, full backup will be performed.")
    
    # Build and execute rsync command
    cmd = build_rsync_command(
        source,
        backup_path,
        latest_backup,
        args.compress,
        args.dry_run
    )
    
    print(f"Running: {' '.join(cmd)}")
    
    if args.dry_run:
        print("[DRY RUN] Would execute above command")
        return 0
    
    try:
        result = subprocess.run(cmd, check=True)
        print(f"Backup completed successfully: {backup_path}")
        
        # Cleanup old backups
        cleanup_old_backups(
            target,
            backup_type,
            {
                "daily": args.keep_daily,
                "monthly": args.keep_monthly,
                "yearly": args.keep_yearly
            }
        )
        
        return 0
    except subprocess.CalledProcessError as e:
        print(f"Backup failed: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("Error: rsync not found. Please install rsync.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
