import os
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

from backend.database import SessionLocal, DBComponentTemplate, DBGeneratedProject
from backend.models import (
    HardwareIR, ProjectOverview, FunctionalRequirements, 
    ComponentInstance, ConnectionNet, PinReference, AssemblyStep, 
    MechanicalNotes, MechanicalSource, MechanicalVector3, MechanicalRotation3,
    MechanicalPlacement, MechanicalSpatialRelationship, PinMappingEntry,
    ValidationIssue, PinDefinition, ValidationSummary, BusConnection, PowerRail
)
from backend.validation import validate_circuit, check_safety_violations, build_validation_summary

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_GEMINI_FALLBACK_MODEL = "gemini-2.5-flash"
UNAVAILABLE_GEMINI_35_FLASH_MESSAGE = (
    "Configured model gemini-3.5-flash is not available for this API key/provider. "
    "Check available models or configure a valid Gemini 3.5 Flash model ID."
)


class GeminiModelConfigError(RuntimeError):
    """Raised when Gemini model configuration prevents live generation."""


@dataclass
class GeminiModelValidation:
    requested_model: str
    actual_model: Optional[str]
    requested_model_available: bool
    strict_mode: bool
    fallback_active: bool
    fallback_model: str
    validation_error: Optional[str] = None

    def as_debug_dict(self) -> Dict[str, Any]:
        return {
            "requested_model": self.requested_model,
            "actual_model": self.actual_model,
            "requested_model_available": self.requested_model_available,
            "strict_mode": self.strict_mode,
            "fallback_active": self.fallback_active,
            "validation_error": self.validation_error,
        }


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_model_name(model_name: str) -> str:
    return model_name.strip().removeprefix("models/")


def _model_is_available(model_name: str, available_models: List[str]) -> bool:
    requested = _normalize_model_name(model_name)
    return any(_normalize_model_name(candidate) == requested for candidate in available_models)


def _list_generate_content_models() -> List[str]:
    """List models available to this Gemini API key/provider for generateContent."""
    if client is None:
        return []

    available_models: List[str] = []
    for model in client.models.list():
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

# Initialize Google GenAI Client if API key is provided
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
client = None
if GEMINI_API_KEY and genai:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("Google GenAI client initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing GenAI client: {e}")
elif GEMINI_API_KEY and not genai:
    logger.warning("GEMINI_API_KEY is set, but google-genai is unavailable. Running in simulated/fallback mode.")
else:
    logger.warning("No GEMINI_API_KEY or GOOGLE_API_KEY found. Multi-agent generation will run in high-fidelity simulated/fallback mode.")

# Tool to query database templates
def get_db_component_templates() -> List[Dict[str, Any]]:
    """Helper tool that returns all available hardware templates in the seed database."""
    db = SessionLocal()
    try:
        db_templates = db.query(DBComponentTemplate).all()
        templates = []
        for t in db_templates:
            templates.append({
                "part_number": t.part_number,
                "name": t.name,
                "category": t.category,
                "description": t.description,
                "price": t.price,
                "pins": t.pins,
                "use_cases": t.use_cases
            })
        return templates
    finally:
        db.close()

# Helper utilities to enrich HardwareIR schemas dynamically
def extract_power_rails(components: List[ComponentInstance], nets: List[ConnectionNet]) -> List[PowerRail]:
    rails = []
    component_lookup = {component.ref_des: component for component in components}
    for net in nets:
        if net.net_type.lower() == "power" and net.voltage:
            source = None
            for pin_ref in net.pins:
                component = component_lookup.get(pin_ref.ref_des)
                if component and component.category.lower() == "power":
                    source = pin_ref.ref_des
                    break
                if pin_ref.ref_des == "BAT1":
                    source = "BAT1"
                elif pin_ref.ref_des == "USB-Power" or "power" in pin_ref.ref_des.lower():
                    source = pin_ref.ref_des
            if not source:
                source = "U1"
            
            rails.append(PowerRail(
                rail_id=f"RAIL_{str(net.voltage).replace('.', 'V')}",
                voltage=net.voltage,
                max_current_capacity_ma=500.0 if net.voltage == 3.3 else 1000.0,
                source_component=source
            ))
    return rails

def extract_buses(nets: List[ConnectionNet]) -> List[BusConnection]:
    buses = []
    i2c_nets = [net.net_id for net in nets if net.net_type.lower() == "i2c"]
    if i2c_nets:
        buses.append(BusConnection(
            bus_id="BUS_I2C_1",
            bus_type="I2C",
            clock_frequency_hz=100000.0,
            nets=i2c_nets
        ))
    spi_nets = [net.net_id for net in nets if net.net_type.lower() == "spi"]
    if spi_nets:
        buses.append(BusConnection(
            bus_id="BUS_SPI_1",
            bus_type="SPI",
            clock_frequency_hz=1000000.0,
            nets=spi_nets
        ))
    return buses

def estimate_current_draw(components: List[ComponentInstance]) -> float:
    draw = 0.0
    for comp in components:
        cat = comp.category.lower()
        if cat == "microcontroller":
            draw += 80.0
        elif cat == "display":
            draw += 25.0
        elif cat == "actuator":
            if comp.part_number == "SG90-Servo":
                draw += 250.0
            else:
                draw += 70.0 # relay coil
        elif cat == "sensor":
            draw += 5.0
        elif comp.part_number == "LED-Red-Generic":
            draw += 15.0
    return draw

def _mechanical_vector(x_mm: float, y_mm: float, z_mm: float) -> MechanicalVector3:
    return MechanicalVector3(
        x_mm=round(float(x_mm), 2),
        y_mm=round(float(y_mm), 2),
        z_mm=round(float(z_mm), 2)
    )

def _component_text(component: ComponentInstance) -> str:
    return f"{component.ref_des} {component.name} {component.part_number} {component.category}".lower()

def _category_key(component: ComponentInstance) -> str:
    return component.category.strip().lower()

def _is_enclosure_component(component: ComponentInstance) -> bool:
    text = _component_text(component)
    if any(token in text for token in ["screw", "insert", "standoff", "button cap", "fastener"]):
        return False
    return any(token in text for token in ["main enclosure", "enclosure shell", "project box", "shell", "housing", "case"])

def _infer_render_dimensions(ir: HardwareIR) -> MechanicalVector3:
    if ir.mechanical and ir.mechanical.render_dimensions:
        return ir.mechanical.render_dimensions

    haystack = " ".join([
        ir.overview.title if ir.overview else "",
        ir.overview.description if ir.overview else "",
        " ".join(ir.constraints or []),
        " ".join(ir.fabrication_notes or []),
    ]).lower()

    if any(token in haystack for token in ["mp3", "audio", "pocket", "portable"]):
        return _mechanical_vector(100, 21, 54)
    if any(token in haystack for token in ["plant", "water", "soil", "garden"]):
        return _mechanical_vector(116, 82, 55)
    if any(token in haystack for token in ["thermostat", "nest", "hvac"]):
        return _mechanical_vector(86, 24, 86)
    if any(token in haystack for token in ["deadbolt", "lock", "servo"]):
        return _mechanical_vector(92, 64, 38)

    electrical_count = len([component for component in ir.components if _category_key(component) not in {"mechanical", "3d print"}])
    width = max(92, min(150, 70 + electrical_count * 7))
    depth = max(48, min(92, 36 + electrical_count * 4))
    height = max(30, min(70, 24 + electrical_count * 3))
    return _mechanical_vector(width, depth, height)

def _placement_layer(component: ComponentInstance) -> str:
    key = _category_key(component)
    text = _component_text(component)

    if _is_enclosure_component(component):
        return "enclosure"
    if key == "3d print":
        return "print"
    if key == "mechanical":
        if any(token in text for token in ["screw", "insert", "standoff", "boss"]):
            return "structural"
        return "mechanism"
    return "electrical"

def _placement_size(component: ComponentInstance, dimensions: MechanicalVector3) -> MechanicalVector3:
    key = _category_key(component)
    text = _component_text(component)

    if _is_enclosure_component(component):
        return dimensions
    if any(token in text for token in ["front bezel", "faceplate", "acrylic", "window", "trim"]):
        return _mechanical_vector(dimensions.x_mm * 0.82, max(2.0, dimensions.y_mm * 0.12), dimensions.z_mm * 0.72)
    if any(token in text for token in ["back cover", "rear cover", "cover"]):
        return _mechanical_vector(dimensions.x_mm * 0.88, max(2.0, dimensions.y_mm * 0.12), dimensions.z_mm * 0.86)
    if "battery" in text:
        return _mechanical_vector(min(48, dimensions.x_mm * 0.45), min(26, dimensions.y_mm * 0.65), min(9, dimensions.z_mm * 0.22))
    if "speaker" in text:
        return _mechanical_vector(24, min(12, dimensions.y_mm * 0.45), 24)
    if "relay" in text:
        return _mechanical_vector(38, 26, 16)
    if "servo" in text:
        return _mechanical_vector(23, 12, 29)
    if any(token in text for token in ["oled", "display"]):
        return _mechanical_vector(34, 3, 18)
    if any(token in text for token in ["button", "switch", "cap"]):
        return _mechanical_vector(10, 7, 10)
    if any(token in text for token in ["usb-c", "usb"]):
        return _mechanical_vector(18, 8, 7)
    if any(token in text for token in ["screw", "insert", "standoff"]):
        return _mechanical_vector(5, 5, 8)
    if any(token in text for token in ["mount", "bracket", "plate"]):
        return _mechanical_vector(34, 4, 18)

    sizes = {
        "microcontroller": (38, 28, 5),
        "sensor": (20, 12, 14),
        "actuator": (30, 22, 14),
        "display": (34, 3, 18),
        "power": (42, 22, 8),
        "passives": (15, 12, 8),
        "communication": (28, 18, 5),
        "mechanical": (14, 10, 8),
        "3d print": (30, 5, 18),
    }
    x_mm, y_mm, z_mm = sizes.get(key, (22, 16, 6))
    return _mechanical_vector(x_mm, y_mm, z_mm)

def _row_position(index: int, count: int, span: float) -> float:
    if count <= 1:
        return 0.0
    return -span / 2 + span * (index / (count - 1))

def _placement_position(component: ComponentInstance, components: List[ComponentInstance], dimensions: MechanicalVector3) -> MechanicalVector3:
    key = _category_key(component)
    text = _component_text(component)
    width = dimensions.x_mm
    depth = dimensions.y_mm
    height = dimensions.z_mm

    if _is_enclosure_component(component):
        return _mechanical_vector(0, 0, 0)
    if any(token in text for token in ["front bezel", "faceplate", "trim plate", "acrylic cover", "window"]):
        return _mechanical_vector(0, -depth * 0.46, height * 0.04)
    if any(token in text for token in ["back cover", "rear cover", "cover"]):
        return _mechanical_vector(0, depth * 0.46, 0)
    if any(token in text for token in ["oled mount", "display bezel"]):
        return _mechanical_vector(0, -depth * 0.36, height * 0.22)
    if any(token in text for token in ["controller mount", "esp32 mount", "board mount"]):
        return _mechanical_vector(0, -depth * 0.05, -height * 0.1)

    button_like = [item for item in components if any(token in _component_text(item) for token in ["button", "switch", "cap"])]
    button_index = next((index for index, item in enumerate(button_like) if item.ref_des == component.ref_des), -1)
    if button_index >= 0:
        return _mechanical_vector(_row_position(button_index, len(button_like), width * 0.42), -depth * 0.43, -height * 0.12)

    structural = [item for item in components if _placement_layer(item) == "structural"]
    structural_index = next((index for index, item in enumerate(structural) if item.ref_des == component.ref_des), -1)
    if structural_index >= 0:
        corner_x = -width * 0.42 if structural_index % 2 == 0 else width * 0.42
        corner_z = -height * 0.36 if structural_index < 2 else height * 0.36
        return _mechanical_vector(corner_x, depth * 0.28, corner_z)

    if "display" in key or "oled" in text:
        return _mechanical_vector(0, -depth * 0.43, height * 0.24)
    if key == "microcontroller":
        return _mechanical_vector(0, 0, -height * 0.04)
    if "battery" in text:
        return _mechanical_vector(-width * 0.27, depth * 0.24, -height * 0.26)
    if any(token in text for token in ["charger", "usb-c", "usb"]):
        return _mechanical_vector(width * 0.28, -depth * 0.36, -height * 0.28)
    if "speaker" in text:
        return _mechanical_vector(width * 0.32, depth * 0.3, height * 0.2)
    if any(token in text for token in ["sd", "storage"]):
        return _mechanical_vector(-width * 0.3, -depth * 0.04, height * 0.04)
    if any(token in text for token in ["dac", "audio"]):
        return _mechanical_vector(width * 0.22, depth * 0.02, 0)
    if key == "sensor":
        sensors = [item for item in components if _category_key(item) == "sensor"]
        sensor_index = max(0, next((index for index, item in enumerate(sensors) if item.ref_des == component.ref_des), 0))
        return _mechanical_vector(_row_position(sensor_index, len(sensors), width * 0.44), -depth * 0.42, height * 0.16)
    if key == "actuator":
        actuators = [item for item in components if _category_key(item) == "actuator"]
        actuator_index = max(0, next((index for index, item in enumerate(actuators) if item.ref_des == component.ref_des), 0))
        return _mechanical_vector(width * 0.3, depth * (0.12 - actuator_index * 0.18), -height * 0.04 + actuator_index * height * 0.18)
    if key == "power":
        power_parts = [item for item in components if _category_key(item) == "power"]
        power_index = max(0, next((index for index, item in enumerate(power_parts) if item.ref_des == component.ref_des), 0))
        return _mechanical_vector(-width * 0.28 + power_index * width * 0.22, depth * 0.22, -height * 0.25)

    remaining = [
        item for item in components
        if _category_key(item) not in {"mechanical", "3d print"}
        and _category_key(item) not in {"microcontroller", "display", "sensor", "actuator", "power"}
    ]
    remaining_index = max(0, next((index for index, item in enumerate(remaining) if item.ref_des == component.ref_des), 0))
    return _mechanical_vector(_row_position(remaining_index, len(remaining), width * 0.64), -depth * 0.16, -height * 0.03)

