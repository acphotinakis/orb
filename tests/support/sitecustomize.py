"""Test-only network guard inherited by fresh Python worker interpreters."""

import os
import socket


def deny_network(*args, **kwargs):
    raise RuntimeError(
        "Unexpected network access in offline test (including worker subprocess)"
    )


def install():
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network
    socket.getaddrinfo = deny_network


if os.environ.get("ORB_TEST_OFFLINE") == "1":
    install()
