"""
The MIT License (MIT)

Copyright (c) 2023-present Snifo

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NON-INFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
"""

from __future__ import annotations

import logging
from aiohttp import ClientSession, helpers
from asyncio import Lock, sleep, Task
from time import time
from typing import TYPE_CHECKING, Optional, List, Callable, Any

from .errors import (
    UnknownError, TwitchServerError, BadRequest, Unauthorized,
    Forbidden, HTTPException, SubscriptionError, NotFound
)
from . import __version__, __github__
from .utils import format_seconds

if TYPE_CHECKING:
    from .types.stream import Stream
    from .types.user import UserType
    from .types.channel import Channel
    from .types.eventsub.subscriptions import SubscriptionPayload
    from aiohttp import ClientWebSocketResponse
    from .types.http import Validate, Refresh


_logger = logging.getLogger(__name__)


class Route:
    BASE_ROUTE: str = 'https://api.twitch.tv/helix/'

    __slots__ = ('method', 'url')

    def __init__(self, method: str, path: Optional[str] = None, url: Optional[str] = None) -> None:
        """
        Initialize a Route object.

        :param method: The HTTP method of the route.
        :param path: The path of the route.
        :param url: The complete URL of the route (overrides BASE_ROUTE + path). Defaults to None.
        """
        self.method: str = method
        if path is None and url is None:
            raise TypeError
        else:
            if url is not None:
                self.url = url
            else:
                self.url = self.BASE_ROUTE + path

    def __repr__(self) -> str:
        return f'<Route method={self.method} url={self.url}>'


