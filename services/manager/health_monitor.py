"""
Health Monitor

Monitors wrapper instances and automatically recovers from failures.
Provides continuous health checking, failure detection, and automatic restart logic.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Awaitable
from enum import Enum

from ..logger import LoggerInterface, get_logger
logger = get_logger()

from .instance_manager import InstanceManager, WrapperInstance, InstanceStatus


class HealthStatus(Enum):
    """Health status of an instance."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # Some issues but still functional
    UNHEALTHY = "unhealthy"  # Not functional, needs recovery
    RECOVERING = "recovering"  # Currently being recovered


@dataclass
class HealthCheckResult:
    """Result of a health check."""
    instance_id: str
    status: HealthStatus
    timestamp: datetime = field(default_factory=datetime.now)
    error: Optional[str] = None
    response_time_ms: Optional[float] = None
    consecutive_failures: int = 0


@dataclass
class RecoveryAction:
    """Recovery action to perform."""
    instance_id: str
    action_type: str  # "restart", "recreate", "alert"
    reason: str
    timestamp: datetime = field(default_factory=datetime.now)


class HealthMonitor:
    """
    Monitors instance health and performs automatic recovery.

    Features:
    - Periodic health checks
    - Failure detection with configurable thresholds
    - Automatic restart for failed instances
    - Exponential backoff for repeated failures
    - Health metrics collection
    - Recovery callbacks for notifications
    """

    def __init__(
        self,
        instance_manager: InstanceManager,
        check_interval: int = 30,
        failure_threshold: int = 3,
        recovery_enabled: bool = True,
        max_recovery_attempts: int = 5,
    ):
        """
        Initialize health monitor.

        Args:
            instance_manager: InstanceManager to monitor
            check_interval: Health check interval in seconds
            failure_threshold: Number of consecutive failures before recovery
            recovery_enabled: Whether to enable automatic recovery
            max_recovery_attempts: Maximum recovery attempts per instance
        """
        self.instance_manager = instance_manager
        self.check_interval = check_interval
        self.failure_threshold = failure_threshold
        self.recovery_enabled = recovery_enabled
        self.max_recovery_attempts = max_recovery_attempts

        # Health tracking
        self._health_history: Dict[str, List[HealthCheckResult]] = {}
        self._recovery_attempts: Dict[str, int] = {}
        self._last_recovery: Dict[str, datetime] = {}

        # Callbacks
        self._on_health_change: Optional[Callable[[str, HealthStatus, HealthStatus], Awaitable[None]]] = None
        self._on_recovery_start: Optional[Callable[[RecoveryAction], Awaitable[None]]] = None
        self._on_recovery_complete: Optional[Callable[[str, bool, str], Awaitable[None]]] = None

        # Control
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None

        logger.info(f"Health monitor initialized (interval={check_interval}s, threshold={failure_threshold})")

    def set_health_change_callback(
        self,
        callback: Callable[[str, HealthStatus, HealthStatus], Awaitable[None]]
    ):
        """
        Set callback for health status changes.

        Args:
            callback: async function(instance_id, old_status, new_status)
        """
        self._on_health_change = callback

    def set_recovery_start_callback(
        self,
        callback: Callable[[RecoveryAction], Awaitable[None]]
    ):
        """
        Set callback for recovery start events.

        Args:
            callback: async function(recovery_action)
        """
        self._on_recovery_start = callback

    def set_recovery_complete_callback(
        self,
        callback: Callable[[str, bool, str], Awaitable[None]]
    ):
        """
        Set callback for recovery completion.

        Args:
            callback: async function(instance_id, success, message)
        """
        self._on_recovery_complete = callback

    async def start(self):
        """Start health monitoring."""
        if self._running:
            logger.warning("Health monitor already running")
            return

        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Health monitor started")

    async def stop(self):
        """Stop health monitoring."""
        if not self._running:
            return

        self._running = False

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        logger.info("Health monitor stopped")

    async def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                await self._perform_health_checks()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in health monitor loop: {e}", exc_info=True)
                await asyncio.sleep(self.check_interval)

    async def _perform_health_checks(self):
        """Perform health checks on all instances."""
        instances = self.instance_manager.list_instances()

        if not instances:
            return

        logger.debug(f"Performing health checks on {len(instances)} instances")

        # Run checks concurrently
        tasks = []
        for instance in instances:
            if instance.status != InstanceStatus.STOPPED:
                task = self._check_instance_health(instance)
                tasks.append(task)

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Health check exception: {result}")
                elif result:
                    await self._process_health_result(result)

    async def _check_instance_health(self, instance: WrapperInstance) -> HealthCheckResult:
        """
        Check health of a single instance.

        Args:
            instance: Instance to check

        Returns:
            HealthCheckResult
        """
        instance_id = instance.instance_id
        start_time = datetime.now()

        try:
            # Perform health check
            if not instance.proxy:
                return HealthCheckResult(
                    instance_id=instance_id,
                    status=HealthStatus.UNHEALTHY,
                    error="No proxy available",
                )

            # Check if proxy is responsive
            healthy = await asyncio.wait_for(
                instance.proxy.health_check(),
                timeout=5.0
            )

            # Calculate response time
            response_time = (datetime.now() - start_time).total_seconds() * 1000

            if healthy:
                return HealthCheckResult(
                    instance_id=instance_id,
                    status=HealthStatus.HEALTHY,
                    response_time_ms=response_time,
                    consecutive_failures=0,
                )
            else:
                # Get previous failures
                prev_failures = self._get_consecutive_failures(instance_id)

                return HealthCheckResult(
                    instance_id=instance_id,
                    status=HealthStatus.DEGRADED,
                    error="Health check returned false",
                    response_time_ms=response_time,
                    consecutive_failures=prev_failures + 1,
                )

        except asyncio.TimeoutError:
            prev_failures = self._get_consecutive_failures(instance_id)
            return HealthCheckResult(
                instance_id=instance_id,
                status=HealthStatus.UNHEALTHY,
                error="Health check timeout",
                consecutive_failures=prev_failures + 1,
            )

        except Exception as e:
            prev_failures = self._get_consecutive_failures(instance_id)
            return HealthCheckResult(
                instance_id=instance_id,
                status=HealthStatus.UNHEALTHY,
                error=str(e),
                consecutive_failures=prev_failures + 1,
            )

    def _get_consecutive_failures(self, instance_id: str) -> int:
        """Get number of consecutive failures for an instance."""
        history = self._health_history.get(instance_id, [])
        if not history:
            return 0

        # Count failures from the end
        failures = 0
        for result in reversed(history):
            if result.status == HealthStatus.HEALTHY:
                break
            failures += 1

        return failures

    async def _process_health_result(self, result: HealthCheckResult):
        """
        Process health check result and trigger recovery if needed.

        Args:
            result: HealthCheckResult to process
        """
        instance_id = result.instance_id

        # Store result in history
        if instance_id not in self._health_history:
            self._health_history[instance_id] = []

        self._health_history[instance_id].append(result)

        # Keep only last 100 results
        if len(self._health_history[instance_id]) > 100:
            self._health_history[instance_id] = self._health_history[instance_id][-100:]

        # Check if recovery is needed
        if result.status == HealthStatus.UNHEALTHY:
            if result.consecutive_failures >= self.failure_threshold:
                await self._trigger_recovery(result)

        # Log health status
        if result.status != HealthStatus.HEALTHY:
            logger.warning(
                f"Instance {instance_id} health: {result.status.value} "
                f"(failures: {result.consecutive_failures}, error: {result.error})"
            )

    async def _trigger_recovery(self, result: HealthCheckResult):
        """
        Trigger recovery for an unhealthy instance.

        Args:
            result: HealthCheckResult that triggered recovery
        """
        instance_id = result.instance_id

        if not self.recovery_enabled:
            logger.warning(f"Recovery disabled, skipping instance {instance_id}")
            return

        # Check recovery attempts
        attempts = self._recovery_attempts.get(instance_id, 0)
        if attempts >= self.max_recovery_attempts:
            logger.error(
                f"Instance {instance_id} exceeded max recovery attempts ({self.max_recovery_attempts}), "
                f"marking as failed"
            )
            instance = self.instance_manager.get_instance(instance_id)
            if instance:
                instance.status = InstanceStatus.FAILED
                instance.no_restart = True
            return

        # Check if recently recovered (exponential backoff)
        if instance_id in self._last_recovery:
            last_recovery = self._last_recovery[instance_id]
            min_interval = timedelta(seconds=30 * (2 ** attempts))  # Exponential backoff

            if datetime.now() - last_recovery < min_interval:
                logger.debug(f"Skipping recovery for {instance_id}, too soon after last attempt")
                return

        # Create recovery action
        action = RecoveryAction(
            instance_id=instance_id,
            action_type="restart",
            reason=f"Consecutive failures: {result.consecutive_failures}, error: {result.error}",
        )

        # Notify recovery start
        if self._on_recovery_start:
            try:
                await self._on_recovery_start(action)
            except Exception as e:
                logger.error(f"Error in recovery start callback: {e}")

        # Perform recovery
        logger.info(f"Starting recovery for instance {instance_id} (attempt {attempts + 1}/{self.max_recovery_attempts})")
        success, message = await self._perform_recovery(instance_id)

        # Update tracking
        self._recovery_attempts[instance_id] = attempts + 1
        self._last_recovery[instance_id] = datetime.now()

        # Notify recovery complete
        if self._on_recovery_complete:
            try:
                await self._on_recovery_complete(instance_id, success, message)
            except Exception as e:
                logger.error(f"Error in recovery complete callback: {e}")

        if success:
            logger.info(f"Recovery successful for instance {instance_id}: {message}")
            # Reset failure count
            self._recovery_attempts[instance_id] = 0
        else:
            logger.error(f"Recovery failed for instance {instance_id}: {message}")

    async def _perform_recovery(self, instance_id: str) -> tuple[bool, str]:
        """
        Perform recovery actions for an instance.

        Args:
            instance_id: Instance to recover

        Returns:
            Tuple of (success, message)
        """
        instance = self.instance_manager.get_instance(instance_id)
        if not instance:
            return False, "Instance not found"

        try:
            # Mark as recovering
            old_status = instance.status
            instance.status = InstanceStatus.INITIALIZING

            # Stop proxy
            if instance.proxy:
                try:
                    await instance.proxy.stop()
                except Exception as e:
                    logger.warning(f"Error stopping proxy during recovery: {e}")

            # Restart proxy
            if instance.proxy:
                await asyncio.sleep(1)  # Brief delay
                await instance.proxy.start()

                # Verify health
                await asyncio.sleep(2)
                healthy = await instance.proxy.health_check()

                if healthy:
                    instance.status = InstanceStatus.ACTIVE
                    return True, "Instance restarted successfully"
                else:
                    instance.status = old_status
                    return False, "Instance restart failed health check"
            else:
                instance.status = old_status
                return False, "No proxy to restart"

        except Exception as e:
            logger.error(f"Error during recovery: {e}", exc_info=True)
            instance.status = InstanceStatus.FAILED
            return False, f"Recovery exception: {str(e)}"

    def get_health_status(self, instance_id: str) -> Optional[HealthStatus]:
        """
        Get current health status of an instance.

        Args:
            instance_id: Instance ID

        Returns:
            HealthStatus or None
        """
        history = self._health_history.get(instance_id)
        if not history:
            return None

        return history[-1].status

    def get_health_metrics(self, instance_id: str) -> Dict:
        """
        Get health metrics for an instance.

        Args:
            instance_id: Instance ID

        Returns:
            Dict with health metrics
        """
        history = self._health_history.get(instance_id, [])

        if not history:
            return {
                "total_checks": 0,
                "healthy_count": 0,
                "unhealthy_count": 0,
                "avg_response_time_ms": 0,
                "consecutive_failures": 0,
                "recovery_attempts": 0,
            }

        healthy_count = sum(1 for r in history if r.status == HealthStatus.HEALTHY)
        unhealthy_count = sum(1 for r in history if r.status == HealthStatus.UNHEALTHY)

        # Calculate average response time
        response_times = [r.response_time_ms for r in history if r.response_time_ms is not None]
        avg_response_time = sum(response_times) / len(response_times) if response_times else 0

        return {
            "total_checks": len(history),
            "healthy_count": healthy_count,
            "unhealthy_count": unhealthy_count,
            "avg_response_time_ms": round(avg_response_time, 2),
            "consecutive_failures": history[-1].consecutive_failures,
            "recovery_attempts": self._recovery_attempts.get(instance_id, 0),
            "last_check": history[-1].timestamp.isoformat(),
            "last_status": history[-1].status.value,
        }

    def get_all_metrics(self) -> Dict[str, Dict]:
        """
        Get health metrics for all instances.

        Returns:
            Dict mapping instance_id to metrics
        """
        return {
            instance_id: self.get_health_metrics(instance_id)
            for instance_id in self._health_history.keys()
        }
