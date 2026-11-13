"""OPC UA server tùy chọn cho 20 camera. Không làm app lỗi nếu thiếu asyncua."""

from __future__ import annotations

import asyncio
import threading
from typing import Dict
from urllib.parse import urlparse


class OpcUaBackgroundServer:
    def __init__(self, endpoint: str, namespace: str, server_name: str, hub) -> None:
        port = urlparse(endpoint).port or 4840
        if port in {102, 4840, 4890, 5100}:
            raise ValueError(f"Từ chối mở OPC UA server trên cổng OT/WinCC được bảo vệ: {port}")
        self.endpoint = endpoint
        self.namespace = namespace
        self.server_name = server_name
        self.hub = hub
        self.running = False
        self.thread = None
        self.error = ""

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:
            self.error = str(exc)
            print("[OPC UA] Disabled/Error:", self.error)

    async def _main(self) -> None:
        try:
            from asyncua import Server, ua
        except Exception as exc:
            self.error = f"Chưa cài asyncua: {exc}"
            print("[OPC UA]", self.error)
            return

        async def set_typed(node, value, variant_type) -> None:
            await node.set_value(ua.Variant(value, variant_type))

        server = Server()
        await server.init()
        server.set_endpoint(self.endpoint)
        server.set_server_name(self.server_name)
        idx = await server.register_namespace(self.namespace)
        root = await server.nodes.objects.add_object(idx, "WaterAI")

        nodes: Dict[str, Dict] = {}
        for cid in sorted(self.hub.cameras.keys()):
            obj = await root.add_object(idx, cid)
            nodes[cid] = {
                "WaterLevel": await obj.add_variable(idx, "WaterLevel", ua.Variant(0.0, ua.VariantType.Double)),
                "StatusCode": await obj.add_variable(idx, "StatusCode", ua.Variant(0, ua.VariantType.Int32)),
                "StatusText": await obj.add_variable(idx, "StatusText", ua.Variant("NO_DATA", ua.VariantType.String)),
                "CameraOnline": await obj.add_variable(idx, "CameraOnline", ua.Variant(False, ua.VariantType.Boolean)),
                "FPS": await obj.add_variable(idx, "FPS", ua.Variant(0.0, ua.VariantType.Double)),
                "Confidence": await obj.add_variable(idx, "Confidence", ua.Variant(0.0, ua.VariantType.Double)),
            }

        async with server:
            self.running = True
            while True:
                data = self.hub.opc_data()
                for cid, info in data.items():
                    if cid not in nodes:
                        continue
                    status = info.get("status", "NO_DATA")
                    if not info.get("online", False):
                        code = 4
                    elif status == "LOW":
                        code = 1
                    elif status == "NORMAL":
                        code = 2
                    elif status == "HIGH":
                        code = 3
                    else:
                        code = 0

                    await set_typed(nodes[cid]["WaterLevel"], float(info.get("level", 0.0)), ua.VariantType.Double)
                    await set_typed(nodes[cid]["StatusCode"], int(code), ua.VariantType.Int32)
                    await set_typed(nodes[cid]["StatusText"], str(status), ua.VariantType.String)
                    await set_typed(nodes[cid]["CameraOnline"], bool(info.get("online", False)), ua.VariantType.Boolean)
                    await set_typed(nodes[cid]["FPS"], float(info.get("fps", 0.0)), ua.VariantType.Double)
                    await set_typed(nodes[cid]["Confidence"], float(info.get("confidence", 0.0)), ua.VariantType.Double)
                await asyncio.sleep(0.5)
