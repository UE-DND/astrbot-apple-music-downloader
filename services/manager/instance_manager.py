"""
Instance Manager

Manages wrapper instances (accounts) with full lifecycle control.
Each instance represents one Apple Music account with its own wrapper container.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from enum import Enum

from ..logger import LoggerInterface, get_logger
logger = get_logger()

from .wrapper_proxy import WrapperProxy, WrapperProxyConfig, create_instance_id


class InstanceStatus(Enum):
    """Instance status."""
    INITIALIZING = "initializing"
    ACTIVE = "active"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass
class WrapperInstance:
    """
    Wrapper instance data.

    Represents one Apple Music account with its wrapper container.
    """
    instance_id: str
    username: str
    region: str
    status: InstanceStatus = InstanceStatus.INITIALIZING
    created_at: datetime = field(default_factory=datetime.now)
    last_used: datetime = field(default_factory=datetime.now)
    error: Optional[str] = None
    no_restart: bool = False  # If True, don't restart on crash

    # Proxy to wrapper container
    proxy: Optional[WrapperProxy] = None

    def update_last_used(self):
        """Update last used timestamp."""
        self.last_used = datetime.now()

    def is_active(self) -> bool:
        """Check if instance is active."""
        return self.status == InstanceStatus.ACTIVE and self.proxy is not None


class InstanceManager:
    """
    Manages wrapper instances.

    Responsibilities:
    - Add/remove instances
    - Track instance lifecycle
    - Provide instance lookup
    - Monitor instance health
    """

    def __init__(self, proxy_config: Optional[WrapperProxyConfig] = None):
        """
        Initialize instance manager.

        Args:
            proxy_config: Default proxy configuration
        """
        self.proxy_config = proxy_config or WrapperProxyConfig()
        self._instances: Dict[str, WrapperInstance] = {}
        self._username_to_id: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def add_instance(
        self,
        username: str,
        password: str,
        region: str = "us",
    ) -> Tuple[bool, str, Optional[WrapperInstance]]:
        """
        Add a new wrapper instance.

        Args:
            username: Apple Music username
            password: Apple Music password
            region: User's region/storefront

        Returns:
            Tuple of (success, message, instance)
        """
        async with self._lock:
            # Generate instance ID
            instance_id = create_instance_id(username)

            # Check if already exists
            if instance_id in self._instances:
                existing = self._instances[instance_id]
                return False, f"账户 {username} 已存在", existing

            try:
                # Create wrapper proxy
                proxy = WrapperProxy(
                    instance_id=instance_id,
                    username=username,
                    region=region,
                    config=self.proxy_config,
                )

                # Start proxy
                await proxy.start()

                # Create instance
                instance = WrapperInstance(
                    instance_id=instance_id,
                    username=username,
                    region=region,
                    status=InstanceStatus.ACTIVE,
                    proxy=proxy,
                )

                # Store instance
                self._instances[instance_id] = instance
                self._username_to_id[username] = instance_id

                logger.info(f"Added instance: {username} ({instance_id})")
                return True, f"成功添加账户 {username}", instance

            except Exception as e:
                logger.error(f"Failed to add instance {username}: {e}")
                return False, f"添加账户失败: {str(e)}", None

    async def remove_instance(self, instance_id: str) -> Tuple[bool, str]:
        """
        Remove wrapper instance.

        Args:
            instance_id: Instance ID to remove

        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            if instance_id not in self._instances:
                return False, "实例不存在"

            try:
                instance = self._instances[instance_id]

                # Stop proxy
                if instance.proxy:
                    await instance.proxy.stop()

                # Remove from tracking
                del self._instances[instance_id]
                if instance.username in self._username_to_id:
                    del self._username_to_id[instance.username]

                logger.info(f"Removed instance: {instance.username} ({instance_id})")
                return True, f"成功移除账户 {instance.username}"

            except Exception as e:
                logger.error(f"Failed to remove instance {instance_id}: {e}")
                return False, f"移除账户失败: {str(e)}"

    def get_instance(self, instance_id: str) -> Optional[WrapperInstance]:
        """
        Get instance by ID.

        Args:
            instance_id: Instance ID

        Returns:
            WrapperInstance or None
        """
        return self._instances.get(instance_id)

    def get_instance_by_username(self, username: str) -> Optional[WrapperInstance]:
        """
        Get instance by username.

        Args:
            username: Apple Music username

        Returns:
            WrapperInstance or None
        """
        instance_id = self._username_to_id.get(username)
        if instance_id:
            return self._instances.get(instance_id)
        return None

    def list_instances(self) -> List[WrapperInstance]:
        """
        List all instances.

        Returns:
            List of WrapperInstance
        """
        return list(self._instances.values())

    def get_regions(self) -> List[str]:
        """
        Get list of available regions.

        Returns:
            List of unique region codes
        """
        regions = set()
        for instance in self._instances.values():
            if instance.is_active():
                regions.add(instance.region)
        return list(regions)

    def get_client_count(self) -> int:
        """
        Get count of active instances.

        Returns:
            Number of active instances
        """
        return sum(1 for inst in self._instances.values() if inst.is_active())

    async def health_check_all(self) -> Dict[str, bool]:
        """
        Perform health check on all instances.

        Returns:
            Dict mapping instance_id to health status
        """
        results = {}
        tasks = []

        for instance_id, instance in self._instances.items():
            if instance.proxy:
                task = instance.proxy.health_check()
                tasks.append((instance_id, task))

        # Run health checks concurrently
        for instance_id, task in tasks:
            try:
                healthy = await task
                results[instance_id] = healthy
            except Exception as e:
                logger.error(f"Health check failed for {instance_id}: {e}")
                results[instance_id] = False

        return results

    async def cleanup_inactive(self, max_idle_seconds: int = 3600):
        """
        Clean up instances that haven't been used recently.

        Args:
            max_idle_seconds: Maximum idle time in seconds
        """
        now = datetime.now()
        to_remove = []

        for instance_id, instance in self._instances.items():
            idle_seconds = (now - instance.last_used).total_seconds()
            if idle_seconds > max_idle_seconds and not instance.no_restart:
                to_remove.append(instance_id)

        for instance_id in to_remove:
            logger.info(f"Cleaning up idle instance: {instance_id}")
            await self.remove_instance(instance_id)

    async def shutdown_all(self):
        """Shutdown all instances."""
        logger.info("Shutting down all instances...")
        instance_ids = list(self._instances.keys())

        for instance_id in instance_ids:
            await self.remove_instance(instance_id)

        logger.info("All instances shut down")


from typing import Tuple  # Add missing import
