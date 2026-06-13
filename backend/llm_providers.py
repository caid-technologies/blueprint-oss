import base64
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    genai = None
    genai_types = None


logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_GEMINI_FALLBACK_MODEL = "gemini-2.5-flash"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT_SECONDS = 90.0


class LLMProviderConfigError(RuntimeError):
    """Raised when provider configuration prevents live generation."""


@dataclass
class LLMProviderValidation:
    provider: str
    requested_model: str
    actual_model: Optional[str]
    requested_model_available: bool
    strict_mode: bool
    fallback_active: bool
    fallback_model: Optional[str] = None
    validation_error: Optional[str] = None
    model_availability_checked: bool = False
    live_generation_enabled: bool = True

    def as_debug_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "requested_model": self.requested_model,
            "actual_model": self.actual_model,
            "requested_model_available": self.requested_model_available,
            "model_availability_checked": self.model_availability_checked,
            "strict_mode": self.strict_mode,
            "fallback_active": self.fallback_active,
            "fallback_model": self.fallback_model,
            "validation_error": self.validation_error,
            "live_generation_enabled": self.live_generation_enabled,
        }


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return default
    stripped = value.strip()
    return stripped if stripped else default


def _first_env(names: List[str], default: Optional[str] = None) -> Optional[str]:
    for name in names:
        value = _env(name)
        if value is not None:
            return value
    return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _first_env_bool(names: List[str], default: bool = False) -> bool:
    for name in names:
        if os.getenv(name) is not None:
            return _env_bool(name, default)
    return default


def _first_env_float(names: List[str], default: float) -> float:
    raw_value = _first_env(names)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError:
        logger.warning("Invalid timeout value %r; using %.1fs.", raw_value, default)
        return default


def _first_env_int(names: List[str]) -> Optional[int]:
    raw_value = _first_env(names)
    if raw_value is None:
        return None
    try:
        return int(raw_value)
    except ValueError:
        logger.warning("Invalid integer value %r; ignoring it.", raw_value)
        return None


def _normalize_model_name(model_name: str) -> str:
    return model_name.strip().removeprefix("models/")


def _model_is_available(model_name: str, available_models: List[str]) -> bool:
    requested = _normalize_model_name(model_name)
    return any(_normalize_model_name(candidate) == requested for candidate in available_models)


def _schema_name(schema_class: Any) -> str:
    raw_name = getattr(schema_class, "__name__", "StructuredResponse")
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in raw_name)


