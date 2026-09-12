import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import yaml

from scripts import inferencia


class FakeInputs(dict):
    def __init__(self):
        super().__init__({"input_ids": [[1, 2]]})
        self.input_ids = inferencia.torch.tensor([[1, 2]])

    def to(self, device):
        self.device = device
        return self


class FakeTokenizer:
    eos_token_id = 99
    chat_template = "fake-template"

    def __init__(self):
        self.messages = None
        self.formatted_prompt = None
        self.decode_args = None

    def apply_chat_template(
        self, messages, tokenize, add_generation_prompt, enable_thinking, **kwargs
    ):
        self.messages = messages
        assert add_generation_prompt is True
        assert enable_thinking is False
        return "PROMPT"

    def __call__(self, formatted_prompt, return_tensors):
        self.formatted_prompt = formatted_prompt
        assert return_tensors == "pt"
        return FakeInputs()

    def decode(self, tokens, skip_special_tokens):
        self.decode_args = (tokens.tolist(), skip_special_tokens)
        return "Resposta final"


class FakeModel:
    device = "cpu"

    def __init__(self, outputs=None):
        self.generate_kwargs = None
        self.generate_calls = []
        self.outputs = [
            inferencia.torch.tensor(output)
            for output in (outputs or [[[1, 2, 7, 8]]])
        ]

    def generate(self, **kwargs):
        self.generate_kwargs = kwargs
        self.generate_calls.append(kwargs)
        if len(self.outputs) > 1:
            return self.outputs.pop(0)
        return self.outputs[0]


class FakeMistralTokenizer:
    def __init__(self):
        self.request = None
        self.decode_tokens = None

    def encode_chat_completion(self, request):
        self.request = request
        return SimpleNamespace(tokens=[1, 2, 3])

    def decode(self, tokens):
        self.decode_tokens = tokens
        return "Resposta Mistral"


