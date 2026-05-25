import os
from typing import Dict, Any
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from dotenv import load_dotenv

load_dotenv()

from backend.database import get_db, init_db, DBGeneratedProject, DBComponentTemplate
from backend.seed_db import seed_database
from backend.models import (
    GenerateProjectRequest, HardwareIR, ValidationReport, 
    ComponentInstance, ConnectionNet, ValidationIssue
)
from backend.agents.orchestrator import HardwarePipelineOrchestrator
from backend.validation import validate_circuit
from backend.utils import generate_mermaid_chart, generate_svg_schematic

app = FastAPI(
    title="Blueprint Open-Source API",
    description="AI-native prompt-to-hardware compilation, validation, and design generation platform.",
    version="1.0.0"
)

# Enable CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In development, allow all. Can narrow in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize and seed database on startup
@app.on_event("startup")
def startup_event():
    print("Starting up Blueprint server...")
    try:
        init_db()
        # Seed component templates if empty
        db = next(get_db())
        count = db.query(DBComponentTemplate).count()
        if count == 0:
            print("Database empty. Seeding templates automatically...")
            seed_database()
        else:
            print(f"Database ready with {count} component templates.")
    except Exception as e:
        print(f"Error during database startup: {e}")

@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "Blueprint Open-Source Hardware Compiler",
        "version": "1.0.0",
        "docs_url": "/docs"
    }

@app.get("/debug/config")
def debug_config_endpoint():
    """
    Reports Gemini model resolution state without exposing the configured API key.
    """
    try:
        orchestrator = HardwarePipelineOrchestrator()
        return orchestrator.get_debug_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Debug config failed: {str(e)}")

@app.post("/api/generate", response_model=Dict if not os.getenv("GEMINI_API_KEY") else Any)
def generate_project_endpoint(request: GenerateProjectRequest):
    """
    Submits a natural language hardware idea and optional multimodal reference image.
    Runs the 7-agent compilation workflow, circuit safety auditor, and returns a verified Hardware IR, SVG schematic, and Mermaid diagram.
    """
    prompt_text = request.prompt.strip()
    if not prompt_text and not request.image_data:
        raise HTTPException(status_code=400, detail="Provide a prompt or reference image.")
    if not prompt_text:
        prompt_text = "Infer a buildable hardware project from the uploaded reference image."
    
    try:
        image_bytes = None
        image_mime_type = None
        if request.image_data:
            try:
                base64_data = request.image_data.strip()
                if "," in request.image_data:
                    header, base64_data = request.image_data.split(",", 1)
                    if "data:" in header and ";base64" in header:
                        image_mime_type = header.split(";")[0].replace("data:", "")
                    base64_data = base64_data.strip()
                if not image_mime_type:
                    image_mime_type = "image/png"
                
                import base64
                image_bytes = base64.b64decode(base64_data)
            except Exception as e:
                print(f"Error decoding base64 image: {e}")
                if not request.prompt.strip():
                    raise HTTPException(status_code=400, detail="Reference image could not be decoded.")

        orchestrator = HardwarePipelineOrchestrator()
        ir = orchestrator.generate_project(prompt_text, image_bytes=image_bytes, image_mime_type=image_mime_type)

        if request.image_data:
            metadata = ir.assembly_metadata or {}
            ir.assembly_metadata = {
                **metadata,
                "reference_image_data": request.image_data,
                "image_features": metadata.get("image_features") or ir.constraints[:12],
                "input_mode": "prompt_image",
            }
        
        # Calculate diagrams
        mermaid_code = generate_mermaid_chart(ir)
        svg_schematic = generate_svg_schematic(ir)
        
        return {
            "project_ir": ir.model_dump(),
            "mermaid_code": mermaid_code,
            "svg_schematic": svg_schematic
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")

@app.get("/api/projects")
def list_projects_endpoint(db: Session = Depends(get_db)):
    """Lists all previously compiled hardware projects."""
    try:
        projects = db.query(DBGeneratedProject).order_by(DBGeneratedProject.id.desc()).all()
        return [
            {
                "project_id": p.project_id,
                "title": p.title,
                "prompt": p.prompt,
                "created_at": p.created_at
            }
            for p in projects
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects/{project_id}")
def get_project_endpoint(project_id: str, db: Session = Depends(get_db)):
    """Retrieves a specific hardware design and its corresponding schematics."""
    project = db.query(DBGeneratedProject).filter(DBGeneratedProject.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    
    try:
        ir = HardwareIR(**project.hardware_ir)
        mermaid_code = generate_mermaid_chart(ir)
        svg_schematic = generate_svg_schematic(ir)
        
        return {
            "project_id": project.project_id,
            "prompt": project.prompt,
            "created_at": project.created_at,
            "project_ir": ir.model_dump(),
            "mermaid_code": mermaid_code,
            "svg_schematic": svg_schematic
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading project IR: {str(e)}")

@app.get("/api/components")
def get_components_endpoint(db: Session = Depends(get_db)):
    """Returns the template library of seed electrical parts."""
    try:
        components = db.query(DBComponentTemplate).all()
        return [
            {
                "id": c.id,
                "part_number": c.part_number,
                "name": c.name,
                "category": c.category,
                "description": c.description,
                "price": c.price,
                "sourcing_url": c.sourcing_url,
                "pins": c.pins,
                "use_cases": c.use_cases
            }
            for c in components
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/seed", status_code=status.HTTP_201_CREATED)
def trigger_db_seeding():
    """Manual trigger to re-seed the parts library database."""
    try:
        seed_database()
        return {"message": "Database templates successfully seeded."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Dedicated schemas for validate endpoint
from pydantic import BaseModel
from typing import List, Dict

class ValidateCircuitRequest(BaseModel):
    components: List[ComponentInstance]
    nets: List[ConnectionNet]

@app.post("/api/validate", response_model=ValidationReport)
def validate_circuit_endpoint(request: ValidateCircuitRequest):
    """
    Accepts arbitrary list of parts and electrical connection nets.
    Runs rule checks and returns validation errors or warnings.
    """
    try:
        issues = validate_circuit(request.components, request.nets)
        is_valid = not any(issue.severity.upper() == "CRITICAL" for issue in issues)
        return ValidationReport(is_valid=is_valid, issues=issues)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
