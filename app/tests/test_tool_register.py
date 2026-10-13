import pytest
import asyncio
from fastmcp import FastMCP, Client

# Write an async test using the client with in-memory transport
@pytest.mark.asyncio
async def test_get_name_tool(mcp_server):
    async with Client(mcp_server) as client:
        result = await client.call_tool("find_list_partners", {})
        print(result)
    
    async with Client(mcp_server) as client:
        result = await client.call_tool("find_name", {})
        print(result)