class TestInferencia(unittest.TestCase):
    def test_load_config_accepts_explicit_config_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_dir = root / "config" / "inferencia"
            config_dir.mkdir(parents=True)
            config_file = config_dir / "local.yaml"
            config_file.write_text(
                yaml.dump({"configuracio_global": {"seed": 42}}),
                encoding="utf-8",
            )

            config = inferencia.load_config(root=root, config_path=config_file)

        self.assertEqual(config["configuracio_global"]["seed"], 42)

    def test_parse_args_uses_default_config_path(self):
        with patch("sys.argv", ["inferencia.py"]):
            args = inferencia.parse_args()

        self.assertEqual(args.config, inferencia.DEFAULT_INFERENCIA_CONFIG)
        self.assertEqual(args.log_level, "INFO")

    def test_parse_args_accepts_config_path(self):
        with patch(
            "sys.argv",
            [
                "inferencia.py",
                "--config",
                "config/inferencia/inferencia_local_config.yaml",
            ],
        ):
            args = inferencia.parse_args()

        self.assertEqual(args.config, "config/inferencia/inferencia_local_config.yaml")

    def test_parse_args_accepts_log_level(self):
        with patch("sys.argv", ["inferencia.py", "--log-level", "WARNING"]):
            args = inferencia.parse_args()

        self.assertEqual(args.log_level, "WARNING")

    def test_parse_args_accepts_prompt_and_model_filters(self):
        with patch(
            "sys.argv",
            [
                "inferencia.py",
                "--prompt-prefix",
                "traduccio_",
                "--model-id",
                "qwen3.8-27b",
                "--model-id",
                "gemma-3-27b-it",
            ],
        ):
            args = inferencia.parse_args()

        self.assertEqual(args.prompt_prefix, "traduccio_")
        self.assertEqual(args.model_id, ["qwen3.8-27b", "gemma-3-27b-it"])

    def test_parse_args_accepts_force(self):
        with patch("sys.argv", ["inferencia.py", "--force"]):
            args = inferencia.parse_args()

        self.assertTrue(args.force)

    def test_load_prompts_reads_txt_files_with_stem_as_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prompt_dir = root / "data" / "prompts" / "v1"
            prompt_dir.mkdir(parents=True)

            (prompt_dir / "traduccio_1.txt").write_text(
                "Un prompt amb : dos punts al final:\n\nCos.", encoding="utf-8"
            )
            (prompt_dir / "traduccio_2.txt").write_text(
                "Un altre prompt.", encoding="utf-8"
            )

            prompts = inferencia.load_prompts(
                inferencia.discover_prompt_files(root),
                root=root,
            )

        self.assertEqual([p["id"] for p in prompts], ["traduccio_1", "traduccio_2"])
        self.assertEqual(
            prompts[0]["text"], "Un prompt amb : dos punts al final:\n\nCos."
        )
        self.assertEqual(prompts[0]["_path_origen"], "data/prompts/v1/traduccio_1.txt")

    def test_get_dtype_accepts_supported_types(self):
        self.assertIs(inferencia.get_dtype("float16"), inferencia.torch.float16)
        self.assertIs(inferencia.get_dtype("bfloat16"), inferencia.torch.bfloat16)
        self.assertIs(inferencia.get_dtype("float32"), inferencia.torch.float32)

        with self.assertRaises(ValueError):
            inferencia.get_dtype("int8")

    def test_tokenize_chat_messages_returns_none_without_chat_template(self):
        tokenizer = Mock()
        tokenizer.apply_chat_template.side_effect = ValueError("missing template")

        inputs = inferencia.tokenize_chat_messages(
            tokenizer,
            FakeModel(),
            [{"role": "user", "content": "Hola"}],
        )

        self.assertIsNone(inputs)

    def test_load_model_passes_mockable_parameters(self):
        calls = []

        def fake_loader(*args, **kwargs):
            calls.append((args, kwargs))
            return "MODEL"

        entry_model = {
            "id": "model-id",
            "model_name": "org/model",
            "revision": "main",
            "torch_dtype": "bfloat16",
            "device_map": "auto",
        }

        model = inferencia.load_model(
            entry_model,
            hf_token="hf_test",
            model_loader=fake_loader,
        )

        self.assertEqual(model, "MODEL")
        self.assertEqual(calls[0][0], ("org/model",))
        self.assertEqual(calls[0][1]["revision"], "main")
        self.assertIs(calls[0][1]["dtype"], inferencia.torch.bfloat16)
        self.assertEqual(calls[0][1]["device_map"], "auto")
        self.assertEqual(calls[0][1]["token"], "hf_test")

    def test_load_model_configures_4bit_quantization(self):
        calls = []

        def fake_loader(*args, **kwargs):
            calls.append((args, kwargs))
            return "MODEL"

        entry_model = {
            "id": "model-id",
            "model_name": "org/model",
            "revision": "main",
            "torch_dtype": "bfloat16",
            "device_map": "auto",
            "quantization": "4bit",
        }

        inferencia.load_model(entry_model, model_loader=fake_loader)

        quant_config = calls[0][1]["quantization_config"]
        self.assertTrue(quant_config.load_in_4bit)
        self.assertIs(quant_config.bnb_4bit_compute_dtype, inferencia.torch.bfloat16)
        self.assertEqual(quant_config.bnb_4bit_quant_type, "nf4")

    def test_load_tokenizer_uses_mistral_common_backend(self):
        entry_model = {
            "id": "model-id",
            "model_name": "org/mistral-model",
            "revision": "main",
            "backend": "mistral_common",
        }

        with patch.object(
            inferencia, "load_mistral_common_tokenizer", return_value="TOKENIZER"
        ) as loader:
            tokenizer = inferencia.load_tokenizer(entry_model)

        self.assertEqual(tokenizer, "TOKENIZER")
        loader.assert_called_once_with("org/mistral-model")

    def test_load_model_uses_mistral3_model_class(self):
        entry_model = {
            "id": "model-id",
            "model_name": "org/mistral-model",
            "revision": "main",
            "torch_dtype": "bfloat16",
            "device_map": "auto",
            "quantization": "none",
            "backend": "mistral_common",
        }

        with patch.object(
            inferencia, "load_mistral3_model", return_value="MODEL"
        ) as loader:
            model = inferencia.load_model(entry_model, hf_token="hf_test")

        self.assertEqual(model, "MODEL")
        loader.assert_called_once()
        args, kwargs = loader.call_args
        self.assertEqual(args, ("org/mistral-model",))
        self.assertEqual(kwargs["revision"], "main")
        self.assertIs(kwargs["dtype"], inferencia.torch.bfloat16)
        self.assertEqual(kwargs["device_map"], "auto")
        self.assertEqual(kwargs["token"], "hf_test")

    def test_apply_device_map_override_updates_all_models(self):
        config = {
            "models": [
                {"id": "model-1", "device_map": "auto"},
                {"id": "model-2", "device_map": "cpu"},
            ]
        }

        result = inferencia.apply_device_map_override(config, "cpu")

        self.assertIs(result, config)
        self.assertEqual(
            [model["device_map"] for model in result["models"]],
            ["cpu", "cpu"],
        )

    def test_apply_device_map_override_keeps_config_when_missing(self):
        config = {"models": [{"id": "model-1", "device_map": "auto"}]}

        result = inferencia.apply_device_map_override(config, None)

        self.assertIs(result, config)
        self.assertEqual(result["models"][0]["device_map"], "auto")

    def test_build_messages_adds_system_prompt_when_present(self):
        messages = inferencia.build_messages(
            "Hola",
            {"system_prompt": "Respon en catala"},
        )

        self.assertEqual(
            messages,
            [
                {"role": "system", "content": "Respon en catala"},
                {"role": "user", "content": "Hola"},
            ],
        )

    def test_generate_text_uses_tokenizer_and_model_without_hf(self):
        tokenizer = FakeTokenizer()
        model = FakeModel()
        generation_params = {
            "system_prompt": "Sistema",
            "max_new_tokens": 12,
            "temperature": 0,
            "top_p": 0.9,
        }

        text = inferencia.generate_text(tokenizer, model, "Usuari", generation_params)

        self.assertEqual(text, "Resposta final")
        self.assertEqual(tokenizer.formatted_prompt, "PROMPT")
        self.assertEqual(
            tokenizer.messages,
            [
                {"role": "system", "content": "Sistema"},
                {"role": "user", "content": "Usuari"},
            ],
        )
        self.assertFalse(model.generate_kwargs["do_sample"])
        self.assertNotIn("temperature", model.generate_kwargs)
        self.assertNotIn("top_p", model.generate_kwargs)
        self.assertTrue(model.generate_kwargs["remove_invalid_values"])
        self.assertEqual(model.generate_kwargs["max_new_tokens"], 12)
        self.assertEqual(tokenizer.decode_args, ([7, 8], True))

    def test_generate_text_retries_when_min_token_len_is_not_reached(self):
        tokenizer = FakeTokenizer()
        model = FakeModel(outputs=[[[1, 2, 7]], [[1, 2, 7, 8, 9, 10]]])
        generation_params = {
            "system_prompt": "Sistema",
            "max_new_tokens": 6,
            "min_token_len": 4,
            "temperature": 0,
            "top_p": 0.9,
        }

        with patch.object(inferencia.LOGGER, "warning") as log_warning:
            text = inferencia.generate_text(
                tokenizer, model, "Usuari", generation_params
            )

        self.assertEqual(text, "Resposta final")
        self.assertEqual(len(model.generate_calls), 2)
        log_warning.assert_called_once()
        self.assertEqual(log_warning.call_args.args[-1], "Resposta final")
        self.assertNotIn("min_new_tokens", model.generate_calls[0])
        self.assertNotIn("min_new_tokens", model.generate_calls[1])
        self.assertIn("mínim configurat és 4", tokenizer.messages[0]["content"])
        self.assertIn(
            "resposta anterior tenia 1 tokens",
            tokenizer.messages[0]["content"],
        )

    def test_generate_text_passes_sampling_parameters_when_temperature_is_positive(
        self,
    ):
        tokenizer = FakeTokenizer()
        model = FakeModel()
        generation_params = {
            "max_new_tokens": 12,
            "temperature": 0.7,
            "top_p": 0.9,
        }

        inferencia.generate_text(tokenizer, model, "Usuari", generation_params)

        self.assertTrue(model.generate_kwargs["do_sample"])
        self.assertEqual(model.generate_kwargs["temperature"], 0.7)
        self.assertEqual(model.generate_kwargs["top_p"], 0.9)

    def test_generate_text_uses_mistral_common_tokenizer(self):
        tokenizer = FakeMistralTokenizer()
        model = FakeModel()
        generation_params = {
            "system_prompt": "Sistema",
            "max_new_tokens": 12,
            "min_token_len": 1,
            "temperature": 0,
            "top_p": 0.9,
        }

        request_module = ModuleType("mistral_common.protocol.instruct.request")

        class ChatCompletionRequest:
            def __init__(self, messages):
                self.messages = messages

        request_module.ChatCompletionRequest = ChatCompletionRequest

        with patch.dict(
            sys.modules,
            {
                "mistral_common": ModuleType("mistral_common"),
                "mistral_common.protocol": ModuleType("mistral_common.protocol"),
                "mistral_common.protocol.instruct": ModuleType(
                    "mistral_common.protocol.instruct"
                ),
                "mistral_common.protocol.instruct.request": request_module,
            },
        ):
            text = inferencia.generate_text(
                tokenizer, model, "Usuari", generation_params
            )

        self.assertEqual(text, "Resposta Mistral")
        self.assertEqual(
            [message["content"] for message in tokenizer.request.messages],
            ["Sistema", "Usuari"],
        )
        self.assertFalse(model.generate_kwargs["do_sample"])
        self.assertEqual(model.generate_kwargs["max_new_tokens"], 12)
        self.assertNotIn("min_new_tokens", model.generate_kwargs)
        self.assertEqual(tokenizer.decode_tokens, [8])

    def test_build_result_splits_reasoning_and_metadata(self):
        resultat = inferencia.build_result(
            prompt={
                "id": "prompt-1",
                "text": "Text prompt",
                "_path_origen": "data/prompts/v1/prompt-1.txt",
            },
            model_entry={
                "id": "model-1",
                "model_name": "org/model",
                "revision": "abc",
            },
            generation_params={
                "temperature": 0.7,
                "top_p": 0.9,
                "max_new_tokens": 128,
                "min_token_len": 32,
            },
            global_config={
                "seed": 42,
                "backend_preferit": "transformers",
            },
            generated_text="<think>raons</think>Resposta",
            current_timestamp="2026-06-20T10:00:00Z",
            git_commit="abc123",
        )

        self.assertEqual(resultat["output"]["answer"], "Resposta")
        self.assertEqual(resultat["reasoning"]["content"], "raons")
        self.assertEqual(
            resultat["prompt"]["sha256"],
            inferencia.calculate_sha256("Text prompt"),
        )
        self.assertEqual(resultat["model"]["model_name"], "org/model")
        self.assertEqual(resultat["generation"]["min_token_len"], 32)

    def test_run_pipeline_does_not_load_models_when_there_are_no_prompts(self):
        config = {
            "configuracio_global": {"seed": 42, "backend_preferit": "transformers"},
            "parametres_generacio": {},
            "models": [{"id": "model-id", "model_name": "org/model"}],
        }

        with (
            patch.object(inferencia, "load_config", return_value=config),
            patch.object(inferencia, "discover_prompt_files", return_value=[]),
            patch.object(inferencia, "run_model") as run_model,
            patch.object(inferencia.LOGGER, "error") as log_error,
        ):
            inferencia.run_pipeline(root=Path("/tmp/no-prompts"))

        run_model.assert_not_called()
        log_error.assert_called_once_with("No s'han trobat prompts")

    def test_run_pipeline_with_mock_model_saves_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_dir = root / "config" / "inferencia"
            prompt_dir = root / "data" / "prompts" / "v1"
            config_dir.mkdir(parents=True)
            prompt_dir.mkdir(parents=True)

            (prompt_dir / "prompt.txt").write_text("Digues hola", encoding="utf-8")
            (config_dir / "inferencia_config.yaml").write_text(
                yaml.dump(
                    {
                        "configuracio_global": {
                            "seed": 42,
                            "backend_preferit": "transformers",
                        },
                        "parametres_generacio": {
                            "system_prompt": "Sistema",
                            "max_new_tokens": 12,
                            "temperature": 0,
                            "top_p": 1.0,
                        },
                        "models": [
                            {
                                "id": "fake-model",
                                "model_name": "org/fake-model",
                                "revision": "main",
                                "torch_dtype": "float32",
                                "device_map": "cpu",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch.object(inferencia, "get_git_commit", return_value="git123"),
                patch.object(
                    inferencia, "timestamp_utc", return_value="2026-06-20T10:00:00Z"
                ),
                patch.object(inferencia.LOGGER, "info"),
            ):
                inferencia.run_pipeline(
                    root=root,
                    device_map="cpu",
                    tokenizer_loader=lambda *args, **kwargs: FakeTokenizer(),
                    model_loader=lambda *args, **kwargs: FakeModel(),
                )

            output_path = (
                root / "data" / "inferencies" / "v1" / "fake-model" / "prompt.yaml"
            )
            resultat = yaml.safe_load(output_path.read_text(encoding="utf-8"))

        self.assertEqual(resultat["output"]["answer"], "Resposta final")
        self.assertEqual(resultat["run"]["git_commit"], "git123")
        self.assertEqual(resultat["model"]["model_name"], "org/fake-model")
        self.assertEqual(
            resultat["fingerprint"]["prompt_sha256"],
            inferencia.calculate_sha256("Digues hola"),
        )
        self.assertEqual(
            resultat["fingerprint"]["generation"]["system_prompt"], "Sistema"
        )

    def test_run_model_skips_current_inference_by_default(self):
        tokenizer_loader = Mock()
        model_loader = Mock()

        with (
            patch.object(inferencia, "is_inference_current", return_value=True),
            patch.object(inferencia.LOGGER, "info"),
        ):
            avg_time = inferencia.run_model(
                {"id": "fake-model", "model_name": "org/fake-model"},
                [{"id": "prompt"}],
                {},
                {},
                {},
                root=Path("unused"),
                tokenizer_loader=tokenizer_loader,
                model_loader=model_loader,
            )

        self.assertIsNone(avg_time)
        tokenizer_loader.assert_not_called()
        model_loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
