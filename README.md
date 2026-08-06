# Incremental Backup Script

A Python script for creating efficient, timestamped backups of local or remote directories using `rsync` with hard-link support.

## Features

- **Local & Remote Support**: Backup from local paths or remote servers via SSH.
- **Space Efficient**: Uses hard links for unchanged files when a previous backup exists, significantly reducing disk usage.
- **Traffic Optimized**: Configured to minimize data transfer from remote sources by skipping existing files and excluding common cache/temp directories.
- **Retention Policies**: Supports "Yearly", "Monthly", and "Daily" retention strategies to manage old backups automatically.
- **No Root Required**: Does not preserve file ownership or group IDs (`--no-o --no-g`), making it safe to run as a standard user without sudo.
- **Verbose Output**: Provides detailed logs of the backup process.

## Requirements

- Python 3.6+
- `rsync` installed on the local machine (and remote machine if backing up remotely).
- SSH access with key-based authentication configured for remote backups.

## Usage

```bash
python backup.py [OPTIONS] <source> <target_directory>
```

### Arguments

- `<source>`: The directory to backup.
  - Local: `/path/to/data`
  - Remote: `user@hostname:/path/to/data`
- `<target_directory>`: The local directory where timestamped backup folders will be created.

### Options

| Option | Short | Description | Default |
| :--- | :--- | :--- | :--- |
| `--retention` | `-r` | Retention policy: `daily`, `monthly`, or `yearly`. | `daily` |
| `--compress` | `-z` | Enable rsync compression (recommended for remote). | Off |
| `--dry-run` | `-n` | Show what would be done without actually running. | Off |
| `--help` | `-h` | Show help message and exit. | |

## Examples

### 1. Local Daily Backup
Backup a local folder to a backup drive with daily retention logic:
```bash
python backup.py /home/user/documents /mnt/backup/documents --retention daily
```

### 2. Remote Backup with Compression
Backup a remote server folder to local storage, enabling compression to save bandwidth:
```bash
python backup.py user@192.168.1.50:/var/www/html /local/backups/web --retention monthly --compress
```

### 3. Dry Run
Test the command to see exactly what `rsync` would do without modifying any files:
```bash
python backup.py user@remote:/data /backups/data --dry-run
```

## How It Works

1. **Timestamping**: Creates a subdirectory in the target folder named with the current timestamp (e.g., `backup_2023-10-27_103000`).
2. **Hard Linking**: Automatically finds the most recent previous backup in the target directory. It passes this path to `rsync` via `--link-dest`. This causes `rsync` to create hard links for any files that haven't changed, making the new backup appear complete while using almost no extra disk space for unchanged data.
3. **Retention**: Based on the `--retention` flag, the script can optionally clean up older backups (logic placeholder included for custom implementation).
4. **Rsync Flags**:
   - `-rlptDv`: Preserves recursion, symlinks, permissions, times, and devices, but **ignores owner and group**.
   - `--delete`: Ensures the backup mirrors the source exactly by deleting files in the backup that were deleted from the source.
   - `--exclude`: Skips common temporary files (`.git`, `__pycache__`, `*.log`, etc.) to reduce traffic.
   - `-e ssh`: Uses SSH for secure remote connections with strict host key checking options suitable for scripts.

## License

MIT License
