from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MCP_DIR.parent
CATALOG_PATH = REPO_ROOT / "Возможности AI"
SERVER_PATH = MCP_DIR / "server.py"
sys.path.insert(0, str(MCP_DIR))

from catalog import Catalog, URI_PREFIX  # noqa: E402
from server import (  # noqa: E402
    LATEST_LEGACY_VERSION,
    LATEST_MODERN_VERSION,
    MCPServer,
    PROMPTS,
    TOOLS,
)


class MCPServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = Catalog(CATALOG_PATH)
        cls.server = MCPServer(cls.catalog)

    def request(self, method: str, params: dict | None = None, request_id: int = 1):
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        response = self.server.handle(message)
        assert response is not None
        return response

    def test_discover(self) -> None:
        response = self.request(
            "server/discover",
            {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": LATEST_MODERN_VERSION,
                    "io.modelcontextprotocol/clientInfo": {
                        "name": "test",
                        "version": "1.0",
                    },
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        )
        result = response["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertIn(LATEST_MODERN_VERSION, result["supportedVersions"])
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(
            result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"],
            "ai-capabilities",
        )

    def test_legacy_initialize(self) -> None:
        response = self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "legacy", "version": "1.0"},
            },
        )
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("instructions", response["result"])

        unknown = self.request(
            "initialize",
            {
                "protocolVersion": "1900-01-01",
                "capabilities": {},
                "clientInfo": {"name": "legacy", "version": "1.0"},
            },
        )
        self.assertEqual(
            unknown["result"]["protocolVersion"], LATEST_LEGACY_VERSION
        )

    def test_unsupported_modern_version(self) -> None:
        for method in ("tools/list", "server/discover"):
            with self.subTest(method=method):
                response = self.request(
                    method,
                    {
                        "_meta": {
                            "io.modelcontextprotocol/protocolVersion": "1900-01-01"
                        }
                    },
                )
                self.assertEqual(response["error"]["code"], -32022)
                self.assertIn(
                    "2026-07-28", response["error"]["data"]["supported"]
                )

    def test_tools_list(self) -> None:
        response = self.request("tools/list")
        tools = response["result"]["tools"]
        self.assertEqual(len(tools), 7)
        self.assertEqual(
            {tool["name"] for tool in tools},
            {
                "list_capabilities",
                "get_capability",
                "list_risks",
                "get_risk",
                "search_catalog",
                "get_risk_scale",
                "get_overview",
            },
        )
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["description"])
            self.assertTrue(tool["annotations"]["readOnlyHint"])

    def test_tools_call(self) -> None:
        response = self.request(
            "tools/call",
            {"name": "list_capabilities", "arguments": {}},
        )
        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["count"], 8)
        self.assertIn("Каталоги возможностей AI", result["content"][0]["text"])

        response = self.request(
            "tools/call",
            {"name": "get_risk", "arguments": {"risk_id": "files-1"}},
        )
        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["id"], "files-1")
        self.assertIn("Меры снижения", result["content"][0]["text"])

        response = self.request(
            "tools/call",
            {"name": "search_catalog", "arguments": {"query": "утечка", "limit": 3}},
        )
        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["count"], 3)
        self.assertGreaterEqual(result["structuredContent"]["total"], 3)

    def test_tool_execution_errors_are_soft(self) -> None:
        response = self.request(
            "tools/call",
            {"name": "get_risk", "arguments": {"risk_id": "unknown-1"}},
        )
        self.assertNotIn("error", response)
        self.assertTrue(response["result"]["isError"])
        self.assertIn("не найден", response["result"]["content"][0]["text"])

        response = self.request(
            "tools/call",
            {"name": "list_risks", "arguments": {"level": "very-high"}},
        )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("уровень", response["result"]["content"][0]["text"])

        response = self.request(
            "tools/call",
            {"name": "unknown_tool", "arguments": {}},
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_tools_call_validates_arguments(self) -> None:
        response = self.request("tools/call", {"name": "list_risks", "arguments": []})
        self.assertTrue(response["result"]["isError"])

        response = self.request(
            "tools/call",
            {"name": "get_capability", "arguments": {}},
        )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("category", response["result"]["content"][0]["text"])

    def test_resources(self) -> None:
        response = self.request("resources/list")
        resources = response["result"]["resources"]
        self.assertEqual(len(resources), 19)

        response = self.request("resources/templates/list")
        templates = response["result"]["resourceTemplates"]
        self.assertEqual(len(templates), 4)

        response = self.request(
            "resources/read",
            {"uri": f"{URI_PREFIX}categories/files/risks/1"},
        )
        contents = response["result"]["contents"]
        self.assertEqual(contents[0]["mimeType"], "text/markdown")
        self.assertIn("files-1", contents[0]["text"])

        response = self.request(
            "resources/read", {"uri": f"{URI_PREFIX}no-such-resource"}
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_prompts(self) -> None:
        response = self.request("prompts/list")
        prompts = response["result"]["prompts"]
        self.assertEqual(len(prompts), 2)
        self.assertEqual(
            {prompt["name"] for prompt in prompts},
            {"analyze_scenario", "review_capability_risks"},
        )

        response = self.request(
            "prompts/get",
            {
                "name": "analyze_scenario",
                "arguments": {"scenario": "Автоматизировать разбор писем"},
            },
        )
        text = response["result"]["messages"][0]["content"]["text"]
        self.assertIn("Автоматизировать разбор писем", text)
        self.assertIn("get_capability", text)

        response = self.request(
            "prompts/get", {"name": "analyze_scenario", "arguments": {}}
        )
        self.assertEqual(response["error"]["code"], -32602)

        response = self.request(
            "prompts/get", {"name": "no-such-prompt", "arguments": {}}
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_unknown_method(self) -> None:
        response = self.request("unknown/method")
        self.assertEqual(response["error"]["code"], -32601)

    def test_notifications_do_not_get_responses(self) -> None:
        self.assertIsNone(
            self.server.handle(
                {"jsonrpc": "2.0", "method": "notifications/initialized"}
            )
        )
        self.assertIsNone(
            self.server.handle(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": 1},
                }
            )
        )

    def test_batch_request(self) -> None:
        response = self.server.handle(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            ]
        )
        self.assertIsInstance(response, list)
        assert isinstance(response, list)
        self.assertEqual([item["id"] for item in response], [1, 2])

    def test_prompt_and_tool_manifests_are_consistent(self) -> None:
        self.assertEqual(len(TOOLS), 7)
        self.assertEqual(len(PROMPTS), 2)


class StdioIntegrationTests(unittest.TestCase):
    def test_stdio_session(self) -> None:
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": LATEST_MODERN_VERSION,
                        "io.modelcontextprotocol/clientInfo": {
                            "name": "integration-test",
                            "version": "1.0",
                        },
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "legacy-test", "version": "1.0"},
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "list_capabilities",
                    "arguments": {},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "resources/read",
                "params": {"uri": f"{URI_PREFIX}overview"},
            },
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "prompts/get",
                "params": {
                    "name": "review_capability_risks",
                    "arguments": {"category": "files", "context": "тест"},
                },
            },
        ]
        payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in messages)
        process = subprocess.run(
            [
                sys.executable,
                str(SERVER_PATH),
                "--catalog",
                str(CATALOG_PATH),
            ],
            input=(payload + "\n").encode("utf-8"),
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8"))
        self.assertEqual(process.stderr, b"")

        lines = process.stdout.decode("utf-8").splitlines()
        self.assertEqual(len(lines), 6)
        responses = [json.loads(line) for line in lines]
        self.assertEqual([item["id"] for item in responses], [1, 2, 3, 4, 5, 6])
        for response in responses:
            self.assertEqual(response["jsonrpc"], "2.0")
            self.assertNotIn("error", response)
        self.assertEqual(responses[3]["result"]["structuredContent"]["count"], 8)
        self.assertIn("overview", responses[4]["result"]["contents"][0]["uri"])
        self.assertIn("files", responses[5]["result"]["messages"][0]["content"]["text"])

    def test_check_command(self) -> None:
        process = subprocess.run(
            [
                sys.executable,
                str(SERVER_PATH),
                "--catalog",
                str(CATALOG_PATH),
                "--check",
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(process.returncode, 0)
        payload = json.loads(process.stdout.decode("utf-8"))
        self.assertEqual(payload["capabilities"], 8)
        self.assertEqual(payload["risks"], 71)


if __name__ == "__main__":
    unittest.main()
