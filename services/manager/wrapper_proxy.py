"""
Wrapper Proxy

Proxies decryption requests to Docker wrapper containers.
This allows us to keep the complex C/C++ decryption logic in containers
while implementing the manager logic in Python.
"""

import asyncio
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple
import socket
import struct
import aiohttp

from ..logger import LoggerInterface, get_logger
logger = get_logger()
from .retry_utils import retry_async, RetryConfig, RetryStrategy, ErrorHandler


@dataclass
class WrapperProxyConfig:
    """Configuration for wrapper proxy."""
    host: str = "127.0.0.1"
    decrypt_port: int = 10020
    m3u8_port: int = 20020
    account_port: int = 30020
    timeout: int = 30


class WrapperProxy:
    """
    Proxy for Docker wrapper container.

    Forwards requests to the wrapper HTTP endpoints and handles responses.
    Each proxy instance corresponds to one wrapper container (one account).
    """

    def __init__(
        self,
        instance_id: str,
        username: str,
        region: str,
        config: WrapperProxyConfig,
    ):
        """
        Initialize wrapper proxy.

        Args:
            instance_id: Unique instance ID
            username: Apple Music username
            region: User's region/storefront
            config: Proxy configuration
        """
        self.instance_id = instance_id
        self.username = username
        self.region = region
        self.config = config

        # Construct base URLs
        self.account_url = f"http://{config.host}:{config.account_port}"

        # HTTP client (only for account service)
        self._session: Optional[aiohttp.ClientSession] = None

        # State tracking
        self._last_adam_id: str = ""
        self._active: bool = False

    async def start(self):
        """Start the proxy (create HTTP session for account service)."""
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
            self._active = True
            logger.debug(f"Wrapper proxy started: {self.instance_id}")

    async def stop(self):
        """Stop the proxy (close HTTP session)."""
        if self._session:
            await self._session.close()
            self._session = None
        self._active = False
        logger.debug(f"Wrapper proxy stopped: {self.instance_id}")

    @property
    def is_active(self) -> bool:
        """Check if proxy is active."""
        return self._active and self._session is not None

    def get_last_adam_id(self) -> str:
        """Get the last adam_id this instance processed."""
        return self._last_adam_id

    def set_last_adam_id(self, adam_id: str):
        """Set the last adam_id this instance processed."""
        self._last_adam_id = adam_id

    async def decrypt(
        self,
        adam_id: str,
        key: str,
        sample: bytes,
        sample_index: int
    ) -> Tuple[bool, bytes, Optional[str]]:
        """
        Decrypt audio sample using raw socket communication.

        The C wrapper protocol (handle function in main.c):
        Initial context setup (once per song/connection):
        1. adamSize (1 byte uint8) - adam_id length
        2. adam (adamSize bytes) - adam_id string
        3. uri_size (1 byte uint8) - key URI length
        4. uri (uri_size bytes) - key URI string

        Then loop for each sample:
        5. size (4 bytes uint32 native endian) - sample size
        6. sample (size bytes) - encrypted data
        7. Server returns decrypted data (size bytes)

        Args:
            adam_id: Song ID
            key: Decryption key URI (e.g., "skd://...")
            sample: Encrypted sample data
            sample_index: Sample index

        Returns:
            Tuple of (success, decrypted_data, error)
        """
        print(f"[WRAPPER_PROXY] DEBUG: decrypt called with adam_id={adam_id}, sample_size={len(sample)}")
        if not self.is_active:
            return False, b"", "Proxy not active"

        try:
            # Connect to decrypt port
            reader, writer = await asyncio.open_connection(
                self.config.host,
                self.config.decrypt_port
            )

            logger.info(f"[Decrypt] Connecting to {self.config.host}:{self.config.decrypt_port} with adam_id={adam_id}, sample_size={len(sample)}")

            # Send context information
            # Format: [adamSize (1 byte)][adam][uri_size (1 byte)][uri]
            adam_id_bytes = adam_id.encode('utf-8')
            key_bytes = key.encode('utf-8')

            # Send adam_id length (1 byte uint8)
            writer.write(bytes([len(adam_id_bytes)]))
            # Send adam_id
            writer.write(adam_id_bytes)
            # Send uri/key length (1 byte uint8) - NOT 4 bytes!
            writer.write(bytes([len(key_bytes)]))
            # Send uri/key
            writer.write(key_bytes)
            await writer.drain()

            # Now send sample for decryption
            # Send sample length (4 bytes uint32 native/little endian)
            writer.write(struct.pack('<I', len(sample)))
            # Send sample data
            writer.write(sample)
            await writer.drain()

            # Read decrypted data (same length as sample)
            decrypted_data = await asyncio.wait_for(
                reader.readexactly(len(sample)),
                timeout=30.0
            )

            logger.debug(f"[Decrypt] Success: decrypted {len(decrypted_data)} bytes")
            return True, decrypted_data, None

        except asyncio.TimeoutError:
            logger.error(f"[Decrypt] Timeout for {adam_id}")
            return False, b"", "Socket timeout"
        except ConnectionRefusedError:
            logger.error(f"[Decrypt] Connection refused to {self.config.host}:{self.config.decrypt_port}")
            return False, b"", "Connection refused"
        except Exception as e:
            logger.error(f"[Decrypt] Exception: {e}")
            return False, b"", str(e)
        finally:
            # Close connection
            if 'writer' in locals():
                writer.close()
                await writer.wait_closed()

    async def decrypt_all(
        self,
        adam_id: str,
        key: str,
        samples: list,
        progress_callback=None
    ) -> Tuple[bool, list, Optional[str]]:
        """
        Decrypt all audio samples using a SINGLE connection (fast mode).

        The C wrapper protocol allows sending multiple samples on one connection:
        1. Connect once
        2. Send adam_id and key (once)
        3. Loop: send sample size + data, read decrypted data
        4. Close connection

        This is ~100x faster than decrypt() which creates a new connection per sample.

        Args:
            adam_id: Song ID
            key: Decryption key URI (e.g., "skd://...")
            samples: List of (sample_data, sample_index) tuples
            progress_callback: Optional callback(decrypted_count, total_count)

        Returns:
            Tuple of (success, decrypted_samples_list, error)
        """
        if not self.is_active:
            return False, [], "Proxy not active"

        total_samples = len(samples)
        # Return decrypted samples in INPUT ORDER (not by sample_index!)
        # Caller is responsible for mapping back to original positions
        decrypted_samples = []

        try:
            # Connect to decrypt port (ONCE for all samples)
            reader, writer = await asyncio.open_connection(
                self.config.host,
                self.config.decrypt_port
            )

            logger.info(f"[Decrypt] Connected to {self.config.host}:{self.config.decrypt_port} for {total_samples} samples")

            # Send context information (ONCE)
            adam_id_bytes = adam_id.encode('utf-8')
            key_bytes = key.encode('utf-8')

            # Send adam_id length (1 byte uint8)
            writer.write(bytes([len(adam_id_bytes)]))
            # Send adam_id
            writer.write(adam_id_bytes)
            # Send uri/key length (1 byte uint8)
            writer.write(bytes([len(key_bytes)]))
            # Send uri/key
            writer.write(key_bytes)
            await writer.drain()

            # Now loop through all samples on the SAME connection
            for i, (sample_data, sample_index) in enumerate(samples):
                # Send sample length (4 bytes uint32 little endian)
                writer.write(struct.pack('<I', len(sample_data)))
                # Send sample data
                writer.write(sample_data)
                await writer.drain()

                # Read decrypted data (same length as sample)
                decrypted_data = await asyncio.wait_for(
                    reader.readexactly(len(sample_data)),
                    timeout=30.0
                )

                # Append in input order - caller maps back to original positions
                decrypted_samples.append(decrypted_data)

                # Progress callback
                if progress_callback and (i + 1) % 100 == 0:
                    progress_callback(i + 1, total_samples)

            logger.info(f"[Decrypt] All {total_samples} samples decrypted successfully")
            return True, decrypted_samples, None

        except asyncio.TimeoutError:
            logger.error(f"[Decrypt] Timeout for {adam_id}")
            return False, [], "Socket timeout"
        except asyncio.IncompleteReadError as e:
            logger.error(f"[Decrypt] Incomplete read: {e}")
            return False, [], f"Incomplete read: {e}"
        except ConnectionRefusedError:
            logger.error(f"[Decrypt] Connection refused to {self.config.host}:{self.config.decrypt_port}")
            return False, [], "Connection refused"
        except Exception as e:
            logger.error(f"[Decrypt] Exception: {e}")
            return False, [], str(e)
        finally:
            # Close connection
            if 'writer' in locals():
                writer.close()
                await writer.wait_closed()

    @retry_async(RetryConfig(
        max_attempts=3,
        initial_delay=0.5,
        strategy=RetryStrategy.EXPONENTIAL,
        retry_on_exceptions=(ConnectionError, socket.timeout)
    ))
    async def get_m3u8(self, adam_id: str) -> Tuple[bool, str, Optional[str]]:
        """
        Get M3U8 playlist URL using raw socket communication.

        The C wrapper protocol:
        - Send: [adamSize (1 byte)][adamId (adamSize bytes)]
        - Receive: plain text URL + newline (or just newline on failure)

        Args:
            adam_id: Song ID

        Returns:
            Tuple of (success, m3u8_url, error)
        """
        if not self.is_active:
            return False, "", "Proxy not active"

        # Create socket connection
        try:
            # Connect to m3u8 port
            reader, writer = await asyncio.open_connection(
                self.config.host,
                self.config.m3u8_port
            )

            logger.debug(f"[M3U8] Connecting to {self.config.host}:{self.config.m3u8_port} for adam_id={adam_id}")

            # According to C code: send adam_id as [adamSize][adamId]
            adam_id_bytes = adam_id.encode('utf-8')

            # Send adam_id length and then adam_id
            writer.write(bytes([len(adam_id_bytes)]))  # uint8
            writer.write(adam_id_bytes)
            await writer.drain()

            # Read response: plain text URL ending with newline
            # The C code sends: m3u8_url + "\n" or just "\n" on failure
            response = await asyncio.wait_for(reader.readline(), timeout=30.0)
            m3u8_url = response.decode('utf-8', errors='ignore').strip()

            if m3u8_url and m3u8_url.startswith('http'):
                logger.debug(f"[M3U8] Success: got URL length {len(m3u8_url)}")
                return True, m3u8_url, None
            else:
                logger.error(f"[M3U8] Failed: empty or invalid URL response")
                return False, "", "Failed to get M3U8 URL"

        except asyncio.TimeoutError:
            logger.error(f"[M3U8] Timeout for {adam_id}")
            return False, "", "Socket timeout"
        except ConnectionRefusedError:
            logger.error(f"[M3U8] Connection refused to {self.config.host}:{self.config.m3u8_port}")
            return False, "", "Connection refused"
        except Exception as e:
            logger.error(f"[M3U8] Exception: {e}")
            return False, "", str(e)
        finally:
            # Close connection
            if 'writer' in locals():
                writer.close()
                await writer.wait_closed()

    async def get_lyrics(
        self,
        adam_id: str,
        storefront: str,
        language: str
    ) -> Tuple[bool, dict, Optional[str]]:
        """
        Get song lyrics.

        Args:
            adam_id: Song ID
            storefront: Storefront/region code
            language: Language code

        Returns:
            Tuple of (success, lyrics_data, error)
        """
        if not self.is_active:
            return False, {}, "Proxy not active"

        try:
            payload = {
                "adam_id": adam_id,
                "storefront": storefront,
                "language": language,
            }

            async with self._session.post(
                f"{self.account_url}/lyrics",
                json=payload
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    return False, {}, f"HTTP {response.status}: {error_text}"

                result = await response.json()

                if result.get("success"):
                    lyrics_data = result.get("lyrics", {})
                    return True, lyrics_data, None
                else:
                    error = result.get("error", "Unknown error")
                    return False, {}, error

        except Exception as e:
            logger.error(f"Get lyrics exception: {e}")
            return False, {}, str(e)

    async def get_account_info(self) -> Optional[dict]:
        """
        Get account information from wrapper.

        Returns:
            Dict with storefront_id, dev_token, music_token or None
        """
        if not self.is_active:
            return None

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json'
            }
            async with self._session.get(
                f"{self.account_url}/account",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    logger.error(f"Account info request failed: {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Failed to get account info for {self.instance_id}: {e}")
            return None

    async def health_check(self) -> bool:
        """
        Check if wrapper container is healthy.

        Returns:
            True if healthy, False otherwise
        """
        if not self.is_active:
            return False

        try:
            # Use account endpoint for health check
            async with self._session.get(
                f"{self.account_url}/account",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                return response.status == 200
        except Exception:
            return False


def create_instance_id(username: str) -> str:
    """
    Create deterministic instance ID from username.

    Uses UUID v5 with the same namespace as the Go implementation.

    Args:
        username: Apple Music username

    Returns:
        Instance ID string
    """
    namespace = uuid.UUID("77777777-7777-7777-7777-777777777777")
    return str(uuid.uuid5(namespace, username))
