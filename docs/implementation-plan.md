# Implementation Plan

## Architecture

React dashboard calls a Python API. The backend stores PDFs in S3, stores workflow state in MongoDB, extracts fields through EyeLevel/GroundX, maps fields to FT Williams rules, and generates review-only ftwLink XML.

## Intake flows

1. Manual upload: available now.
2. ShareFile sync: optional automated intake once OAuth/folder access is available.

Both flows feed the same pipeline:

```text
PDF intake -> S3 -> Mongo filing -> extraction -> mapping -> validation -> review dashboard -> XML preview
```

## Backend modules

- `models.py`: Pydantic schemas
- `repositories.py`: MongoDB repository with in-memory local fallback
- `services/extractor.py`: EyeLevel/GroundX adapter and PydanticAI-ready normalization
- `services/mapping.py`: field matching, confidence, missing high-priority detection
- `services/xml_builder.py`: proposed ftwLink XML
- `services/sharefile.py`: ShareFile connector status and future folder sync
- `api/*.py`: HTTP routes
