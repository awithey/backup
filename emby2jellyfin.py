#!/usr/bin/env python3
"""
Emby to Jellyfin User Data Migration Script

Copies the following from Emby to Jellyfin:
- Watched items
- Favourites
- TV recording schedules
- Resume info for partially watched items

Requirements:
    pip install requests

Usage:
    python emby2jellyfin.py --emby-url http://emby-server:8096 \
                            --emby-api-key YOUR_EMBY_API_KEY \
                            --jellyfin-url http://jellyfin-server:8096 \
                            --jellyfin-api-key YOUR_JELLYFIN_API_KEY \
                            [--user-id USER_ID] [--dry-run]
"""

import argparse
import hashlib
import json
import logging
import sys
from typing import Any, Optional

import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class EmbyToJellyfinMigrator:
    """Handles migration of user data from Emby to Jellyfin."""

    def __init__(
        self,
        emby_url: str,
        emby_api_key: str,
        jellyfin_url: str,
        jellyfin_api_key: str,
        dry_run: bool = False
    ):
        self.emby_url = emby_url.rstrip('/')
        self.emby_api_key = emby_api_key
        self.jellyfin_url = jellyfin_url.rstrip('/')
        self.jellyfin_api_key = jellyfin_api_key
        self.dry_run = dry_run

        # Session headers for API calls
        self.emby_headers = {
            'X-Emby-Token': emby_api_key,
            'Content-Type': 'application/json'
        }
        self.jellyfin_headers = {
            'X-Emby-Token': jellyfin_api_key,
            'Content-Type': 'application/json'
        }

    def _get_emby(self, endpoint: str, params: Optional[dict] = None) -> dict:
        """Make GET request to Emby API."""
        url = f"{self.emby_url}{endpoint}"
        try:
            response = requests.get(url, headers=self.emby_headers, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Emby API GET error on {endpoint}: {e}")
            raise

    def _post_jellyfin(self, endpoint: str, data: Optional[dict] = None) -> dict:
        """Make POST request to Jellyfin API."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would POST to {endpoint} with data: {json.dumps(data, indent=2)}")
            return {}

        url = f"{self.jellyfin_url}{endpoint}"
        try:
            response = requests.post(url, headers=self.jellyfin_headers, json=data, timeout=30)
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.exceptions.RequestException as e:
            logger.error(f"Jellyfin API POST error on {endpoint}: {e}")
            raise

    def _put_jellyfin(self, endpoint: str, data: Optional[dict] = None) -> dict:
        """Make PUT request to Jellyfin API."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would PUT to {endpoint} with data: {json.dumps(data, indent=2)}")
            return {}

        url = f"{self.jellyfin_url}{endpoint}"
        try:
            response = requests.put(url, headers=self.jellyfin_headers, json=data, timeout=30)
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.exceptions.RequestException as e:
            logger.error(f"Jellyfin API PUT error on {endpoint}: {e}")
            raise

    def _delete_jellyfin(self, endpoint: str) -> None:
        """Make DELETE request to Jellyfin API."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would DELETE {endpoint}")
            return

        url = f"{self.jellyfin_url}{endpoint}"
        try:
            response = requests.delete(url, headers=self.jellyfin_headers, timeout=30)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"Jellyfin API DELETE error on {endpoint}: {e}")
            raise

    def get_users(self) -> list[dict]:
        """Get all users from Emby."""
        try:
            users = self._get_emby('/Users')
            logger.info(f"Found {len(users)} users in Emby")
            return users
        except Exception as e:
            logger.error(f"Failed to get users: {e}")
            return []

    def get_user_data(self, user_id: str) -> dict:
        """Get user playback progress and watched status from Emby."""
        try:
            # Get resume items (partially watched)
            resume_items = self._get_emby(
                f'/Users/{user_id}/Items/Resume',
                params={'Recursive': 'true', 'IncludeItemTypes': 'Movie,Episode'}
            )

            # Get favourite items using the correct endpoint with Favorite=true filter
            # Reference: https://github.com/jonjonsson/Emby-MDBList-Collection-Creator
            favourite_items = self._get_emby(
                f'/Users/{user_id}/Items',
                params={'Recursive': 'true', 'Filters': 'IsFavorite'}
            )

            # Get played items (watched)
            played_items = self._get_emby(
                f'/Users/{user_id}/Items',
                params={'Recursive': 'true', 'Filters': 'IsPlayed'}
            )

            return {
                'resume': resume_items.get('Items', []),
                'favourites': favourite_items.get('Items', []),
                'played': played_items.get('Items', [])
            }
        except Exception as e:
            logger.error(f"Failed to get user data for {user_id}: {e}")
            return {'resume': [], 'favourites': [], 'played': []}

    def get_recording_schedules(self, user_id: str) -> list[dict]:
        """Get TV recording schedules from Emby."""
        try:
            schedules = self._get_emby(f'/LiveTv/Recordings/Schedule')
            logger.info(f"Found {len(schedules)} recording schedules")
            return schedules
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 500:
                logger.warning(
                    "Emby server returned 500 for recording schedules. "
                    "This usually means Live TV is not configured or there's a server issue. "
                    "Skipping recording schedule migration."
                )
            else:
                logger.error(f"Failed to get recording schedules: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to get recording schedules: {e}")
            return []

    def find_matching_item_in_jellyfin(self, emby_item: dict, jellyfin_user_id: str) -> Optional[str]:
        """
        Find matching item in Jellyfin using provider IDs or name/path matching.
        Returns the Jellyfin item ID if found, None otherwise.
        """
        emby_providers = emby_item.get('ProviderIds', {})
        emby_name = emby_item.get('Name', '')
        emby_type = emby_item.get('Type', '')

        # Try matching by ProviderIds (IMDB, TMDB, TVDB, etc.)
        for provider, provider_id in emby_providers.items():
            try:
                # Search in Jellyfin by provider ID
                params = {
                    'IncludeItemTypes': emby_type,
                    'Limit': 1
                }
                
                # Map common provider IDs
                if provider.lower() == 'imdb':
                    params['ImdbId'] = provider_id
                elif provider.lower() == 'tmdb':
                    params['TmdbId'] = provider_id
                elif provider.lower() == 'tvdb':
                    params['TvDbId'] = provider_id
                
                result = self._get_jellyfin('/Items', params)
                if result.get('Items'):
                    jellyfin_id = result['Items'][0]['Id']
                    logger.debug(f"Matched by {provider}: {emby_name} -> {jellyfin_id}")
                    return jellyfin_id
            except Exception:
                continue

        # Fallback: search by name and type
        try:
            params = {
                'SearchTerm': emby_name,
                'IncludeItemTypes': emby_type,
                'Limit': 5
            }
            result = self._get_jellyfin('/Items', params)
            
            for item in result.get('Items', []):
                # Simple name matching (could be improved with fuzzy matching)
                if item.get('Name', '').lower() == emby_name.lower():
                    logger.debug(f"Matched by name: {emby_name} -> {item['Id']}")
                    return item['Id']
        except Exception:
            pass

        logger.warning(f"No match found in Jellyfin for: {emby_name} ({emby_type})")
        return None

    def _get_jellyfin(self, endpoint: str, params: Optional[dict] = None) -> dict:
        """Make GET request to Jellyfin API."""
        url = f"{self.jellyfin_url}{endpoint}"
        try:
            response = requests.get(url, headers=self.jellyfin_headers, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Jellyfin API GET error on {endpoint}: {e}")
            raise

    def mark_as_watched(self, jellyfin_user_id: str, jellyfin_item_id: str) -> bool:
        """Mark an item as watched in Jellyfin."""
        try:
            self._post_jellyfin(f'/Users/{jellyfin_user_id}/PlayedItems/{jellyfin_item_id}')
            logger.info(f"Marked item {jellyfin_item_id} as watched for user {jellyfin_user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to mark item as watched: {e}")
            return False

    def mark_as_favourite(self, jellyfin_user_id: str, jellyfin_item_id: str) -> bool:
        """Mark an item as favourite in Jellyfin."""
        try:
            data = {'IsFavorite': True}
            self._post_jellyfin(f'/Users/{jellyfin_user_id}/FavoriteItems/{jellyfin_item_id}', data)
            logger.info(f"Marked item {jellyfin_item_id} as favourite for user {jellyfin_user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to mark item as favourite: {e}")
            return False

    def set_resume_point(
        self,
        jellyfin_user_id: str,
        jellyfin_item_id: str,
        position_ticks: int,
        runtime_ticks: int
    ) -> bool:
        """Set resume point for partially watched item in Jellyfin."""
        try:
            # Convert ticks to milliseconds (1 tick = 100 nanoseconds)
            position_ms = position_ticks // 10000
            runtime_ms = runtime_ticks // 10000

            data = {
                'PlaybackPositionTicks': position_ticks,
                'PlayCount': 1,
                'IsFavorite': False,
                'LastPlayedDate': None,
                'PlayedKey': None,
                'PlayMethod': None,
                'Played': False,
                'AudioStreamIndex': None,
                'SubtitleStreamIndex': None
            }

            self._post_jellyfin(
                f'/Users/{jellyfin_user_id}/PlayingState',
                data
            )
            logger.info(
                f"Set resume point for item {jellyfin_item_id} at "
                f"{position_ms//60000}:{(position_ms//1000)%60:02d} / "
                f"{runtime_ms//60000}:{(runtime_ms//1000)%60:02d}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to set resume point: {e}")
            return False

    def create_recording_schedule(self, schedule: dict) -> bool:
        """Create a recording schedule in Jellyfin."""
        try:
            # Map Emby schedule to Jellyfin format
            jellyfin_schedule = {
                'ChannelId': schedule.get('ChannelId'),
                'ProgramId': schedule.get('ProgramId'),
                'StartDate': schedule.get('StartDate'),
                'EndDate': schedule.get('EndDate'),
                'IsPrePaddingRequired': schedule.get('IsPrePaddingRequired', False),
                'IsPostPaddingRequired': schedule.get('IsPostPaddingRequired', False),
                'PrePaddingSeconds': schedule.get('PrePaddingSeconds', 0),
                'PostPaddingSeconds': schedule.get('PostPaddingSeconds', 0),
                'KeepUntil': schedule.get('KeepUntil', 'UntilSpaceNeeded'),
                'SkipEpisodesInLibrary': schedule.get('SkipEpisodesInLibrary', False),
                'KeepUpTo': schedule.get('KeepUpTo', 0),
                'Days': schedule.get('Days', []),
                'Id': schedule.get('Id')
            }

            self._post_jellyfin('/LiveTv/SeriesTimers', jellyfin_schedule)
            logger.info(f"Created recording schedule for program {schedule.get('ProgramId')}")
            return True
        except Exception as e:
            logger.error(f"Failed to create recording schedule: {e}")
            return False

    def migrate_user_data(
        self,
        emby_user_id: str,
        jellyfin_user_id: str,
        emby_username: str = ""
    ) -> dict:
        """Migrate all user data from Emby to Jellyfin for a specific user."""
        
        stats = {
            'watched': 0,
            'favourites': 0,
            'resume_points': 0,
            'recording_schedules': 0,
            'errors': 0
        }

        user_label = f"User {emby_username} ({emby_user_id})" if emby_username else f"User {emby_user_id}"
        logger.info(f"Starting migration for {user_label}")

        # Get user data from Emby
        user_data = self.get_user_data(emby_user_id)

        # Create sets to avoid duplicates
        processed_items = set()

        # Migrate watched items
        logger.info(f"Migrating watched items...")
        for item in user_data['played']:
            item_id = item.get('Id')
            if item_id in processed_items:
                continue
            
            jellyfin_id = self.find_matching_item_in_jellyfin(item, jellyfin_user_id)
            if jellyfin_id:
                if self.mark_as_watched(jellyfin_user_id, jellyfin_id):
                    stats['watched'] += 1
                else:
                    stats['errors'] += 1
            processed_items.add(item_id)

        # Migrate favourites
        logger.info(f"Migrating favourites...")
        for item in user_data['favourites']:
            item_id = item.get('Id')
            if item_id in processed_items:
                continue
            
            jellyfin_id = self.find_matching_item_in_jellyfin(item, jellyfin_user_id)
            if jellyfin_id:
                if self.mark_as_favourite(jellyfin_user_id, jellyfin_id):
                    stats['favourites'] += 1
                else:
                    stats['errors'] += 1
            processed_items.add(item_id)

        # Migrate resume points (partially watched items)
        logger.info(f"Migrating resume points...")
        for item in user_data['resume']:
            item_id = item.get('Id')
            position_ticks = item.get('UserData', {}).get('PlaybackPositionTicks', 0)
            runtime_ticks = item.get('RunTimeTicks', 0)

            # Skip if already fully watched or no progress
            if position_ticks == 0 or position_ticks >= runtime_ticks:
                continue

            if item_id in processed_items:
                continue

            jellyfin_id = self.find_matching_item_in_jellyfin(item, jellyfin_user_id)
            if jellyfin_id:
                if self.set_resume_point(jellyfin_user_id, jellyfin_id, position_ticks, runtime_ticks):
                    stats['resume_points'] += 1
                else:
                    stats['errors'] += 1
            processed_items.add(item_id)

        # Migrate recording schedules
        logger.info(f"Migrating recording schedules...")
        schedules = self.get_recording_schedules(emby_user_id)
        for schedule in schedules:
            if self.create_recording_schedule(schedule):
                stats['recording_schedules'] += 1
            else:
                stats['errors'] += 1

        logger.info(
            f"Migration complete for {user_label}: "
            f"{stats['watched']} watched, {stats['favourites']} favourites, "
            f"{stats['resume_points']} resume points, "
            f"{stats['recording_schedules']} recording schedules, "
            f"{stats['errors']} errors"
        )

        return stats

    def migrate_all_users(self) -> dict:
        """Migrate data for all users from Emby to Jellyfin."""
        total_stats = {
            'watched': 0,
            'favourites': 0,
            'resume_points': 0,
            'recording_schedules': 0,
            'errors': 0
        }

        users = self.get_users()
        if not users:
            logger.error("No users found in Emby")
            return total_stats

        # Note: For multi-user migration, you need to map Emby users to Jellyfin users
        # This is a simplified example assuming single user or manual mapping
        logger.warning(
            "Multi-user migration requires mapping Emby user IDs to Jellyfin user IDs. "
            "Please use --user-id and --jellyfin-user-id for specific user migration."
        )

        for user in users:
            user_id = user.get('Id')
            username = user.get('Name', '')
            logger.info(f"\nProcessing user: {username}")
            
            # You would need to get the corresponding Jellyfin user ID here
            # For now, this is a placeholder - implement user mapping as needed
            jellyfin_user_id = input(
                f"Enter Jellyfin user ID for '{username}' (or press Enter to skip): "
            ).strip()
            
            if jellyfin_user_id:
                stats = self.migrate_user_data(user_id, jellyfin_user_id, username)
                for key in total_stats:
                    total_stats[key] += stats[key]

        return total_stats


def main():
    parser = argparse.ArgumentParser(
        description='Migrate user data from Emby to Jellyfin',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        '--emby-url',
        required=True,
        help='Emby server URL (e.g., http://localhost:8096)'
    )
    parser.add_argument(
        '--emby-api-key',
        required=True,
        help='Emby API key'
    )
    parser.add_argument(
        '--jellyfin-url',
        required=True,
        help='Jellyfin server URL (e.g., http://localhost:8096)'
    )
    parser.add_argument(
        '--jellyfin-api-key',
        required=True,
        help='Jellyfin API key'
    )
    parser.add_argument(
        '--user-id',
        help='Specific Emby user ID to migrate (optional, migrates all users if not specified)'
    )
    parser.add_argument(
        '--jellyfin-user-id',
        help='Corresponding Jellyfin user ID (required if --user-id is specified)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without actually making changes'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose output'
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.user_id and not args.jellyfin_user_id:
        parser.error("--jellyfin-user-id is required when --user-id is specified")

    migrator = EmbyToJellyfinMigrator(
        emby_url=args.emby_url,
        emby_api_key=args.emby_api_key,
        jellyfin_url=args.jellyfin_url,
        jellyfin_api_key=args.jellyfin_api_key,
        dry_run=args.dry_run
    )

    try:
        if args.user_id:
            stats = migrator.migrate_user_data(args.user_id, args.jellyfin_user_id)
        else:
            stats = migrator.migrate_all_users()

        print("\n" + "="*50)
        print("Migration Summary:")
        print("="*50)
        print(f"Watched items migrated:      {stats['watched']}")
        print(f"Favourites migrated:         {stats['favourites']}")
        print(f"Resume points migrated:      {stats['resume_points']}")
        print(f"Recording schedules created: {stats['recording_schedules']}")
        print(f"Errors encountered:          {stats['errors']}")
        print("="*50)

        if args.dry_run:
            print("\n[DRY RUN] No actual changes were made.")

    except KeyboardInterrupt:
        print("\nMigration interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
