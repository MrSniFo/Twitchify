# Twitchify

---

[![Discord](https://img.shields.io/discord/938786168592547880)](https://discord.gg/hH4ZkNg6cA)
[![PyPI Version](https://img.shields.io/pypi/v/twitchify)](https://pypi.org/project/twitchify)
![Python versions](https://img.shields.io/pypi/pyversions/twitchify)

Python library for Twitch's WebSocket **EventSub** integration

## Features
- Comprehensive support for WebSocket EventSub, providing real-time Twitch event notifications.
- User-friendly interfaces for seamless integration.
- Built-in support for type hinting, ensuring code clarity and maintainability.

## Installation

You can install Twitchify using pip:

```shell
# Windows
py -3 -m pip install -U twitchify

# Linux/macOS
python3 -m pip install -U twitchify
```

## Documentation
Please refer to the [Events Documentation](https://github.com/MrSniFo/Twitchify/blob/main/docs) for detailed information on handling events with Twitchify.

## Quick Example
```python
from twitch import Client
from twitch.user import Follower

client = Client(client_id="CLIENT ID HERE")

@client.event
async def on_ready():
    """
    Event handler triggered when the client is ready to start processing events.
    """
    print("Ready as %s" % client.user.display_name)


@client.event
async def on_follow(user: Follower):
    """
    Event handler triggered when a user follows the channel.
    """
    print("%s just followed you!" % user.display_name)

client.run(access_token="USER ACCESS TOKEN HERE")
```
