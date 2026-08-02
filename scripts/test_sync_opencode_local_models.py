"""Focused tests for local OpenCode model synchronization."""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

import sync_opencode_local_models as sync_models


class LocalModelSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_model(
        self,
        relative: str,
        size: int = 16,
        context: int = 32768,
    ) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        architecture = b"test"
        architecture_key = b"general.architecture"
        context_key = b"test.context_length"
        metadata = (
            struct.pack("<Q", len(architecture_key))
            + architecture_key
            + struct.pack("<I", 8)
            + struct.pack("<Q", len(architecture))
            + architecture
            + struct.pack("<Q", len(context_key))
            + context_key
            + struct.pack("<I", 4)
            + struct.pack("<I", context)
        )
        header = b"GGUF" + struct.pack("<IQQ", 3, 0, 2)
        path.write_bytes(header + metadata + (b"x" * size))
        return path

    def test_discovers_complete_models_and_skips_incomplete_shards(self) -> None:
        self.write_model("single-model-from-ollama.gguf")
        self.write_model("complete/model-00001-of-00002.gguf")
        self.write_model("complete/model-00002-of-00002.gguf")
        self.write_model("incomplete/missing-00001-of-00003.gguf")

        models, warnings = sync_models.discover_gguf_models(self.root)

        self.assertEqual([model.alias for model in models], ["model", "single-model"])
        self.assertEqual(models[0].parts, 2)
        self.assertEqual(models[0].context, 32768)
        self.assertTrue(any("1/3" in warning for warning in warnings))

    def test_update_preserves_other_providers_and_secrets(self) -> None:
        config_path = self.root / "opencode.json"
        config_path.write_text(
            json.dumps(
                {
                    "provider": {
                        "siemens": {
                            "options": {
                                "baseURL": "https://example.invalid/v1",
                                "apiKey": "keep-secret",
                            },
                            "models": {"cloud": {}},
                        },
                        "ollama": {"models": {"old": {}}},
                    }
                }
            ),
            encoding="utf-8",
        )
        model_path = self.write_model("model.gguf")
        llama_models = [
            sync_models.GgufModel(
                "model",
                model_path,
                parts=1,
                size_bytes=16,
                context=32768,
            )
        ]

        changed = sync_models.update_opencode_config(
            config_path,
            {"local:latest": sync_models.model_definition("Ollama | local:latest")},
            llama_models,
            "http://127.0.0.1:11434",
            "http://127.0.0.1:8080/v1",
        )

        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertTrue(changed)
        self.assertEqual(
            config["provider"]["siemens"]["options"]["apiKey"],
            "keep-secret",
        )
        self.assertEqual(
            list(config["provider"]["ollama"]["models"]),
            ["local:latest"],
        )
        self.assertIn("model", config["provider"]["llama-cpp"]["models"])
        self.assertTrue(config_path.with_name("opencode.json.bak").is_file())

    def test_rendered_preset_uses_first_shard_path(self) -> None:
        model = sync_models.GgufModel(
            alias="large-model",
            path=Path(r"C:\models\large-00001-of-00002.gguf"),
            parts=2,
            size_bytes=32,
            context=262144,
        )

        preset = sync_models.render_llama_presets([model])

        self.assertIn("[large-model]", preset)
        self.assertIn(r"model = C:\models\large-00001-of-00002.gguf", preset)
        self.assertIn("ctx-size = 262144", preset)
        self.assertIn("cache-type-k = q4_0", preset)

    def test_normalizes_qwen_and_ollama_export_names(self) -> None:
        self.assertEqual(
            sync_models.normalize_alias("Qwen_Qwen3.6-35B-A3B-Q4_K_M"),
            "qwen3.6-35b-a3b-q4-k-m",
        )
        self.assertEqual(
            sync_models.normalize_alias("qwen3-coder-30b-from-ollama"),
            "qwen3-coder-30b",
        )

    def test_ollama_discovery_excludes_cloud_and_zero_size_models(self) -> None:
        def requester(
            url: str,
            payload: dict[str, object] | None,
        ) -> dict[str, object]:
            if url.endswith("/api/tags"):
                return {
                    "models": [
                        {"name": "local:q4", "size": 42},
                        {"name": "remote:cloud", "size": 42},
                        {"name": "empty:latest", "size": 0},
                    ]
                }
            self.assertEqual(payload, {"model": "local:q4"})
            return {
                "model_info": {"test.context_length": 32768},
                "capabilities": ["completion"],
                "parameters": "num_ctx 65536",
            }

        models, warnings = sync_models.discover_ollama_models(
            "http://127.0.0.1:11434",
            requester=requester,
        )

        self.assertEqual(list(models), ["local:q4"])
        self.assertEqual(models["local:q4"]["limit"]["context"], 65536)
        self.assertEqual(len(warnings), 2)

    def test_model_definition_caps_native_context_to_operating_budget(self) -> None:
        definition = sync_models.model_definition("Ollama | large", context=262144)

        self.assertEqual(
            definition["limit"]["context"],
            sync_models.MAX_OPERATING_CONTEXT,
        )
        self.assertEqual(definition["limit"]["output"], sync_models.DEFAULT_OUTPUT)


if __name__ == "__main__":
    unittest.main()
