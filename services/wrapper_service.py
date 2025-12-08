"""
Wrapper Service Management

Manages the connection to wrapper-manager service for decryption.
Supports two modes: Native (Python) and Remote.
"""

import asyncio
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple, Union

from .logger import LoggerInterface, get_logger

# 尝试相对导入,失败则使用绝对导入(支持独立运行)
try:
    from ..core.grpc import WrapperManager, WrapperManagerException
    from ..core.config import PluginConfig
except ImportError:
    from core.grpc import WrapperManager, WrapperManagerException
    from core.config import PluginConfig

class WrapperMode(Enum):
    """Wrapper connection mode."""
    NATIVE = "native"     # native Python wrapper-manager (recommended)
    REMOTE = "remote"     # remote wrapper-manager service


@dataclass
class WrapperStatus:
    """Wrapper service status."""
    mode: WrapperMode
    connected: bool = False
    url: str = ""
    regions: list = None
    error: Optional[str] = None
    ready: bool = False
    client_count: int = 0

    def __post_init__(self):
        if self.regions is None:
            self.regions = []


class WrapperService:
    """
    Wrapper service manager.

    Provides unified interface for different wrapper connection modes:
    - Native: Run wrapper-manager in Python (recommended)
    - Remote: Connect to a remote wrapper-manager service
    """

    def __init__(
        self,
        config: Union[PluginConfig, str],
        url: str = "127.0.0.1:18923",
        secure: bool = False,
        plugin_dir: Optional[Path] = None,
        logger: Optional[LoggerInterface] = None
    ):
        """
        Initialize the wrapper service.

        Args:
            config: Either a PluginConfig object or a mode string (native, remote)
            url: Wrapper-manager service URL (host:port for gRPC) - only used if config is str
            secure: Use TLS for gRPC connection - only used if config is str
            plugin_dir: Plugin directory path
            logger: Logger instance (auto-detect if None)
        """
        # Support both PluginConfig object and individual parameters
        if isinstance(config, PluginConfig):
            # Extract configuration from PluginConfig
            self.mode = WrapperMode(config.wrapper.mode)
            self.url = config.wrapper.url
            self.secure = config.wrapper.secure
            self.plugin_dir = config.plugin_dir or Path(".")
            self._debug_mode = config.debug_mode
        else:
            # Legacy mode: individual parameters
            self.mode = WrapperMode(config)
            self.url = url
            self.secure = secure
            self.plugin_dir = plugin_dir or Path(".")
            self._debug_mode = False

        # Logger dependency injection
        self.logger = logger or get_logger()

        self._manager: Optional[WrapperManager] = None
        self._connected = False

        # Native Python wrapper-manager server
        self._native_server = None

    @property
    def is_connected(self) -> bool:
        """Check if connected to wrapper service."""
        return self._connected and self._manager is not None

    @property
    def manager(self) -> Optional[WrapperManager]:
        """Get the WrapperManager instance."""
        return self._manager

    async def init(self) -> Tuple[bool, str]:
        """
        Initialize and connect to the wrapper service.

        Idempotent: Safe to call multiple times, will return success if already initialized.

        Returns:
            Tuple of (success, message)
        """
        # Check if already initialized
        if self._connected and self._manager:
            return True, "服务已初始化"

        try:
            match self.mode:
                case WrapperMode.NATIVE:
                    return await self._init_native()
                case WrapperMode.REMOTE:
                    return await self._init_remote()
        except Exception as e:
            self.logger.error(f"Failed to initialize wrapper service: {e}")
            return False, f"初始化失败: {str(e)}"

    async def _init_native(self) -> Tuple[bool, str]:
        """Initialize native Python wrapper-manager mode."""
        try:
            self.logger.info("启动原生 Python wrapper-manager 服务...")

            # Import native wrapper-manager
            from .wrapper_manager import (
                NativeWrapperManagerServer,
                WrapperProxyConfig
            )

            # Create wrapper proxy config
            proxy_config = WrapperProxyConfig(
                host="127.0.0.1",
                decrypt_port=10020,
                m3u8_port=20020,
                account_port=30020,
                timeout=30
            )

            # Create and start native server
            # Extract port from URL (format: "host:port")
            grpc_port = 18923
            if ":" in self.url:
                try:
                    grpc_port = int(self.url.split(":")[-1])
                except ValueError:
                    self.logger.warning(f"无法解析端口号，使用默认值 18923")

            self._native_server = NativeWrapperManagerServer(
                host="127.0.0.1",
                port=grpc_port,
                proxy_config=proxy_config
            )

            await self._native_server.start()
            self.logger.info(f"原生 wrapper-manager 服务已启动 (端口 {grpc_port})")

            # Wait a moment for server to be ready
            await asyncio.sleep(0.5)

            # Connect to the service
            success, msg = await self._connect_to_manager()
            if not success:
                return False, msg

            # Auto-add default wrapper instance on localhost
            try:
                from .wrapper_manager import WrapperInstance, InstanceStatus
                from .wrapper_manager.wrapper_proxy import WrapperProxy

                instance_id = "default"

                # Create wrapper proxy
                proxy = WrapperProxy(
                    instance_id=instance_id,
                    username="docker-wrapper",
                    region="cn",
                    config=proxy_config
                )

                # Start the proxy (create HTTP session)
                await proxy.start()

                # Create wrapper instance
                instance = WrapperInstance(
                    instance_id=instance_id,
                    username="docker-wrapper",
                    region="cn",
                    status=InstanceStatus.ACTIVE,
                    proxy=proxy
                )

                # Directly add to instance manager
                self._native_server.instance_manager._instances[instance_id] = instance
                self._native_server.instance_manager._username_to_id["docker-wrapper"] = instance_id

                self.logger.info(f"已自动添加 wrapper 实例: {instance_id} (active: {proxy.is_active})")
            except Exception as e:
                self.logger.warning(f"自动添加 wrapper 实例失败: {e}")

            return True, "服务初始化成功"

        except ImportError as e:
            self.logger.error(f"无法导入原生 wrapper-manager: {e}")
            return False, f"原生模式不可用: {str(e)}"
        except Exception as e:
            self.logger.error(f"启动原生 wrapper-manager 失败: {e}")
            return False, f"启动失败: {str(e)}"

    async def _init_remote(self) -> Tuple[bool, str]:
        """Initialize remote connection mode."""
        return await self._connect_to_manager()

    async def _connect_to_manager(self) -> Tuple[bool, str]:
        """Connect to the wrapper manager service."""
        try:
            self._manager = WrapperManager()
            await self._manager.init(self.url, self.secure)

            # Test connection
            status = await self._manager.status()
            self._connected = True

            regions = status.regions if status else []
            client_count = status.client_count if status else 0
            ready = status.ready if status else False

            self.logger.info(
                f"Connected to wrapper-manager at {self.url}, "
                f"regions: {regions}, clients: {client_count}, ready: {ready}"
            )

            if not ready:
                return True, f"已连接到 Wrapper-Manager (等待就绪，当前 {client_count} 个账户)"

            return True, f"已连接到 Wrapper-Manager ({len(regions)} 个地区, {client_count} 个账户)"

        except WrapperManagerException as e:
            self._connected = False
            self.logger.error(f"Failed to connect to wrapper-manager: {e}")
            return False, f"连接失败: {str(e)}"
        except Exception as e:
            self._connected = False
            self.logger.error(f"Unexpected error connecting to wrapper-manager: {e}")
            return False, f"连接失败: {str(e)}"

    async def start(self) -> Tuple[bool, str]:
        """
        Start the wrapper service.

        Returns:
            Tuple of (success, message)
        """
        match self.mode:
            case WrapperMode.NATIVE:
                return True, "原生模式由 init() 自动启动"
            case WrapperMode.REMOTE:
                return True, "远程模式无需启动服务"

    async def stop(self) -> Tuple[bool, str]:
        """
        Stop the wrapper service.

        Returns:
            Tuple of (success, message)
        """
        match self.mode:
            case WrapperMode.NATIVE:
                return await self._stop_native()
            case WrapperMode.REMOTE:
                self._connected = False
                return True, "已断开远程连接"

    async def _stop_native(self) -> Tuple[bool, str]:
        """Stop native Python wrapper-manager."""
        try:
            if self._native_server:
                self.logger.info("停止原生 wrapper-manager 服务...")
                await self._native_server.stop()
                self._native_server = None

            self._connected = False
            return True, "原生 wrapper-manager 已停止"

        except Exception as e:
            self.logger.error(f"停止原生服务失败: {e}")
            return False, f"停止失败: {str(e)}"

    async def get_status(self) -> WrapperStatus:
        """
        Get wrapper service status.

        Returns:
            WrapperStatus object
        """
        status = WrapperStatus(
            mode=self.mode,
            url=self.url,
            connected=self._connected
        )

        if self._connected and self._manager:
            try:
                manager_status = await self._manager.status()
                if manager_status:
                    status.regions = manager_status.regions or []
                    status.ready = manager_status.ready
                    status.client_count = manager_status.client_count
            except Exception as e:
                status.error = str(e)
                status.connected = False

        return status

    async def get_manager(self) -> Optional[WrapperManager]:
        """
        Get the WrapperManager instance.

        Returns:
            WrapperManager if connected, None otherwise
        """
        if not self._connected:
            success, _ = await self._connect_to_manager()
            if not success:
                return None

        return self._manager

    async def close(self):
        """Close the wrapper service connection."""
        # Stop native server if running
        if self.mode == WrapperMode.NATIVE and self._native_server:
            await self._native_server.stop()
            self._native_server = None

        # Close gRPC client
        if self._manager:
            await self._manager.close()
            self._manager = None

        self._connected = False

    # ==================== Helper Methods ====================

    async def _run_command(
        self,
        cmd: list,
        timeout: int = 60,
        env: dict = None
    ) -> Tuple[int, str, str]:
        """Run a shell command."""
        try:
            import os
            env_vars = env or os.environ.copy()

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env_vars
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )

            return (
                process.returncode,
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace")
            )

        except asyncio.TimeoutError:
            if process:
                process.kill()
            return -1, "", "命令执行超时"
        except Exception as e:
            return -1, "", str(e)

    # ==================== Fast Decrypt Methods ====================

    async def decrypt_all(
        self,
        adam_id: str,
        key: str,
        samples: list,
        progress_callback=None
    ) -> Tuple[bool, list, Optional[str]]:
        """
        Decrypt all samples using a SINGLE connection (fast mode).

        This bypasses the gRPC streaming and directly calls WrapperProxy.decrypt_all()
        which is ~100x faster than the per-sample decrypt().

        Args:
            adam_id: Apple Music track ID
            key: Decryption key URI
            samples: List of (sample_data, sample_index) tuples
            progress_callback: Optional callback(decrypted_count, total_count)

        Returns:
            Tuple of (success, decrypted_samples_list, error_message)
        """
        if self.mode == WrapperMode.NATIVE and self._native_server:
            # Direct access to WrapperProxy for maximum speed
            try:
                # Get the default instance's proxy
                instance = self._native_server.instance_manager._instances.get("default")
                if not instance or not instance.proxy:
                    return False, [], "No wrapper instance available"

                # Call decrypt_all directly on the proxy
                return await instance.proxy.decrypt_all(
                    adam_id, key, samples, progress_callback
                )
            except Exception as e:
                self.logger.error(f"Fast decrypt failed: {e}")
                return False, [], str(e)
        else:
            # Remote mode: fall back to per-sample decrypt via gRPC
            # This will be slow but maintains compatibility
            self.logger.warning("decrypt_all not available in remote mode, using slow path")
            return False, [], "Remote mode does not support fast decrypt"
