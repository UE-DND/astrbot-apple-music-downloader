"""
Decrypt Dispatcher

Routes decryption tasks to appropriate wrapper instances using smart selection strategies.
Implements the same logic as the Go implementation for compatibility.
"""

import asyncio
import random
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime

from ..logger import LoggerInterface, get_logger
logger = get_logger()

from .instance_manager import InstanceManager, WrapperInstance


@dataclass
class DecryptTask:
    """Decryption task."""
    adam_id: str
    key: str
    sample: bytes
    sample_index: int
    created_at: datetime = datetime.now()


@dataclass
class DecryptResult:
    """Decryption result."""
    success: bool
    data: bytes
    error: Optional[str] = None
    instance_id: Optional[str] = None


class DecryptDispatcher:
    """
    Decryption task dispatcher.

    Routes decryption tasks to the most suitable wrapper instance
    using smart selection strategies:
    1. Sticky routing: Reuse instance that processed the same adam_id
    2. Fresh instance: Select idle instance for new songs
    3. Region matching: Prefer instances in the same region
    4. Load balancing: Random selection among candidates
    """

    def __init__(self, instance_manager: InstanceManager):
        """
        Initialize dispatcher.

        Args:
            instance_manager: Instance manager
        """
        self.instance_manager = instance_manager
        self._lock = asyncio.Lock()

    async def dispatch(self, task: DecryptTask) -> DecryptResult:
        """
        Dispatch decryption task.

        Args:
            task: Decryption task

        Returns:
            DecryptResult
        """
        # Select instance
        instance = await self._select_instance(task.adam_id)

        if not instance:
            logger.error("No available wrapper instance")
            return DecryptResult(
                success=False,
                data=b"",
                error="没有可用的 wrapper 实例"
            )

        if not instance.proxy:
            logger.error(f"Instance {instance.instance_id} has no proxy")
            return DecryptResult(
                success=False,
                data=b"",
                error="实例代理未初始化"
            )

        # Update instance last used time
        instance.update_last_used()

        # Execute decryption
        try:
            success, decrypted_data, error = await instance.proxy.decrypt(
                adam_id=task.adam_id,
                key=task.key,
                sample=task.sample,
                sample_index=task.sample_index
            )

            if success:
                logger.debug(
                    f"Decrypt success: {task.adam_id}[{task.sample_index}] "
                    f"via {instance.instance_id}"
                )
            else:
                logger.warning(
                    f"Decrypt failed: {task.adam_id}[{task.sample_index}] "
                    f"via {instance.instance_id}: {error}"
                )

            return DecryptResult(
                success=success,
                data=decrypted_data,
                error=error,
                instance_id=instance.instance_id
            )

        except Exception as e:
            logger.error(f"Decrypt exception: {e}")
            return DecryptResult(
                success=False,
                data=b"",
                error=str(e),
                instance_id=instance.instance_id
            )

    async def _select_instance(self, adam_id: str) -> Optional[WrapperInstance]:
        """
        Select the best instance for decryption.

        Selection strategy (same as Go implementation):
        1. If an instance recently processed this adam_id, reuse it (sticky routing)
        2. Otherwise, select an idle instance (empty last_adam_id)
        3. Otherwise, random selection among all active instances

        Args:
            adam_id: Song ID

        Returns:
            Selected WrapperInstance or None
        """
        async with self._lock:
            instances = self.instance_manager.list_instances()

            # Filter active instances
            active_instances = [inst for inst in instances if inst.is_active()]

            if not active_instances:
                return None

            # Strategy 1: Find instance that processed this adam_id recently
            for instance in active_instances:
                if instance.proxy and instance.proxy.get_last_adam_id() == adam_id:
                    logger.debug(
                        f"Reusing instance {instance.instance_id} for {adam_id} (sticky)"
                    )
                    return instance

            # Strategy 2: Find idle instance (no recent adam_id)
            idle_instances = [
                inst for inst in active_instances
                if inst.proxy and inst.proxy.get_last_adam_id() == ""
            ]

            if idle_instances:
                # Check region matching if applicable
                # TODO: Implement region-based filtering
                selected = random.choice(idle_instances)
                logger.debug(
                    f"Selected idle instance {selected.instance_id} for {adam_id}"
                )
                return selected

            # Strategy 3: Random selection among all active instances
            # TODO: Implement region-based filtering
            selected = random.choice(active_instances)
            logger.debug(
                f"Selected random instance {selected.instance_id} for {adam_id}"
            )
            return selected

    def _check_region_availability(
        self,
        adam_id: str,
        region: str
    ) -> bool:
        """
        Check if a song is available in a given region.

        This is a simplified implementation. The full version would
        query Apple Music API or maintain a cache.

        Args:
            adam_id: Song ID
            region: Region code

        Returns:
            True if available (currently always returns True)
        """
        # TODO: Implement actual region check
        # For now, assume all songs are available in all regions
        return True

    async def get_statistics(self) -> dict:
        """
        Get dispatcher statistics.

        Returns:
            Statistics dictionary
        """
        instances = self.instance_manager.list_instances()
        active_count = sum(1 for inst in instances if inst.is_active())

        return {
            "total_instances": len(instances),
            "active_instances": active_count,
            "idle_instances": sum(
                1 for inst in instances
                if inst.is_active() and inst.proxy and inst.proxy.get_last_adam_id() == ""
            ),
            "busy_instances": sum(
                1 for inst in instances
                if inst.is_active() and inst.proxy and inst.proxy.get_last_adam_id() != ""
            ),
        }
