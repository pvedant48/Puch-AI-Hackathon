import asyncio
from typing import Annotated, List
import os
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth.providers.bearer import BearerAuthProvider, RSAKeyPair
from mcp.server.auth.provider import AccessToken
from mcp.types import TextContent, ImageContent, INVALID_PARAMS, INTERNAL_ERROR
from pydantic import BaseModel, Field

import httpx

# --- Load environment variables ---
load_dotenv()
TOKEN = os.environ.get("AUTH_TOKEN")
MY_NUMBER = os.environ.get("MY_NUMBER")
TWITCH_CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID")
TWITCH_OAUTH_TOKEN = os.environ.get("TWITCH_OAUTH_TOKEN")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

assert TOKEN is not None, "Please set AUTH_TOKEN in your .env file"
assert MY_NUMBER is not None, "Please set MY_NUMBER in your .env file"
assert TWITCH_CLIENT_ID is not None, "Set TWITCH_CLIENT_ID in .env"
assert TWITCH_OAUTH_TOKEN is not None, "Set TWITCH_OAUTH_TOKEN in .env"
assert YOUTUBE_API_KEY is not None, "Set YOUTUBE_API_KEY in .env"

# --- Auth Provider ---
class SimpleBearerAuthProvider(BearerAuthProvider):
    def __init__(self, token: str):
        k = RSAKeyPair.generate()
        super().__init__(public_key=k.public_key, jwks_uri=None, issuer=None, audience=None)
        self.token = token

    async def load_access_token(self, token: str) -> AccessToken | None:
        if token == self.token:
            return AccessToken(
                token=token,
                client_id="puch-client",
                scopes=["*"],
                expires_at=None,
            )
        return None

# --- Rich Tool Description model ---
class RichToolDescription(BaseModel):
    description: str
    use_when: str
    side_effects: str | None = None

# --- Twitch & YouTube search helpers ---
async def get_twitch_live_streams(game: str) -> List[dict]:
    # Get game ID first
    async with httpx.AsyncClient() as client:
        game_search = await client.get(
            "https://api.twitch.tv/helix/games",
            params={"name": game},
            headers={"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {TWITCH_OAUTH_TOKEN}"}
        )
        data = game_search.json().get("data", [])
        if not data:
            return []
        game_id = data[0]["id"]
        # Get live streams
        streams_resp = await client.get(
            "https://api.twitch.tv/helix/streams",
            params={"game_id": game_id, "first": 5},
            headers={"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {TWITCH_OAUTH_TOKEN}"}
        )
        results = []
        for s in streams_resp.json().get("data", []):
            results.append({
                "user_name": s["user_name"],
                "title": s["title"],
                "url": f"https://twitch.tv/{s['user_login']}"
            })
        return results

async def get_youtube_live_streams(game: str) -> List[dict]:
    url = (
        f"https://www.googleapis.com/youtube/v3/search?"
        f"part=snippet&q={game}&type=video&eventType=live&maxResults=5&key={YOUTUBE_API_KEY}"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        results = []
        for item in resp.json().get("items", []):
            snippet = item["snippet"]
            video_id = item["id"]["videoId"]
            results.append({
                "channelTitle": snippet["channelTitle"],
                "title": snippet["title"],
                "url": f"https://www.youtube.com/watch?v={video_id}"
            })
        return results

# --- MCP Server Setup ---
mcp = FastMCP(
    "Live Stream Finder MCP Server",
    auth=SimpleBearerAuthProvider(MY_NUMBER),
)

# --- Tool: validate (required by Puch) ---
@mcp.tool
async def validate() -> str:
    return MY_NUMBER

# --- Tool: stream_finder ---
StreamFinderDescription = RichToolDescription(
    description="Find live streams for a game on Twitch or YouTube.",
    use_when="User asks for streams by game and/or platform.",
    side_effects="Returns results with links.",
)

@mcp.tool(description=StreamFinderDescription.model_dump_json())
async def stream_finder(
    game_name: Annotated[str, Field(description="Name of the game, e.g. 'Rocket League'")],
    platform: Annotated[str | None, Field(description="Platform: 'twitch' or 'youtube'. Optional.")] = None,
) -> str:
    results = []
    if not platform or platform.lower() == "twitch":
        twitch_streams = await get_twitch_live_streams(game_name)
        results.extend([
            f"[Twitch] {s['user_name']}: {s['title']} ({s['url']})"
            for s in twitch_streams
        ])
    if not platform or platform.lower() == "youtube":
        youtube_streams = await get_youtube_live_streams(game_name)
        results.extend([
            f"[YouTube] {s['channelTitle']}: {s['title']} ({s['url']})"
            for s in youtube_streams
        ])
    return "\n".join(results) if results else "No live streams found right now."

# --- WhatsApp AI chat message handler example ---
async def handle_user_message(user_input: str) -> str:
    user_input = user_input.strip()
    # You can expand parsing for more platforms/games here
    if "twitch" in user_input.lower():
        game = user_input.replace("twitch", "").strip()
        platform = "twitch"
    elif "youtube" in user_input.lower():
        game = user_input.replace("youtube", "").strip()
        platform = "youtube"
    else:
        game = user_input
        platform = None
    return await stream_finder(game_name=game, platform=platform)

# --- Run MCP Server ---
async def main():
    print("🚀 Starting MCP server on http://0.0.0.0:8086")
    await mcp.run_async("streamable-http", host="0.0.0.0", port=8086)

if __name__ == "__main__":
    asyncio.run(main())
