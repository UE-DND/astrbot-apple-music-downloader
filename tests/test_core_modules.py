"""
Unit Tests for Core Modules

Tests for WrapperProxy, InstanceManager, and Dispatcher.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from services.manager import (
    WrapperProxy,
    WrapperProxyConfig,
    InstanceManager,
    WrapperInstance,
    DecryptDispatcher,
    DecryptTask,
    InstanceStatus,
)


# ============================================================================
# WrapperProxy Tests
# ============================================================================

@pytest.fixture
def wrapper_proxy_config():
    """Create wrapper proxy config."""
    return WrapperProxyConfig(
        host="127.0.0.1",
        decrypt_port=10020,
        m3u8_port=20020,
        timeout=5,
    )


@pytest.fixture
def wrapper_proxy(wrapper_proxy_config):
    """Create wrapper proxy instance."""
    return WrapperProxy(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        config=wrapper_proxy_config,
    )


@pytest.mark.asyncio
async def test_wrapper_proxy_initialization(wrapper_proxy):
    """Test wrapper proxy initialization."""
    assert wrapper_proxy.instance_id == "test-instance"
    assert wrapper_proxy.username == "test@example.com"
    assert wrapper_proxy.region == "us"
    assert wrapper_proxy._session is None


@pytest.mark.asyncio
async def test_wrapper_proxy_start_stop(wrapper_proxy):
    """Test wrapper proxy start and stop."""
    # Start
    await wrapper_proxy.start()
    assert wrapper_proxy._session is not None

    # Stop
    await wrapper_proxy.stop()
    assert wrapper_proxy._session is None


@pytest.mark.asyncio
async def test_wrapper_proxy_health_check_success(wrapper_proxy):
    """Test successful health check."""
    # Mock successful HTTP response
    with patch('aiohttp.ClientSession.get') as mock_get:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.__aenter__.return_value = mock_response
        mock_response.__aexit__.return_value = None
        mock_get.return_value = mock_response

        await wrapper_proxy.start()
        healthy = await wrapper_proxy.health_check()
        await wrapper_proxy.stop()

        assert healthy is True


@pytest.mark.asyncio
async def test_wrapper_proxy_health_check_failure(wrapper_proxy):
    """Test failed health check."""
    # Mock failed HTTP response
    with patch('aiohttp.ClientSession.get') as mock_get:
        mock_get.side_effect = Exception("Connection refused")

        await wrapper_proxy.start()
        healthy = await wrapper_proxy.health_check()
        await wrapper_proxy.stop()

        assert healthy is False


@pytest.mark.asyncio
async def test_wrapper_proxy_decrypt_success(wrapper_proxy):
    """Test successful decryption."""
    # Mock successful socket response
    with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_open:
        reader = AsyncMock()
        reader.readexactly = AsyncMock(return_value=b"decrypted_data")
        writer = AsyncMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()
        mock_open.return_value = (reader, writer)

        await wrapper_proxy.start()
        success, data, error = await wrapper_proxy.decrypt(
            adam_id="123456",
            key="test_key",
            sample=b"encrypted_sample",
            sample_index=0,
        )
        await wrapper_proxy.stop()

        assert success is True
        assert data == b"decrypted_data"
        assert error is None


@pytest.mark.asyncio
async def test_wrapper_proxy_decrypt_failure(wrapper_proxy):
    """Test failed decryption."""
    # Mock connection failure
    with patch(
        "asyncio.open_connection",
        new_callable=AsyncMock,
        side_effect=ConnectionRefusedError,
    ):

        await wrapper_proxy.start()
        success, data, error = await wrapper_proxy.decrypt(
            adam_id="123456",
            key="test_key",
            sample=b"encrypted_sample",
            sample_index=0,
        )
        await wrapper_proxy.stop()

        assert success is False
        assert data == b""
        assert "Connection refused" in error


# ============================================================================
# InstanceManager Tests
# ============================================================================

@pytest.fixture
def instance_manager():
    """Create instance manager."""
    return InstanceManager(WrapperProxyConfig())


@pytest.mark.asyncio
async def test_instance_manager_initialization(instance_manager):
    """Test instance manager initialization."""
    assert len(instance_manager._instances) == 0
    assert len(instance_manager._username_to_id) == 0


@pytest.mark.asyncio
async def test_add_instance_success(instance_manager):
    """Test adding instance successfully."""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        success, message, instance = await instance_manager.add_instance(
            username="test@example.com",
            password="password123",
            region="us",
        )

        assert success is True
        assert instance is not None
        assert instance.username == "test@example.com"
        assert instance.region == "us"
        assert instance.status == InstanceStatus.ACTIVE


@pytest.mark.asyncio
async def test_add_instance_duplicate(instance_manager):
    """Test adding duplicate instance."""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # Add first instance
        await instance_manager.add_instance(
            username="test@example.com",
            password="password123",
            region="us",
        )

        # Try to add duplicate
        success, message, instance = await instance_manager.add_instance(
            username="test@example.com",
            password="password123",
            region="us",
        )

        assert success is False
        assert "已存在" in message


@pytest.mark.asyncio
async def test_remove_instance(instance_manager):
    """Test removing instance."""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        with patch.object(WrapperProxy, 'stop', new_callable=AsyncMock):
            # Add instance
            success, _, instance = await instance_manager.add_instance(
                username="test@example.com",
                password="password123",
                region="us",
            )
            instance_id = instance.instance_id

            # Remove instance
            success, message = await instance_manager.remove_instance(instance_id)

            assert success is True
            assert len(instance_manager._instances) == 0


@pytest.mark.asyncio
async def test_get_instance(instance_manager):
    """Test getting instance by ID."""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # Add instance
        _, _, instance = await instance_manager.add_instance(
            username="test@example.com",
            password="password123",
            region="us",
        )
        instance_id = instance.instance_id

        # Get instance
        retrieved = instance_manager.get_instance(instance_id)

        assert retrieved is not None
        assert retrieved.instance_id == instance_id


@pytest.mark.asyncio
async def test_get_instance_by_username(instance_manager):
    """Test getting instance by username."""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # Add instance
        await instance_manager.add_instance(
            username="test@example.com",
            password="password123",
            region="us",
        )

        # Get instance by username
        retrieved = instance_manager.get_instance_by_username("test@example.com")

        assert retrieved is not None
        assert retrieved.username == "test@example.com"


@pytest.mark.asyncio
async def test_list_instances(instance_manager):
    """Test listing all instances."""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # Add multiple instances
        for i in range(3):
            await instance_manager.add_instance(
                username=f"test{i}@example.com",
                password="password123",
                region="us",
            )

        # List instances
        instances = instance_manager.list_instances()

        assert len(instances) == 3


@pytest.mark.asyncio
async def test_get_regions(instance_manager):
    """Test getting available regions."""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # Add instances in different regions
        await instance_manager.add_instance(
            username="test1@example.com",
            password="password123",
            region="us",
        )
        await instance_manager.add_instance(
            username="test2@example.com",
            password="password123",
            region="cn",
        )

        # Get regions
        regions = instance_manager.get_regions()

        assert "us" in regions
        assert "cn" in regions


@pytest.mark.asyncio
async def test_get_client_count(instance_manager):
    """Test getting active client count."""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # Add instances
        for i in range(3):
            await instance_manager.add_instance(
                username=f"test{i}@example.com",
                password="password123",
                region="us",
            )

        # Get count
        count = instance_manager.get_client_count()

        assert count == 3


@pytest.mark.asyncio
async def test_health_check_all(instance_manager):
    """Test health check on all instances."""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        with patch.object(WrapperProxy, 'health_check', new_callable=AsyncMock, return_value=True):
            # Add instances
            for i in range(3):
                await instance_manager.add_instance(
                    username=f"test{i}@example.com",
                    password="password123",
                    region="us",
                )

            # Health check all
            results = await instance_manager.health_check_all()

            assert len(results) == 3
            assert all(healthy for healthy in results.values())


# ============================================================================
# DecryptDispatcher Tests
# ============================================================================

@pytest.fixture
def dispatcher(instance_manager):
    """Create decrypt dispatcher."""
    return DecryptDispatcher(instance_manager)


@pytest.mark.asyncio
async def test_dispatcher_initialization(dispatcher):
    """Test dispatcher initialization."""
    assert dispatcher.instance_manager is not None


@pytest.mark.asyncio
async def test_dispatcher_select_instance_no_instances(dispatcher):
    """Test instance selection with no instances."""
    instance = await dispatcher._select_instance("123456")
    assert instance is None


@pytest.mark.asyncio
async def test_dispatcher_select_instance_with_instances(instance_manager, dispatcher):
    """Test instance selection with available instances."""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # Add instance
        await instance_manager.add_instance(
            username="test@example.com",
            password="password123",
            region="us",
        )

        # Select instance
        instance = await dispatcher._select_instance("123456")

        assert instance is not None
        assert instance.username == "test@example.com"


@pytest.mark.asyncio
async def test_dispatcher_sticky_routing(instance_manager, dispatcher):
    """Test sticky routing for same adam_id."""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # Add multiple instances
        for i in range(3):
            await instance_manager.add_instance(
                username=f"test{i}@example.com",
                password="password123",
                region="us",
            )

        # Select instance for same adam_id twice
        instance1 = await dispatcher._select_instance("123456")
        instance1.proxy.set_last_adam_id("123456")
        instance2 = await dispatcher._select_instance("123456")

        # Should be the same instance (sticky routing)
        assert instance1 is not None
        assert instance2 is not None
        assert instance1.instance_id == instance2.instance_id


@pytest.mark.asyncio
async def test_dispatcher_load_balancing(instance_manager, dispatcher):
    """Test load balancing across instances."""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # Add multiple instances
        for i in range(3):
            await instance_manager.add_instance(
                username=f"test{i}@example.com",
                password="password123",
                region="us",
            )

        # Select instances for different adam_ids
        instances = []
        for i in range(10):
            instance = await dispatcher._select_instance(f"adam-{i}")
            instances.append(instance)

        # Check that different instances were used
        instance_ids = {inst.instance_id for inst in instances if inst}
        assert len(instance_ids) > 1  # Should use multiple instances


@pytest.mark.asyncio
async def test_dispatcher_dispatch_success(instance_manager, dispatcher):
    """Test successful task dispatch."""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        with patch.object(WrapperProxy, 'decrypt', new_callable=AsyncMock) as mock_decrypt:
            mock_decrypt.return_value = (True, b"decrypted_data", None)

            # Add instance
            await instance_manager.add_instance(
                username="test@example.com",
                password="password123",
                region="us",
            )

            # Create task
            task = DecryptTask(
                adam_id="123456",
                key="test_key",
                sample=b"encrypted_sample",
                sample_index=0,
            )

            # Dispatch
            result = await dispatcher.dispatch(task)

            assert result.success is True
            assert result.data == b"decrypted_data"
            assert result.error is None


@pytest.mark.asyncio
async def test_dispatcher_dispatch_no_instances(dispatcher):
    """Test task dispatch with no instances."""
    # Create task
    task = DecryptTask(
        adam_id="123456",
        key="test_key",
        sample=b"encrypted_sample",
        sample_index=0,
    )

    # Dispatch
    result = await dispatcher.dispatch(task)

    assert result.success is False
    assert "没有可用的 wrapper 实例" in result.error


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