def _dominant_axis(source: MechanicalPlacement, target: MechanicalPlacement) -> str:
    deltas = {
        "X": abs(target.position.x_mm - source.position.x_mm),
        "Y": abs(target.position.y_mm - source.position.y_mm),
        "Z": abs(target.position.z_mm - source.position.z_mm),
    }
    return max(deltas, key=deltas.get)

def _offset_for_axis(source: MechanicalPlacement, target: MechanicalPlacement, axis: str) -> float:
    if axis == "X":
        return target.position.x_mm - source.position.x_mm
    if axis == "Y":
        return target.position.y_mm - source.position.y_mm
    return target.position.z_mm - source.position.z_mm

def build_mechanical_render_data(ir: HardwareIR) -> HardwareIR:
    """Populate the live Three.js/R3F render contract when the agent output is sparse."""
    if not ir.mechanical or not ir.components:
        return ir

    dimensions = _infer_render_dimensions(ir)
    ir.mechanical.render_dimensions = dimensions

    existing_placement_refs = {placement.ref_des for placement in ir.mechanical.component_placements}
    generated_placements: List[MechanicalPlacement] = []
    for component in ir.components:
        if component.ref_des in existing_placement_refs:
            continue

        position = _placement_position(component, ir.components, dimensions)
        generated_placements.append(
            MechanicalPlacement(
                ref_des=component.ref_des,
                label=component.name,
                category=component.category,
                layer=_placement_layer(component),
                position=position,
                size=_placement_size(component, dimensions),
                orientation_deg=MechanicalRotation3(),
                mounting_face="front" if position.y_mm < -dimensions.y_mm * 0.32 else "internal",
                notes=component.rationale
            )
        )

    if generated_placements:
        ir.mechanical.component_placements = [
            *ir.mechanical.component_placements,
            *generated_placements,
        ]

    if not ir.mechanical.spatial_relationships:
        placements_by_ref = {placement.ref_des: placement for placement in ir.mechanical.component_placements}
        controller = next(
            (placement for placement in ir.mechanical.component_placements if (placement.category or "").lower() == "microcontroller"),
            None
        )
        relationships: List[MechanicalSpatialRelationship] = []

        if controller:
            for placement in ir.mechanical.component_placements:
                if placement.ref_des == controller.ref_des or placement.layer == "enclosure":
                    continue
                axis = _dominant_axis(controller, placement)
                relationships.append(MechanicalSpatialRelationship(
                    source_ref_des=controller.ref_des,
                    target_ref_des=placement.ref_des,
                    relation="spatial offset from controller",
                    axis=axis,
                    offset_mm=round(_offset_for_axis(controller, placement, axis), 2),
                    notes=f"{placement.ref_des} is placed along the {axis} axis relative to {controller.ref_des}."
                ))
                if len(relationships) >= 9:
                    break

        for placement in ir.mechanical.component_placements:
            text = f"{placement.ref_des} {placement.label or ''}".lower()
            if "display" in text or "oled" in text:
                bezel = next((candidate for candidate in ir.mechanical.component_placements if "bezel" in f"{candidate.label or ''}".lower()), None)
                if bezel and placement.ref_des != bezel.ref_des:
                    relationships.append(MechanicalSpatialRelationship(
                        source_ref_des=placement.ref_des,
                        target_ref_des=bezel.ref_des,
                        relation="aligned with display opening",
                        axis="Y",
                        offset_mm=round(bezel.position.y_mm - placement.position.y_mm, 2),
                        notes="Display centerline is aligned to the front bezel/window cutout."
                    ))
                    break

        ir.mechanical.spatial_relationships = [
            relationship
            for relationship in relationships
            if relationship.source_ref_des in placements_by_ref and relationship.target_ref_des in placements_by_ref
        ]

    metadata = ir.assembly_metadata or {}
    ir.assembly_metadata = {
        **metadata,
        "render_dimensions": dimensions.model_dump(),
        "component_placement_count": len(ir.mechanical.component_placements),
        "spatial_relationship_count": len(ir.mechanical.spatial_relationships),
        "render_pipeline": "Three.js + React Three Fiber",
    }
    return ir