class HTTPClient:
    """Serves as an HTTP client responsible for sending HTTP requests to the Twitch API."""

    __slots__ = ('_dispatch', '_client_id', '_client_secret', '__session', '_session_lock',
                 '_user_agent', '_refresh_token')

    def __init__(self, dispatcher: Callable[..., Any], client: str, secret: Optional[str]) -> None:
        """
        Initialize the HTTPClient object.
        """
        self._dispatch: Callable[[str, Any, Any], Task] = dispatcher
        self._client_id = client
        self._client_secret = secret
        self.__session: Optional[ClientSession] = None
        self._session_lock: Lock = Lock()
        self._user_agent: str = f'Twitchify/{__version__} (GitHub: {__github__})'
        self._refresh_token: Optional[str] = None

    @property
    def is_open(self) -> bool:
        """
        Checks if the HTTP session is open.

        :return: True if the session is open, False otherwise.
        """
        return self.__session is not None and not self.__session.closed

    async def _open(self, *, access_token: str) -> None:
        """
        Opens an HTTP session.

        :param access_token: The access token to use for authentication.
        """
        async with self._session_lock:
            if not self.is_open:
                headers = {
                    'Client-ID': self._client_id,
                    'Authorization': f'Bearer {access_token}',
                    'Content-Type': 'application/json'
                }
                self.__session = ClientSession(headers=headers)
                _logger.debug('New HTTP session has been created.')

    async def close(self) -> bool:
        """
        Closes the HTTP session.

        :return:
         True if the session is closed, False otherwise.
        """
        async with self._session_lock:
            if self.is_open:
                await self.__session.close()
                self.__session = None
                _logger.debug('HTTP session has been closed.')
        return not self.is_open

    async def open_session(self, token: str, refresh_token: Optional[str] = None) -> Validate:
        """
        Verifies the access token and opens a new session with it.

        :param token:
         The access token for authentication.

        :param refresh_token:
         (Optional) The refresh token for refreshing the access token.

        :return: A validation response.
        """
        # Opening a session.
        await self._open(access_token=token)
        self._refresh_token: Optional[str] = refresh_token
        validation: Validate = await self._validate_token(generate=True)
        if validation['expires_in'] == 0:
            _logger.debug('Old application detected, exempt from expiration rules.'
                          ' Investigation needed.')
            if self._refresh_token is not None:
                _logger.warning(
                    'The refresh token has been removed due to the access token returning an'
                    ' expire time of 0.')
        else:
            self._refresh_token = refresh_token

        return validation

    async def ws_connect(self, *, url: str) -> ClientWebSocketResponse:
        """
        Creates a websocket using the existing session.

        :return:
         The created websocket.
        """

        websocket: ClientWebSocketResponse = await self.__session.ws_connect(
            url=url,
            headers={'User-Agent': self._user_agent},
            timeout=30,
            autoclose=False
        )
        return websocket

    async def _request(self, *, route: Route, **kwargs) -> dict:
        """
        HTTP request base.

        :param route:
         The route to send the request to.

        :param kwargs:
         Additional parameters to pass to the request.

        :return:
         The response from the API.
        """
        method = route.method
        url = route.url
        for retry_count in range(1, 4):
            try:
                async with self.__session.request(method, url, **kwargs) as response:

                    if response.status in [200, 202]:
                        return await response.json()
                    elif response.status == 400:
                        print(await response.json())
                        raise BadRequest
                    elif response.status == 401:
                        raise Unauthorized
                    elif response.status == 403:
                        raise Forbidden
                    elif response.status == 404:
                        raise NotFound
                    elif 500 <= response.status < 600:
                        raise TwitchServerError
                    else:
                        raise UnknownError
            except OSError:
                if 3 >= retry_count:
                    _logger.info(f'Request failed: {route}. Retrying in'
                                 f' {5 * retry_count}seconds...')
                    await sleep(5 * retry_count)
        else:
            raise HTTPException

    async def _generate_token(self) -> Optional[Refresh]:
        if self._refresh_token and self._client_secret:
            # There is a chance that both the task and the normal
            # request can refresh the token at the same time.
            if not self._session_lock.locked():
                async with self._session_lock:
                    # Encoding the client secret.
                    encoded_secret = helpers.quote(self._refresh_token)
                    params = {'grant_type': 'refresh_token',
                              'refresh_token': encoded_secret,
                              'client_id': self._client_id,
                              'client_secret': self._client_secret}
                    _logger.debug('Generating a new access token to refresh the existing one.')
                    route = Route(method='POST', url='https://id.twitch.tv/oauth2/token')
                    refresh: Refresh = await self.request(route=route, params=params)
                    # Updating the session headers.
                    self.__session.headers.update({
                        'Authorization': f'Bearer {refresh["access_token"]}'
                    })
                    _logger.debug('Session headers have been successfully updated with'
                                  ' the new access token.')
                    self._dispatch('refresh_token', refresh['access_token'])
                    return refresh
        return None

    async def _validate_token(self, *, generate: bool = False) -> Validate:
        """
        Validating access token.

        :param generate:
         Generate a new token if it's unauthorized. Defaults to False.

        :return:
         The validation response.
        """
        while True:
            try:
                route = Route(method='GET', url='https://id.twitch.tv/oauth2/validate')
                validation: Validate = await self._request(route=route)
                _logger.debug('Access token successfully validated.')
                return validation
            except Unauthorized:
                if generate and (self._client_secret and self._refresh_token):
                    try:
                        # Generating a new access token.
                        await self._generate_token()
                    except (BadRequest, Forbidden):
                        raise Unauthorized(message='Invalid refresh token or client secret.')
                else:
                    raise

    async def Refresher(self, *, expires_in: int) -> None:
        """
        Refresher task to refresh the current access token.

        :param expires_in:
         The expiration time of the current access token.
        """
        start_time = time()
        # If the refresh_token or client_secret is missing,
        if self._refresh_token and self._client_secret:
            _logger.debug(f'A new token will be generated in {format_seconds(expires_in - 300)}.')
        else:
            # Set expires_in to a default value of 3540 seconds (59 minutes).
            expires_in = 3540 + 300
            _logger.debug('Access token generation disabled due to'
                          ' missing refresh token or client secret.')
        while True:
            # Create a new access token approximately 5 minutes before the
            # current token's expiration.
            await sleep(min((expires_in - 300), 3540))
            current_time = time()
            elapsed_time = current_time - start_time
            try:
                if elapsed_time >= expires_in - 300:
                    # Reset the refresh token timer
                    start_time = time()
                    await self._generate_token()
                    _logger.debug(f'A new token will be generated in '
                                  f'{format_seconds(expires_in - 300)}.')
                # ==> Validating the access token <==
                validation: Validate = await self._validate_token()
                # Update the expiration time of the access token.
                expires_in = validation['expires_in']
            except BadRequest:
                _logger.warning('Invalid Refresh Token.'
                                ' The automatic generation feature has been disabled.')
                self._refresh_token = None

    async def request(self, *, route: Route, **kwargs) -> _request:
        """
        Sends an HTTP request to the specified route.

        :param route:
         The route to send the request to.

        :param kwargs:
         Additional parameters to pass to the request.

        :return:
         The response from the API.
        """
        while True:
            try:
                _logger.debug(f'Sending request: {route} kwargs: {kwargs}.')
                data: dict = await self._request(route=route, **kwargs)
                _logger.debug(f'Received response: {data}')
                return data
            except Unauthorized:
                try:
                    _logger.error(f'Unable to make the request to URL: {route.url}.'
                                  f' Unauthorized access.')
                    await self._validate_token(generate=True)
                except (Unauthorized, BadRequest):
                    raise

    async def subscribe(self, *, user_id: str, session_id: str,
                        subscriptions: List[SubscriptionPayload]) -> None:
        """
        Subscribes to multiple events with the specified subscriptions.

        :param user_id:
         The user ID.

        :param session_id:
         The session ID for the subscriptions.

        :param subscriptions:
         A list of event Subscription.
        """
        for subscription in subscriptions:
            try:
                data = {
                    'type': subscription['name'],
                    'version': subscription['version'],
                    'condition': {
                        'user_id': user_id,
                        'broadcaster_user_id': user_id,
                        'moderator_user_id': user_id,
                        'to_broadcaster_user_id': user_id
                    },
                    'transport': {
                        'method': 'websocket',
                        'session_id': session_id
                    }
                }
                _logger.debug(f'Subscribing to '
                              f'`{subscription["name"]}` event version {subscription["version"]}.')
                route = Route(method='POST', path='eventsub/subscriptions')
                await self.request(route=route, json=data)
            except Forbidden:
                raise Forbidden(f'Subscription'
                                f' `{subscription["name"]}` is missing proper authorization')
            except BadRequest:
                raise SubscriptionError(subscription=subscription['name'],
                                        version=subscription['version'])
        self._dispatch('ready')

    async def get_client(self) -> UserType:
        """
        Retrieves the broadcaster with the associated access token.
        """
        data = await self.request(route=Route(method='GET', path='users'))
        return data['data'][0]

    async def get_channel(self, broadcaster_id: str) -> Channel:
        """
        Retrieves the channel information.
        """
        _route = Route(method='GET', path=f'channels?broadcaster_id={broadcaster_id}')
        data = await self.request(route=_route)
        return data['data'][0]

    async def get_stream(self, user_id: str) -> Optional[Stream]:
        """
        Retrieves the stream.
        """
        try:
            _route = Route(method='GET', path=f'streams?user_id={user_id}')
            data = await self.request(route=_route)
            if len(data['data']) == 1:
                return data['data'][0]
        except NotFound:
            pass
        return None
