"""
Python Native Wrapper Manager

This module provides a Python-native implementation of the wrapper-manager gRPC service.
It manages multiple wrapper instances and provides decryption services without Docker dependency.

Architecture:
- gRPC Server: Implements the WrapperManagerService protocol
- Instance Manager: Manages wrapper instances (accounts)
- Decrypt Dispatcher: Routes decryption tasks to appropriate instances
- Wrapper Proxy: Proxies requests to Docker wrapper containers
- Health Monitor: Monitors instance health and performs automatic recovery
"""

from .grpc_server import NativeWrapperManagerServer
from .instance_manager import InstanceManager, WrapperInstance, InstanceStatus
from .dispatcher import DecryptDispatcher, DecryptTask
from .wrapper_proxy import WrapperProxy, WrapperProxyConfig
from .login_handler import LoginHandler, LoginSession
from .health_monitor import HealthMonitor, HealthStatus, HealthCheckResult, RecoveryAction

__all__ = [
    "NativeWrapperManagerServer",
    "InstanceManager",
    "WrapperInstance",
    "InstanceStatus",
    "DecryptDispatcher",
    "DecryptTask",
    "WrapperProxy",
    "WrapperProxyConfig",
    "LoginHandler",
    "LoginSession",
    "HealthMonitor",
    "HealthStatus",
    "HealthCheckResult",
    "RecoveryAction",
]
