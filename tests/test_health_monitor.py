"""
Unit Tests for Health Monitor

Tests health checking, failure detection, and automatic recovery.
"""

import asyncio
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from services.manager import (
    HealthMonitor,
    HealthStatus,
    InstanceManager,
    WrapperInstance,
    WrapperProxy,
    WrapperProxyConfig,
    InstanceStatus,
)


@pytest.fixture
def instance_manager():
    """Create instance manager fixture."""
    return InstanceManager(WrapperProxyConfig())


@pytest.fixture
def health_monitor(instance_manager):
    """Create health monitor fixture."""
    return HealthMonitor(
        instance_manager=instance_manager,
        check_interval=1,  # Short interval for testing
        failure_threshold=2,
        recovery_enabled=True,
        max_recovery_attempts=3,
    )


@pytest.mark.asyncio
async def test_health_monitor_initialization(health_monitor):
    """Test health monitor initialization."""
    assert health_monitor.check_interval == 1
    assert health_monitor.failure_threshold == 2
    assert health_monitor.recovery_enabled is True
    assert health_monitor.max_recovery_attempts == 3
    assert health_monitor._running is False


@pytest.mark.asyncio
async def test_health_monitor_start_stop(health_monitor):
    """Test starting and stopping health monitor."""
    # Start monitor
    await health_monitor.start()
    assert health_monitor._running is True
    assert health_monitor._monitor_task is not None

    # Stop monitor
    await health_monitor.stop()
    assert health_monitor._running is False


@pytest.mark.asyncio
async def test_health_check_healthy_instance(instance_manager, health_monitor):
    """Test health check on healthy instance."""
    # Create mock instance with healthy proxy
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    # Mock proxy
    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = AsyncMock(return_value=True)

    instance_manager._instances["test-instance"] = instance

    # Perform health check
    result = await health_monitor._check_instance_health(instance)

    assert result.instance_id == "test-instance"
    assert result.status == HealthStatus.HEALTHY
    assert result.consecutive_failures == 0
    assert result.error is None
    assert result.response_time_ms is not None


@pytest.mark.asyncio
async def test_health_check_unhealthy_instance(instance_manager, health_monitor):
    """Test health check on unhealthy instance."""
    # Create mock instance with unhealthy proxy
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    # Mock proxy that returns False
    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = AsyncMock(return_value=False)

    instance_manager._instances["test-instance"] = instance

    # Perform health check
    result = await health_monitor._check_instance_health(instance)

    assert result.instance_id == "test-instance"
    assert result.status == HealthStatus.DEGRADED
    assert result.consecutive_failures == 1
    assert "Health check returned false" in result.error


@pytest.mark.asyncio
async def test_health_check_timeout(instance_manager, health_monitor):
    """Test health check timeout."""
    # Create mock instance with slow proxy
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    # Mock proxy that times out
    async def slow_health_check():
        await asyncio.sleep(10)  # Longer than timeout
        return True

    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = slow_health_check

    instance_manager._instances["test-instance"] = instance

    # Perform health check
    result = await health_monitor._check_instance_health(instance)

    assert result.instance_id == "test-instance"
    assert result.status == HealthStatus.UNHEALTHY
    assert result.consecutive_failures == 1
    assert "timeout" in result.error.lower()


@pytest.mark.asyncio
async def test_consecutive_failure_tracking(instance_manager, health_monitor):
    """Test tracking of consecutive failures."""
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = AsyncMock(return_value=False)

    instance_manager._instances["test-instance"] = instance

    # Perform multiple health checks
    for i in range(3):
        result = await health_monitor._check_instance_health(instance)
        await health_monitor._process_health_result(result)

    # Check consecutive failures
    failures = health_monitor._get_consecutive_failures("test-instance")
    assert failures == 3


@pytest.mark.asyncio
async def test_recovery_trigger(instance_manager, health_monitor):
    """Test automatic recovery trigger."""
    # Create unhealthy instance
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = AsyncMock(side_effect=asyncio.TimeoutError)
    instance.proxy.stop = AsyncMock()
    instance.proxy.start = AsyncMock()

    instance_manager._instances["test-instance"] = instance

    health_monitor._perform_recovery = AsyncMock(return_value=(True, "ok"))

    # Mock recovery callbacks
    recovery_start_called = False
    recovery_complete_called = False

    async def on_recovery_start(action):
        nonlocal recovery_start_called
        recovery_start_called = True

    async def on_recovery_complete(instance_id, success, message):
        nonlocal recovery_complete_called
        recovery_complete_called = True

    health_monitor.set_recovery_start_callback(on_recovery_start)
    health_monitor.set_recovery_complete_callback(on_recovery_complete)

    # Trigger failures
    for i in range(3):
        result = await health_monitor._check_instance_health(instance)
        await health_monitor._process_health_result(result)

    # Wait for recovery
    await asyncio.sleep(0.1)

    # Check callbacks were called
    assert recovery_start_called
    assert recovery_complete_called


