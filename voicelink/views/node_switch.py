"""MIT License

Copyright (c) 2023 - present Titli Development

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

import discord
import voicelink
from typing import Optional

class NodeSwitchDropdown(discord.ui.Select):
    def __init__(self, player: voicelink.Player):
        self.player: voicelink.Player = player

        options = [
            discord.SelectOption(
                label="Auto",
                value="auto",
                description="Automatically switch to the best node based on latency.",
                emoji="🚀"
            )
        ]

        for name, node in voicelink.NodePool._nodes.items():
            status = "🟢" if node._available else "🔴"
            options.append(
                discord.SelectOption(
                    label=name,
                    value=name,
                    description=f"{status} Latency: {node.latency if node._available else 0:.2f}ms | Players: {len(node._players)}",
                    emoji="📡"
                )
            )

        super().__init__(
            placeholder="Select a node to switch to...",
            options=options,
            custom_id="node_switch_dropdown"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self.player.is_privileged(interaction.user):
            return await interaction.response.send_message("Only the DJ or admins can switch nodes!", ephemeral=True)

        selected = self.values[0]
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            if selected == "auto":
                best_node = voicelink.NodePool.get_best_node(algorithm=voicelink.NodeAlgorithm.BY_PING)
                identifier = best_node._identifier
            else:
                identifier = selected
            
            await self.player.change_node(identifier=identifier)
            await interaction.followup.send(f"Successfully switched to **{identifier}** node!", ephemeral=True)
            
            # Disable the dropdown after use
            self.disabled = True
            await interaction.edit_original_response(view=self.view)
            
        except Exception as e:
            await interaction.followup.send(f"Failed to switch node: {e}", ephemeral=True)

class NodeSwitchView(discord.ui.View):
    def __init__(self, player: voicelink.Player, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.add_item(NodeSwitchDropdown(player))
