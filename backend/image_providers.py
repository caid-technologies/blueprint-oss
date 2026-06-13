import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-2"
DEFAULT_IMAGE_SIZE = "1024x1024"
DEFAULT_IMAGE_TIMEOUT_SECONDS = 120.0


@dataclass
class GeneratedImage:
    data_url: str
    provider: str
    model: str
    size: str
    prompt: str
    output_format: str = "png"


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
        logger.warning("Invalid image timeout value %r; using %.1fs.", raw_value, default)
        return default


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _limit_list(values: List[Any], limit: int, item_limit: int = 140) -> List[str]:
    result: List[str] = []
    for value in values:
        text = _truncate(value, item_limit)
        if text:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _model_supports_gpt_image_params(model_name: str) -> bool:
    return model_name.strip().lower().startswith("gpt-image")


def _mime_for_output_format(output_format: str) -> str:
    normalized = (output_format or "png").strip().lower()
    if normalized == "jpg":
        normalized = "jpeg"
    if normalized in {"jpeg", "png", "webp"}:
        return f"image/{normalized}"
    return "image/png"


class ImageProvider:
    provider_name = "none"
    model_name = "none"
    is_configured = False
    enabled = False

    def get_debug_config(self) -> Dict[str, Any]:
        return {
            "provider": self.provider_name,
            "enabled": self.enabled,
            "configured": self.is_configured,
            "model_name": self.model_name,
        }

    def generate_project_image(self, user_prompt: str, ir: Any) -> Optional[GeneratedImage]:
        raise NotImplementedError


class NoImageProvider(ImageProvider):
    provider_name = "none"
    model_name = "none"

    def __init__(self, reason: str = "Image output is disabled.") -> None:
        self.reason = reason
        self.enabled = False
        self.is_configured = False

    def get_debug_config(self) -> Dict[str, Any]:
        return {
            **super().get_debug_config(),
            "reason": self.reason,
        }

    def generate_project_image(self, user_prompt: str, ir: Any) -> Optional[GeneratedImage]:
        return None


