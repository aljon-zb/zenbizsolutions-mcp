import pytest
import asyncio
from fastmcp import FastMCP, Client


async def test_get_list_tool():
    async with Client('http://127.0.0.1:8000/mcp') as client2:
        for tool in await client2.list_tools():
            print(tool)

asyncio.run(test_get_list_tool())