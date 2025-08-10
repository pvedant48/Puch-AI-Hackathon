import asyncio
from typing import Annotated, List, Dict
import os
import time
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth.providers.bearer import BearerAuthProvider, RSAKeyPair
from mcp.server.auth.provider import AccessToken
from pydantic import BaseModel, Field
import httpx

# ====== In-memory tracking ======
user_last_request: Dict[str, float] = {}  # timestamp of last problem given
user_points: Dict[str, int] = {}
user_current_problem: Dict[str, dict] = {}

# ====== Load env ======
load_dotenv()
TOKEN = os.environ.get("AUTH_TOKEN")
MY_NUMBER = os.environ.get("MY_NUMBER")

assert TOKEN and MY_NUMBER, "AUTH_TOKEN and MY_NUMBER required in .env"

# ====== Auth Provider ======
class SimpleBearerAuthProvider(BearerAuthProvider):
    def __init__(self, token: str):
        k = RSAKeyPair.generate()
        super().__init__(public_key=k.public_key)
        self.token = token

    async def load_access_token(self, token: str) -> AccessToken | None:
        if token == self.token:
            return AccessToken(token=token, client_id="puch-client", scopes=["*"])
        return None

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

# ====== MCP server ======
mcp = FastMCP(
    "DSA Daily Problem MCP Server",
    auth=SimpleBearerAuthProvider(MY_NUMBER),
)

# ====== Basic tool descriptions ======
class RichToolDescription(BaseModel):
    description: str
    use_when: str
    side_effects: str | None = None

# ====== Tool: daily problem ======
@mcp.tool(description=RichToolDescription(
    description="Get today's DSA problem from LeetCode",
    use_when="When user asks 'dsa'",
    side_effects="Updates last request time"
).model_dump_json())
async def dsa_daily_problem(user_id: Annotated[str, Field(description="User ID")]) -> str:
    now = time.time()
    last_time = user_last_request.get(user_id, 0)

    # enforce 24h cooldown unless manually requested earlier
    if now - last_time < 86400 and user_id in user_current_problem:
        p = user_current_problem[user_id]
        return f"Your DSA problem: {p['title']} ({p['difficulty']})\n{p['link']}"

    prob = await get_leetcode_daily_problem()
    if not prob:
        return "Couldn't fetch today's problem."

    user_current_problem[user_id] = prob
    user_last_request[user_id] = now

    return f"Here’s your DSA problem: {prob['title']} ({prob['difficulty']})\n{prob['link']}"

# ====== Tool: submit solution ======
@mcp.tool(description="Submit solution to daily problem and earn points.")
async def check_dsa_solution(user_id: str, solution: str) -> str:
    if user_id not in user_current_problem:
        return "You have no active daily problem."
    if not solution.strip():
        return "Please provide a valid solution."

    user_points[user_id] = user_points.get(user_id, 0) + 10
    return f"Solution saved! 🎉 You now have {user_points[user_id]} points."

# ====== Tool: check points ======
@mcp.tool(description="Show user's points")
async def show_points(user_id: str) -> str:
    return f"You have {user_points.get(user_id, 0)} points."

# ====== Tool: claim rewards ======
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
            # If >=24 hours passed → push new one
            if now - last_time >= 86400:
                prob = await get_leetcode_daily_problem()
                if prob:
                    user_current_problem[user_id] = prob
                    user_last_request[user_id] = now
                    # Here you would integrate with WhatsApp API to send message directly
                    print(f"📤 Auto-sent to {user_id}: {prob['title']} - {prob['link']}")
        await asyncio.sleep(3600)  # check every hour

# ====== Message handler ======
async def handle_user_message(user_id: str, text: str) -> str:
    t = text.strip().lower()
    if t == "dsa":
        return await dsa_daily_problem(user_id)
    elif t in ["my points", "dsa points"]:
        return await show_points(user_id)
    elif t == "claim":
        return await claim_rewards(user_id)
    elif user_id in user_current_problem:
        return await check_dsa_solution(user_id, text)
    else:
        return "Type 'dsa' for today's problem."

# ====== Run MCP server with scheduler ======
async def main():
    asyncio.create_task(auto_push_daily_problems())
    print("🚀 MCP server running with auto-push enabled on http://0.0.0.0:8086")
    await mcp.run_async("streamable-http", host="0.0.0.0", port=8086)

if __name__ == "__main__":
    asyncio.run(main())