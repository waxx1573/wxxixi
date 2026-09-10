"""Reject a candidate image that loses or changes an installed runtime package."""
import json
import subprocess
import sys

code = 'import json,importlib.metadata as m; print(json.dumps({d.metadata["Name"].lower().replace("_","-"): d.version for d in m.distributions() if d.metadata["Name"]}))'
current = json.loads(subprocess.check_output(["docker", "exec", "astrbot", "python", "-c", code], text=True))
candidate = json.loads(subprocess.check_output(["docker", "run", "--rm", "--network", "none", "--entrypoint", "python", sys.argv[1], "-c", code], text=True))
missing = {name: value for name, value in current.items() if name not in candidate}
changed = {name: [value, candidate[name]] for name, value in current.items() if name in candidate and candidate[name] != value}
print("package_preservation", json.dumps({"current_count": len(current), "candidate_count": len(candidate), "missing": missing, "changed": changed}))
metadata_correction = changed.get("astrbot") == ["4.28.0b1", "4.28.0"]
if metadata_correction:
    del changed["astrbot"]
    print("astrbot_metadata_correction", "beta package metadata -> existing stable core; source comparison still required")
if missing or changed:
    raise SystemExit("candidate differs from production; review and pin dependencies before switching")

# Compare actual executable source as well as versions; runtime updates can
# leave an old image tag while the core has already moved to another release.
source_code = r'''import hashlib,json,pathlib
r=pathlib.Path("/AstrBot")
files=list((r/"astrbot").rglob("*.py"))+[r/"main.py"]
files += [p for p in (r/"dashboard/dist").rglob("*") if p.is_file()]
print(json.dumps({str(p.relative_to(r)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}))'''
old_source = json.loads(subprocess.check_output(["docker", "exec", "astrbot", "python", "-c", source_code], text=True))
new_source = json.loads(subprocess.check_output(["docker", "run", "--rm", "--network", "none", "--entrypoint", "python", sys.argv[1], "-c", source_code], text=True))
differences = sorted(name for name in old_source.keys() | new_source.keys() if old_source.get(name) != new_source.get(name))
print("source_preservation", json.dumps({"files": len(old_source), "differences": differences}))
if differences:
    raise SystemExit("candidate core differs from runtime; review actual source baseline before switching")
