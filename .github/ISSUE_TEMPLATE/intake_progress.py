#!/usr/bin/env python3
"""Post (or refresh) a completion summary comment on a project-intake issue.

Reads the issue form template to learn every field, its section, and whether it
is required, then checks which fields were answered in the issue body.
Run by .github/workflows/intake-progress.yml on issue open/edit.
Use --dry-run BODY_FILE to print the comment locally without calling GitHub.
"""
import json
import os
import re
import sys
import urllib.request

import yaml

MARKER = "<!-- intake-progress -->"
TEMPLATE = os.environ.get("INTAKE_TEMPLATE", ".github/ISSUE_TEMPLATE/project-intake-revised.yml")
EMPTY = {"", "_no response_", "none"}


def load_fields(path):
    with open(path, encoding="utf-8") as f:
        form = yaml.safe_load(f)
    fields, section = [], "General"
    for item in form["body"]:
        attrs = item.get("attributes", {})
        if item["type"] == "markdown":
            m = re.search(r"<!--\s*section:\s*(.+?)\s*-->", attrs.get("value", ""))
            if m:
                section = m.group(1)
            continue
        fields.append({
            "label": attrs["label"].strip(),
            "type": item["type"],
            "required": bool(item.get("validations", {}).get("required", False)),
            "section": section,
        })
    return fields


def parse_body(body, labels):
    """Split the rendered issue body into {field label: answer}."""
    answers, current = {}, None
    for line in (body or "").splitlines():
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m and m.group(1) in labels:
            current = m.group(1)
            answers[current] = []
        elif current is not None:
            answers[current].append(line)
    return {k: "\n".join(v).strip() for k, v in answers.items()}


def is_answered(field, value):
    if value is None:
        return False
    if field["type"] == "checkboxes":
        return bool(re.search(r"^\s*-\s*\[[xX]\]", value, re.M))
    return value.strip().lower() not in EMPTY


def bar(pct, width=20):
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def pct(done, total):
    return round(100 * done / total) if total else 100


def build_comment(fields, answers):
    for f in fields:
        f["done"] = is_answered(f, answers.get(f["label"]))
    done = sum(f["done"] for f in fields)
    req = [f for f in fields if f["required"]]
    req_done = sum(f["done"] for f in req)
    overall = pct(done, len(fields))

    lines = [
        MARKER,
        f"## 📋 Intake form completion: {overall}%",
        "",
        f"`{bar(overall)}` &nbsp; **{done} of {len(fields)}** fields answered",
        "",
    ]
    if req_done == len(req):
        lines.append(f"✅ **All required fields complete** ({req_done}/{len(req)})")
    else:
        lines.append(f"⚠️ **Required fields complete: {req_done}/{len(req)} ({pct(req_done, len(req))}%)**")
    lines += ["", "| Section | Answered | Progress |", "|:---|:---:|:---|"]

    sections = list(dict.fromkeys(f["section"] for f in fields))
    for s in sections:
        sf = [f for f in fields if f["section"] == s]
        sd = sum(f["done"] for f in sf)
        p = pct(sd, len(sf))
        lines.append(f"| {s} | {sd}/{len(sf)} | `{bar(p, 10)}` {p}% |")

    missing = [f for f in fields if not f["done"]]
    if missing:
        lines += ["", f"<details><summary>Fields still blank ({len(missing)})</summary>", ""]
        for f in missing:
            tag = " **(required)**" if f["required"] else ""
            lines.append(f"- {f['section']} → {f['label']}{tag}")
        lines += ["", "</details>"]
    lines += ["", "<sub>Updates automatically when the issue is edited.</sub>"]
    return "\n".join(lines)


def api(method, url, token, data=None):
    req = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or "null")


def upsert_comment(repo, number, token, body):
    base = f"https://api.github.com/repos/{repo}/issues"
    page = 1
    while True:
        comments = api("GET", f"{base}/{number}/comments?per_page=100&page={page}", token)
        for c in comments:
            if MARKER in (c.get("body") or "") and c["user"]["type"] == "Bot":
                api("PATCH", f"{base}/comments/{c['id']}", token, {"body": body})
                return "updated"
        if len(comments) < 100:
            break
        page += 1
    api("POST", f"{base}/{number}/comments", token, {"body": body})
    return "created"


def main():
    fields = load_fields(TEMPLATE)
    labels = {f["label"] for f in fields}
    if len(sys.argv) == 3 and sys.argv[1] == "--dry-run":
        body = open(sys.argv[2], encoding="utf-8").read()
        print(build_comment(fields, parse_body(body, labels)))
        return
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
        issue = json.load(f)["issue"]
    comment = build_comment(fields, parse_body(issue.get("body"), labels))
    print(upsert_comment(os.environ["GITHUB_REPOSITORY"], issue["number"], os.environ["GITHUB_TOKEN"], comment))


if __name__ == "__main__":
    main()
