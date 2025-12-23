"""
Native Wrapper Manager - Usage Example and Test

This file demonstrates how to use the native wrapper-manager implementation.
"""

import asyncio
from wrapper_manager_native import (
    NativeWrapperManagerServer,
    WrapperProxyConfig
)


async def example_server():
    """
    Example: Start a native wrapper-manager server.
    """
    print("=" * 60)
    print("Starting Native Wrapper Manager Server")
    print("=" * 60)

    # Create proxy config
    proxy_config = WrapperProxyConfig(
        host="127.0.0.1",
        decrypt_port=10020,
        m3u8_port=20020,
        account_port=30020,
        timeout=30
    )

    # Create server
    server = NativeWrapperManagerServer(
        host="127.0.0.1",
        port=18923,
        proxy_config=proxy_config
    )

    try:
        # Start server
        await server.start()
        print("\n✅ Server started successfully!")
        print(f"   - gRPC endpoint: 127.0.0.1:18923")
        print(f"   - Wrapper proxy: 127.0.0.1:10020 (decrypt)")
        print(f"   - Wrapper proxy: 127.0.0.1:20020 (m3u8)")
        print("\nPress Ctrl+C to stop...")

        # Keep running
        await server.wait_for_termination()

    except KeyboardInterrupt:
        print("\n\n⏹  Stopping server...")
        await server.stop()
        print("✅ Server stopped")


async def example_client():
    """
    Example: Connect to the server as a client.
    """
    print("\n" + "=" * 60)
    print("Testing Client Connection")
    print("=" * 60)

    # Import the existing gRPC client
    from ...core.grpc import WrapperManager

    try:
        # Connect to server
        manager = WrapperManager(
            url="127.0.0.1:18923",
            secure=False
        )

        print("\n🔗 Connecting to wrapper-manager...")

        # Get status
        status = await manager.status()
        print(f"\n✅ Connected successfully!")
        print(f"   - Ready: {status.ready}")
        print(f"   - Status: {status.status}")
        print(f"   - Client count: {status.client_count}")
        print(f"   - Regions: {', '.join(status.regions) if status.regions else 'None'}")

        # Close connection
        await manager.close()

    except Exception as e:
        print(f"\n❌ Connection failed: {e}")


async def example_standalone_components():
    """
    Example: Use components independently without gRPC.
    """
    print("\n" + "=" * 60)
    print("Testing Standalone Components")
    print("=" * 60)

    from .instance_manager import InstanceManager, WrapperProxyConfig
    from .dispatcher import DecryptDispatcher, DecryptTask

    # Create instance manager
    proxy_config = WrapperProxyConfig(
        host="127.0.0.1",
        decrypt_port=10020
    )
    instance_manager = InstanceManager(proxy_config)

    # Create dispatcher
    dispatcher = DecryptDispatcher(instance_manager)

    print("\n📦 Components initialized")
    print(f"   - Instance manager: {instance_manager}")
    print(f"   - Dispatcher: {dispatcher}")

    # Example: Add instance
    print("\n➕ Adding test instance...")
    success, msg, instance = await instance_manager.add_instance(
        username="test@example.com",
        password="password123",
        region="us"
    )

    if success:
        print(f"   ✅ {msg}")
        print(f"   - Instance ID: {instance.instance_id}")
        print(f"   - Status: {instance.status.value}")
    else:
        print(f"   ❌ {msg}")

    # List instances
    instances = instance_manager.list_instances()
    print(f"\n📊 Total instances: {len(instances)}")
    for inst in instances:
        print(f"   - {inst.username} ({inst.region}) - {inst.status.value}")

    # Cleanup
    print("\n🧹 Cleaning up...")
    await instance_manager.shutdown_all()
    print("   ✅ All instances shut down")


async def main():
    """
    Main entry point for examples.
    """
    print("""
╔══════════════════════════════════════════════════════════════╗
║  Native Wrapper Manager - Python Implementation             ║
║  Strategy: Hybrid (Rewrite manager, keep wrapper container) ║
╚══════════════════════════════════════════════════════════════╝
    """)

    # Choose which example to run
    print("\nAvailable examples:")
    print("1. Start gRPC server")
    print("2. Test client connection (requires server running)")
    print("3. Test standalone components")
    print("4. Run all tests")

    choice = input("\nEnter choice (1-4): ").strip()

    if choice == "1":
        await example_server()
    elif choice == "2":
        await example_client()
    elif choice == "3":
        await example_standalone_components()
    elif choice == "4":
        print("\n🧪 Running all tests...\n")
        # Start server in background
        from .wrapper_manager_native import NativeWrapperManagerServer
        proxy_config = WrapperProxyConfig()
        server = NativeWrapperManagerServer(proxy_config=proxy_config)

        await server.start()
        await asyncio.sleep(2)  # Wait for server to start

        # Run tests
        await example_client()
        await example_standalone_components()

        # Stop server
        await server.stop()
    else:
        print("Invalid choice")


if __name__ == "__main__":
    asyncio.run(main())
