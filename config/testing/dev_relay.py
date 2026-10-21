"""Development stack only (dev/docker-compose.yml): pass connections on to the demo server.

The demo server's container is on a Docker network with no internet access, and Docker can't
publish a port from such a network. This relay sits on both networks and forwards one port to
the demo server, byte for byte. It can only ever connect to the demo server.

    python config/testing/dev_relay.py        (listens on 0.0.0.0:8765, forwards to demo:8765)
"""
import asyncio

LISTEN_PORT = 8765
TARGET = ("demo", 8765)  # the demo service in dev/docker-compose.yml; fixed on purpose


async def _pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def _handle(client_reader, client_writer):
    try:
        demo_reader, demo_writer = await asyncio.open_connection(*TARGET)
    except OSError:
        client_writer.close()  # the demo server is still starting
        return
    await asyncio.gather(_pipe(client_reader, demo_writer), _pipe(demo_reader, client_writer))


async def main():
    server = await asyncio.start_server(_handle, "0.0.0.0", LISTEN_PORT)
    print(f"Development relay: port {LISTEN_PORT} -> {TARGET[0]}:{TARGET[1]}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
