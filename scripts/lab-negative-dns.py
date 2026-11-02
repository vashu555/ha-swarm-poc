#!/usr/bin/env python3
"""Fast negative-only upstream for Docker's internal lab service discovery.

Docker answers existing overlay service names itself. Missing names reach this
server and receive NXDOMAIN instead of SERVFAIL, which musl retries synchronously
inside Redis/Sentinel's event loop. This server never resolves Internet names.
Bind only to the local Docker bridge, never the NetBird/public interface.
"""
import argparse
import socketserver
import struct
import threading


def response(data):
    if len(data) < 12 or len(data) > 1232:
        return None
    ident, flags, count, _, _, _ = struct.unpack('!6H', data[:12])
    if flags & 0x8000 or flags & 0x7800 or count != 1:
        return None
    end = 12
    while end < len(data):
        length = data[end]
        end += 1
        if length == 0:
            break
        if length > 63 or end + length > len(data):
            return None
        end += length
    else:
        return None
    if end + 4 > len(data):
        return None
    return struct.pack('!6H', ident, 0x8083 | (flags & 0x0100), 1, 0, 0, 0) + data[12:end+4]


class UDP(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        reply = response(data)
        if reply:
            sock.sendto(reply, self.client_address)


class TCP(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(.5)
        try:
            def read(n):
                data = b''
                while len(data) < n:
                    chunk = self.request.recv(n-len(data))
                    if not chunk:
                        raise ConnectionError()
                    data += chunk
                return data
            size = struct.unpack('!H', read(2))[0]
            if size > 1232:
                return
            reply = response(read(size))
            if reply:
                self.request.sendall(struct.pack('!H', len(reply))+reply)
        except (OSError, ConnectionError):
            return


class TCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def serve(host, port=53):
    with socketserver.UDPServer((host, port), UDP) as udp, TCPServer((host, port), TCP) as tcp:
        threading.Thread(target=tcp.serve_forever, daemon=True).start()
        print(f'Lab negative DNS listening on Docker bridge {host}:{port}', flush=True)
        udp.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--listen', default='172.17.0.1')
    parser.add_argument('--port', type=int, default=53)
    args = parser.parse_args()
    serve(args.listen, args.port)