class OpenAIImageProvider(ImageProvider):
    def __init__(self, provider_name: str = "openai", enabled: bool = True, force_enabled: bool = False) -> None:
        normalized_provider = provider_name.strip().lower().replace("_", "-")
        self.provider_name = "openai-compatible" if normalized_provider != "openai" else "openai"
        self.enabled = enabled or force_enabled

        if self.provider_name == "openai":
            api_key_names = ["OPENAI_IMAGE_API_KEY", "IMAGE_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"]
            base_url_names = ["OPENAI_IMAGE_BASE_URL", "IMAGE_BASE_URL", "OPENAI_BASE_URL", "LLM_BASE_URL"]
            model_names = ["OPENAI_IMAGE_MODEL", "IMAGE_MODEL"]
            timeout_names = ["OPENAI_IMAGE_TIMEOUT_SECONDS", "IMAGE_TIMEOUT_SECONDS"]
            allow_no_api_key_names = ["OPENAI_IMAGE_ALLOW_NO_API_KEY", "IMAGE_ALLOW_NO_API_KEY"]
        else:
            api_key_names = ["IMAGE_API_KEY", "OPENAI_IMAGE_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY"]
            base_url_names = ["IMAGE_BASE_URL", "OPENAI_IMAGE_BASE_URL", "LLM_BASE_URL", "OPENAI_BASE_URL"]
            model_names = ["IMAGE_MODEL", "OPENAI_IMAGE_MODEL"]
            timeout_names = ["IMAGE_TIMEOUT_SECONDS", "OPENAI_IMAGE_TIMEOUT_SECONDS"]
            allow_no_api_key_names = ["IMAGE_ALLOW_NO_API_KEY", "OPENAI_IMAGE_ALLOW_NO_API_KEY", "LLM_ALLOW_NO_API_KEY"]

        configured_base_url = _first_env(base_url_names)
        default_base_url = "https://api.openai.com/v1" if self.provider_name == "openai" else None
        self.base_url = (configured_base_url or default_base_url or "").rstrip("/")
        self.api_key = _first_env(api_key_names)
        self.organization_id = _first_env(["OPENAI_ORG_ID", "OPENAI_ORGANIZATION", "OPENAI_ORGANIZATION_ID"])
        self.project_id = _first_env(["OPENAI_PROJECT_ID", "OPENAI_PROJECT"])
        self.model_name = _first_env(model_names, DEFAULT_OPENAI_IMAGE_MODEL) or DEFAULT_OPENAI_IMAGE_MODEL
        self.size = _first_env(["OPENAI_IMAGE_SIZE", "IMAGE_SIZE"], DEFAULT_IMAGE_SIZE) or DEFAULT_IMAGE_SIZE
        self.quality = _first_env(["OPENAI_IMAGE_QUALITY", "IMAGE_QUALITY"])
        self.output_format = _first_env(["OPENAI_IMAGE_OUTPUT_FORMAT", "IMAGE_OUTPUT_FORMAT"], "png") or "png"
        self.timeout_seconds = _first_env_float(timeout_names, DEFAULT_IMAGE_TIMEOUT_SECONDS)
        self.allow_no_api_key = _first_env_bool(
            allow_no_api_key_names,
            default=self.provider_name != "openai" and configured_base_url is not None,
        )
        self.is_configured = bool(self.enabled and self.base_url and (self.api_key or self.allow_no_api_key))

    def get_debug_config(self) -> Dict[str, Any]:
        reason = None
        if not self.enabled:
            reason = "Image output is disabled."
        elif not self.base_url:
            reason = "Image provider base URL is missing."
        elif not self.api_key and not self.allow_no_api_key:
            reason = "Image provider API key is missing."

        return {
            **super().get_debug_config(),
            "base_url": self.base_url,
            "size": self.size,
            "quality": self.quality,
            "output_format": self.output_format,
            "reason": reason,
        }

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
            raise RuntimeError(f"{self.provider_name} image request failed with HTTP {exc.code}: {detail[:500]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"{self.provider_name} image request failed: {exc}") from exc

        if not body.strip():
            return {}
        return json.loads(body)

    def generate_project_image(self, user_prompt: str, ir: Any) -> Optional[GeneratedImage]:
        if not self.is_configured:
            return None

        image_prompt = build_project_image_prompt(user_prompt, ir)
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "prompt": image_prompt,
            "size": self.size,
            "n": 1,
        }

        if _model_supports_gpt_image_params(self.model_name):
            if self.quality:
                payload["quality"] = self.quality
            if self.output_format:
                payload["output_format"] = self.output_format

        response = self._request_json("images/generations", method="POST", payload=payload)
        item = _first_image_item(response)
        if not item:
            raise RuntimeError(f"{self.provider_name} image response did not include image data.")

        b64_json = item.get("b64_json") or item.get("base64") or item.get("image_base64")
        if isinstance(b64_json, str) and b64_json.strip():
            mime_type = _mime_for_output_format(self.output_format)
            data_url = f"data:{mime_type};base64,{b64_json.strip()}"
        else:
            url = item.get("url")
            if not isinstance(url, str) or not url.strip():
                raise RuntimeError(f"{self.provider_name} image response did not include b64_json or url.")
            data_url = url.strip()

        return GeneratedImage(
            data_url=data_url,
            provider=self.provider_name,
            model=self.model_name,
            size=self.size,
            prompt=image_prompt,
            output_format=self.output_format,
        )


