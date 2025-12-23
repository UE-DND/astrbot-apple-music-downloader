"""
Region Detector

Detects available regions and song availability across different storefronts.
"""

import asyncio
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import aiohttp

from ..logger import LoggerInterface, get_logger
logger = get_logger()


@dataclass
class RegionInfo:
    """Information about a specific region/storefront."""
    code: str  # Region code (e.g., "us", "cn", "jp")
    name: str  # Human-readable name
    available: bool = True
    last_check: datetime = field(default_factory=datetime.now)


@dataclass
class SongAvailability:
    """Song availability across regions."""
    adam_id: str
    available_regions: Set[str] = field(default_factory=set)
    unavailable_regions: Set[str] = field(default_factory=set)
    last_check: datetime = field(default_factory=datetime.now)


class RegionDetector:
    """
    Detects and tracks region availability.

    Features:
    - Discover available regions from Apple Music API
    - Check song availability in specific regions
    - Cache availability results
    - Auto-refresh stale data
    """

    # Common Apple Music storefronts
    KNOWN_REGIONS = {
        "us": "United States",
        "cn": "China",
        "jp": "Japan",
        "hk": "Hong Kong",
        "tw": "Taiwan",
        "gb": "United Kingdom",
        "de": "Germany",
        "fr": "France",
        "au": "Australia",
        "ca": "Canada",
        "kr": "South Korea",
        "sg": "Singapore",
        "th": "Thailand",
        "in": "India",
        "br": "Brazil",
        "mx": "Mexico",
        "es": "Spain",
        "it": "Italy",
        "nl": "Netherlands",
        "se": "Sweden",
    }

    def __init__(
        self,
        cache_ttl: int = 3600,  # 1 hour default
        api_timeout: int = 10,
    ):
        """
        Initialize region detector.

        Args:
            cache_ttl: Cache TTL in seconds
            api_timeout: API request timeout in seconds
        """
        self.cache_ttl = cache_ttl
        self.api_timeout = api_timeout

        # Caches
        self._regions: Dict[str, RegionInfo] = {}
        self._song_availability: Dict[str, SongAvailability] = {}

        # Initialize known regions
        for code, name in self.KNOWN_REGIONS.items():
            self._regions[code] = RegionInfo(code=code, name=name)

        logger.info(f"Region detector initialized with {len(self._regions)} known regions")

    def get_all_regions(self) -> List[RegionInfo]:
        """
        Get all known regions.

        Returns:
            List of RegionInfo
        """
        return list(self._regions.values())

    def get_available_regions(self) -> List[str]:
        """
        Get list of available region codes.

        Returns:
            List of region codes
        """
        return [
            region.code
            for region in self._regions.values()
            if region.available
        ]

    def get_region_name(self, code: str) -> Optional[str]:
        """
        Get human-readable name for region code.

        Args:
            code: Region code

        Returns:
            Region name or None
        """
        region = self._regions.get(code)
        return region.name if region else None

    async def check_song_availability(
        self,
        adam_id: str,
        regions: Optional[List[str]] = None,
        force_refresh: bool = False
    ) -> SongAvailability:
        """
        Check song availability across regions.

        Args:
            adam_id: Song ID
            regions: Specific regions to check (None = all known regions)
            force_refresh: Force refresh cached data

        Returns:
            SongAvailability object
        """
        # Check cache
        if not force_refresh and adam_id in self._song_availability:
            cached = self._song_availability[adam_id]
            age = (datetime.now() - cached.last_check).total_seconds()

            if age < self.cache_ttl:
                logger.debug(f"Using cached availability for {adam_id}")
                return cached

        # Determine regions to check
        if regions is None:
            regions = self.get_available_regions()

        logger.info(f"Checking availability for {adam_id} in {len(regions)} regions")

        # Check availability concurrently
        availability = SongAvailability(adam_id=adam_id)

        tasks = []
        for region in regions:
            task = self._check_song_in_region(adam_id, region)
            tasks.append((region, task))

        # Gather results
        for region, task in tasks:
            try:
                is_available = await task
                if is_available:
                    availability.available_regions.add(region)
                else:
                    availability.unavailable_regions.add(region)
            except Exception as e:
                logger.error(f"Error checking {adam_id} in {region}: {e}")
                availability.unavailable_regions.add(region)

        # Cache result
        self._song_availability[adam_id] = availability

        logger.info(
            f"Song {adam_id} available in {len(availability.available_regions)} regions, "
            f"unavailable in {len(availability.unavailable_regions)}"
        )

        return availability

    async def _check_song_in_region(self, adam_id: str, region: str) -> bool:
        """
        Check if song is available in a specific region.

        Args:
            adam_id: Song ID
            region: Region code

        Returns:
            True if available, False otherwise
        """
        try:
            # Use Apple Music API lookup endpoint
            url = f"https://itunes.apple.com/lookup"
            params = {
                "id": adam_id,
                "country": region,
                "entity": "song",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=self.api_timeout)
                ) as response:
                    if response.status != 200:
                        logger.warning(f"API returned {response.status} for {adam_id} in {region}")
                        return False

                    data = await response.json()

                    # Check if song is in results
                    result_count = data.get("resultCount", 0)
                    return result_count > 0

        except asyncio.TimeoutError:
            logger.warning(f"Timeout checking {adam_id} in {region}")
            return False

        except Exception as e:
            logger.error(f"Error checking {adam_id} in {region}: {e}")
            return False

    def get_cached_availability(self, adam_id: str) -> Optional[SongAvailability]:
        """
        Get cached availability for a song.

        Args:
            adam_id: Song ID

        Returns:
            SongAvailability or None if not cached
        """
        return self._song_availability.get(adam_id)

    def is_available_in_region(self, adam_id: str, region: str) -> Optional[bool]:
        """
        Check if song is available in a region (from cache).

        Args:
            adam_id: Song ID
            region: Region code

        Returns:
            True/False if known, None if not cached
        """
        availability = self.get_cached_availability(adam_id)
        if not availability:
            return None

        if region in availability.available_regions:
            return True
        elif region in availability.unavailable_regions:
            return False
        else:
            return None

    async def suggest_alternative_regions(
        self,
        adam_id: str,
        preferred_region: str
    ) -> List[str]:
        """
        Suggest alternative regions if song is not available in preferred region.

        Args:
            adam_id: Song ID
            preferred_region: User's preferred region

        Returns:
            List of alternative region codes
        """
        # Check availability across all regions
        availability = await self.check_song_availability(adam_id)

        # If available in preferred region, no alternatives needed
        if preferred_region in availability.available_regions:
            return []

        # Return all available regions
        return list(availability.available_regions)

    def clear_cache(self, adam_id: Optional[str] = None):
        """
        Clear availability cache.

        Args:
            adam_id: Specific song ID to clear, or None to clear all
        """
        if adam_id:
            self._song_availability.pop(adam_id, None)
            logger.info(f"Cleared cache for {adam_id}")
        else:
            self._song_availability.clear()
            logger.info("Cleared all availability cache")

    def get_cache_stats(self) -> Dict:
        """
        Get cache statistics.

        Returns:
            Dict with cache stats
        """
        now = datetime.now()
        fresh_count = 0
        stale_count = 0

        for availability in self._song_availability.values():
            age = (now - availability.last_check).total_seconds()
            if age < self.cache_ttl:
                fresh_count += 1
            else:
                stale_count += 1

        return {
            "total_cached": len(self._song_availability),
            "fresh": fresh_count,
            "stale": stale_count,
            "cache_ttl": self.cache_ttl,
            "known_regions": len(self._regions),
        }

    async def refresh_stale_cache(self):
        """Refresh stale cache entries."""
        now = datetime.now()
        to_refresh = []

        for adam_id, availability in self._song_availability.items():
            age = (now - availability.last_check).total_seconds()
            if age >= self.cache_ttl:
                to_refresh.append(adam_id)

        if to_refresh:
            logger.info(f"Refreshing {len(to_refresh)} stale cache entries")

            for adam_id in to_refresh:
                try:
                    await self.check_song_availability(adam_id, force_refresh=True)
                except Exception as e:
                    logger.error(f"Error refreshing {adam_id}: {e}")

    def add_custom_region(self, code: str, name: str):
        """
        Add a custom region.

        Args:
            code: Region code
            name: Region name
        """
        if code not in self._regions:
            self._regions[code] = RegionInfo(code=code, name=name)
            logger.info(f"Added custom region: {code} ({name})")

    def mark_region_unavailable(self, code: str):
        """
        Mark a region as unavailable.

        Args:
            code: Region code
        """
        if code in self._regions:
            self._regions[code].available = False
            self._regions[code].last_check = datetime.now()
            logger.warning(f"Marked region {code} as unavailable")

    def mark_region_available(self, code: str):
        """
        Mark a region as available.

        Args:
            code: Region code
        """
        if code in self._regions:
            self._regions[code].available = True
            self._regions[code].last_check = datetime.now()
            logger.info(f"Marked region {code} as available")