def _strip_json_markdown(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json_document(text: str) -> Optional[str]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in {"{", "["}:
            continue
        try:
            _, end_index = decoder.raw_decode(text[index:])
            return text[index:index + end_index]
        except json.JSONDecodeError:
            continue
    return None


def _validate_structured_json(response_text: str, schema_class: Any) -> Any:
    try:
        return schema_class.model_validate_json(response_text)
    except Exception as first_error:
        cleaned = _strip_json_markdown(response_text)
        if cleaned != response_text:
            try:
                return schema_class.model_validate_json(cleaned)
            except Exception:
                pass

        extracted = _extract_json_document(cleaned)
        if extracted:
            try:
                return schema_class.model_validate_json(extracted)
            except Exception:
                pass

        raise first_error


class StructuredLLMProvider:
    provider_name = "base"
    requested_model = "simulation"
    fallback_model: Optional[str] = None
    strict_mode = False
    model_name = "simulation"
    is_configured = False

    def validate_configured_model(self, *, raise_on_strict: bool = True) -> LLMProviderValidation:
        raise NotImplementedError

    def get_debug_config(self) -> Dict[str, Any]:
        return self.validate_configured_model(raise_on_strict=False).as_debug_dict()

    def generate_structured(
        self,
        prompt: str,
        schema_class: Any,
        image_bytes: Optional[bytes] = None,
        image_mime_type: Optional[str] = None,
    ) -> Any:
        raise NotImplementedError


class SimulationProvider(StructuredLLMProvider):
    provider_name = "simulation"
    requested_model = "simulation"
    model_name = "simulation"
    is_configured = False

    def __init__(self, validation_error: Optional[str] = None):
        self.validation_error = validation_error or "No live LLM provider is configured; simulation mode is active."
        self._validation: Optional[LLMProviderValidation] = None

    def validate_configured_model(self, *, raise_on_strict: bool = True) -> LLMProviderValidation:
        if not self._validation:
            self._validation = LLMProviderValidation(
                provider=self.provider_name,
                requested_model=self.requested_model,
                actual_model=None,
                requested_model_available=False,
                strict_mode=False,
                fallback_active=False,
                fallback_model=None,
                validation_error=self.validation_error,
                live_generation_enabled=False,
            )
        return self._validation

    def generate_structured(
        self,
        prompt: str,
        schema_class: Any,
        image_bytes: Optional[bytes] = None,
        image_mime_type: Optional[str] = None,
    ) -> Any:
        raise RuntimeError("Simulation provider cannot generate live structured responses.")


class GeminiProvider(StructuredLLMProvider):
    provider_name = "gemini"

    def __init__(self):
        self.api_key = _first_env(["GEMINI_API_KEY", "GOOGLE_API_KEY", "LLM_API_KEY"])
        self.requested_model = _first_env(["LLM_MODEL", "GEMINI_MODEL"], DEFAULT_GEMINI_MODEL) or DEFAULT_GEMINI_MODEL
        self.fallback_model = (
            _first_env(["LLM_FALLBACK_MODEL", "GEMINI_FALLBACK_MODEL"], DEFAULT_GEMINI_FALLBACK_MODEL)
            or DEFAULT_GEMINI_FALLBACK_MODEL
        )
        self.strict_mode = _first_env_bool(["STRICT_LLM", "STRICT_GEMINI"], default=True)
        self.model_name = self.requested_model
        self.client = None
        self.init_error: Optional[str] = None
        self._validation: Optional[LLMProviderValidation] = None

        if self.api_key and genai:
            try:
                self.client = genai.Client(api_key=self.api_key)
                logger.info("Gemini LLM provider initialized successfully.")
            except Exception as exc:
                self.init_error = f"Error initializing Gemini provider: {exc}"
                logger.error(self.init_error)
        elif self.api_key and not genai:
            self.init_error = "Gemini API key is set, but google-genai is unavailable."
            logger.warning("%s Running in simulated/fallback mode.", self.init_error)
        else:
            self.init_error = "No Gemini API key found."
            logger.warning("%s Live Gemini generation is disabled.", self.init_error)

        self.is_configured = self.client is not None

    def _list_generate_content_models(self) -> List[str]:
        if self.client is None:
            return []

        available_models: List[str] = []
        for model in self.client.models.list():
            name = getattr(model, "name", None)
            if not name:
                continue

            supported_actions = getattr(model, "supported_actions", None)
            if supported_actions is None and isinstance(model, dict):
                supported_actions = model.get("supportedActions") or model.get("supported_actions")
            supported_actions = supported_actions or []

            if "generateContent" in supported_actions:
                available_models.append(name)

        return available_models

    def validate_configured_model(self, *, raise_on_strict: bool = True) -> LLMProviderValidation:
        if self._validation:
            if raise_on_strict and self._validation.validation_error and self.strict_mode:
                raise LLMProviderConfigError(self._validation.validation_error)
            return self._validation

        if self.client is None:
            self._validation = LLMProviderValidation(
                provider=self.provider_name,
                requested_model=self.requested_model,
                actual_model=None,
                requested_model_available=False,
                strict_mode=self.strict_mode,
                fallback_active=False,
                fallback_model=self.fallback_model,
                validation_error=f"{self.init_error or 'Gemini provider is not configured'} Simulation mode is active.",
                live_generation_enabled=False,
            )
            return self._validation

        try:
            available_models = self._list_generate_content_models()
        except Exception as exc:
            validation_error = f"Unable to validate Gemini model availability: {exc}"
            actual_model = self.fallback_model if not self.strict_mode else None
            self._validation = LLMProviderValidation(
                provider=self.provider_name,
                requested_model=self.requested_model,
                actual_model=actual_model,
                requested_model_available=False,
                strict_mode=self.strict_mode,
                fallback_active=not self.strict_mode,
                fallback_model=self.fallback_model,
                validation_error=validation_error,
                model_availability_checked=True,
            )
            if self.strict_mode and raise_on_strict:
                raise LLMProviderConfigError(validation_error)
            self.model_name = actual_model or self.requested_model
            return self._validation

        requested_available = _model_is_available(self.requested_model, available_models)
        if requested_available:
            self.model_name = self.requested_model
            self._validation = LLMProviderValidation(
                provider=self.provider_name,
                requested_model=self.requested_model,
                actual_model=self.model_name,
                requested_model_available=True,
                strict_mode=self.strict_mode,
                fallback_active=False,
                fallback_model=self.fallback_model,
                model_availability_checked=True,
            )
            return self._validation

        if self.strict_mode:
            validation_error = (
                f"Configured Gemini model {self.requested_model} is not available for this API key/provider. "
                "Check available models or configure a valid Gemini model ID."
            )
            self._validation = LLMProviderValidation(
                provider=self.provider_name,
                requested_model=self.requested_model,
                actual_model=None,
                requested_model_available=False,
                strict_mode=True,
                fallback_active=False,
                fallback_model=self.fallback_model,
                validation_error=validation_error,
                model_availability_checked=True,
            )
            if raise_on_strict:
                raise LLMProviderConfigError(validation_error)
            return self._validation

        fallback_available = _model_is_available(self.fallback_model, available_models)
        if not fallback_available:
            validation_error = (
                f"Configured Gemini model {self.requested_model} is not available, and fallback model "
                f"{self.fallback_model} is not available for this API key/provider."
            )
            self._validation = LLMProviderValidation(
                provider=self.provider_name,
                requested_model=self.requested_model,
                actual_model=None,
                requested_model_available=False,
                strict_mode=False,
                fallback_active=False,
                fallback_model=self.fallback_model,
                validation_error=validation_error,
                model_availability_checked=True,
            )
            raise LLMProviderConfigError(validation_error)

        self.model_name = self.fallback_model
        self._validation = LLMProviderValidation(
            provider=self.provider_name,
            requested_model=self.requested_model,
            actual_model=self.model_name,
            requested_model_available=False,
            strict_mode=False,
            fallback_active=True,
            fallback_model=self.fallback_model,
            model_availability_checked=True,
        )
        return self._validation

    def generate_structured(
        self,
        prompt: str,
        schema_class: Any,
        image_bytes: Optional[bytes] = None,
        image_mime_type: Optional[str] = None,
    ) -> Any:
        if self.client is None or genai_types is None:
            raise RuntimeError("Gemini provider is not configured.")

        contents = []
        if image_bytes and image_mime_type:
            contents.append(genai_types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type))
        contents.append(prompt)

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema_class,
                temperature=0.2,
            ),
        )
        return _validate_structured_json(response.text, schema_class)


