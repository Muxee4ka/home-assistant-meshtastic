# SPDX-FileCopyrightText: 2026 Sergei Mikheev @Muxee4ka
#
# SPDX-License-Identifier: MIT

"""Fork additions on top of upstream: event field aliases, send_reaction, request_nodeinfo."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.meshtastic import api
from custom_components.meshtastic.api import MeshtasticApiClient, MeshtasticApiClientError

from .conftest import GATEWAY_NUM, PEER_NUM

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def bare_client(hass: HomeAssistant | None = None) -> MeshtasticApiClient:
    client = object.__new__(MeshtasticApiClient)
    client._hass = hass
    client._config_entry_id = "entry"
    client._interface = MagicMock()
    client.get_own_node = lambda: {"num": GATEWAY_NUM}
    return client


async def _receive(hass: HomeAssistant, *, text: str, reply_id: int = 0, emoji: int = 0, hop_start: int = 7) -> dict:
    client = bare_client(hass)
    mesh_packet = SimpleNamespace(
        id=42,
        hop_start=hop_start,
        hop_limit=5,
        rx_snr=-6.25,
        rx_rssi=-118,
        via_mqtt=False,
        xeddsa_signed=False,
        decoded=SimpleNamespace(reply_id=reply_id, emoji=emoji),
    )
    packet = SimpleNamespace(
        from_id=PEER_NUM, to_id=GATEWAY_NUM, channel_index=0, app_payload=text, mesh_packet=mesh_packet
    )
    events = []
    hass.bus.async_listen(api.EVENT_MESHTASTIC_API_TEXT_MESSAGE, events.append)
    await client._on_text_message(SimpleNamespace(id=PEER_NUM), packet)
    await hass.async_block_till_done()
    return events[0].data["data"]


async def test_plain_message_has_fork_aliases(hass: HomeAssistant) -> None:
    data = await _receive(hass, text="hi")
    assert data["hops"] == data["hops_away"] == 2
    assert data["rssi"] == -118
    assert data["snr"] == -6.25
    assert data["message_kind"] == "message"
    assert data["is_reply"] is False
    assert data["is_reaction"] is False
    assert data["emoji"] is None
    assert data["emoji_char"] is None


async def test_unknown_hop_count_stays_none(hass: HomeAssistant) -> None:
    data = await _receive(hass, text="hi", hop_start=0)
    assert data["hops"] is None


async def test_reply(hass: HomeAssistant) -> None:
    data = await _receive(hass, text="re", reply_id=1234)
    assert data["reply_id"] == 1234
    assert data["is_reply"] is True
    assert data["message_kind"] == "reply"


@pytest.mark.parametrize(("emoji", "expected"), [(1, "👍"), (ord("❤"), "❤")])
async def test_reaction_char_from_flag_or_codepoint(hass: HomeAssistant, emoji: int, expected: str) -> None:
    data = await _receive(hass, text="👍" if emoji == 1 else "❤", reply_id=1234, emoji=emoji)
    assert data["is_reaction"] is True
    assert data["message_kind"] == "reaction"
    assert data["emoji"] == emoji
    assert data["emoji_char"] == expected


async def test_send_reaction_goes_through_send_text() -> None:
    client = bare_client()
    client.send_text = AsyncMock(return_value=True)

    assert await client.send_reaction("👍", 1234, PEER_NUM, want_ack=True, channel_index=None) is True
    client.send_text.assert_awaited_once_with(
        "👍", PEER_NUM, want_ack=True, channel_index=None, reply_id=1234, is_reaction=True
    )


async def test_request_nodeinfo_reads_node_db() -> None:
    client = bare_client()
    client._interface.connected_node_ready = AsyncMock(return_value=True)
    client._interface.nodes = MagicMock(return_value={PEER_NUM: {"num": PEER_NUM}})
    client._transform_node_info = lambda info: {"transformed": info["num"]}

    assert await client.request_nodeinfo(PEER_NUM) == {"transformed": PEER_NUM}
    with pytest.raises(MeshtasticApiClientError):
        await client.request_nodeinfo(PEER_NUM + 1)
