#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import random
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CARDS_PATH = ROOT / "data" / "kids_english_2_cards.json"
DEFAULT_TEMPLATE = ROOT / "comfyui" / "official" / "LTX-2.3_T2V_I2V_Single_Stage_Distilled_Full.json"
JOBS_DIR = ROOT / "comfyui" / "jobs" / "kids-english-2"
WORKFLOWS_DIR = ROOT / "comfyui" / "workflows" / "kids-english-2"
OUTPUT_DIR = ROOT / "videos" / "kids-english-2"
DEFAULT_COMFY_INPUT_DIR = Path("/home/lpoeeo/comfy/ComfyUI/input")

NON_WIDGET_TYPES = {
    "AUDIO",
    "CLIP",
    "CONDITIONING",
    "CONTROL_NET",
    "GUIDER",
    "IMAGE",
    "LATENT",
    "MASK",
    "MODEL",
    "NOISE",
    "SAMPLER",
    "SIGMAS",
    "VAE",
    "VIDEO",
}


def load_cards(path: Path = CARDS_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def replace_placeholders(value: Any, replacements: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: replace_placeholders(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_placeholders(item, replacements) for item in value]
    if isinstance(value, str):
        if value in replacements:
            return replacements[value]
        for token, replacement in replacements.items():
            value = value.replace(token, str(replacement))
        return value
    return value


def card_payload(card: dict[str, Any], defaults: dict[str, Any], template: dict[str, Any], seed: int) -> dict[str, Any]:
    replacements = {
        "__PROMPT__": card["prompt"],
        "__NEGATIVE_PROMPT__": card["negative_prompt"],
        "__SEED__": seed,
        "__WIDTH__": int(defaults["width"]),
        "__HEIGHT__": int(defaults["height"]),
        "__FRAMES__": int(defaults["frames"]),
        "__FPS__": int(defaults["fps"]),
        "__OUTPUT_PREFIX__": f"kids-english-2/{card['slug']}",
    }
    return replace_placeholders(template, replacements)


def is_ui_workflow(template: dict[str, Any]) -> bool:
    return isinstance(template.get("nodes"), list)


def is_api_prompt(template: dict[str, Any]) -> bool:
    return bool(template) and all(
        isinstance(value, dict) and "class_type" in value and "inputs" in value
        for value in template.values()
    )


def node_label(node: dict[str, Any]) -> str:
    return f"{node.get('type', '')} {node.get('title', '')}".lower()


def set_widget(node: dict[str, Any], index: int, value: Any) -> bool:
    widgets = node.setdefault("widgets_values", [])
    if len(widgets) <= index:
        return False
    widgets[index] = value
    return True


def patch_ui_workflow(card: dict[str, Any], defaults: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    workflow = copy.deepcopy(template)
    changed = {
        "positive": 0,
        "negative": 0,
        "latent": 0,
        "fps": 0,
        "save": 0,
        "image": 0,
        "scheduler": 0,
    }
    output_prefix = f"kids-english-2/{card['slug']}"

    for node in workflow.get("nodes", []):
        label = node_label(node)
        node_type = str(node.get("type", ""))
        if "positive" in label and ("textencode" in label or "gemma" in label):
            if set_widget(node, 0, card["prompt"]):
                changed["positive"] += 1
        if "negative" in label and ("textencode" in label or "gemma" in label):
            widget_index = 1 if node_type == "GemmaAPITextEncode" else 0
            if set_widget(node, widget_index, card["negative_prompt"]):
                changed["negative"] += 1
        if node_type == "EmptyLTXVLatentVideo":
            if (
                set_widget(node, 0, int(defaults["width"]))
                and set_widget(node, 1, int(defaults["height"]))
                and set_widget(node, 2, int(defaults["frames"]))
            ):
                changed["latent"] += 1
        if node_type == "CreateVideo":
            if set_widget(node, 0, int(defaults["fps"])):
                changed["fps"] += 1
        if node_type == "LTXVScheduler":
            if set_widget(node, 0, int(defaults["steps"])):
                changed["scheduler"] += 1
        if node_type == "PrimitiveInt" and "number of frames" in label:
            if set_widget(node, 0, int(defaults["frames"])):
                changed["latent"] += 1
        if node_type == "PrimitiveFloat" and "fps" in label:
            if set_widget(node, 0, int(defaults["fps"])):
                changed["fps"] += 1
        if node_type == "SaveVideo":
            if set_widget(node, 0, output_prefix):
                changed["save"] += 1
        if node_type == "LoadImage" and card.get("poster"):
            # Keep I2V workflows immediately loadable by pointing their image node at a card poster.
            if set_widget(node, 0, f"kids-english-2/{card['slug']}.jpg"):
                changed["image"] += 1

    required = ("positive", "negative", "latent", "fps", "save")
    missing = [name for name in required if changed[name] == 0]
    if missing:
        raise ValueError(f"Could not patch UI workflow for {card['slug']}; missing nodes: {', '.join(missing)}")

    workflow.setdefault("extra", {})
    workflow["extra"]["kids_english_2"] = {
        "card_id": card["id"],
        "english": card["english"],
        "chinese": card["chinese"],
        "output_prefix": output_prefix,
        "patched_nodes": changed,
    }
    return workflow


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def schema_entries(object_info: dict[str, Any], class_type: str) -> list[tuple[str, Any]]:
    node_info = object_info.get(class_type, {})
    input_info = node_info.get("input", {})
    entries: list[tuple[str, Any]] = []
    for group in ("required", "optional"):
        for name, spec in input_info.get(group, {}).items():
            entries.append((name, spec))
    return entries


def is_widget_schema(spec: Any) -> bool:
    if not isinstance(spec, list) or not spec:
        return False
    input_type = spec[0]
    if isinstance(input_type, list):
        return True
    return str(input_type) not in NON_WIDGET_TYPES


def schema_default(spec: Any) -> Any:
    if not isinstance(spec, list) or len(spec) < 2 or not isinstance(spec[1], dict):
        return None
    return spec[1].get("default")


def required_schema_names(object_info: dict[str, Any], class_type: str) -> set[str]:
    node_info = object_info.get(class_type, {})
    input_info = node_info.get("input", {})
    return set(input_info.get("required", {}))


def source_for_link(link: list[Any]) -> list[Any]:
    return [str(link[1]), int(link[2])]


def workflow_ancestor_ids(workflow: dict[str, Any]) -> set[int]:
    nodes_by_id = {int(node["id"]): node for node in workflow.get("nodes", [])}
    links_by_id = {int(link[0]): link for link in workflow.get("links", [])}
    save_ids = [int(node["id"]) for node in workflow.get("nodes", []) if node.get("type") == "SaveVideo"]
    if not save_ids:
        return set(nodes_by_id)

    seen: set[int] = set()

    def visit(node_id: int) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        node = nodes_by_id.get(node_id)
        if not node:
            return
        for input_def in node.get("inputs", []) or []:
            link_id = input_def.get("link")
            if link_id is None:
                continue
            link = links_by_id.get(int(link_id))
            if link:
                visit(int(link[1]))

    for node_id in save_ids:
        visit(node_id)
    return seen


def ui_workflow_to_api_prompt(workflow: dict[str, Any], object_info: dict[str, Any]) -> dict[str, Any]:
    if not is_ui_workflow(workflow):
        raise ValueError("Expected a ComfyUI UI workflow with a nodes array")

    links_by_id = {int(link[0]): link for link in workflow.get("links", [])}
    keep_ids = workflow_ancestor_ids(workflow)
    api_prompt: dict[str, Any] = {}

    for node in workflow.get("nodes", []):
        node_id = int(node["id"])
        if node_id not in keep_ids:
            continue

        class_type = str(node.get("type"))
        if class_type not in object_info:
            raise ValueError(f"ComfyUI server does not know node type {class_type!r}")

        inputs: dict[str, Any] = {}
        linked_names: set[str] = set()
        for input_def in node.get("inputs", []) or []:
            link_id = input_def.get("link")
            if link_id is None:
                continue
            link = links_by_id.get(int(link_id))
            if link is None:
                continue
            name = str(input_def["name"])
            inputs[name] = source_for_link(link)
            linked_names.add(name)

        widgets = list(node.get("widgets_values") or [])
        if class_type == "ResizeImageMaskNode" and len(widgets) >= 3:
            resize_type = widgets[0]
            inputs["resize_type"] = resize_type
            if resize_type == "scale longer dimension":
                inputs["resize_type.longer_size"] = widgets[1]
            elif resize_type == "scale shorter dimension":
                inputs["resize_type.shorter_size"] = widgets[1]
            elif resize_type == "scale by multiplier":
                inputs["resize_type.multiplier"] = widgets[1]
            elif resize_type == "scale width":
                inputs["resize_type.width"] = widgets[1]
            elif resize_type == "scale height":
                inputs["resize_type.height"] = widgets[1]
            elif resize_type == "scale total pixels":
                inputs["resize_type.megapixels"] = widgets[1]
            elif resize_type == "scale to multiple":
                inputs["resize_type.multiple"] = widgets[1]
            inputs["scale_method"] = widgets[2]
            widgets = []

        widget_index = 0
        for name, spec in schema_entries(object_info, class_type):
            if not is_widget_schema(spec):
                continue
            if widget_index >= len(widgets):
                break
            value = widgets[widget_index]
            widget_index += 1
            if name not in linked_names and name not in inputs:
                inputs[name] = value

        required_names = required_schema_names(object_info, class_type)
        for name, spec in schema_entries(object_info, class_type):
            if name in inputs or name not in required_names or not is_widget_schema(spec):
                continue
            default = schema_default(spec)
            if default is not None:
                inputs[name] = default

        api_prompt[str(node_id)] = {
            "class_type": class_type,
            "inputs": inputs,
        }

    return api_prompt


def write_jobs(args: argparse.Namespace) -> int:
    data = load_cards(args.cards)
    template = json.loads(args.template.read_text(encoding="utf-8"))
    if is_ui_workflow(template):
        out_dir = WORKFLOWS_DIR
        kind = "workflow"
    elif is_api_prompt(template):
        out_dir = JOBS_DIR
        kind = "api job"
    else:
        raise SystemExit(f"Template is neither a ComfyUI UI workflow nor an API prompt: {args.template}")
    out_dir.mkdir(parents=True, exist_ok=True)

    for index, card in enumerate(data["cards"]):
        seed = args.seed + index if args.seed is not None else random.randint(1, 2**31 - 1)
        if is_ui_workflow(template):
            payload = patch_ui_workflow(card, data["video_defaults"], template)
        else:
            payload = card_payload(card, data["video_defaults"], template, seed)
        job_path = out_dir / f"{index + 1:02d}_{card['slug']}.json"
        write_payload(job_path, payload)
        print(f"wrote {kind} {job_path.relative_to(ROOT)}")
    return 0


def write_api_jobs(args: argparse.Namespace) -> int:
    data = load_cards(args.cards)
    object_info = http_json(f"{args.server.rstrip('/')}/object_info", timeout=20)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for index, card in enumerate(data["cards"]):
        workflow_path = WORKFLOWS_DIR / f"{index + 1:02d}_{card['slug']}.json"
        if not workflow_path.exists():
            raise FileNotFoundError(f"Missing patched UI workflow: {workflow_path}")
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        api_prompt = ui_workflow_to_api_prompt(workflow, object_info)
        job_path = JOBS_DIR / workflow_path.name
        write_payload(job_path, api_prompt)
        count += 1
        print(f"wrote api job {job_path.relative_to(ROOT)}")
    print(f"wrote {count} API jobs")
    return 0


def prepare_inputs(args: argparse.Namespace) -> int:
    data = load_cards(args.cards)
    out_dir = args.comfy_input_dir / "kids-english-2"
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for card in data["cards"]:
        source = ROOT / card["poster"]
        if not source.exists():
            raise FileNotFoundError(f"Missing poster for {card['slug']}: {source}")
        target = out_dir / f"{card['slug']}.jpg"
        shutil.copyfile(source, target)
        count += 1
        print(f"staged {target}")
    print(f"staged {count} ComfyUI input images")
    return 0


def http_json(url: str, payload: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def queue_prompt(server: str, prompt: dict[str, Any], client_id: str) -> str:
    response = http_json(f"{server.rstrip('/')}/prompt", {"prompt": prompt, "client_id": client_id})
    prompt_id = response.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI did not return a prompt_id: {response}")
    return str(prompt_id)


def wait_for_history(server: str, prompt_id: str, poll_seconds: float, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        history = http_json(f"{server.rstrip('/')}/history/{urllib.parse.quote(prompt_id)}")
        if prompt_id in history:
            return history[prompt_id]
        time.sleep(poll_seconds)
    raise TimeoutError(f"Timed out waiting for ComfyUI prompt {prompt_id}")


def download_outputs(server: str, history: dict[str, Any], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    outputs = history.get("outputs", {})
    for node_output in outputs.values():
        for kind in ("videos", "gifs", "images"):
            for item in node_output.get(kind, []):
                filename = item.get("filename")
                if not filename:
                    continue
                params = urllib.parse.urlencode(
                    {
                        "filename": filename,
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                    }
                )
                url = f"{server.rstrip('/')}/view?{params}"
                target = out_dir / Path(filename).name
                with urllib.request.urlopen(url, timeout=120) as response:
                    target.write_bytes(response.read())
                saved.append(target)
                print(f"downloaded {target.relative_to(ROOT)}")
    return saved


def promote_card_video(slug: str, saved: list[Path]) -> Path | None:
    videos = [path for path in saved if path.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}]
    if not videos:
        return None

    final_output = sorted(videos, key=lambda path: (path.stat().st_mtime_ns, path.name))[-1]
    target = OUTPUT_DIR / f"{slug}.mp4"
    if final_output.resolve() != target.resolve():
        shutil.copyfile(final_output, target)
    print(f"promoted {final_output.name} to {target.relative_to(ROOT)}")
    return target


def submit(args: argparse.Namespace) -> int:
    try:
        http_json(f"{args.server.rstrip('/')}/system_stats", timeout=5)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SystemExit(f"ComfyUI server is not reachable at {args.server}: {exc}") from exc

    data = load_cards(args.cards)
    template = json.loads(args.template.read_text(encoding="utf-8"))
    if is_ui_workflow(template):
        raise SystemExit(
            "submit requires a ComfyUI API-format workflow, but the selected template is a UI workflow. "
            "Load one generated file from comfyui/workflows/kids-english-2 in ComfyUI, export/save it in API format, "
            "then rerun submit with --template PATH_TO_API_JSON."
        )
    if not is_api_prompt(template):
        raise SystemExit(f"Template is not a ComfyUI API prompt: {args.template}")

    client_id = str(uuid.uuid4())
    for index, card in enumerate(data["cards"]):
        if args.only and card["slug"] not in args.only:
            continue
        seed = args.seed + index if args.seed is not None else random.randint(1, 2**31 - 1)
        prompt = card_payload(card, data["video_defaults"], template, seed)
        prompt_id = queue_prompt(args.server, prompt, client_id)
        print(f"queued {card['slug']} as {prompt_id}")
        history = wait_for_history(args.server, prompt_id, args.poll_seconds, args.timeout_seconds)
        saved = download_outputs(args.server, history, OUTPUT_DIR)
        promoted = promote_card_video(card["slug"], saved)
        if not saved:
            print(f"warning: no downloadable output reported for {card['slug']}")
        elif not promoted:
            print(f"warning: no video output reported for {card['slug']}")
    return 0


def submit_jobs(args: argparse.Namespace) -> int:
    try:
        http_json(f"{args.server.rstrip('/')}/system_stats", timeout=5)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SystemExit(f"ComfyUI server is not reachable at {args.server}: {exc}") from exc

    data = load_cards(args.cards)
    card_by_slug = {card["slug"]: card for card in data["cards"]}
    job_paths = sorted(JOBS_DIR.glob("*.json"))
    if args.only:
        wanted = set(args.only)
        job_paths = [path for path in job_paths if path.stem.split("_", 1)[-1] in wanted]
    if not job_paths:
        raise SystemExit(f"No API job files found in {JOBS_DIR.relative_to(ROOT)}")

    client_id = str(uuid.uuid4())
    for job_path in job_paths:
        slug = job_path.stem.split("_", 1)[-1]
        prompt = json.loads(job_path.read_text(encoding="utf-8"))
        prompt_id = queue_prompt(args.server, prompt, client_id)
        print(f"queued {slug} as {prompt_id}")
        history = wait_for_history(args.server, prompt_id, args.poll_seconds, args.timeout_seconds)
        saved = download_outputs(args.server, history, OUTPUT_DIR)
        promoted = promote_card_video(slug, saved)
        if not saved and slug in card_by_slug:
            print(f"warning: no downloadable output reported for {slug}")
        elif not promoted and slug in card_by_slug:
            print(f"warning: no video output reported for {slug}")
    return 0


def validate(args: argparse.Namespace) -> int:
    data = load_cards(args.cards)
    missing: list[str] = []
    for card in data["cards"]:
        for key in ("audio_en", "audio_cn", "video"):
            if not (ROOT / card[key]).exists():
                missing.append(card[key])
        if len(card["prompt"]) < 240:
            missing.append(f"{card['slug']}: prompt is not detailed enough")
        if not card.get("negative_prompt"):
            missing.append(f"{card['slug']}: missing negative prompt")
        workflow_path = WORKFLOWS_DIR / f"{data['cards'].index(card) + 1:02d}_{card['slug']}.json"
        if not workflow_path.exists():
            missing.append(str(workflow_path.relative_to(ROOT)))
    if not DEFAULT_TEMPLATE.exists():
        missing.append(str(DEFAULT_TEMPLATE.relative_to(ROOT)))
    if missing:
        for item in missing:
            print(f"missing: {item}")
        return 1
    print(f"validated {len(data['cards'])} kids English 2.0 cards")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kids English 2.0 ComfyUI LTX-2.3 pipeline")
    parser.add_argument("--cards", type=Path, default=CARDS_PATH)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    sub = parser.add_subparsers(dest="command", required=True)

    write = sub.add_parser("write-jobs", help="Write one patched ComfyUI workflow or API payload per card")
    write.add_argument("--seed", type=int)
    write.set_defaults(func=write_jobs)

    api_jobs = sub.add_parser("write-api-jobs", help="Convert patched UI workflows to ComfyUI /prompt API jobs")
    api_jobs.add_argument("--server", default="http://127.0.0.1:8188")
    api_jobs.set_defaults(func=write_api_jobs)

    inputs = sub.add_parser("prepare-inputs", help="Copy card poster images into the ComfyUI input directory")
    inputs.add_argument("--comfy-input-dir", type=Path, default=DEFAULT_COMFY_INPUT_DIR)
    inputs.set_defaults(func=prepare_inputs)

    submit_parser = sub.add_parser("submit", help="Queue cards through a running ComfyUI API server")
    submit_parser.add_argument("--server", default="http://127.0.0.1:8188")
    submit_parser.add_argument("--seed", type=int)
    submit_parser.add_argument("--only", nargs="*", help="Optional list of slugs to submit")
    submit_parser.add_argument("--poll-seconds", type=float, default=2.0)
    submit_parser.add_argument("--timeout-seconds", type=int, default=1800)
    submit_parser.set_defaults(func=submit)

    submit_jobs_parser = sub.add_parser("submit-jobs", help="Submit API jobs already written in comfyui/jobs/kids-english-2")
    submit_jobs_parser.add_argument("--server", default="http://127.0.0.1:8188")
    submit_jobs_parser.add_argument("--only", nargs="*", help="Optional list of slugs to submit")
    submit_jobs_parser.add_argument("--poll-seconds", type=float, default=2.0)
    submit_jobs_parser.add_argument("--timeout-seconds", type=int, default=1800)
    submit_jobs_parser.set_defaults(func=submit_jobs)

    validate_parser = sub.add_parser("validate", help="Validate card manifest and local website assets")
    validate_parser.set_defaults(func=validate)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