# Define the ADK-style Multi-Agent Orchestrator
class HardwarePipelineOrchestrator:
    def __init__(self, use_simulation: bool = False):
        self.use_simulation = use_simulation or (client is None)
        self.requested_model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
        self.fallback_model = os.getenv("GEMINI_FALLBACK_MODEL", DEFAULT_GEMINI_FALLBACK_MODEL).strip() or DEFAULT_GEMINI_FALLBACK_MODEL
        self.strict_gemini = _env_bool("STRICT_GEMINI", default=True)
        self.model_name = self.requested_model
        self._model_validation: Optional[GeminiModelValidation] = None

    def validate_configured_model(self, *, raise_on_strict: bool = True) -> GeminiModelValidation:
        """Resolve and validate the Gemini model that should be used for generation."""
        if self._model_validation:
            if raise_on_strict and self._model_validation.validation_error and self.strict_gemini:
                raise GeminiModelConfigError(self._model_validation.validation_error)
            return self._model_validation

        if client is None:
            self._model_validation = GeminiModelValidation(
                requested_model=self.requested_model,
                actual_model=None,
                requested_model_available=False,
                strict_mode=self.strict_gemini,
                fallback_active=False,
                fallback_model=self.fallback_model,
                validation_error="Gemini client is not configured; live generation is running in simulation mode.",
            )
            return self._model_validation

        try:
            available_models = _list_generate_content_models()
        except Exception as exc:
            validation_error = f"Unable to validate Gemini model availability: {exc}"
            actual_model = self.fallback_model if not self.strict_gemini else None
            self._model_validation = GeminiModelValidation(
                requested_model=self.requested_model,
                actual_model=actual_model,
                requested_model_available=False,
                strict_mode=self.strict_gemini,
                fallback_active=not self.strict_gemini,
                fallback_model=self.fallback_model,
                validation_error=validation_error,
            )
            if self.strict_gemini and raise_on_strict:
                raise GeminiModelConfigError(validation_error)
            self.model_name = actual_model or self.requested_model
            return self._model_validation

        requested_available = _model_is_available(self.requested_model, available_models)
        if requested_available:
            self.model_name = self.requested_model
            self._model_validation = GeminiModelValidation(
                requested_model=self.requested_model,
                actual_model=self.model_name,
                requested_model_available=True,
                strict_mode=self.strict_gemini,
                fallback_active=False,
                fallback_model=self.fallback_model,
            )
            return self._model_validation

        if self.strict_gemini:
            validation_error = (
                UNAVAILABLE_GEMINI_35_FLASH_MESSAGE
                if self.requested_model == DEFAULT_GEMINI_MODEL
                else (
                    f"Configured model {self.requested_model} is not available for this API key/provider. "
                    "Check available models or configure a valid Gemini model ID."
                )
            )
            self._model_validation = GeminiModelValidation(
                requested_model=self.requested_model,
                actual_model=None,
                requested_model_available=False,
                strict_mode=True,
                fallback_active=False,
                fallback_model=self.fallback_model,
                validation_error=validation_error,
            )
            if raise_on_strict:
                raise GeminiModelConfigError(validation_error)
            return self._model_validation

        fallback_available = _model_is_available(self.fallback_model, available_models)
        if not fallback_available:
            validation_error = (
                f"Configured model {self.requested_model} is not available, and fallback model "
                f"{self.fallback_model} is not available for this API key/provider."
            )
            self._model_validation = GeminiModelValidation(
                requested_model=self.requested_model,
                actual_model=None,
                requested_model_available=False,
                strict_mode=False,
                fallback_active=False,
                fallback_model=self.fallback_model,
                validation_error=validation_error,
            )
            raise GeminiModelConfigError(validation_error)

        self.model_name = self.fallback_model
        self._model_validation = GeminiModelValidation(
            requested_model=self.requested_model,
            actual_model=self.model_name,
            requested_model_available=False,
            strict_mode=False,
            fallback_active=True,
            fallback_model=self.fallback_model,
        )
        return self._model_validation

    def get_debug_config(self) -> Dict[str, Any]:
        """Return Gemini model resolution details without exposing credentials."""
        return self.validate_configured_model(raise_on_strict=False).as_debug_dict()

    def _call_gemini_structured(self, prompt: str, schema_class: Any, image_bytes: Optional[bytes] = None, image_mime_type: Optional[str] = None) -> Any:
        """Helper to invoke Gemini with structured JSON schemas, supporting optional multimodal image input."""
        if self.use_simulation:
            raise RuntimeError("Simulation mode is active; should use simulated generator instead.")
            
        try:
            contents = []
            if image_bytes and image_mime_type:
                contents.append(types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type))
            contents.append(prompt)

            response = client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema_class,
                    temperature=0.2,
                )
            )
            return schema_class.model_validate_json(response.text)
        except Exception as e:
            logger.error(f"Gemini structured call failed: {e}")
            raise e

    def generate_project(self, user_prompt: str, image_bytes: Optional[bytes] = None, image_mime_type: Optional[str] = None) -> HardwareIR:
        """Orchestrates the 7-agent hardware compilation pipeline with verification loop."""
        # 0. Safety Guardrail Pre-check
        safety_error = check_safety_violations(user_prompt)
        if safety_error:
            logger.warning(f"Safety block triggered for prompt: '{user_prompt}'")
            # Compile a default safety-blocked IR package
            overview = ProjectOverview(
                title="PROJECT BLOCKED - Safe Scope Enforced",
                description="Your design compilation was blocked because it falls outside of the low-voltage, educational hardware MVP scope.",
                difficulty="N/A",
                estimated_cost=0.0,
                category="Safety Blocked"
            )
            issue = ValidationIssue(
                severity="CRITICAL",
                category="Safety Block",
                description=safety_error,
                troubleshooting="Please modify your design request to focus exclusively on safe, low-voltage educational electronics (e.g. Arduino, ESP32, low-voltage sensors, displays, standard 3V-5V DC relays or simple hobbyist motors)."
            )
            validation_summary = ValidationSummary(critical=[issue])
            
            project_ir = HardwareIR(
                hardware_ir_version="0.1",
                overview=overview,
                requirements=FunctionalRequirements(
                    requirements=["Compile blocked due to high-voltage, weapons, automotive, or clinical risk."],
                    power_needs="Blocked",
                    operating_voltage=0.0,
                    missing_info=["Blocked"]
                ),
                components=[],
                nets=[],
                buses=[],
                pin_mappings=[],
                assembly=[],
                mechanical=None,
                constraints=["Safety envelope enforcement"],
                power_rails=[],
                estimated_current_draw_ma=0.0,
                fabrication_notes=["Compilation blocked"],
                assembly_metadata={"status": "blocked"},
                project_version_history=[{"version": "0.1", "description": "Blocked design generation due to safety violations"}],
                validation=validation_summary,
                is_valid=False
            )
            # Save blocked run as record in database
            self.save_project_to_db(user_prompt, project_ir)
            return project_ir

        if self.use_simulation:
            return self._generate_simulated_project(user_prompt, has_image=bool(image_bytes))

        model_validation = self.validate_configured_model()

        try:
            logger.info("Starting 7-Agent Pipeline Execution...")
            
            # 1. Intent Parser Agent
            logger.info("Invoking Intent Parser Agent...")
            intent_prompt = f"""
            You are an Intent Parser Agent. Convert the user's idea and visual reference (if provided) into a structured hardware project overview.
            User Idea: "{user_prompt}"
            Generate the ProjectOverview schema containing title, description, difficulty, estimated cost (set to 0 for now), and category.
            """
            overview: ProjectOverview = self._call_gemini_structured(intent_prompt, ProjectOverview, image_bytes, image_mime_type)

            # 2. Requirements Agent
            logger.info("Invoking Requirements Agent...")
            req_prompt = f"""
            You are a Requirements Agent. Extract the functional requirements, power needs, physical constraints, operating voltage, safety notes, and missing information for this hardware project.
            User Idea: "{user_prompt}"
            Project Title: "{overview.title}"
            Project Description: "{overview.description}"
            Generate the FunctionalRequirements schema. Make sure to identify appropriate operating voltage (usually 3.3V or 5V depending on common microcontrollers like ESP32 or Arduino).
            """
            requirements: FunctionalRequirements = self._call_gemini_structured(req_prompt, FunctionalRequirements, image_bytes, image_mime_type)

            # 3. Component Selection Agent
            logger.info("Invoking Component Selection Agent...")
            db_components = get_db_component_templates()
            db_comp_json = json.dumps(db_components, indent=2)
            
            comp_prompt = f"""
            You are a Component Selection Agent.
            Your job is to select compatible components from our inventory database to fulfill the project's requirements.
            
            Requirements: {requirements.model_dump_json()}
            
            Here are the available components in our database with their pin definitions and prices:
            {db_comp_json}
            
            Select a suitable list of components. You MUST include a microcontroller (e.g., ESP32-WROOM-32D or Arduino-Nano-V3) and any sensors, actuators, displays, or passive/power parts needed.
            For each selected component, instantiate it as a ComponentInstance with:
            - ref_des: Unique ID like 'U1' (for MCUs), 'SEN1', 'ACT1', 'DISP1', 'R1', 'LED1', 'BAT1'
            - part_number: MUST match exactly one of the available part_numbers in the database list above.
            - name, category, quantity, unit_price, sourcing_url: Match the selected DB template.
            - rationale: Explain why this component is selected and how it fits.
            - pins: MUST match the exact list of pins from the template, including pin_id, name, pin_type, voltage.
            
            Output a JSON representation conforming to a List[ComponentInstance].
            """
            # Helper class to wrap list output
            class ComponentListWrapper(BaseModel):
                components: List[ComponentInstance]
                
            comp_wrapper: ComponentListWrapper = self._call_gemini_structured(comp_prompt, ComponentListWrapper, image_bytes, image_mime_type)
            components = comp_wrapper.components

            # Compile intermediate IR for wiring
            components_json = json.dumps([c.model_dump() for c in components], indent=2)

            # 4. Wiring/Netlist Agent (With Auto-Correction Loop)
            logger.info("Invoking Wiring/Netlist Agent...")
            wiring_prompt = f"""
            You are a Wiring/Netlist Agent. Your task is to connect the physical pins of the selected components to create a working circuit.
            
            Selected Components:
            {components_json}
            
            Requirements: {requirements.model_dump_json()}
            
            Rules for connecting:
            1. Establish a Ground rail (GND) net and connect all Ground/GND pins to it (e.g., ESP32 'GND', SSD1306 'GND', sensor 'GND', battery 'NEG').
            2. Establish a Power rail (VCC/3.3V/5V) net and connect VCC power pins to it. Make sure operating voltages match! Don't short 5V to 3.3V!
            3. Wire signal pins: Connect communication pins together:
               - I2C SCL connects to the MCU's SCL (e.g., ESP32 pin 'D22' or Arduino pin 'A5')
               - I2C SDA connects to the MCU's SDA (e.g., ESP32 pin 'D21' or Arduino pin 'A4')
               - Digital sensor data pins connect to any Digital/GPIO pin on the MCU.
               - PWM actuators connect to a PWM-capable pin on the MCU.
            4. Do NOT leave critical pins unconnected.
            
            Generate:
            - nets: List of ConnectionNet. Each net has net_id, name, net_type (Power, Ground, I2C, SPI, Digital, PWM, Analog), voltage, and pins (list of PinReference: ref_des + pin_id).
            - pin_mappings: List of PinMappingEntry mapping the MCU's pins to functional connections.
            
            Output a JSON representation of:
            """
            class WiringWrapper(BaseModel):
                nets: List[ConnectionNet]
                pin_mappings: List[PinMappingEntry]

            wiring_data: WiringWrapper = self._call_gemini_structured(wiring_prompt, WiringWrapper, image_bytes, image_mime_type)
            nets = wiring_data.nets
            pin_mappings = wiring_data.pin_mappings

            # Self-healing loop: Run validation checks on wiring
            logger.info("Running circuit validation checks on generated netlist...")
            validation_issues = validate_circuit(components, nets)
            is_valid = not any(issue.severity == "CRITICAL" for issue in validation_issues)

            if not is_valid:
                logger.warning("Critical circuit validation errors found! Triggering self-healing validation loop...")
                issues_json = json.dumps([issue.model_dump() for issue in validation_issues], indent=2)
                
                healing_prompt = f"""
                You are a Wiring/Netlist Auto-Correction Agent. The previous wiring configuration contained critical electrical or logical errors.
                
                Selected Components:
                {components_json}
                
                Previous Wiring Nets:
                {json.dumps([n.model_dump() for n in nets], indent=2)}
                
                Critical Validation Errors:
                {issues_json}
                
                Fix these connections!
                - If there's a Short Circuit (VCC connected to GND), separate them.
                - If there's a Voltage Mismatch (e.g. 5V logic connected to 3.3V), either suggest level conversion or use a compatible operating voltage / net.
                - If an IC is unpowered, connect its VCC and GND pins to the corresponding power/ground nets.
                - If a pin is reused in multiple signal nets, fix the mapping to separate GPIO pins.
                
                Generate a corrected list of ConnectionNet and PinMappingEntry.
                """
                corrected_wiring: WiringWrapper = self._call_gemini_structured(healing_prompt, WiringWrapper, image_bytes, image_mime_type)
                nets = corrected_wiring.nets
                pin_mappings = corrected_wiring.pin_mappings
                
                # Re-validate
                validation_issues = validate_circuit(components, nets)
                is_valid = not any(issue.severity == "CRITICAL" for issue in validation_issues)
                logger.info(f"Self-healing completed. Is valid: {is_valid}")

            # 5. BOM Agent
            logger.info("Invoking BOM Agent...")
            total_cost = sum(c.unit_price * c.quantity for c in components)
            overview.estimated_cost = round(total_cost, 2)

            # 6. Mechanical/Fabrication Agent
            logger.info("Invoking Mechanical/Fabrication Agent...")
            mech_prompt = f"""
            You are a Mechanical/Fabrication and CAD Sourcing Agent. Provide enclosure, mounting, material, and 3D printing/laser cutting details for this project.
            Project: "{overview.title}" - Description: "{overview.description}"
            Components Selected: {components_json}
            Populate the MechanicalNotes schema, including:
            - fabrication_cost_estimate_usd: realistic mechanical-only print/cut/enclosure cost, excluding electrical components.
            - cad_sources: CAD/enclosure/fabrication records with name, source_type, url, file_formats, license, estimated_unit_price_usd, and adaptation notes.
            - render_dimensions: overall X/Y/Z envelope dimensions in millimeters.
            - component_placements: live 3D render placements for relevant electrical and mechanical components. Use an enclosure-centered coordinate system: X is width left/right, Y is depth front/back, Z is height bottom/top. Include center position, approximate component size, visibility layer, mounting face, and notes.
            - spatial_relationships: important physical offsets and alignment relationships between placed components, with dominant X/Y/Z axis and offset_mm when known.
            Use source URLs only when they are known from supplied source data or a browsing/sourcing agent result. Do not invent URLs.
            Generate the MechanicalNotes schema.
            """
            mechanical: MechanicalNotes = self._call_gemini_structured(mech_prompt, MechanicalNotes, image_bytes, image_mime_type)

            # 7. Assembly Instruction Agent
            logger.info("Invoking Assembly Instruction Agent...")
            assembly_prompt = f"""
            You are an Assembly Instruction Agent. Produce step-by-step physical and electronic build instructions for the user.
            Project: "{overview.title}"
            Components: {components_json}
            Wiring Nets: {json.dumps([n.model_dump() for n in nets], indent=2)}
            Mechanical Guide: {mechanical.model_dump_json()}
            
            Provide structured sequential steps in the AssemblyStep schema (list of steps). Specify warnings/dangers where necessary.
            """
            class AssemblyWrapper(BaseModel):
                steps: List[AssemblyStep]
                
            assembly_wrapper: AssemblyWrapper = self._call_gemini_structured(assembly_prompt, AssemblyWrapper, image_bytes, image_mime_type)
            assembly = assembly_wrapper.steps

            # Dynamic field extractions
            power_rails = extract_power_rails(components, nets)
            buses = extract_buses(nets)
            current_draw = estimate_current_draw(components)
            constraints = requirements.physical_constraints + [f"Operating Voltage: {requirements.operating_voltage}V"]
            fab_notes = mechanical.fabrication_details if mechanical else []
            
            validation_summary = build_validation_summary(validation_issues)

            # Compile into final HardwareIR
            project_ir = HardwareIR(
                hardware_ir_version="0.1",
                overview=overview,
                requirements=requirements,
                components=components,
                nets=nets,
                buses=buses,
                pin_mappings=pin_mappings,
                assembly=assembly,
                mechanical=mechanical,
                constraints=constraints,
                power_rails=power_rails,
                estimated_current_draw_ma=current_draw,
                fabrication_notes=fab_notes,
            assembly_metadata={
                "generated_at": datetime.utcnow().isoformat(),
                "revision": 1,
                "model_name": self.model_name,
                "fallback_mode": model_validation.fallback_active,
                "requested_model": model_validation.requested_model,
                "actual_model": model_validation.actual_model,
                "pipeline": "Gemini multimodal + ADK-style hardware agents",
            },
                project_version_history=[{"version": "0.1", "description": "Initial design compilation via 7-agent ADK pipeline"}],
                validation=validation_summary,
                is_valid=is_valid
            )
            
            # Save generated project to DB
            project_ir = build_mechanical_render_data(project_ir)
            self.save_project_to_db(user_prompt, project_ir)
            return project_ir

        except GeminiModelConfigError:
            raise
        except Exception as e:
            logger.error(f"Pipeline execution encountered an error: {e}. Falling back to simulation.")
            return self._generate_simulated_project(user_prompt, has_image=bool(image_bytes))

    def save_project_to_db(self, prompt: str, ir: HardwareIR) -> str:
        """Saves a successfully generated HardwareIR to the PostgreSQL/SQLite database."""
        db = SessionLocal()
        try:
            import uuid
            project_id = f"proj_{uuid.uuid4().hex[:8]}"
            
            db_project = DBGeneratedProject(
                project_id=project_id,
                title=ir.overview.title,
                prompt=prompt,
                hardware_ir=ir.model_dump(),
                created_at=datetime.utcnow().isoformat()
            )
            db.add(db_project)
            db.commit()
            logger.info(f"Project saved to database with ID: {project_id}")
            return project_id
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to save project to database: {e}")
            return ""
        finally:
            db.close()

    def _generate_simulated_project(self, prompt: str, has_image: bool = False) -> HardwareIR:
        """High-fidelity, deterministic simulated generator used as fallback or when GEMINI_API_KEY is not configured."""
        logger.info(f"Generating simulated project package for: '{prompt}'")
        
        prompt_lower = prompt.lower()
        if has_image or "mp3" in prompt_lower or "music" in prompt_lower or "audio" in prompt_lower or "player" in prompt_lower or "pocket" in prompt_lower:
            return self._load_simulated_mp3_player_project(prompt)
        elif "water" in prompt_lower or "plant" in prompt_lower or "soil" in prompt_lower or "garden" in prompt_lower:
            return self._load_simulated_watering_project(prompt)
        elif "thermostat" in prompt_lower or "temperature" in prompt_lower or "weather" in prompt_lower:
            return self._load_simulated_thermostat_project(prompt)
        else:
            return self._load_simulated_smart_lock_project(prompt)

    def _load_simulated_mp3_player_project(self, prompt: str) -> HardwareIR:
        """Reference-style Blueprint project used for prompt+image MP3 player examples."""
        def pin(pin_id: str, name: str, pin_type: str, voltage: Optional[float] = None) -> PinDefinition:
            return PinDefinition(pin_id=pin_id, name=name, pin_type=pin_type, voltage=voltage, description=name)

        overview = ProjectOverview(
            title="Pocket MP3 Player",
            description=f"A slim, portable MP3 player powered by an ESP32 audio stack and compiled for: '{prompt}'",
            difficulty="Intermediate",
            estimated_cost=45.05,
            category="Portable Audio"
        )
        requirements = FunctionalRequirements(
            requirements=[
                "Play local MP3 files from expandable microSD storage.",
                "Show track, battery, and playback state on a small color display.",
                "Provide physical play/pause, next, and previous buttons.",
                "Charge a compact Li-Po battery through USB-C and expose a 3.5mm audio output."
            ],
            power_needs="3.7V Li-Po battery with USB-C charging and protected 3.3V logic regulation.",
            operating_voltage=3.3,
            physical_constraints=[
                "Slim and portable",
                "Pocket-sized",
                "Rectangular body",
                "Rounded edges",
                "Color display",
                "Physical buttons",
                "USB-C connectivity",
                "Bluetooth integration",
                "Minimalist design",
                "Lightweight",
                "Ergonomic"
            ],
            safety_notes=[
                "Use a protected Li-Po charging module and verify battery polarity before power-up.",
                "Keep audio amplifier output isolated from logic pins and enclosure fasteners."
            ],
            missing_info=[]
        )

        components = [
            ComponentInstance(
                ref_des="U1",
                part_number="ESP32-WROOM-32E",
                name="ESP32-WROOM-32E Module",
                category="Microcontroller",
                quantity=1,
                unit_price=5.50,
                rationale="Main Controller. Provides Wi-Fi, Bluetooth, and enough GPIO for display, storage, buttons, and audio control.",
                pins=[
                    pin("3V3", "3.3V Power", "Power", 3.3),
                    pin("GND", "Ground", "Ground", 0.0),
                    pin("GPIO21", "I2C SDA", "I2C", 3.3),
                    pin("GPIO22", "I2C SCL", "I2C", 3.3),
                    pin("GPIO18", "SPI SCK", "SPI", 3.3),
                    pin("GPIO23", "SPI MOSI", "SPI", 3.3),
                    pin("GPIO19", "SPI MISO", "SPI", 3.3),
                    pin("GPIO5", "SD CS", "Digital", 3.3),
                    pin("GPIO25", "Audio BCLK", "Digital", 3.3),
                    pin("GPIO26", "Audio LRCLK", "Digital", 3.3),
                    pin("GPIO27", "Audio DIN", "Digital", 3.3),
                    pin("GPIO32", "Play/Pause", "Digital", 3.3),
                    pin("GPIO33", "Next", "Digital", 3.3),
                    pin("GPIO34", "Previous", "Digital", 3.3)
                ]
            ),
            ComponentInstance(
                ref_des="SPK1",
                part_number="SPK-8OHM-1W",
                name="8 Ohm 1W Mini Speaker",
                category="Actuator",
                quantity=1,
                unit_price=2.00,
                rationale="Small Internal Speaker. Provides onboard audio playback without headphones.",
                pins=[pin("SPK+", "Speaker Positive", "Passive"), pin("SPK-", "Speaker Negative", "Passive")]
            ),
            ComponentInstance(
                ref_des="BAT1",
                part_number="LiPo-3V7-500mAh",
                name="3.7V 500mAh Li-Po Battery",
                category="Power",
                quantity=1,
                unit_price=8.00,
                rationale="Rechargeable Power Source. Small battery for a pocket-sized portable design.",
                pins=[pin("POS", "Battery Positive", "Power", 3.7), pin("NEG", "Battery Negative", "Ground", 0.0)]
            ),
            ComponentInstance(
                ref_des="PWR1",
                part_number="TP4056-USB-C",
                name="TP4056 USB-C Li-Ion Charger Module",
                category="Power",
                quantity=1,
                unit_price=3.00,
                rationale="Li-Po Charging & Power Management. Charges the battery and provides protection.",
                pins=[
                    pin("USB5V", "USB-C 5V Input", "Power", 5.0),
                    pin("BAT+", "Battery Charge Positive", "Power", 3.7),
                    pin("BAT-", "Battery Charge Negative", "Ground", 0.0),
                    pin("OUT+", "Protected Positive Output", "Power", 3.3),
                    pin("OUT-", "Protected Ground Output", "Ground", 0.0)
                ]
            ),
            ComponentInstance(ref_des="BTN1", part_number="TACT-6MM", name="Play/Pause Button", category="Passives", quantity=1, unit_price=0.35, rationale="Primary transport control.", pins=[pin("A", "Switch A", "Passive"), pin("B", "Switch B", "Passive")]),
            ComponentInstance(ref_des="BTN2", part_number="TACT-6MM", name="Next Track Button", category="Passives", quantity=1, unit_price=0.35, rationale="Advances to the next track.", pins=[pin("A", "Switch A", "Passive"), pin("B", "Switch B", "Passive")]),
            ComponentInstance(ref_des="BTN3", part_number="TACT-6MM", name="Previous Track Button", category="Passives", quantity=1, unit_price=0.35, rationale="Returns to the previous track.", pins=[pin("A", "Switch A", "Passive"), pin("B", "Switch B", "Passive")]),
            ComponentInstance(
                ref_des="SD1",
                part_number="MICROSD-SPI",
                name="Expandable Storage Module",
                category="Passives",
                quantity=1,
                unit_price=2.50,
                rationale="Stores MP3 files on removable media.",
                pins=[
                    pin("VCC", "3.3V Power", "Power", 3.3),
                    pin("GND", "Ground", "Ground", 0.0),
                    pin("CS", "Chip Select", "Digital", 3.3),
                    pin("SCK", "Clock", "SPI", 3.3),
                    pin("MOSI", "Data In", "SPI", 3.3),
                    pin("MISO", "Data Out", "SPI", 3.3)
                ]
            ),
            ComponentInstance(ref_des="J1", part_number="TRS-3.5MM", name="3.5mm Headphone Output", category="Passives", quantity=1, unit_price=1.20, rationale="External analog audio output.", pins=[pin("L", "Left Audio", "Passive"), pin("R", "Right Audio", "Passive"), pin("GND", "Audio Ground", "Ground", 0.0)]),
            ComponentInstance(
                ref_des="DAC1",
                part_number="I2S-DAC-AMP",
                name="Audio DAC with Amplifier",
                category="Actuator",
                quantity=1,
                unit_price=4.20,
                rationale="Decodes I2S digital audio and drives speaker/headphone output.",
                pins=[
                    pin("VCC", "3.3V Power", "Power", 3.3),
                    pin("GND", "Ground", "Ground", 0.0),
                    pin("BCLK", "Bit Clock", "Digital", 3.3),
                    pin("LRCLK", "Word Select", "Digital", 3.3),
                    pin("DIN", "Audio Data", "Digital", 3.3),
                    pin("SPK+", "Speaker Positive", "Passive"),
                    pin("SPK-", "Speaker Negative", "Passive"),
                    pin("OUTL", "Headphone Left", "Passive"),
                    pin("OUTR", "Headphone Right", "Passive")
                ]
            ),
            ComponentInstance(ref_des="USB1", part_number="USB-C-16P", name="USB-C Data & Charging Port", category="Power", quantity=1, unit_price=1.50, rationale="USB-C connectivity for charging and file transfer.", pins=[pin("VBUS", "USB 5V", "Power", 5.0), pin("GND", "USB Ground", "Ground", 0.0), pin("D+", "USB Data Plus", "Digital", 3.3), pin("D-", "USB Data Minus", "Digital", 3.3)]),
            ComponentInstance(
                ref_des="DISP1",
                part_number="OLED-COLOR-1.3",
                name="Track Information Display",
                category="Display",
                quantity=1,
                unit_price=9.00,
                rationale="Color display for waveform, battery, and track metadata.",
                pins=[pin("VCC", "3.3V Power", "Power", 3.3), pin("GND", "Ground", "Ground", 0.0), pin("SDA", "I2C SDA", "I2C", 3.3), pin("SCL", "I2C SCL", "I2C", 3.3)]
            ),
            ComponentInstance(ref_des="MCAP1", part_number="CAP-PLAY", name="Play/Pause Button Cap", category="Mechanical", quantity=1, unit_price=0.30, rationale="Tactile exterior control cap.", pins=[]),
            ComponentInstance(ref_des="MCAP2", part_number="CAP-NEXT", name="Next Track Button Cap", category="Mechanical", quantity=1, unit_price=0.30, rationale="Tactile exterior control cap.", pins=[]),
            ComponentInstance(ref_des="MCAP3", part_number="CAP-PREV", name="Previous Track Button Cap", category="Mechanical", quantity=1, unit_price=0.30, rationale="Tactile exterior control cap.", pins=[]),
            ComponentInstance(ref_des="SCR1", part_number="M2-SCREW", name="M2 Enclosure Screws", category="Mechanical", quantity=4, unit_price=0.20, rationale="Secures shell, bezel, and cover.", pins=[]),
            ComponentInstance(ref_des="INS1", part_number="M2-INSERT", name="M2 Heat-Set Inserts", category="Mechanical", quantity=4, unit_price=0.225, rationale="Reusable threaded mounting points.", pins=[]),
            ComponentInstance(ref_des="MECH1", part_number="MP3-SHELL", name="Main Enclosure Shell", category="3D Print", quantity=1, unit_price=2.10, rationale="Primary rounded pocket enclosure.", pins=[]),
            ComponentInstance(ref_des="MECH2", part_number="MP3-FRONT", name="Front Bezel", category="3D Print", quantity=1, unit_price=0.60, rationale="Faceplate for display and controls.", pins=[]),
            ComponentInstance(ref_des="MECH3", part_number="MP3-BACK", name="Back Cover with Speaker Vents", category="3D Print", quantity=1, unit_price=1.00, rationale="Rear cover with acoustic grille.", pins=[]),
            ComponentInstance(ref_des="MECH4", part_number="OLED-MOUNT", name="OLED Display Bezel Mount", category="3D Print", quantity=1, unit_price=0.40, rationale="Aligns display behind window.", pins=[]),
            ComponentInstance(ref_des="MECH5", part_number="ESP32-MOUNT", name="ESP32 Main Controller Mount", category="3D Print", quantity=1, unit_price=0.40, rationale="Internal board mounting platform.", pins=[]),
        ]

        nets = [
            ConnectionNet(net_id="NET_GND", name="System Ground", net_type="Ground", voltage=0.0, pins=[
                PinReference(ref_des="U1", pin_id="GND"), PinReference(ref_des="BAT1", pin_id="NEG"),
                PinReference(ref_des="PWR1", pin_id="BAT-"), PinReference(ref_des="PWR1", pin_id="OUT-"),
                PinReference(ref_des="SD1", pin_id="GND"), PinReference(ref_des="DAC1", pin_id="GND"),
                PinReference(ref_des="USB1", pin_id="GND"), PinReference(ref_des="DISP1", pin_id="GND"),
                PinReference(ref_des="J1", pin_id="GND")
            ]),
            ConnectionNet(net_id="NET_BAT", name="Li-Po Battery Bus", net_type="Power", voltage=3.7, pins=[
                PinReference(ref_des="BAT1", pin_id="POS"), PinReference(ref_des="PWR1", pin_id="BAT+")
            ]),
            ConnectionNet(net_id="NET_USB_5V", name="USB-C Charge Input", net_type="Power", voltage=5.0, pins=[
                PinReference(ref_des="USB1", pin_id="VBUS"), PinReference(ref_des="PWR1", pin_id="USB5V")
            ]),
            ConnectionNet(net_id="NET_3V3", name="Protected 3.3V Logic Rail", net_type="Power", voltage=3.3, pins=[
                PinReference(ref_des="PWR1", pin_id="OUT+"), PinReference(ref_des="U1", pin_id="3V3"),
                PinReference(ref_des="SD1", pin_id="VCC"), PinReference(ref_des="DAC1", pin_id="VCC"),
                PinReference(ref_des="DISP1", pin_id="VCC")
            ]),
            ConnectionNet(net_id="NET_I2C_SDA", name="Display SDA", net_type="I2C", voltage=3.3, pins=[
                PinReference(ref_des="U1", pin_id="GPIO21"), PinReference(ref_des="DISP1", pin_id="SDA")
            ]),
            ConnectionNet(net_id="NET_I2C_SCL", name="Display SCL", net_type="I2C", voltage=3.3, pins=[
                PinReference(ref_des="U1", pin_id="GPIO22"), PinReference(ref_des="DISP1", pin_id="SCL")
            ]),
            ConnectionNet(net_id="NET_SPI_SCK", name="microSD SPI Clock", net_type="SPI", voltage=3.3, pins=[
                PinReference(ref_des="U1", pin_id="GPIO18"), PinReference(ref_des="SD1", pin_id="SCK")
            ]),
            ConnectionNet(net_id="NET_SPI_MOSI", name="microSD SPI MOSI", net_type="SPI", voltage=3.3, pins=[
                PinReference(ref_des="U1", pin_id="GPIO23"), PinReference(ref_des="SD1", pin_id="MOSI")
            ]),
            ConnectionNet(net_id="NET_SPI_MISO", name="microSD SPI MISO", net_type="SPI", voltage=3.3, pins=[
                PinReference(ref_des="U1", pin_id="GPIO19"), PinReference(ref_des="SD1", pin_id="MISO")
            ]),
            ConnectionNet(net_id="NET_SD_CS", name="microSD Chip Select", net_type="Digital", voltage=3.3, pins=[
                PinReference(ref_des="U1", pin_id="GPIO5"), PinReference(ref_des="SD1", pin_id="CS")
            ]),
            ConnectionNet(net_id="NET_AUDIO_BCLK", name="I2S Bit Clock", net_type="Digital", voltage=3.3, pins=[
                PinReference(ref_des="U1", pin_id="GPIO25"), PinReference(ref_des="DAC1", pin_id="BCLK")
            ]),
            ConnectionNet(net_id="NET_AUDIO_LRCLK", name="I2S Word Select", net_type="Digital", voltage=3.3, pins=[
                PinReference(ref_des="U1", pin_id="GPIO26"), PinReference(ref_des="DAC1", pin_id="LRCLK")
            ]),
            ConnectionNet(net_id="NET_AUDIO_DIN", name="I2S Audio Data", net_type="Digital", voltage=3.3, pins=[
                PinReference(ref_des="U1", pin_id="GPIO27"), PinReference(ref_des="DAC1", pin_id="DIN")
            ]),
            ConnectionNet(net_id="NET_SPK", name="Amplified Speaker Output", net_type="Analog", voltage=None, pins=[
                PinReference(ref_des="DAC1", pin_id="SPK+"), PinReference(ref_des="SPK1", pin_id="SPK+"),
                PinReference(ref_des="DAC1", pin_id="SPK-"), PinReference(ref_des="SPK1", pin_id="SPK-")
            ]),
        ]

        pin_mappings = [
            PinMappingEntry(mcu_pin="GPIO21/GPIO22", connected_to="Color display I2C bus", net_name="NET_I2C_SDA/SCL"),
            PinMappingEntry(mcu_pin="GPIO18/GPIO23/GPIO19/GPIO5", connected_to="microSD SPI module", net_name="NET_SPI"),
            PinMappingEntry(mcu_pin="GPIO25/GPIO26/GPIO27", connected_to="I2S DAC amplifier", net_name="NET_AUDIO_I2S"),
            PinMappingEntry(mcu_pin="GPIO32/GPIO33/GPIO34", connected_to="Playback control buttons", net_name="NET_BUTTONS")
        ]
        assembly = [
            AssemblyStep(step_num=1, title="Print enclosure set", description="Print the main shell, front bezel, back cover, OLED mount, and ESP32 mount in matte PLA or PETG with the button cap tolerances preserved.", affected_components=["MECH1", "MECH2", "MECH3", "MECH4", "MECH5"]),
            AssemblyStep(step_num=2, title="Build power subsystem", description="Connect the Li-Po cell to the TP4056 charger module and route protected 3.3V output to the ESP32, display, DAC, and microSD module.", danger_flag=True, danger_message="Verify Li-Po polarity before plugging in USB-C power.", affected_components=["BAT1", "PWR1", "USB1"]),
            AssemblyStep(step_num=3, title="Wire display, storage, and audio", description="Wire the display over I2C, the microSD module over SPI, and the DAC amplifier over I2S. Keep speaker leads away from the antenna side of the ESP32.", affected_components=["DISP1", "SD1", "DAC1", "SPK1", "U1"]),
            AssemblyStep(step_num=4, title="Install buttons and close enclosure", description="Mount the tactile switches behind the front controls, press-fit the printed caps, install heat-set inserts, and close the enclosure with M2 screws.", affected_components=["BTN1", "BTN2", "BTN3", "MCAP1", "MCAP2", "MCAP3", "SCR1", "INS1"])
        ]
        mechanical = MechanicalNotes(
            enclosure_type="3D Printed Rounded Pocket Enclosure",
            mounting_guidance="Use internal bosses for the ESP32, DAC, charger, microSD module, and OLED bezel. Route USB-C and headphone cutouts to the short edges.",
            fabrication_details=[
                "Overall body target: 100mm x 54mm x 21mm.",
                "2.0mm wall thickness with 0.4mm display lip.",
                "Heat-set M2 inserts for serviceable back cover.",
                "Speaker vent slots on rear or side wall."
            ],
            fabrication_cost_estimate_usd=6.00,
            cad_sources=[
                MechanicalSource(
                    name="Adafruit Walkmp3rson CAD files",
                    source_type="Reference CAD",
                    url="https://learn.adafruit.com/walkmp3rson-personal-mp3-tape-player/cad-files",
                    file_formats=["STL", "STEP", "Fusion 360"],
                    license="Adafruit Learn reference design; verify reuse terms on source page",
                    estimated_unit_price_usd=0.00,
                    notes="Use as a portable audio enclosure reference and adapt cutouts for ESP32, OLED, USB-C, buttons, and speaker."
                ),
                MechanicalSource(
                    name="ESP32 Project Enclosure with OLED SSD1306",
                    source_type="Open STL",
                    url="https://3dgo.app/models/printables/94864",
                    file_formats=["STL"],
                    license="Creative Commons attribution-sharealike listing mirror; verify original Printables license",
                    estimated_unit_price_usd=0.00,
                    notes="Good reference for ESP32/OLED internal mounting and display bezel geometry."
                )
            ],
            manufacturability_rating="Moderate"
        )
        validation_issues = validate_circuit(components, nets)
        validation_summary = build_validation_summary(validation_issues)
        project_ir = HardwareIR(
            hardware_ir_version="0.1",
            overview=overview,
            requirements=requirements,
            components=components,
            nets=nets,
            buses=extract_buses(nets),
            pin_mappings=pin_mappings,
            assembly=assembly,
            mechanical=mechanical,
            constraints=requirements.physical_constraints,
            power_rails=extract_power_rails(components, nets),
            estimated_current_draw_ma=180.0,
            fabrication_notes=mechanical.fabrication_details,
            assembly_metadata={
                "status": "active",
                "model_name": self.model_name,
                "pipeline": "Gemini multimodal + ADK-style hardware agents",
                "product_visual": "pocket_mp3_player",
                "image_features": requirements.physical_constraints,
                "render_dimensions": {"x_mm": 100, "y_mm": 21, "z_mm": 54}
            },
            project_version_history=[{"version": "0.1", "description": "Initial prompt and image driven MP3 player design"}],
            validation=validation_summary,
            is_valid=not any(issue.severity.upper() == "CRITICAL" for issue in validation_issues)
        )
        project_ir = build_mechanical_render_data(project_ir)
        self.save_project_to_db(prompt, project_ir)
        return project_ir

    def _load_simulated_watering_project(self, prompt: str) -> HardwareIR:
        overview = ProjectOverview(
            title="Auto-Grow Plant Moisture Monitor & Watering System",
            description=f"An automated soil-sensing irrigation and environment dashboard compiled for: '{prompt}'",
            difficulty="Intermediate",
            estimated_cost=11.00,
            category="Smart Home"
        )
        requirements = FunctionalRequirements(
            requirements=[
                "Monitor real-time soil moisture and environmental temperature.",
                "Turn on a 5V relay to activate an irrigation water pump when moisture drops below 30%.",
                "Display current soil and environmental readings on a sharp 0.96 inch OLED screen.",
                "Log all data points and warnings over a WiFi-connected database endpoint."
            ],
            power_needs="5V USB Wall Supply, powering MCU, Relay, and OLED screen.",
            operating_voltage=3.3,
            physical_constraints=["Water-resistant sensor probes.", "Enclosure footprint under 10x10x5cm."],
            safety_notes=["Keep relay AC connection/switching terminals isolated from water lines.", "Operate pump with separate power grounds if electrical noise interferes with readings."],
            missing_info=[]
        )
        
        components = [
            ComponentInstance(
                ref_des="U1",
                part_number="ESP32-WROOM-32D",
                name="ESP32 NodeMCU Development Board",
                category="Microcontroller",
                quantity=1,
                unit_price=4.50,
                rationale="Provides dual core processor, WiFi connectivity, and plenty of GPIOs for sensor and relay control.",
                pins=self._get_pins_for_part("ESP32-WROOM-32D")
            ),
            ComponentInstance(
                ref_des="SEN1",
                part_number="DHT22",
                name="DHT22 Temperature & Humidity Sensor",
                category="Sensor",
                quantity=1,
                unit_price=2.80,
                rationale="Collects environmental temperature and relative humidity to guard against heat-stress.",
                pins=self._get_pins_for_part("DHT22")
            ),
            ComponentInstance(
                ref_des="ACT1",
                part_number="Relay-5V-1Ch",
                name="5V 1-Channel Optocoupled Relay Module",
                category="Actuator",
                quantity=1,
                unit_price=1.20,
                rationale="Safely switches power to the 5V water pump actuator using low-voltage ESP32 GPIO pins.",
                pins=self._get_relay_pins_3v3_input()
            ),
            ComponentInstance(
                ref_des="DISP1",
                part_number="SSD1306-I2C",
                name="0.96 inch OLED Display (I2C)",
                category="Display",
                quantity=1,
                unit_price=2.50,
                rationale="Displays quick status updates, soil moisture %, and humidity readouts locally.",
                pins=self._get_pins_for_part("SSD1306-I2C")
            ),
            ComponentInstance(
                ref_des="PWR1",
                part_number="USB-5V-Plug",
                name="5V USB Wall Power Supply",
                category="Power",
                quantity=1,
                unit_price=1.50,
                rationale="Provides the regulated 5V rail feeding ESP32 VIN and the relay coil supply.",
                pins=self._get_pins_for_part("USB-5V-Plug")
            )
        ]

        overview.estimated_cost = sum(c.unit_price * c.quantity for c in components)

        nets = [
            ConnectionNet(
                net_id="NET_GND",
                name="System Ground",
                net_type="Ground",
                voltage=0.0,
                pins=[
                    PinReference(ref_des="U1", pin_id="GND"),
                    PinReference(ref_des="SEN1", pin_id="GND"),
                    PinReference(ref_des="ACT1", pin_id="GND"),
                    PinReference(ref_des="DISP1", pin_id="GND"),
                    PinReference(ref_des="PWR1", pin_id="GND")
                ]
            ),
            ConnectionNet(
                net_id="NET_3V3",
                name="3.3V Power Rail",
                net_type="Power",
                voltage=3.3,
                pins=[
                    PinReference(ref_des="U1", pin_id="3V3"),
                    PinReference(ref_des="SEN1", pin_id="VCC"),
                    PinReference(ref_des="DISP1", pin_id="VCC")
                ]
            ),
            ConnectionNet(
                net_id="NET_5V",
                name="5V Power Rail",
                net_type="Power",
                voltage=5.0,
                pins=[
                    PinReference(ref_des="PWR1", pin_id="5V"),
                    PinReference(ref_des="U1", pin_id="VIN"),
                    PinReference(ref_des="ACT1", pin_id="VCC")
                ]
            ),
            ConnectionNet(
                net_id="NET_I2C_SDA",
                name="I2C Serial Data",
                net_type="I2C",
                voltage=3.3,
                pins=[
                    PinReference(ref_des="U1", pin_id="D21"),
                    PinReference(ref_des="DISP1", pin_id="SDA")
                ]
            ),
            ConnectionNet(
                net_id="NET_I2C_SCL",
                name="I2C Serial Clock",
                net_type="I2C",
                voltage=3.3,
                pins=[
                    PinReference(ref_des="U1", pin_id="D22"),
                    PinReference(ref_des="DISP1", pin_id="SCL")
                ]
            ),
            ConnectionNet(
                net_id="NET_DHT_DATA",
                name="DHT22 Sensor Connection",
                net_type="Digital",
                voltage=3.3,
                pins=[
                    PinReference(ref_des="U1", pin_id="D27"),
                    PinReference(ref_des="SEN1", pin_id="DATA")
                ]
            ),
            ConnectionNet(
                net_id="NET_RELAY_IN",
                name="Relay Command Line",
                net_type="Digital",
                voltage=3.3,
                pins=[
                    PinReference(ref_des="U1", pin_id="D25"),
                    PinReference(ref_des="ACT1", pin_id="IN")
                ]
            )
        ]

        pin_mappings = [
            PinMappingEntry(mcu_pin="D21", connected_to="SSD1306 SDA Pin", net_name="NET_I2C_SDA"),
            PinMappingEntry(mcu_pin="D22", connected_to="SSD1306 SCL Pin", net_name="NET_I2C_SCL"),
            PinMappingEntry(mcu_pin="D27", connected_to="DHT22 Sensor Pin", net_name="NET_DHT_DATA"),
            PinMappingEntry(mcu_pin="D25", connected_to="Relay Switch Command", net_name="NET_RELAY_IN")
        ]

        assembly = [
            AssemblyStep(
                step_num=1,
                title="Prepare Components & Power rails",
                description="Place the ESP32 board onto a half-sized breadboard. Connect PWR1 5V to ESP32 VIN and the relay VCC rail, then tie PWR1 GND to the shared ground rail. Connect ESP32 3V3 to the low-voltage sensor/display rail.",
                danger_flag=False,
                affected_components=["U1", "PWR1"]
            ),
            AssemblyStep(
                step_num=2,
                title="Assemble & Power the DHT22 Temperature Sensor",
                description="Connect DHT22 Pin 1 (VCC) to the 3.3V breadboard rail. Connect Pin 4 (GND) to the GND rail. Connect DHT22 Pin 2 (DATA) directly to the ESP32 pin D27. Place a 10k resistor between VCC and DATA to act as an external pull-up if necessary.",
                danger_flag=False,
                affected_components=["SEN1", "U1"]
            ),
            AssemblyStep(
                step_num=3,
                title="Wire up the SSD1306 OLED Display over I2C",
                description="Mount the OLED screen. Run VCC to 3.3V, GND to GND, SDA to ESP32 Pin D21, and SCL to ESP32 Pin D22. This sets up the serial hardware bus.",
                danger_flag=False,
                affected_components=["DISP1", "U1"]
            ),
            AssemblyStep(
                step_num=4,
                title="Install and Wire the 5V Relay Module",
                description="Connect Relay VCC to the PWR1 5V rail and GND to system ground. Connect the 3.3V-compatible signal input (IN) of the relay to ESP32 Pin D25.",
                danger_flag=True,
                danger_message="Never connect AC mains electricity to the relay terminals without proper enclosure insulated blocks!",
                affected_components=["ACT1", "U1"]
            )
        ]

        mechanical = MechanicalNotes(
            enclosure_type="3D Printed",
            mounting_guidance="Use M3 brass standoffs inside a pre-measured PLA project box. Drill rubber routing holes for moisture probes and power.",
            fabrication_details=[
                "Wall thickness: 2.0mm.",
                "Infill: 20% grid pattern.",
                "Material: Green or Black PLA.",
                "Ventilation grills on the side of the housing to keep the DHT22 breathing correctly."
            ],
            fabrication_cost_estimate_usd=10.00,
            cad_sources=[
                MechanicalSource(
                    name="Hammond 1554BGY watertight enclosure CAD",
                    source_type="Vendor CAD",
                    url="https://www.digikey.ca/en/products/detail/hammond-manufacturing/1554BGY/1090730",
                    file_formats=["STEP", "IGES"],
                    license="Vendor CAD reference for Hammond 1554BGY; verify distributor terms",
                    estimated_unit_price_usd=18.93,
                    notes="Use as an off-the-shelf sealed enclosure baseline; drill only low-side cable glands for probes and power."
                ),
                MechanicalSource(
                    name="ESP32 Project Enclosure with OLED SSD1306",
                    source_type="Open STL",
                    url="https://3dgo.app/models/printables/94864",
                    file_formats=["STL"],
                    license="Creative Commons attribution-sharealike listing mirror; verify original Printables license",
                    estimated_unit_price_usd=0.00,
                    notes="Reference display bezel and ESP32 standoff placement if a fully printed enclosure is preferred."
                )
            ],
            manufacturability_rating="Easy"
        )

        validation_issues = validate_circuit(components, nets)
        validation_summary = build_validation_summary(validation_issues)
        power_rails = extract_power_rails(components, nets)
        buses = extract_buses(nets)
        current_draw = estimate_current_draw(components)

        project_ir = HardwareIR(
            hardware_ir_version="0.1",
            overview=overview,
            requirements=requirements,
            components=components,
            nets=nets,
            buses=buses,
            pin_mappings=pin_mappings,
            assembly=assembly,
            mechanical=mechanical,
            constraints=requirements.physical_constraints,
            power_rails=power_rails,
            estimated_current_draw_ma=current_draw,
            fabrication_notes=mechanical.fabrication_details,
            assembly_metadata={
                "status": "active",
                "schematic": {
                    "canvas": {"width": 1180, "height": 720},
                    "placements": {
                        "PWR1": {"x": 80, "y": 80},
                        "U1": {"x": 460, "y": 300},
                        "SEN1": {"x": 250, "y": 410},
                        "DISP1": {"x": 870, "y": 380},
                        "ACT1": {"x": 870, "y": 120}
                    }
                }
            },
            project_version_history=[{"version": "0.1", "description": "Initial fallback design generation"}],
            validation=validation_summary,
            is_valid=not any(issue.severity.upper() == "CRITICAL" for issue in validation_issues)
        )

        project_ir = build_mechanical_render_data(project_ir)
        self.save_project_to_db(prompt, project_ir)
        return project_ir

    def _load_simulated_thermostat_project(self, prompt: str) -> HardwareIR:
        overview = ProjectOverview(
            title="Smart Nest-Style Environmental Thermostat Controller",
            description=f"Intelligent wall-mounted environment controller with climate regulation compiled for: '{prompt}'",
            difficulty="Intermediate",
            estimated_cost=11.50,
            category="Smart Home"
        )
        requirements = FunctionalRequirements(
            requirements=[
                "Collect high-precision altitude, pressure, and ambient temperature readings.",
                "Display custom menu, heat indices, and setpoint targets on OLED.",
                "Actuate solid-state heating elements or fan control through an optoisolated relay switch.",
                "Maintain low-power standby during battery backups."
            ],
            power_needs="5V stationary adapter feed. Rechargeable battery backup requires a charger/boost module not represented in this low-voltage wiring preset.",
            operating_voltage=3.3,
            physical_constraints=["Standard wall box mounting.", "Total weight under 150g."],
            safety_notes=["Incorporate thermal fusing if controlling resistive heater elements.", "Double check voltage bounds before wiring rechargeable LiPos."],
            missing_info=[]
        )
        components = [
            ComponentInstance(
                ref_des="U1",
                part_number="ESP32-WROOM-32D",
                name="ESP32 NodeMCU Development Board",
                category="Microcontroller",
                quantity=1,
                unit_price=4.50,
                rationale="Onboard Bluetooth and WiFi allow web setpoints, scheduling, and logging integrations.",
                pins=self._get_pins_for_part("ESP32-WROOM-32D")
            ),
            ComponentInstance(
                ref_des="SEN1",
                part_number="BMP280",
                name="BMP280 Barometric Pressure & Temp Sensor",
                category="Sensor",
                quantity=1,
                unit_price=1.80,
                rationale="Performs precision temperature tracking to regulate comfortable setpoints within 0.1C.",
                pins=self._get_pins_for_part("BMP280")
            ),
            ComponentInstance(
                ref_des="ACT1",
                part_number="Relay-5V-1Ch",
                name="5V 1-Channel Optocoupled Relay Module",
                category="Actuator",
                quantity=1,
                unit_price=1.20,
                rationale="Switches active HVAC furnace control logic safely.",
                pins=self._get_relay_pins_3v3_input()
            ),
            ComponentInstance(
                ref_des="DISP1",
                part_number="SSD1306-I2C",
                name="0.96 inch OLED Display (I2C)",
                category="Display",
                quantity=1,
                unit_price=2.50,
                rationale="Renders a real-time UI showing current temp vs user target setpoints.",
                pins=self._get_pins_for_part("SSD1306-I2C")
            ),
            ComponentInstance(
                ref_des="PWR1",
                part_number="USB-5V-Plug",
                name="5V Stationary Adapter Feed",
                category="Power",
                quantity=1,
                unit_price=1.50,
                rationale="Provides regulated 5V input for ESP32 VIN and the relay coil supply.",
                pins=self._get_pins_for_part("USB-5V-Plug")
            )
        ]
        
        nets = [
            ConnectionNet(
                net_id="NET_GND",
                name="Ground Wire",
                net_type="Ground",
                voltage=0.0,
                pins=[
                    PinReference(ref_des="U1", pin_id="GND"),
                    PinReference(ref_des="SEN1", pin_id="GND"),
                    PinReference(ref_des="ACT1", pin_id="GND"),
                    PinReference(ref_des="DISP1", pin_id="GND"),
                    PinReference(ref_des="PWR1", pin_id="GND")
                ]
            ),
            ConnectionNet(
                net_id="NET_3V3",
                name="3.3V Power Line",
                net_type="Power",
                voltage=3.3,
                pins=[
                    PinReference(ref_des="U1", pin_id="3V3"),
                    PinReference(ref_des="SEN1", pin_id="VCC"),
                    PinReference(ref_des="DISP1", pin_id="VCC")
                ]
            ),
            ConnectionNet(
                net_id="NET_5V",
                name="5V Adapter Rail",
                net_type="Power",
                voltage=5.0,
                pins=[
                    PinReference(ref_des="PWR1", pin_id="5V"),
                    PinReference(ref_des="U1", pin_id="VIN"),
                    PinReference(ref_des="ACT1", pin_id="VCC")
                ]
            ),
            ConnectionNet(
                net_id="NET_I2C_SDA",
                name="I2C Serial Data",
                net_type="I2C",
                voltage=3.3,
                pins=[
                    PinReference(ref_des="U1", pin_id="D21"),
                    PinReference(ref_des="DISP1", pin_id="SDA"),
                    PinReference(ref_des="SEN1", pin_id="SDA")
                ]
            ),
            ConnectionNet(
                net_id="NET_I2C_SCL",
                name="I2C Serial Clock",
                net_type="I2C",
                voltage=3.3,
                pins=[
                    PinReference(ref_des="U1", pin_id="D22"),
                    PinReference(ref_des="DISP1", pin_id="SCL"),
                    PinReference(ref_des="SEN1", pin_id="SCL")
                ]
            ),
            ConnectionNet(
                net_id="NET_RELAY_CTRL",
                name="Furnace Control Net",
                net_type="Digital",
                voltage=3.3,
                pins=[
                    PinReference(ref_des="U1", pin_id="D25"),
                    PinReference(ref_des="ACT1", pin_id="IN")
                ]
            )
        ]

        pin_mappings = [
            PinMappingEntry(mcu_pin="D21", connected_to="OLED/BMP280 Data", net_name="NET_I2C_SDA"),
            PinMappingEntry(mcu_pin="D22", connected_to="OLED/BMP280 Clock", net_name="NET_I2C_SCL"),
            PinMappingEntry(mcu_pin="D25", connected_to="Climate Control Relay Trigger", net_name="NET_RELAY_CTRL")
        ]

        assembly = [
            AssemblyStep(
                step_num=1,
                title="Wire 5V adapter and low-voltage rails",
                description="Seat the ESP32 on your proto-board. Connect PWR1 5V to ESP32 VIN and relay VCC, then tie PWR1 GND to the shared ground rail. Use ESP32 3V3 only for the BMP280 and OLED logic rail.",
                danger_flag=True,
                danger_message="Do not attach a bare LiPo directly to VIN. Add a proper charger/boost module before implementing battery backup.",
                affected_components=["U1", "PWR1", "ACT1"]
            ),
            AssemblyStep(
                step_num=2,
                title="Construct Shared I2C Bus",
                description="Connect SCL on both OLED and BMP280 to ESP32 Pin D22. Connect SDA on both display and pressure sensor to ESP32 Pin D21. Power them with 3.3V power rails and verify no crosstalk.",
                danger_flag=False,
                affected_components=["SEN1", "DISP1", "U1"]
            ),
            AssemblyStep(
                step_num=3,
                title="Integrate HVAC Switching relay",
                description="Power the relay coil from the PWR1 5V rail. Send the 3.3V-compatible input line (IN) to ESP32 GPIO pin D25. Route furnace control lines into Normally Open (NO) and Common (COM) blocks.",
                danger_flag=True,
                danger_message="Unplug structural heating supplies before hooking up high voltage terminals!",
                affected_components=["ACT1", "U1"]
            )
        ]

        mechanical = MechanicalNotes(
            enclosure_type="Custom Acrylic",
            mounting_guidance="Screw back-plate onto dry-wall with standard drywall screws. Clip the acrylic cover overlay on top for clean bezel appearance.",
            fabrication_details=[
                "Front-facing slot for BMP280 air-exposure.",
                "Laser cut clear acrylic viewing window.",
                "Mounting holes spaced 60mm vertically to match US wall standards."
            ],
            fabrication_cost_estimate_usd=7.50,
            cad_sources=[
                MechanicalSource(
                    name="Thermostat trim plate STL candidate index",
                    source_type="Reference CAD",
                    url="https://www.stlfinder.com/3dmodels/thermostat-trim-plate/",
                    file_formats=["STL"],
                    license="Model-specific licenses vary; verify selected model before fabrication",
                    estimated_unit_price_usd=0.00,
                    notes="Use as a geometry reference for wall coverage, then generate a project-specific acrylic DXF for the OLED/BMP280 layout."
                ),
                MechanicalSource(
                    name="Google Nest thermostat wallmount candidate index",
                    source_type="Paid STL",
                    url="https://cults3d.com/es/etiquetas/termostato%2Bnest%2Be%2Buk",
                    file_formats=["STL"],
                    license="Commercial model listing; verify current download price and license before reuse",
                    estimated_unit_price_usd=4.02,
                    notes="Useful reference for wall-mount clip geometry and cable pass-through placement."
                )
            ],
            manufacturability_rating="Moderate"
        )

        validation_issues = validate_circuit(components, nets)
        validation_issues.append(ValidationIssue(
            severity="INFO",
            category="Scope Gap",
            description="The battery-backup requirement is documented, but this preset wiring only models the safe 5V adapter-powered thermostat core.",
            troubleshooting="Add a LiPo charger/protection module and boost or power-path regulator before implementing rechargeable backup operation."
        ))
        validation_summary = build_validation_summary(validation_issues)
        power_rails = extract_power_rails(components, nets)
        buses = extract_buses(nets)
        current_draw = estimate_current_draw(components)

        project_ir = HardwareIR(
            hardware_ir_version="0.1",
            overview=overview,
            requirements=requirements,
            components=components,
            nets=nets,
            buses=buses,
            pin_mappings=pin_mappings,
            assembly=assembly,
            mechanical=mechanical,
            constraints=requirements.physical_constraints,
            power_rails=power_rails,
            estimated_current_draw_ma=current_draw,
            fabrication_notes=mechanical.fabrication_details,
            assembly_metadata={
                "status": "active",
                "schematic": {
                    "canvas": {"width": 1180, "height": 720},
                    "placements": {
                        "PWR1": {"x": 80, "y": 80},
                        "U1": {"x": 460, "y": 300},
                        "SEN1": {"x": 250, "y": 420},
                        "DISP1": {"x": 870, "y": 380},
                        "ACT1": {"x": 870, "y": 120}
                    }
                }
            },
            project_version_history=[{"version": "0.1", "description": "Initial fallback design generation"}],
            validation=validation_summary,
            is_valid=not any(issue.severity.upper() == "CRITICAL" for issue in validation_issues)
        )

        project_ir = build_mechanical_render_data(project_ir)
        self.save_project_to_db(prompt, project_ir)
        return project_ir

    def _load_simulated_smart_lock_project(self, prompt: str) -> HardwareIR:
        overview = ProjectOverview(
            title="Biometric & Keyless Bluetooth Smart Deadbolt",
            description=f"A smart lock mechanism utilizing servos, status indicator LEDs, and low power bluetooth.",
            difficulty="Beginner",
            estimated_cost=14.35,
            category="Smart Home"
        )
        requirements = FunctionalRequirements(
            requirements=[
                "Physically retract a deadbolt lock using a high-torque SG90 micro-servo motor.",
                "Display lock/unlock status locally with a green or red LED indicator.",
                "Accept secure bluetooth encryption handshakes to release deadbolt.",
                "Accept external physical push-buttons as deadbolts bypass."
            ],
            power_needs="5V external power bank or Micro-USB feed.",
            operating_voltage=5.0,
            physical_constraints=["Must fit inside deadbolt door handle cavities.", "Low power standby."],
            safety_notes=["Incorporate manual lock override lever to avoid physical lockouts during total power failures.", "Operate servo logic through clean voltage buffers."],
            missing_info=[]
        )
        components = [
            ComponentInstance(
                ref_des="U1",
                part_number="Arduino-Nano-V3",
                name="Arduino Nano v3.0",
                category="Microcontroller",
                quantity=1,
                unit_price=3.20,
                rationale="Highly portable controller with plenty of digital PWM pins to manage servo positions easily.",
                pins=self._get_pins_for_part("Arduino-Nano-V3")
            ),
            ComponentInstance(
                ref_des="ACT1",
                part_number="SG90-Servo",
                name="SG90 Micro Servo Motor",
                category="Actuator",
                quantity=1,
                unit_price=2.00,
                rationale="Provides exact rotational control (0-180deg) to throw the manual deadbolt lever.",
                pins=self._get_pins_for_part("SG90-Servo")
            ),
            ComponentInstance(
                ref_des="LED1",
                part_number="LED-Red-Generic",
                name="Standard Red LED (5mm)",
                category="Passives",
                quantity=1,
                unit_price=0.10,
                rationale="Indicates current lock/locked visual feedback for physical debugging.",
                pins=self._get_pins_for_part("LED-Red-Generic")
            ),
            ComponentInstance(
                ref_des="R1",
                part_number="Resistor-220R",
                name="220 Ohm Carbon Film Resistor (1/4W)",
                category="Passives",
                quantity=1,
                unit_price=0.05,
                rationale="Protects LED1 from overloading by limiting current from Arduino GPIO pins.",
                pins=self._get_pins_for_part("Resistor-220R")
            ),
            ComponentInstance(
                ref_des="PWR1",
                part_number="USB-5V-Plug",
                name="5V USB Power Bank Feed",
                category="Power",
                quantity=1,
                unit_price=1.50,
                rationale="Provides a regulated 5V input for the Arduino Nano and SG90 servo without routing a 3.7V LiPo into VIN.",
                pins=self._get_pins_for_part("USB-5V-Plug")
            )
        ]

        overview.estimated_cost = sum(c.unit_price * c.quantity for c in components)

        nets = [
            ConnectionNet(
                net_id="NET_GND",
                name="Ground Wire",
                net_type="Ground",
                voltage=0.0,
                pins=[
                    PinReference(ref_des="U1", pin_id="GND"),
                    PinReference(ref_des="ACT1", pin_id="GND"),
                    PinReference(ref_des="LED1", pin_id="CATHODE"),
                    PinReference(ref_des="PWR1", pin_id="GND")
                ]
            ),
            ConnectionNet(
                net_id="NET_5V",
                name="5V Power Rail",
                net_type="Power",
                voltage=5.0,
                pins=[
                    PinReference(ref_des="PWR1", pin_id="5V"),
                    PinReference(ref_des="U1", pin_id="5V"),
                    PinReference(ref_des="ACT1", pin_id="5V")
                ]
            ),
            ConnectionNet(
                net_id="NET_SERVO_PWM",
                name="Servo Signal Wire",
                net_type="PWM",
                voltage=5.0,
                pins=[
                    PinReference(ref_des="U1", pin_id="D9"),
                    PinReference(ref_des="ACT1", pin_id="PWM")
                ]
            ),
            ConnectionNet(
                net_id="NET_LED_DRIVE",
                name="LED Signal Net",
                net_type="Digital",
                voltage=5.0,
                pins=[
                    PinReference(ref_des="U1", pin_id="D3"),
                    PinReference(ref_des="R1", pin_id="1")
                ]
            ),
            ConnectionNet(
                net_id="NET_LED_RESISTOR",
                name="Resistor to LED Anode",
                net_type="Digital",
                voltage=2.0,
                pins=[
                    PinReference(ref_des="R1", pin_id="2"),
                    PinReference(ref_des="LED1", pin_id="ANODE")
                ]
            ),
        ]

        pin_mappings = [
            PinMappingEntry(mcu_pin="D9", connected_to="SG90 Servo PWM Command", net_name="NET_SERVO_PWM"),
            PinMappingEntry(mcu_pin="D3", connected_to="220R Current Limiter Input", net_name="NET_LED_DRIVE")
        ]

        assembly = [
            AssemblyStep(
                step_num=1,
                title="Mount micro controller and 5V feed",
                description="Secure the Arduino Nano on a mini breadboard with its USB connector accessible. Bring the 5V USB power-bank feed to the positive rail and common ground rail before attaching loads.",
                danger_flag=False,
                affected_components=["U1", "PWR1"]
            ),
            AssemblyStep(
                step_num=2,
                title="Wire the Servo motor",
                description="Plug the brown wire of the SG90 to common GND. Connect the red wire to the regulated 5V rail from PWR1, not to an unregulated LiPo or VIN feed. Connect the orange control wire to PWM pin D9 on the Nano.",
                danger_flag=False,
                affected_components=["ACT1", "U1"]
            ),
            AssemblyStep(
                step_num=3,
                title="Configure current limiting Status indicator",
                description="Connect a 220 Ohm resistor (R1) between Arduino pin D3 and the long lead (Anode) of the Red LED. Run a short wire from the flat edge (Cathode) of the LED to common system ground.",
                danger_flag=False,
                affected_components=["LED1", "R1", "U1"]
            )
        ]

        mechanical = MechanicalNotes(
            enclosure_type="3D Printed",
            mounting_guidance="Standard deadbolt faceplate installation. Feed physical lock override pin through structural center.",
            fabrication_details=[
                "Print in robust PETG or ABS to stand up to physical forcing.",
                "Infill density: 40% with tri-hexagon pattern.",
                "Custom slot on the internal face to allow backup mechanical turn-keys."
            ],
            fabrication_cost_estimate_usd=5.50,
            cad_sources=[
                MechanicalSource(
                    name="Arduino-based electronic lock files",
                    source_type="Open STL",
                    url="https://www.thingiverse.com/thing:2350856/files",
                    file_formats=["STL"],
                    license="Thingiverse file license; verify on source page",
                    estimated_unit_price_usd=0.00,
                    notes="Reference layout includes Arduino Nano and SG90-class servo mounting for lock actuation."
                ),
                MechanicalSource(
                    name="Motorized door lock for SG90 servo",
                    source_type="Open STL",
                    url="https://cults3d.com/en/3d-model/gadget/motorized-door-lock-for-sg90-servo/similar-designs",
                    file_formats=["STL"],
                    license="Cults listing marked free in search result; verify license before reuse",
                    estimated_unit_price_usd=0.00,
                    notes="Use the SG90 servo bracket and linkage proportions as a reference for the deadbolt adapter."
                )
            ],
            manufacturability_rating="Moderate"
        )

        validation_issues = validate_circuit(components, nets)
        validation_issues.append(ValidationIssue(
            severity="INFO",
            category="Scope Gap",
            description="The project title and requirements mention Bluetooth, biometric unlock, and bypass buttons, but this minimal wiring pass only represents the 5V servo deadbolt core, status LED, and current-limiting resistor.",
            troubleshooting="Add a BLE-capable controller or module, fingerprint reader, and debounced bypass inputs before claiming those functions are electrically implemented."
        ))
        validation_summary = build_validation_summary(validation_issues)
        power_rails = extract_power_rails(components, nets)
        buses = extract_buses(nets)
        current_draw = estimate_current_draw(components)

        project_ir = HardwareIR(
            hardware_ir_version="0.1",
            overview=overview,
            requirements=requirements,
            components=components,
            nets=nets,
            buses=buses,
            pin_mappings=pin_mappings,
            assembly=assembly,
            mechanical=mechanical,
            constraints=requirements.physical_constraints,
            power_rails=power_rails,
            estimated_current_draw_ma=current_draw,
            fabrication_notes=mechanical.fabrication_details,
            assembly_metadata={
                "status": "active",
                "schematic": {
                    "canvas": {"width": 1180, "height": 680},
                    "placements": {
                        "PWR1": {"x": 300, "y": 60},
                        "U1": {"x": 460, "y": 320},
                        "ACT1": {"x": 870, "y": 120},
                        "R1": {"x": 760, "y": 340},
                        "LED1": {"x": 1010, "y": 340}
                    }
                }
            },
            project_version_history=[{"version": "0.1", "description": "Initial fallback design generation"}],
            validation=validation_summary,
            is_valid=not any(issue.severity.upper() == "CRITICAL" for issue in validation_issues)
        )

        project_ir = build_mechanical_render_data(project_ir)
        self.save_project_to_db(prompt, project_ir)
        return project_ir

    def _get_pins_for_part(self, part_number: str) -> List[PinDefinition]:
        """Fetch pin template mapping from components database directly."""
        db = SessionLocal()
        try:
            db_template = db.query(DBComponentTemplate).filter(DBComponentTemplate.part_number == part_number).first()
            if db_template:
                return [PinDefinition(**pin) for pin in db_template.pins]
            return []
        except Exception:
            # Absolute hardcoded fallback to keep simulated run bulletproof
            for comp in SEED_COMPONENTS:
                if comp["part_number"] == part_number:
                    return [PinDefinition(**pin) for pin in comp["pins"]]
            return []
        finally:
            db.close()

    def _get_relay_pins_3v3_input(self) -> List[PinDefinition]:
        pins = []
        for pin in self._get_pins_for_part("Relay-5V-1Ch"):
            if pin.pin_id == "IN":
                pins.append(PinDefinition(
                    pin_id=pin.pin_id,
                    name=pin.name,
                    pin_type=pin.pin_type,
                    voltage=3.3,
                    description="3.3V-compatible logic input to trigger optocoupled relay module"
                ))
            else:
                pins.append(pin)
        return pins