class OpenAICompatibleProvider(StructuredLLMProvider):
    def __init__(self, provider_name: str = "openai"):
        self.provider_name = "openai-compatible" if provider_name != "openai" else "openai"
        if self.provider_name == "openai":
            api_key_names = ["OPENAI_API_KEY", "LLM_API_KEY"]
            base_url_names = ["OPENAI_BASE_URL", "LLM_BASE_URL"]
            model_names = ["OPENAI_MODEL", "LLM_MODEL"]
            fallback_model_names = ["OPENAI_FALLBACK_MODEL", "LLM_FALLBACK_MODEL"]
            strict_names = ["STRICT_OPENAI", "STRICT_LLM"]
            validate_model_names = ["OPENAI_VALIDATE_MODELS", "LLM_VALIDATE_MODELS"]
            response_format_names = ["OPENAI_RESPONSE_FORMAT", "LLM_RESPONSE_FORMAT"]
            timeout_names = ["OPENAI_TIMEOUT_SECONDS", "LLM_TIMEOUT_SECONDS"]
            max_tokens_names = ["OPENAI_MAX_TOKENS", "LLM_MAX_TOKENS"]
            allow_no_api_key_names = ["OPENAI_ALLOW_NO_API_KEY", "LLM_ALLOW_NO_API_KEY"]
        else:
            api_key_names = ["LLM_API_KEY", "OPENAI_API_KEY"]
            base_url_names = ["LLM_BASE_URL", "OPENAI_BASE_URL"]
            model_names = ["LLM_MODEL", "OPENAI_MODEL"]
            fallback_model_names = ["LLM_FALLBACK_MODEL", "OPENAI_FALLBACK_MODEL"]
            strict_names = ["STRICT_LLM", "STRICT_OPENAI"]
            validate_model_names = ["LLM_VALIDATE_MODELS", "OPENAI_VALIDATE_MODELS"]
            response_format_names = ["LLM_RESPONSE_FORMAT", "OPENAI_RESPONSE_FORMAT"]
            timeout_names = ["LLM_TIMEOUT_SECONDS", "OPENAI_TIMEOUT_SECONDS"]
            max_tokens_names = ["LLM_MAX_TOKENS", "OPENAI_MAX_TOKENS"]
            allow_no_api_key_names = ["LLM_ALLOW_NO_API_KEY", "OPENAI_ALLOW_NO_API_KEY"]

        self.api_key = _first_env(api_key_names)
        self.organization_id = _first_env(["OPENAI_ORG_ID", "OPENAI_ORGANIZATION", "OPENAI_ORGANIZATION_ID"])
        self.project_id = _first_env(["OPENAI_PROJECT_ID", "OPENAI_PROJECT"])
        configured_base_url = _first_env(base_url_names)
        default_base_url = "https://api.openai.com/v1" if self.provider_name == "openai" else None
        self.base_url = (configured_base_url or default_base_url or "").rstrip("/")
        self.requested_model = _first_env(model_names, DEFAULT_OPENAI_MODEL) or DEFAULT_OPENAI_MODEL
        self.fallback_model = _first_env(fallback_model_names)
        self.strict_mode = _first_env_bool(strict_names, default=True)
        self.validate_models = _first_env_bool(validate_model_names, default=False)
        default_response_format = "json_schema" if self.provider_name == "openai" else "json_object"
        self.response_format = (
            _first_env(response_format_names, default_response_format)
            or default_response_format
        ).strip().lower().replace("-", "_")
        self.timeout_seconds = _first_env_float(timeout_names, DEFAULT_TIMEOUT_SECONDS)
        self.max_tokens = _first_env_int(max_tokens_names)
        self.allow_no_api_key = _first_env_bool(
            allow_no_api_key_names,
            default=self.provider_name != "openai" and configured_base_url is not None,
        )
        self.model_name = self.requested_model
        self._validation: Optional[LLMProviderValidation] = None

        self.is_configured = bool(self.base_url and (self.api_key or self.allow_no_api_key))
        if self.is_configured:
            logger.info("%s LLM provider initialized for model %s.", self.provider_name, self.requested_model)
        else:
            logger.warning("%s LLM provider is missing an API key or base URL.", self.provider_name)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.provider_name == "openai":
            if self.organization_id:
                headers["OpenAI-Organization"] = self.organization_id
            if self.project_id:
                headers["OpenAI-Project"] = self.project_id
        return headers

    def _request_json(self, path: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, headers=self._headers(), method=method)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{self.provider_name} request failed with HTTP {exc.code}: {detail[:500]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"{self.provider_name} request failed: {exc}") from exc

        if not body.strip():
            return {}
        return json.loads(body)

    def _list_models(self) -> List[str]:
        payload = self._request_json("models")
        data = payload.get("data", [])
        if not isinstance(data, list):
            return []

        models = []
        for item in data:
            if isinstance(item, dict):
                model_id = item.get("id") or item.get("name")
                if model_id:
                    models.append(model_id)
            elif isinstance(item, str):
                models.append(item)
        return models

    def validate_configured_model(self, *, raise_on_strict: bool = True) -> LLMProviderValidation:
        if self._validation:
            if raise_on_strict and self._validation.validation_error and self.strict_mode:
                raise LLMProviderConfigError(self._validation.validation_error)
            return self._validation

        if not self.is_configured:
            self._validation = LLMProviderValidation(
                provider=self.provider_name,
                requested_model=self.requested_model,
                actual_model=None,
                requested_model_available=False,
                strict_mode=self.strict_mode,
                fallback_active=False,
                fallback_model=self.fallback_model,
                validation_error=(
                    f"{self.provider_name} provider is not configured. Set LLM_API_KEY or OPENAI_API_KEY, "
                    "or set LLM_ALLOW_NO_API_KEY=true for a local OpenAI-compatible endpoint."
                ),
                live_generation_enabled=False,
            )
            return self._validation

        if not self.validate_models:
            self.model_name = self.requested_model
            self._validation = LLMProviderValidation(
                provider=self.provider_name,
                requested_model=self.requested_model,
                actual_model=self.model_name,
                requested_model_available=True,
                strict_mode=self.strict_mode,
                fallback_active=False,
                fallback_model=self.fallback_model,
                model_availability_checked=False,
            )
            return self._validation

        try:
            available_models = self._list_models()
        except Exception as exc:
            validation_error = f"Unable to validate {self.provider_name} model availability: {exc}"
            actual_model = self.fallback_model if (self.fallback_model and not self.strict_mode) else None
            self._validation = LLMProviderValidation(
                provider=self.provider_name,
                requested_model=self.requested_model,
                actual_model=actual_model,
                requested_model_available=False,
                strict_mode=self.strict_mode,
                fallback_active=bool(actual_model),
                fallback_model=self.fallback_model,
                validation_error=validation_error,
                model_availability_checked=True,
            )
            if self.strict_mode and raise_on_strict:
                raise LLMProviderConfigError(validation_error)
            self.model_name = actual_model or self.requested_model
            return self._validation

        requested_available = _model_is_available(self.requested_model, available_models)
        if requested_available:
            self.model_name = self.requested_model
            self._validation = LLMProviderValidation(
                provider=self.provider_name,
                requested_model=self.requested_model,
                actual_model=self.model_name,
                requested_model_available=True,
                strict_mode=self.strict_mode,
                fallback_active=False,
                fallback_model=self.fallback_model,
                model_availability_checked=True,
            )
            return self._validation

        if self.strict_mode or not self.fallback_model:
            validation_error = (
                f"Configured {self.provider_name} model {self.requested_model} is not available for this endpoint."
            )
            self._validation = LLMProviderValidation(
                provider=self.provider_name,
                requested_model=self.requested_model,
                actual_model=None,
                requested_model_available=False,
                strict_mode=self.strict_mode,
                fallback_active=False,
                fallback_model=self.fallback_model,
                validation_error=validation_error,
                model_availability_checked=True,
            )
            if raise_on_strict:
                raise LLMProviderConfigError(validation_error)
            return self._validation

        fallback_available = _model_is_available(self.fallback_model, available_models)
        if not fallback_available:
            validation_error = (
                f"Configured {self.provider_name} model {self.requested_model} is not available, and fallback model "
                f"{self.fallback_model} is not available for this endpoint."
            )
            self._validation = LLMProviderValidation(
                provider=self.provider_name,
                requested_model=self.requested_model,
                actual_model=None,
                requested_model_available=False,
                strict_mode=False,
                fallback_active=False,
                fallback_model=self.fallback_model,
                validation_error=validation_error,
                model_availability_checked=True,
            )
            raise LLMProviderConfigError(validation_error)

        self.model_name = self.fallback_model
        self._validation = LLMProviderValidation(
            provider=self.provider_name,
            requested_model=self.requested_model,
            actual_model=self.model_name,
            requested_model_available=False,
            strict_mode=False,
            fallback_active=True,
            fallback_model=self.fallback_model,
            model_availability_checked=True,
        )
        return self._validation

    def _build_structured_prompt(self, prompt: str, schema_class: Any) -> str:
        schema_json = json.dumps(schema_class.model_json_schema(), indent=2)
        return (
            f"{prompt}\n\n"
            "Return only valid JSON. The JSON must conform to this schema:\n"
            f"{schema_json}"
        )

    def _build_user_content(
        self,
        prompt: str,
        schema_class: Any,
        image_bytes: Optional[bytes],
        image_mime_type: Optional[str],
    ) -> Any:
        structured_prompt = self._build_structured_prompt(prompt, schema_class)
        if not image_bytes or not image_mime_type:
            return structured_prompt

        data = base64.b64encode(image_bytes).decode("ascii")
        return [
            {"type": "text", "text": structured_prompt},
            {"type": "image_url", "image_url": {"url": f"data:{image_mime_type};base64,{data}"}},
        ]

    def generate_structured(
        self,
        prompt: str,
        schema_class: Any,
        image_bytes: Optional[bytes] = None,
        image_mime_type: Optional[str] = None,
    ) -> Any:
        if not self.is_configured:
            raise RuntimeError(f"{self.provider_name} provider is not configured.")

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": "You produce concise, valid JSON only. Do not include markdown or commentary.",
                },
                {
                    "role": "user",
                    "content": self._build_user_content(prompt, schema_class, image_bytes, image_mime_type),
                },
            ],
            "temperature": 0.2,
        }
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens

        if self.response_format == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": _schema_name(schema_class),
                    "schema": schema_class.model_json_schema(),
                    "strict": False,
                },
            }
        elif self.response_format != "none":
            payload["response_format"] = {"type": "json_object"}

        response = self._request_json("chat/completions", method="POST", payload=payload)
        choices = response.get("choices") or []
        if not choices:
            raise RuntimeError(f"{self.provider_name} response did not include any choices.")

        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(f"{self.provider_name} response did not include text content.")

        return _validate_structured_json(content, schema_class)


def build_llm_provider() -> StructuredLLMProvider:
    provider_name = (_env("LLM_PROVIDER") or "").strip().lower().replace("_", "-")
    if not provider_name:
        if _first_env(["GEMINI_API_KEY", "GOOGLE_API_KEY"]):
            provider_name = "gemini"
        elif _first_env(["LLM_BASE_URL", "OPENAI_BASE_URL"]):
            provider_name = "openai-compatible"
        elif _first_env(["OPENAI_API_KEY", "LLM_API_KEY"]):
            provider_name = "openai"
        else:
            provider_name = "simulation"

    if provider_name in {"gemini", "google", "google-genai"}:
        return GeminiProvider()
    if provider_name in {"openai", "openai-compatible", "compatible"}:
        return OpenAICompatibleProvider(provider_name=provider_name)
    if provider_name in {"simulation", "simulated", "offline", "none", "mock"}:
        return SimulationProvider()

    message = (
        f"Unsupported LLM_PROVIDER '{provider_name}'. Supported providers are "
        "gemini, openai, openai-compatible, and simulation."
    )
    logger.warning(message)
    return SimulationProvider(validation_error=message)