def _first_image_item(response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    data = response.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        return first if isinstance(first, dict) else None

    output = response.get("output")
    if isinstance(output, list):
        for item in output:
            if isinstance(item, dict) and any(key in item for key in {"b64_json", "base64", "image_base64", "url"}):
                return item
            if isinstance(item, dict):
                nested = item.get("content")
                if isinstance(nested, list):
                    for nested_item in nested:
                        if isinstance(nested_item, dict) and any(
                            key in nested_item for key in {"b64_json", "base64", "image_base64", "url"}
                        ):
                            return nested_item
    return None


def build_project_image_prompt(user_prompt: str, ir: Any) -> str:
    overview = getattr(ir, "overview", None)
    mechanical = getattr(ir, "mechanical", None)
    title = getattr(overview, "title", "Hardware concept")
    description = getattr(overview, "description", user_prompt)
    components = getattr(ir, "components", []) or []
    constraints = getattr(ir, "constraints", []) or []
    fabrication_notes = getattr(ir, "fabrication_notes", []) or []

    component_lines = _limit_list(
        [
            " ".join(
                item
                for item in [
                    getattr(component, "ref_des", ""),
                    getattr(component, "name", ""),
                    f"({getattr(component, 'category', '')})" if getattr(component, "category", "") else "",
                ]
                if item
            )
            for component in components
        ],
        limit=12,
    )

    dimensions = ""
    render_dimensions = getattr(mechanical, "render_dimensions", None)
    if render_dimensions:
        dimensions = (
            f"{getattr(render_dimensions, 'x_mm', '?')}mm wide x "
            f"{getattr(render_dimensions, 'y_mm', '?')}mm deep x "
            f"{getattr(render_dimensions, 'z_mm', '?')}mm tall"
        )

    prompt_parts = [
        "Create a clean realistic product concept render for a safe low-voltage maker electronics build.",
        "Show the assembled physical device, enclosure, visible controls, display openings, ports, and any exposed low-voltage modules that belong in the design.",
        "Do not include text, labels, watermarks, logos, hands, people, wiring diagrams, schematic symbols, high-voltage equipment, medical devices, or weapons.",
        "Use a neutral studio background, believable materials, and a three-quarter product view.",
        f"Project title: {_truncate(title, 120)}",
        f"Project description: {_truncate(description, 300)}",
        f"User prompt: {_truncate(user_prompt, 220)}",
    ]

    if component_lines:
        prompt_parts.append("Main parts: " + "; ".join(component_lines))
    if constraints:
        prompt_parts.append("Design constraints: " + "; ".join(_limit_list(constraints, 8)))
    if fabrication_notes:
        prompt_parts.append("Fabrication notes: " + "; ".join(_limit_list(fabrication_notes, 5)))
    if dimensions:
        prompt_parts.append(f"Approximate device envelope: {dimensions}.")

    return "\n".join(prompt_parts)


def build_image_provider(force_enabled: bool = False) -> ImageProvider:
    provider_name = (_env("IMAGE_PROVIDER") or "").strip().lower().replace("_", "-")
    enabled_default = bool(provider_name and provider_name not in {"none", "disabled", "off", "false", "simulation", "mock"})
    enabled = _first_env_bool(["IMAGE_OUTPUT_ENABLED", "OPENAI_IMAGE_OUTPUT_ENABLED"], default=enabled_default)

    if not enabled and not force_enabled:
        return NoImageProvider()

    if not provider_name:
        if _first_env(["OPENAI_IMAGE_API_KEY", "OPENAI_API_KEY", "IMAGE_API_KEY", "LLM_API_KEY"]):
            provider_name = "openai"
        elif _first_env(["IMAGE_BASE_URL", "OPENAI_IMAGE_BASE_URL", "LLM_BASE_URL", "OPENAI_BASE_URL"]):
            provider_name = "openai-compatible"
        else:
            return NoImageProvider("Image output is enabled, but no image provider configuration was found.")

    if provider_name in {"none", "disabled", "off", "false", "simulation", "mock"}:
        return NoImageProvider()
    if provider_name in {"openai", "openai-compatible", "compatible"}:
        return OpenAIImageProvider(provider_name=provider_name, enabled=enabled, force_enabled=force_enabled)

    logger.warning("Unsupported IMAGE_PROVIDER %r; image output is disabled.", provider_name)
    return NoImageProvider(
        f"Unsupported IMAGE_PROVIDER '{provider_name}'. Supported providers are openai, openai-compatible, and none."
    )


def get_image_output_debug_config() -> Dict[str, Any]:
    default_config = build_image_provider().get_debug_config()
    request_config = build_image_provider(force_enabled=True).get_debug_config()
    return {
        **default_config,
        "default_enabled": default_config.get("enabled", False),
        "request_capable": bool(request_config.get("configured")),
        "request_provider": request_config.get("provider"),
        "request_model_name": request_config.get("model_name"),
    }
