"""One versioned tool directory shared with the DSH plugin."""
import json
from pathlib import Path


def tool_manifest():
    path = Path(__file__).resolve().parents[2] / 'integrations/dsh-ontology-plugin/tool-manifest.json'
    return json.loads(path.read_text(encoding='utf-8'))
