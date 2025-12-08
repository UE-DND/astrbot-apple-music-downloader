"""
Login Handler

Handles Apple Music account login with 2FA support.
Manages login sessions and coordinates with wrapper instances.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict
import uuid

from ..logger import LoggerInterface, get_logger
logger = get_logger()

from .instance_manager import InstanceManager


class LoginState(Enum):
    """Login state."""
    PENDING_PASSWORD = "pending_password"
    PENDING_2FA = "pending_2fa"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class LoginSession:
    """Login session data."""
    session_id: str
    username: str
    password: str
    state: LoginState = LoginState.PENDING_PASSWORD
    created_at: datetime = field(default_factory=datetime.now)
    error: Optional[str] = None
    two_factor_code: Optional[str] = None


class LoginHandler:
    """
    Handles account login flow.

    Manages login sessions and coordinates with wrapper instances
    to complete the authentication process.
    """

    def __init__(self, instance_manager: InstanceManager):
        """
        Initialize login handler.

        Args:
            instance_manager: Instance manager
        """
        self.instance_manager = instance_manager
        self._sessions: Dict[str, LoginSession] = {}
        self._username_to_session: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def start_login(
        self,
        username: str,
        password: str
    ) -> tuple[bool, str, Optional[str]]:
        """
        Start login process.

        Args:
            username: Apple Music username
            password: Apple Music password

        Returns:
            Tuple of (success, message, session_id)
        """
        async with self._lock:
            # Check if already logged in
            existing_instance = self.instance_manager.get_instance_by_username(username)
            if existing_instance:
                return False, f"账户 {username} 已登录", None

            # Check if login session already exists
            if username in self._username_to_session:
                session_id = self._username_to_session[username]
                session = self._sessions.get(session_id)
                if session and session.state == LoginState.PENDING_2FA:
                    return False, "等待输入双因素验证码", session_id

            # Create new login session
            session_id = str(uuid.uuid4())
            session = LoginSession(
                session_id=session_id,
                username=username,
                password=password,
                state=LoginState.PENDING_PASSWORD
            )

            self._sessions[session_id] = session
            self._username_to_session[username] = session_id

            logger.info(f"Started login session for {username}")

            # Initiate login (async)
            asyncio.create_task(self._perform_login(session_id))

            return True, "登录请求已提交", session_id

    async def provide_2fa_code(
        self,
        username: str,
        code: str
    ) -> tuple[bool, str]:
        """
        Provide 2FA verification code.

        Args:
            username: Apple Music username
            code: 2FA code

        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            # Find session
            session_id = self._username_to_session.get(username)
            if not session_id:
                return False, "未找到登录会话"

            session = self._sessions.get(session_id)
            if not session:
                return False, "登录会话无效"

            if session.state != LoginState.PENDING_2FA:
                return False, f"登录状态错误: {session.state.value}"

            # Update session with 2FA code
            session.two_factor_code = code
            logger.info(f"Received 2FA code for {username}")

            # Continue login process
            asyncio.create_task(self._continue_login_with_2fa(session_id))

            return True, "双因素验证码已提交"

    async def get_session_status(
        self,
        session_id: str
    ) -> Optional[LoginSession]:
        """
        Get login session status.

        Args:
            session_id: Session ID

        Returns:
            LoginSession or None
        """
        return self._sessions.get(session_id)

    async def _perform_login(self, session_id: str):
        """
        Perform actual login operation.

        This is a simplified implementation. The full version would:
        1. Start wrapper container with login credentials
        2. Monitor wrapper output for 2FA prompt
        3. Update session state accordingly

        Args:
            session_id: Session ID
        """
        session = self._sessions.get(session_id)
        if not session:
            return

        try:
            # TODO: Implement actual wrapper login logic
            # For now, simulate login process

            # Step 1: Try to add instance (this would trigger wrapper login)
            success, msg, instance = await self.instance_manager.add_instance(
                username=session.username,
                password=session.password,
                region="us"  # Default region
            )

            if success:
                # Login successful
                session.state = LoginState.COMPLETED
                logger.info(f"Login completed for {session.username}")

                # Clean up session after some time
                await asyncio.sleep(60)
                async with self._lock:
                    if session_id in self._sessions:
                        del self._sessions[session_id]
                    if session.username in self._username_to_session:
                        del self._username_to_session[session.username]

            else:
                # Check if 2FA is required
                if "2FA" in msg or "双因素" in msg or "验证码" in msg:
                    session.state = LoginState.PENDING_2FA
                    logger.info(f"2FA required for {session.username}")
                else:
                    # Login failed
                    session.state = LoginState.FAILED
                    session.error = msg
                    logger.error(f"Login failed for {session.username}: {msg}")

        except Exception as e:
            logger.error(f"Login exception for {session.username}: {e}")
            session.state = LoginState.FAILED
            session.error = str(e)

    async def _continue_login_with_2fa(self, session_id: str):
        """
        Continue login with 2FA code.

        Args:
            session_id: Session ID
        """
        session = self._sessions.get(session_id)
        if not session or not session.two_factor_code:
            return

        try:
            # TODO: Implement actual 2FA verification
            # For now, simulate successful verification

            # Add instance with 2FA code
            success, msg, instance = await self.instance_manager.add_instance(
                username=session.username,
                password=session.password,
                region="us"
            )

            if success:
                session.state = LoginState.COMPLETED
                logger.info(f"Login with 2FA completed for {session.username}")

                # Clean up session
                await asyncio.sleep(60)
                async with self._lock:
                    if session_id in self._sessions:
                        del self._sessions[session_id]
                    if session.username in self._username_to_session:
                        del self._username_to_session[session.username]

            else:
                session.state = LoginState.FAILED
                session.error = msg
                logger.error(f"2FA verification failed for {session.username}: {msg}")

        except Exception as e:
            logger.error(f"2FA verification exception: {e}")
            session.state = LoginState.FAILED
            session.error = str(e)

    async def cleanup_expired_sessions(self, max_age_seconds: int = 600):
        """
        Clean up expired login sessions.

        Args:
            max_age_seconds: Maximum session age in seconds
        """
        now = datetime.now()
        to_remove = []

        async with self._lock:
            for session_id, session in self._sessions.items():
                age = (now - session.created_at).total_seconds()
                if age > max_age_seconds:
                    to_remove.append((session_id, session.username))

            for session_id, username in to_remove:
                logger.info(f"Cleaning up expired session: {username}")
                del self._sessions[session_id]
                if username in self._username_to_session:
                    del self._username_to_session[username]
