"""Local, provider-neutral review packets. Never executes a reviewer command."""
from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from contract_validation import validate_schema_file

SCHEMAS = {
    "reading": "cad-reader-review.schema.json",
    "topology": "topology-review.schema.json",
    "qa": "independent-qa-review.schema.json",
}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def inside(project, value):
    project = project.resolve()
    path = (project / value).resolve()
    if not path.is_relative_to(project):
        raise ValueError("Review artifacts must stay inside the project")
    return path


def artifact(project, value):
    project = project.resolve()
    path = inside(project, value)
    if not path.is_file():
        raise ValueError(f"Missing artifact: {path}")
    return {"path": path.relative_to(project).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def fresh(project, item):
    if artifact(project, item["path"]) != item:
        raise ValueError(f"Stale review evidence: {item['path']}")


def configure(project, mode, reviewer=""):
    project = project.resolve()
    if mode not in {"human", "agent"}:
        raise ValueError("Choose human or agent")
    if mode == "agent" and not reviewer.strip():
        raise ValueError("Name an available reviewer; do not assume a provider")
    value = {"schema": "cad_to_sketchup.review_settings.2026-09-11",
             "mode": mode, "reviewer": reviewer.strip() if mode == "agent" else "human",
             "changed_at": datetime.now(timezone.utc).isoformat()}
    path = inside(project, Path("work/review/settings.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        write(path.parent / f"settings-{uuid.uuid4().hex}.json", load(path))
    temp = path.with_name(f"settings-{uuid.uuid4().hex}.tmp")
    write(temp, value)
    temp.replace(path)
    return path


def checklist(value, prefix=""):
    result = []
    if isinstance(value, dict):
        for key, child in value.items():
            result.extend(checklist(child, f"{prefix}/{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.extend(checklist(child, f"{prefix}/{index}"))
    elif isinstance(value, bool) or value is None:
        result.append(f"- [ ] {prefix}")
    return result


def prepare(project, stage, template, evidence, producer):
    project = project.resolve()
    settings = load(inside(project, Path("work/review/settings.json")))
    if settings.get("mode") not in {"human", "agent"} or not producer.strip():
        raise ValueError("Configure review and identify the producer first")
    if stage not in SCHEMAS or not evidence:
        raise ValueError("A supported stage and evidence are required")
    template_ref = artifact(project, template)
    body = load(inside(project, template))
    schema = load(Path(__file__).resolve().parents[1] / "contracts" / SCHEMAS[stage])
    if body.get("schema") != schema["properties"]["schema"]["const"]:
        raise ValueError("Template does not match selected stage")
    folder = inside(project, Path("work/review") / f"{stage}-{uuid.uuid4().hex}")
    packet = {"schema": "cad_to_sketchup.review_packet.2026-09-11",
              "stage": stage, "settings": settings, "producer": producer.strip(),
              "template": template_ref,
              "evidence": [artifact(project, item) for item in evidence]}
    write(folder / "packet.json", packet)
    text = [f"# {stage} review", "", f"Route: {settings['mode']}",
            "Read the evidence, then report findings. Unseen items remain pending.",
            "The implementation owner must present these checks in plain language.",
            "", "## Evidence"]
    text += [f"- [{item['path']}]({inside(project, item['path']).as_uri()})" for item in packet["evidence"]]
    text += ["", "## Required checks", *checklist(body), "", "## Additional review",
             "Explain crop-edge warnings, missing systems, applicability and cross-view conflicts.",
             "Do not change source hashes or approve on appearance alone."]
    (folder / "packet.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    return folder / "packet.json"


def record(project, packet_path, response_path, review_path):
    project = project.resolve()
    packet_path = inside(project, packet_path)
    packet = load(packet_path)
    if packet.get("schema") != "cad_to_sketchup.review_packet.2026-09-11":
        raise ValueError("Unsupported packet")
    for item in [packet["template"], *packet["evidence"]]:
        fresh(project, item)
    response = inside(project, response_path)
    if not response.read_text(encoding="utf-8-sig").strip():
        raise ValueError("An actual reviewer response is required")
    review = load(inside(project, review_path))
    reviewer = review.get("reviewer", {})
    if not reviewer.get("id") or not reviewer.get("derivation_id"):
        raise ValueError("Reviewer identity and derivation are required")
    if packet["producer"] in {reviewer["id"], reviewer["derivation_id"]}:
        raise ValueError("Producer cannot certify its own interpretation")
    template = load(inside(project, packet["template"]["path"]))
    if packet["stage"] == "reading":
        expected = "human" if packet["settings"]["mode"] == "human" else "independent_agent"
        if reviewer.get("type") != expected:
            raise ValueError("Reviewer type does not match the selected route")
        if review.get("source_package_sha256") != template.get("source_package_sha256"):
            raise ValueError("Review changed the source package hash")
        for section in ("frames", "views"):
            if set(review.get(section, {})) != set(template[section]):
                raise ValueError("Review must cover the exact template inventory")
            for key, checks in template[section].items():
                if any(review[section][key].get(check) is not True for check in checks):
                    raise ValueError("Reading checks are incomplete")
    else:
        key = "candidate_sha256" if packet["stage"] == "topology" else "machine_result"
        if review.get(key) != template.get(key):
            raise ValueError("Review changed the bound stage input")
        if review.get("status") != "verified" or review.get("unresolved"):
            raise ValueError("Review is incomplete or has unresolved findings")
        expected_views = {row["source_view_id"]: row for row in template["view_results"]}
        rows = review.get("view_results", [])
        if len(rows) != len(expected_views) or {row["source_view_id"] for row in rows} != set(expected_views):
            raise ValueError("Review must cover all expected views exactly once")
        for row in rows:
            if row.get("status") != "PASS" or any(row.get("checks", {}).get(check) is not True for check in expected_views[row["source_view_id"]]["checks"]):
                raise ValueError("View checks are incomplete")
        checks_key = "checks" if packet["stage"] == "topology" else "cross_view_checks"
        if any(review.get(checks_key, {}).get(check) is not True for check in template[checks_key]):
            raise ValueError("Cross-view checks are incomplete")
    issues = validate_schema_file(SCHEMAS[packet["stage"]], review)
    if issues:
        raise ValueError("Review schema invalid: " + "; ".join(str(issue) for issue in issues))
    receipt = {"schema": "cad_to_sketchup.review_receipt.2026-09-11",
               "status": "recorded_not_promoted", "stage": packet["stage"],
               "mode": packet["settings"]["mode"], "reviewer": reviewer,
               "packet": artifact(project, packet_path),
               "response": artifact(project, response), "review": artifact(project, review_path)}
    output = packet_path.parent / "receipt.json"
    write(output, receipt)
    return output


def require_receipt(project, review_path, stage):
    """CLI promotion must have an unchanged response/evidence receipt."""
    project = project.resolve()
    expected = artifact(project, review_path)
    for path in (project / "work/review").glob("*/receipt.json"):
        receipt = load(path)
        if receipt.get("review") != expected or receipt.get("stage") != stage:
            continue
        for key in ("packet", "response", "review"):
            fresh(project, receipt[key])
        packet = load(inside(project, receipt["packet"]["path"]))
        for item in [packet["template"], *packet["evidence"]]:
            fresh(project, item)
        return
    raise ValueError("Missing current review receipt; record the actual human/agent response first")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("configure", "prepare", "record"))
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--mode", choices=("human", "agent"))
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--stage", choices=tuple(SCHEMAS))
    parser.add_argument("--producer", default="")
    for flag in ("template", "packet", "response", "review"):
        parser.add_argument(f"--{flag}", type=Path)
    parser.add_argument("--evidence", type=Path, nargs="+")
    args = parser.parse_args()
    project = args.project.resolve()
    try:
        if not project.is_dir():
            raise ValueError("Project folder does not exist")
        if args.action == "configure":
            result = configure(project, args.mode, args.reviewer)
        elif args.action == "prepare":
            if not args.template or not args.evidence:
                raise ValueError("Prepare requires --template and --evidence")
            result = prepare(project, args.stage, args.template, args.evidence, args.producer)
        else:
            if not all((args.packet, args.response, args.review)):
                raise ValueError("Record requires --packet, --response and --review")
            result = record(project, args.packet, args.response, args.review)
        print(json.dumps({"path": str(result)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