@pytest.mark.asyncio
async def test_recovery_exponential_backoff(instance_manager, health_monitor):
    """Test exponential backoff for recovery attempts."""
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = AsyncMock(return_value=False)
    instance.proxy.stop = AsyncMock()
    instance.proxy.start = AsyncMock()

    instance_manager._instances["test-instance"] = instance

    # First recovery
    health_monitor._recovery_attempts["test-instance"] = 1
    health_monitor._last_recovery["test-instance"] = datetime.now() - timedelta(seconds=1)

    # Try to trigger recovery immediately (should be blocked by backoff)
    result = await health_monitor._check_instance_health(instance)
    result.consecutive_failures = 3  # Force trigger threshold

    await health_monitor._trigger_recovery(result)

    # Should not increase attempts due to backoff
    assert health_monitor._recovery_attempts["test-instance"] == 1


@pytest.mark.asyncio
async def test_max_recovery_attempts(instance_manager, health_monitor):
    """Test maximum recovery attempts limit."""
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = AsyncMock(return_value=False)
    instance.proxy.stop = AsyncMock()
    instance.proxy.start = AsyncMock()

    instance_manager._instances["test-instance"] = instance

    # Set to max attempts
    health_monitor._recovery_attempts["test-instance"] = 3  # max_recovery_attempts

    result = await health_monitor._check_instance_health(instance)
    result.consecutive_failures = 3

    await health_monitor._trigger_recovery(result)

    # Instance should be marked as failed
    assert instance.status == InstanceStatus.FAILED
    assert instance.no_restart is True


@pytest.mark.asyncio
async def test_health_metrics(instance_manager, health_monitor):
    """Test health metrics collection."""
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = AsyncMock(return_value=True)

    instance_manager._instances["test-instance"] = instance

    # Perform multiple checks
    for _ in range(5):
        result = await health_monitor._check_instance_health(instance)
        await health_monitor._process_health_result(result)

    # Get metrics
    metrics = health_monitor.get_health_metrics("test-instance")

    assert metrics["total_checks"] == 5
    assert metrics["healthy_count"] == 5
    assert metrics["unhealthy_count"] == 0
    assert metrics["avg_response_time_ms"] > 0
    assert metrics["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_get_all_metrics(instance_manager, health_monitor):
    """Test getting metrics for all instances."""
    # Create multiple instances
    for i in range(3):
        instance = WrapperInstance(
            instance_id=f"test-instance-{i}",
            username=f"test{i}@example.com",
            region="us",
            status=InstanceStatus.ACTIVE,
        )
        instance.proxy = AsyncMock(spec=WrapperProxy)
        instance.proxy.health_check = AsyncMock(return_value=True)
        instance_manager._instances[f"test-instance-{i}"] = instance

        # Perform health check
        result = await health_monitor._check_instance_health(instance)
        await health_monitor._process_health_result(result)

    # Get all metrics
    all_metrics = health_monitor.get_all_metrics()

    assert len(all_metrics) == 3
    assert "test-instance-0" in all_metrics
    assert "test-instance-1" in all_metrics
    assert "test-instance-2" in all_metrics


@pytest.mark.asyncio
async def test_health_status_persistence(instance_manager, health_monitor):
    """Test that health status is persisted across checks."""
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = AsyncMock(return_value=True)

    instance_manager._instances["test-instance"] = instance

    # Perform first check
    result1 = await health_monitor._check_instance_health(instance)
    await health_monitor._process_health_result(result1)

    # Get status
    status = health_monitor.get_health_status("test-instance")
    assert status == HealthStatus.HEALTHY

    # Perform second check
    result2 = await health_monitor._check_instance_health(instance)
    await health_monitor._process_health_result(result2)

    # Status should still be healthy
    status = health_monitor.get_health_status("test-instance")
    assert status == HealthStatus.HEALTHY


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