SEED_COMPONENTS = [
    {
        "part_number": "ESP32-WROOM-32D",
        "name": "ESP32 NodeMCU Development Board",
        "category": "Microcontroller",
        "description": "Powerful WiFi + Bluetooth MCU, perfect for IoT, smart home, and cloud-connected automation.",
        "price": 4.50,
        "pins": [
            {"pin_id": "3V3", "name": "3.3V Power Out", "pin_type": "Power", "voltage": 3.3, "description": "3.3V Regulated Output"},
            {"pin_id": "GND", "name": "Ground", "pin_type": "Ground", "voltage": 0.0, "description": "System Ground Reference"},
            {"pin_id": "EN", "name": "Enable / Reset", "pin_type": "Passive", "voltage": 3.3, "description": "Reset pin, active low"},
            {"pin_id": "D25", "name": "GPIO25 / DAC_CH1", "pin_type": "Digital", "voltage": 3.3, "description": "DAC / General GPIO"},
            {"pin_id": "D22", "name": "GPIO22 / I2C_SCL", "pin_type": "I2C", "voltage": 3.3, "description": "Primary I2C SCL"},
            {"pin_id": "D21", "name": "GPIO21 / I2C_SDA", "pin_type": "I2C", "voltage": 3.3, "description": "Primary I2C SDA"},
            {"pin_id": "D27", "name": "GPIO27 / ADC_CH17", "pin_type": "Digital", "voltage": 3.3, "description": "General GPIO"},
            {"pin_id": "VIN", "name": "External Power In", "pin_type": "Power", "voltage": 5.0, "description": "5V Unregulated Input"}
        ],
        "use_cases": ["iot", "wifi", "bluetooth", "smart-home", "robotics", "automation", "controller", "mcu"]
    },
    {
        "part_number": "Arduino-Nano-V3",
        "name": "Arduino Nano v3.0",
        "category": "Microcontroller",
        "description": "Compact ATmega328P microcontroller board. Ideal for lightweight, non-wireless, breadboard-friendly physical computing.",
        "price": 3.20,
        "pins": [
            {"pin_id": "5V", "name": "5V Power Out", "pin_type": "Power", "voltage": 5.0, "description": "5V Regulated Power Output"},
            {"pin_id": "3V3", "name": "3.3V Power Out", "pin_type": "Power", "voltage": 3.3, "description": "3.3V Regulated Power Output"},
            {"pin_id": "GND", "name": "Ground", "pin_type": "Ground", "voltage": 0.0, "description": "System Ground"},
            {"pin_id": "VIN", "name": "Voltage Input", "pin_type": "Power", "voltage": 12.0, "description": "7V-12V Input (regulated down to 5V)"},
            {"pin_id": "D3", "name": "Digital 3 / PWM", "pin_type": "PWM", "voltage": 5.0, "description": "GPIO / PWM / Interrupt 1"},
            {"pin_id": "D9", "name": "Digital 9 / PWM", "pin_type": "PWM", "voltage": 5.0, "description": "GPIO / PWM"}
        ],
        "use_cases": ["robotics", "learning", "prototyping", "mcu", "basic-electronics", "wearable"]
    },
    {
        "part_number": "DHT22",
        "name": "DHT22 Temperature & Humidity Sensor",
        "category": "Sensor",
        "description": "High-accuracy digital relative temperature and humidity sensor module with single-bus interface.",
        "price": 2.80,
        "pins": [
            {"pin_id": "VCC", "name": "VCC Power", "pin_type": "Power", "voltage": 3.3, "description": "Supports 3.3V to 5.0V Supply"},
            {"pin_id": "DATA", "name": "Signal Out", "pin_type": "Digital", "voltage": 3.3, "description": "Single-wire digital data out (requires pullup)"},
            {"pin_id": "NC", "name": "No Connection", "pin_type": "Passive", "voltage": 0.0, "description": "Do not connect"},
            {"pin_id": "GND", "name": "Ground", "pin_type": "Ground", "voltage": 0.0, "description": "Power ground reference"}
        ],
        "use_cases": ["weather-station", "environmental-monitor", "temperature", "humidity", "smart-home", "gardening"]
    },
    {
        "part_number": "BMP280",
        "name": "BMP280 Barometric Pressure & Temp Sensor",
        "category": "Sensor",
        "description": "High-precision digital altimeter/pressure sensor with I2C and SPI interfaces. Operates at 3.3V.",
        "price": 1.80,
        "pins": [
            {"pin_id": "VCC", "name": "Power VCC", "pin_type": "Power", "voltage": 3.3, "description": "1.8V to 3.6V Supply Input"},
            {"pin_id": "GND", "name": "Ground", "pin_type": "Ground", "voltage": 0.0, "description": "Ground"},
            {"pin_id": "SCL", "name": "I2C SCL / SPI SCK", "pin_type": "I2C", "voltage": 3.3, "description": "Clock Pin"},
            {"pin_id": "SDA", "name": "I2C SDA / SPI MOSI", "pin_type": "I2C", "voltage": 3.3, "description": "Data Input/Output Pin"},
            {"pin_id": "CSB", "name": "Chip Select (SPI)", "pin_type": "SPI", "voltage": 3.3, "description": "SPI CSB, active low (pull high for I2C)"},
            {"pin_id": "SDO", "name": "SPI MISO / I2C Address Select", "pin_type": "Digital", "voltage": 3.3, "description": "Address LSB / MISO"}
        ],
        "use_cases": ["barometer", "weather-station", "altimeter", "drones", "smart-watch"]
    },
    {
        "part_number": "Relay-5V-1Ch",
        "name": "5V 1-Channel Optocoupled Relay Module",
        "category": "Actuator",
        "description": "Safely switches high-voltage AC or DC appliances using low-voltage logic from MCUs. Actuated by active-low or active-high logic.",
        "price": 1.20,
        "pins": [
            {"pin_id": "VCC", "name": "Module Power (5V)", "pin_type": "Power", "voltage": 5.0, "description": "5V Relay coil power"},
            {"pin_id": "GND", "name": "Module Ground", "pin_type": "Ground", "voltage": 0.0, "description": "System Ground"},
            {"pin_id": "IN", "name": "Signal Input", "pin_type": "Digital", "voltage": 5.0, "description": "Logic input to trigger coil (optocoupled)"},
            {"pin_id": "COM", "name": "Switch Common Terminal", "pin_type": "Passive", "voltage": 250.0, "description": "High-power common pole"},
            {"pin_id": "NO", "name": "Switch Normally Open Terminal", "pin_type": "Passive", "voltage": 250.0, "description": "Connected to COM only when energized"},
            {"pin_id": "NC", "name": "Switch Normally Closed Terminal", "pin_type": "Passive", "voltage": 250.0, "description": "Connected to COM by default"}
        ],
        "use_cases": ["home-automation", "smart-plug", "ac-switching", "motor-control", "valve-control"]
    },
    {
        "part_number": "SSD1306-I2C",
        "name": "0.96 inch OLED Display (I2C)",
        "category": "Display",
        "description": "128x64 pixels resolution organic LED display. Sharp, contrasty display controlled over simple I2C.",
        "price": 2.50,
        "pins": [
            {"pin_id": "VCC", "name": "Power VCC", "pin_type": "Power", "voltage": 3.3, "description": "Supports 3.3V or 5V Power Input"},
            {"pin_id": "GND", "name": "Ground", "pin_type": "Ground", "voltage": 0.0, "description": "Ground Reference"},
            {"pin_id": "SCL", "name": "I2C Serial Clock", "pin_type": "I2C", "voltage": 3.3, "description": "I2C SCL"},
            {"pin_id": "SDA", "name": "I2C Serial Data", "pin_type": "I2C", "voltage": 3.3, "description": "I2C SDA"}
        ],
        "use_cases": ["user-interface", "smart-thermostat", "clock", "dashboard", "smart-home"]
    },
    {
        "part_number": "Battery-LiPo-3.7V",
        "name": "3.7V Lithium Polymer Battery (1200mAh)",
        "category": "Power",
        "description": "Rechargeable, high-density LiPo power pack. Essential for wearable and off-grid wireless hardware setups.",
        "price": 5.50,
        "pins": [
            {"pin_id": "POS", "name": "Positive Lead (Red)", "pin_type": "Power", "voltage": 3.7, "description": "Positive terminal"},
            {"pin_id": "NEG", "name": "Negative Lead (Black)", "pin_type": "Ground", "voltage": 0.0, "description": "Negative reference terminal"}
        ],
        "use_cases": ["portable-power", "wearables", "iot-nodes", "drones", "off-grid"]
    },
    {
        "part_number": "SG90-Servo",
        "name": "SG90 Micro Servo Motor",
        "category": "Actuator",
        "description": "High-torque lightweight 180-degree micro servo. Excellent for robotic joints, steering, and physical actuators.",
        "price": 2.00,
        "pins": [
            {"pin_id": "5V", "name": "Power VCC (Red)", "pin_type": "Power", "voltage": 5.0, "description": "5.0V nominal power input"},
            {"pin_id": "GND", "name": "Ground (Brown)", "pin_type": "Ground", "voltage": 0.0, "description": "Power ground reference"},
            {"pin_id": "PWM", "name": "Control Signal (Orange)", "pin_type": "PWM", "voltage": 5.0, "description": "PWM pulse 50Hz, 1ms to 2ms width"}
        ],
        "use_cases": ["robotics", "robotic-arm", "rc-car", "smart-door-lock", "hobbies"]
    },
    {
        "part_number": "LED-Red-Generic",
        "name": "Standard Red LED (5mm)",
        "category": "Passives",
        "description": "Standard 5mm red light emitting diode. Useful for simple indicator signals. Needs current-limiting resistor.",
        "price": 0.10,
        "pins": [
            {"pin_id": "ANODE", "name": "Anode (+) Long Lead", "pin_type": "Passive", "voltage": 2.0, "description": "Positive terminal (needs 1.8V - 2.2V forward drop)"},
            {"pin_id": "CATHODE", "name": "Cathode (-) Flat Lead", "pin_type": "Ground", "voltage": 0.0, "description": "Ground Reference Pin"}
        ],
        "use_cases": ["status-indicator", "debugging", "blinky", "diagnostics"]
    },
    {
        "part_number": "Resistor-220R",
        "name": "220 Ohm Carbon Film Resistor (1/4W)",
        "category": "Passives",
        "description": "Ideal size for current-limiting standard LEDs driven from 5V or 3.3V microcontroller pins.",
        "price": 0.05,
        "pins": [
            {"pin_id": "1", "name": "Lead 1", "pin_type": "Passive", "voltage": None, "description": "Bidirectional passive pin"},
            {"pin_id": "2", "name": "Lead 2", "pin_type": "Passive", "voltage": None, "description": "Bidirectional passive pin"}
        ],
        "use_cases": ["current-limiting", "led-protection", "basic-circuit"]
    }
]
