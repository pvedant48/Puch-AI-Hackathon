import asyncio
import os
import time
from typing import Annotated, List, Dict
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth.providers.bearer import BearerAuthProvider, RSAKeyPair
from mcp.server.auth.provider import AccessToken
from pydantic import BaseModel, Field
import httpx

# ===== Load environment variables =====
load_dotenv()
TOKEN = os.environ.get("AUTH_TOKEN")
MY_NUMBER = os.environ.get("MY_NUMBER")
TWITCH_CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID")
TWITCH_OAUTH_TOKEN = os.environ.get("TWITCH_OAUTH_TOKEN")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

assert TOKEN, "AUTH_TOKEN is required in .env"
assert MY_NUMBER, "MY_NUMBER is required in .env"
assert TWITCH_CLIENT_ID, "TWITCH_CLIENT_ID required in .env"
assert TWITCH_OAUTH_TOKEN, "TWITCH_OAUTH_TOKEN required in .env"
assert YOUTUBE_API_KEY, "YOUTUBE_API_KEY required in .env"

# ===== Auth Provider =====
class SimpleBearerAuthProvider(BearerAuthProvider):
    def __init__(self, token: str):
        k = RSAKeyPair.generate()
        super().__init__(public_key=k.public_key)
        self.token = token

    async def load_access_token(self, token: str) -> AccessToken | None:
        if token == self.token:
            return AccessToken(token=token, client_id="puch-client", scopes=["*"])
        return None

# ===== Single MCP Server =====
mcp = FastMCP("Unified MCP Server", auth=SimpleBearerAuthProvider(MY_NUMBER))

# ===== RichToolDescription =====
class RichToolDescription(BaseModel):
    description: str
    use_when: str
    side_effects: str | None = None

# ====== In-memory tracking for DSA ======
user_last_request: Dict[str, float] = {}
user_points: Dict[str, int] = {}
user_current_problem: Dict[str, dict] = {}

# ====== LeetCode daily problem fetcher ======
async def get_leetcode_daily_problem() -> dict:
    url = "https://leetcode.com/graphql"
    query = """
    query questionOfToday {
      activeDailyCodingChallengeQuestion {
        date
        link
        question {
          title
          titleSlug
          difficulty
        }
      }
    }
    """
    payload = {"operationName": "questionOfToday", "query": query}
    headers = {"Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=headers)
        data = resp.json().get("data", {}).get("activeDailyCodingChallengeQuestion", {})
        if data and data.get("question"):
            q = data["question"]
            return {
                "title": q["title"],
                "link": f"https://leetcode.com/problems/{q['titleSlug']}/",
                "difficulty": q["difficulty"],
                "date": data.get("date"),
            }
    return {}

# ====== DSA Tools ======
@mcp.tool(description=RichToolDescription(
    description="Get today's DSA problem from LeetCode",
    use_when="When user asks 'dsa'",
    side_effects="Updates last request time"
).model_dump_json())
async def dsa_daily_problem(user_id: Annotated[str, Field(description="User ID")]) -> str:
    now = time.time()
    last_time = user_last_request.get(user_id, 0)

    if now - last_time < 86400 and user_id in user_current_problem:
        p = user_current_problem[user_id]
        return f"Your DSA problem: {p['title']} ({p['difficulty']})\n{p['link']}"

    prob = await get_leetcode_daily_problem()
    if not prob:
        return "Couldn't fetch today's problem."

    user_current_problem[user_id] = prob
    user_last_request[user_id] = now

    return f"Here’s your DSA problem: {prob['title']} ({prob['difficulty']})\n{prob['link']}"

@mcp.tool(description="Submit solution to daily problem and earn points.")
async def check_dsa_solution(user_id: str, solution: str) -> str:
    if user_id not in user_current_problem:
        return "You have no active daily problem."
    if not solution.strip():
        return "Please provide a valid solution."

    user_points[user_id] = user_points.get(user_id, 0) + 10
    return f"Solution saved! 🎉 You now have {user_points[user_id]} points."

@mcp.tool(description="Show user's points")
async def show_points(user_id: str) -> str:
    return f"You have {user_points.get(user_id, 0)} points."

@mcp.tool(description="Claim rewards")
async def claim_rewards(user_id: str) -> str:
    if user_points.get(user_id, 0) < 50:
        return f"Need 50 points to claim. You have {user_points.get(user_id, 0)}."
    user_points[user_id] -= 50
    return "🎁 Reward claimed!"

# ====== Auto-push scheduler ======
async def auto_push_daily_problems():
    while True:
        now = time.time()
        for user_id, last_time in list(user_last_request.items()):
            if now - last_time >= 86400:
                prob = await get_leetcode_daily_problem()
                if prob:
                    user_current_problem[user_id] = prob
                    user_last_request[user_id] = now
                    print(f"📤 Auto-sent to {user_id}: {prob['title']} - {prob['link']}")
        await asyncio.sleep(3600)

# ====== Twitch & YouTube helpers ======
async def get_twitch_live_streams(game: str) -> List[dict]:
    async with httpx.AsyncClient() as client:
        game_search = await client.get(
            "https://api.twitch.tv/helix/games",
            params={"name": game},
            headers={
                "Client-ID": TWITCH_CLIENT_ID,
                "Authorization": f"Bearer {TWITCH_OAUTH_TOKEN}"
            }
        )
        data = game_search.json().get("data", [])
        if not data:
            return []
        game_id = data[0]["id"]
        streams_resp = await client.get(
            "https://api.twitch.tv/helix/streams",
            params={"game_id": game_id, "first": 5},
            headers={
                "Client-ID": TWITCH_CLIENT_ID,
                "Authorization": f"Bearer {TWITCH_OAUTH_TOKEN}"
            }
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

# ====== Stream Finder Tool ======
@mcp.tool(description=RichToolDescription(
    description="Find live streams for a game on Twitch or YouTube.",
    use_when="User asks for streams by game and/or platform.",
    side_effects="Returns results with links."
).model_dump_json())
async def stream_finder(
    game_name: Annotated[str, Field(description="Name of the game, e.g. 'Rocket League'")],
    platform: Annotated[str | None, Field(description="Platform: 'twitch' or 'youtube'. Optional.")] = None,
) -> str:
    results = []
    if not platform or platform.lower() == "twitch":
        twitch_streams = await get_twitch_live_streams(game_name)
        results.extend([f"[Twitch] {s['user_name']}: {s['title']} ({s['url']})" for s in twitch_streams])
    if not platform or platform.lower() == "youtube":
        youtube_streams = await get_youtube_live_streams(game_name)
        results.extend([f"[YouTube] {s['channelTitle']}: {s['title']} ({s['url']})" for s in youtube_streams])
    return "\n".join(results) if results else "No live streams found right now."

# ====== Run Unified MCP Server ======
async def main():
    asyncio.create_task(auto_push_daily_problems())
    print("🚀 Unified MCP server running on http://0.0.0.0:8086")
    await mcp.run_async("streamable-http", host="0.0.0.0", port=8086)

if __name__ == "__main__":
    asyncio.run(main())
