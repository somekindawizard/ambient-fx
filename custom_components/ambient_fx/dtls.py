"""Minimal pure-Python DTLS 1.2 PSK client for Hue Entertainment.

Implements exactly what the Hue bridge's entertainment port (2100)
requires: DTLS 1.2, cipher TLS_PSK_WITH_AES_128_GCM_SHA256, cookie
exchange, PSK key exchange, and AEAD application-data records.
No external DTLS library needed — only `cryptography` (bundled with
Home Assistant) plus the standard library.

References: RFC 6347 (DTLS 1.2), RFC 4279 (PSK), RFC 5487 (PSK-GCM).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import socket
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CIPHER_PSK_AES128_GCM_SHA256 = 0x00A8
DTLS_12 = 0xFEFD
DTLS_10 = 0xFEFF

CT_CCS = 20
CT_ALERT = 21
CT_HANDSHAKE = 22
CT_APPDATA = 23

HT_CLIENT_HELLO = 1
HT_HELLO_VERIFY = 3
HT_SERVER_HELLO = 2
HT_SERVER_KEY_EXCHANGE = 12
HT_SERVER_HELLO_DONE = 14
HT_CLIENT_KEY_EXCHANGE = 16
HT_FINISHED = 20


class DTLSError(Exception):
    pass


def _prf(secret: bytes, label: bytes, seed: bytes, length: int) -> bytes:
    """TLS 1.2 PRF (P_SHA256)."""
    seed = label + seed
    out = b""
    a = seed
    while len(out) < length:
        a = hmac.new(secret, a, hashlib.sha256).digest()
        out += hmac.new(secret, a + seed, hashlib.sha256).digest()
    return out[:length]


class DTLSPSKConnection:
    """Blocking DTLS-PSK client over UDP. Use from an executor."""

    def __init__(self, host: str, port: int, psk_identity: str, psk: bytes,
                 timeout: float = 5.0) -> None:
        self._addr = (host, port)
        self._identity = psk_identity.encode()
        self._psk = psk
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(timeout)
        self._sock.connect(self._addr)

        self._epoch = 0
        self._seq = 0            # send sequence within current epoch
        self._msg_seq = 0        # handshake message_seq
        self._handshake_hash = b""  # transcript (post-cookie)
        self._client_key = b""
        self._server_key = b""
        self._client_iv = b""
        self._server_iv = b""
        self._server_epoch = 0

    # MARK: Record layer

    def _send_record(self, ctype: int, payload: bytes) -> None:
        if self._epoch > 0:
            payload = self._encrypt(ctype, payload)
        header = (struct.pack(">BHH", ctype, DTLS_12, self._epoch)
                  + self._seq.to_bytes(6, "big")
                  + struct.pack(">H", len(payload)))
        self._sock.send(header + payload)
        self._seq += 1

    def _encrypt(self, ctype: int, plaintext: bytes) -> bytes:
        explicit = struct.pack(">HHI", self._epoch,
                               (self._seq >> 32) & 0xFFFF,
                               self._seq & 0xFFFFFFFF)
        nonce = self._client_iv + explicit
        aad = explicit + struct.pack(">BHH", ctype, DTLS_12, len(plaintext))
        ct = AESGCM(self._client_key).encrypt(nonce, plaintext, aad)
        return explicit + ct

    def _decrypt(self, ctype: int, epoch: int, seqbytes: bytes,
                 payload: bytes) -> bytes:
        explicit, ct = payload[:8], payload[8:]
        nonce = self._server_iv + explicit
        aad = explicit + struct.pack(">BHH", ctype, DTLS_12, len(ct) - 16)
        return AESGCM(self._server_key).decrypt(nonce, ct, aad)

    def _recv_records(self):
        """Receive one datagram, yield (ctype, plaintext) records."""
        datagram = self._sock.recv(65535)
        off = 0
        while off + 13 <= len(datagram):
            ctype, ver, epoch = struct.unpack_from(">BHH", datagram, off)
            seqbytes = datagram[off + 5:off + 11]
            (length,) = struct.unpack_from(">H", datagram, off + 11)
            payload = datagram[off + 13:off + 13 + length]
            off += 13 + length
            if epoch > 0 and self._server_epoch > 0:
                payload = self._decrypt(ctype, epoch, seqbytes, payload)
            yield ctype, payload

    # MARK: Handshake messages

    def _hs_message(self, msg_type: int, body: bytes) -> bytes:
        msg = struct.pack(">B", msg_type)
        msg += len(body).to_bytes(3, "big")
        msg += struct.pack(">H", self._msg_seq)
        msg += (0).to_bytes(3, "big")           # fragment_offset
        msg += len(body).to_bytes(3, "big")     # fragment_length
        msg += body
        self._msg_seq += 1
        return msg

    def _client_hello(self, cookie: bytes) -> bytes:
        body = struct.pack(">H", DTLS_12)
        body += self._client_random
        body += b"\x00"                          # session_id
        body += bytes([len(cookie)]) + cookie
        body += struct.pack(">HH", 2, CIPHER_PSK_AES128_GCM_SHA256)
        body += b"\x01\x00"                      # compression: null
        return body

    def handshake(self) -> None:
        self._client_random = os.urandom(32)

        # Flight 1: ClientHello without cookie -> HelloVerifyRequest.
        hello1 = self._hs_message(HT_CLIENT_HELLO, self._client_hello(b""))
        self._send_record(CT_HANDSHAKE, hello1)

        cookie = None
        for ctype, payload in self._recv_records():
            if ctype == CT_HANDSHAKE and payload[0] == HT_HELLO_VERIFY:
                body = payload[12:]
                cookie_len = body[2]
                cookie = body[3:3 + cookie_len]
        if cookie is None:
            raise DTLSError("No HelloVerifyRequest from bridge")

        # Flight 2: ClientHello with cookie (starts the transcript).
        hello2 = self._hs_message(HT_CLIENT_HELLO, self._client_hello(cookie))
        self._handshake_hash += hello2
        self._send_record(CT_HANDSHAKE, hello2)

        # Server flight: ServerHello ... ServerHelloDone (may span datagrams).
        server_random = None
        done = False
        for _ in range(10):
            for ctype, payload in self._recv_records():
                if ctype == CT_ALERT:
                    raise DTLSError(f"Bridge alert during handshake: {payload.hex()}")
                if ctype != CT_HANDSHAKE:
                    continue
                # A record may contain several handshake messages.
                off = 0
                while off < len(payload):
                    msg_type = payload[off]
                    body_len = int.from_bytes(payload[off + 1:off + 4], "big")
                    frag = payload[off:off + 12 + body_len]
                    body = frag[12:]
                    off += 12 + body_len
                    self._handshake_hash += frag
                    if msg_type == HT_SERVER_HELLO:
                        server_random = body[2:34]
                        (suite,) = struct.unpack_from(
                            ">H", body, 35 + body[34] + 0)
                        if suite != CIPHER_PSK_AES128_GCM_SHA256:
                            raise DTLSError(f"Bridge chose cipher {suite:#x}")
                    elif msg_type == HT_SERVER_HELLO_DONE:
                        done = True
            if done:
                break
        if server_random is None or not done:
            raise DTLSError("Incomplete server handshake flight")

        # Flight 3: ClientKeyExchange + CCS + Finished.
        cke_body = struct.pack(">H", len(self._identity)) + self._identity
        cke = self._hs_message(HT_CLIENT_KEY_EXCHANGE, cke_body)
        self._handshake_hash += cke
        self._send_record(CT_HANDSHAKE, cke)

        premaster = (struct.pack(">H", len(self._psk)) + b"\x00" * len(self._psk)
                     + struct.pack(">H", len(self._psk)) + self._psk)
        master = _prf(premaster, b"master secret",
                      self._client_random + server_random, 48)
        key_block = _prf(master, b"key expansion",
                         server_random + self._client_random, 40)
        self._client_key = key_block[0:16]
        self._server_key = key_block[16:32]
        self._client_iv = key_block[32:36]
        self._server_iv = key_block[36:40]

        self._send_record(CT_CCS, b"\x01")
        self._epoch = 1
        self._seq = 0

        verify = _prf(master, b"client finished",
                      hashlib.sha256(self._handshake_hash).digest(), 12)
        finished = self._hs_message(HT_FINISHED, verify)
        self._send_record(CT_HANDSHAKE, finished)

        # Server CCS + (encrypted) Finished.
        got_ccs = False
        for _ in range(10):
            try:
                for ctype, payload in self._recv_records():
                    if ctype == CT_CCS:
                        got_ccs = True
                        self._server_epoch = 1
                    elif ctype == CT_ALERT:
                        raise DTLSError(f"Bridge alert after Finished: {payload.hex()}")
                    elif ctype == CT_HANDSHAKE and got_ccs:
                        return  # server Finished decrypted OK — done
            except socket.timeout as err:
                raise DTLSError("Timeout waiting for bridge Finished") from err
        raise DTLSError("Handshake did not complete")

    # MARK: Application data

    def send(self, data: bytes) -> None:
        self._send_record(CT_APPDATA, data)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
