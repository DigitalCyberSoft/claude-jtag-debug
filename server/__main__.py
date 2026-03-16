"""Entry point: python3 -m server."""

from server.mcp_server import create_server

server = create_server()
server.run(transport="stdio")
