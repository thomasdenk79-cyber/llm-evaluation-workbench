"""Tests for persistent Ollama context configuration."""

from __future__ import annotations

import unittest

import configure_ollama_max_context as configure


class OllamaMaxContextTests(unittest.TestCase):
    def test_reads_native_and_scientific_configured_contexts(self) -> None:
        self.assertEqual(
            configure.native_context(
                {
                    "model.context_length": 262144,
                    "vision.context_length": 32768,
                }
            ),
            262144,
        )
        self.assertEqual(configure.parse_num_ctx("num_ctx 1.048576e+06"), 1048576)

    def test_backup_names_are_stable_and_tag_specific(self) -> None:
        name = configure.backup_name("qwen3.6:35b-a3b-q4_K_M")
        self.assertEqual(name, configure.backup_name("qwen3.6:35b-a3b-q4_K_M"))
        self.assertTrue(name.endswith(".Modelfile"))
        self.assertNotEqual(name, configure.backup_name("qwen3.6:35b"))


if __name__ == "__main__":
    unittest.main()
