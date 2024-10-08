from twitch.ext.oauth import DeviceAuthFlow, Scopes
from twitch.types import eventsub
from twitch import Client
import asyncio


class Twitch(Client):
    def __init__(self, client_id: str, **options) -> None:
        super().__init__(client_id, **options)
        self.auth_flow = DeviceAuthFlow(
            self,
            scopes=[Scopes.USER_READ_EMAIL, Scopes.MODERATOR_READ_FOLLOWERS],
            dispatch=False,
            wrap_run=False
        )

    async def on_ready(self) -> None:
        """Notify when the client is ready"""
        print('Client is ready!')

    async def on_follow(self, data: eventsub.channels.FollowEvent) -> None:
        """Handle new follower events"""
        await self.channel.chat.send_message(f'{data["user_name"]} has followed the channel!')

    async def custom_auth_flow(self) -> None:
        """Custom method to manage device authentication flow"""
        async with self.auth_flow:
            # Retrieve device code and display the verification URL
            user_code, device_code, expires_in, interval = await self.auth_flow.get_device_code()
            print(f'Verification URI: https://www.twitch.tv/activate?device-code={user_code}')

            # Poll for the authorization and handle token retrieval
            try:
                access_token, refresh_token = await self.auth_flow.poll_for_authorization(device_code,
                                                                                          expires_in,
                                                                                          interval)
                print(f'Access Token: {access_token}\nRefresh Token: {refresh_token}')
            except Exception as e:
                print(f'Failed to authorize: {e}')
                return

        # Start the client with the obtained tokens
        async with self:
            await self.start(access_token, refresh_token)

    async def run_client(self) -> None:
        """Run the client with full control over device authentication and event handling"""
        await self.custom_auth_flow()


client = Twitch(client_id='YOUR_CLIENT_ID')
asyncio.run(client.run_client())
