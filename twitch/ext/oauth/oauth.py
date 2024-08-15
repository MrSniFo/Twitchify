"""
The MIT License (MIT)

Copyright (c) 2024-present Snifo

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT firstED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NON-INFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
"""

from __future__ import annotations

from twitch import Client, utils
from .http import HTTPClient
from .enums import Scopes
import asyncio

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from typing import Optional, Type, Self, List, Union
    from types import TracebackType

import logging
_logger = logging.getLogger(__name__)

__all__ = ('DeviceAuthFlow',)


class DeviceAuthFlow:
    """
    A class to handle the device authentication flow for the Twitch API.

    Parameters
    ----------
    client: Client
        The Twitch client instance.
    scopes: List[Union[str, Scope]]
        List of scopes for the authentication.
    wrap_run: bool
        If True, automatically assigns the `run` method to the client's `run` attribute.

    Example
    -------
    Example usage when wrap_run is True:

    ```python
    from twitch import Client
    from twitch.ext.oauth import DeviceAuthFlow

    client = Client(client_id=..., client_secret=....)
    # Adds support for authentication.
    DeviceAuthFlow(client=client, scopes=['user:read:email'])

    @client.event
    async def on_code(code: str):
        print(f' verification uri: https://www.twitch.tv/activate?device-code={code}')

    # ........ do weird stuff here *-*.

    # Must be called at the end.
    client.run(log_level=10)
    ```
    """

    __slots__ = ('_client', 'wrap_run', '_http', 'scopes')

    def __init__(self,
                 client: Client,
                 *,
                 scopes: List[Union[str, Scopes]],
                 wrap_run: bool = True
                 ) -> None:
        self._client: Client = client
        self.wrap_run: bool = wrap_run
        self._http: HTTPClient = HTTPClient(self._client.client_id)
        if self.wrap_run:
            self._client.run = self.run  # type: ignore
        self.scopes: List[str] = [scope.value if isinstance(scope, Scopes) else scope for scope in scopes]

    async def __aenter__(self) -> Self:
        """
        Enter the runtime context related to this object.
        """
        return self

    async def __aexit__(self, exc_type: Optional[Type[BaseException]],
                        exc_value: Optional[BaseException],
                        traceback: Optional[TracebackType]) -> None:
        """
        Exit the runtime context related to this object.
        """
        await self.close()

    @staticmethod
    def _set_event_loops(func):
        """
        Decorator function to ensure event loops are set for HTTP and Twitch clients.
        """

        async def ensure_event_loops(self, *args, **kwargs):
            if self._http.loop is None:
                loop = asyncio.get_running_loop()
                self._http.loop = loop
                self._client.loop = loop  # Client side.
                self._client.http.loop = loop
            return await func(self, *args, **kwargs)

        return ensure_event_loops

    @_set_event_loops
    async def get_device_code(self) -> tuple[str, str, int, int]:
        """
        Initiates the device authorization flow and retrieves the device code.

        Returns
        -------
        tuple
            A tuple containing the user code, device code, expiration time, and polling interval.
        """
        data = await self._http.start_device_auth_flow(scopes=self.scopes)
        self._client.dispatch('code', data['user_code'])
        return data['user_code'], data['device_code'], data['expires_in'], data['interval']

    @_set_event_loops
    async def revoke_token(self, token: str) -> None:
        """
        Revoke a token.

        Parameters
        ----------
        token: str
            The token to revoke (access token or refresh token).
        """
        await self._http.revoke_token(token=token)

    @_set_event_loops
    async def poll_for_authorization(self, device_code: str, expires_in: int, interval: int) -> tuple[str, str]:
        """
        Polls the Twitch API to check if the user has authorized the application.

        Parameters
        ----------
        device_code: str
            The device code received from the get_device_code method.
        expires_in: int
            The expiration time of the device code.
        interval: int
            The polling interval.

        Returns
        -------
        tuple
            A tuple containing the access token and refresh token.
        """
        data = await self._http.poll_for_token(device_code, expires_in=expires_in, interval=interval)
        self._client.dispatch('auth', data['access_token'], data['refresh_token'])
        return data['access_token'], data['refresh_token']

    async def close(self):
        """
        Closes the HTTP client session.
        """
        await self._http.close()

    def run(self,
            *,
            reconnect: bool = True,
            log_handler: Optional[logging.Handler] = None,
            log_level: Optional[int] = None,
            root_logger: bool = False):
        """
        Run the client with the authentication flow and handle the event loop.

        !!! danger
            This function must be the last function to call as it blocks
            the execution of anything after it.

        Parameters
        ----------
        reconnect: Optional[str]
            Whether to attempt reconnection.
        log_handler: Optional[logging.Handler]
            The logging handler.
        log_level: Optional[int]
            The logging level. Defaults to MISSING, which gets replaced with logging.INFO.
        root_logger: bool
            If True, configure the root logger. If False, configure a logger specific to the calling module.
            Defaults to False.
        """
        if log_handler is None:
            utils.setup_logging(handler=log_handler, level=log_level, root=root_logger)

        async def runner():
            async with self:
                user_code, device_code, expires_in, interval = await self.get_device_code()
                access_token, refresh_token = await self.poll_for_authorization(device_code, expires_in, interval)

            async with self._client:
                await self._client.start(access_token, refresh_token, reconnect=reconnect)

        try:
            asyncio.run(runner())
        except KeyboardInterrupt:
            return