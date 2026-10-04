#!/usr/bin/env python3
"""Collect verified self-use Actions MSI and unchanged official APK, then publish to NAS."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import urllib.request
import uuid
import zipfile


API = "https://api.github.com"
FORK = "xiaocai218/pairlet"
UPSTREAM = "heypandax/pairlet"
NAS = "codex@192.168.2.222"
REMOTE = "/share/CACHEDEV1_DATA/Public/CC app"


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        redirected = super().redirect_request(request, response, code, message, headers, url)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def request(url, authenticated=False, data=None):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "pairlet-selfhost-maintenance"}
    if authenticated:
        token = Path.home() / "github/release-token.txt"
        if token.stat().st_mode & 0o077:
            raise RuntimeError("Credential file must be private")
        headers["Authorization"] = "Bearer " + token.read_text().strip()
    if data is not None:
        headers["Content-Type"] = "application/json"
    return urllib.request.build_opener(SafeRedirect()).open(
        urllib.request.Request(url, headers=headers, data=None if data is None else json.dumps(data).encode()), timeout=120)


def api(path, authenticated=False, data=None):
    with request(API + path, authenticated, data) as response:
        body = response.read()
        return json.loads(body) if body else None


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(temporary, path)


def download(url, destination, authenticated=False):
    temporary = destination.with_suffix(destination.suffix + ".part")
    with request(url, authenticated) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output)
    os.replace(temporary, destination)


def verify_apk(path, version):
    result = subprocess.run(["aapt", "dump", "badging", str(path)], capture_output=True, text=True, timeout=60)
    match = re.search(r"^package: name='([^']+)' .*versionName='([^']+)'", result.stdout, re.MULTILINE)
    if result.returncode or not match or match.groups() != ("com.panda.ccpocket", version):
        raise RuntimeError("Official APK identity/version mismatch")


def official_apk(version, directory):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected exact release version")
    release = api(f"/repos/{UPSTREAM}/releases/tags/v{version}")
    if release["draft"] or release["prerelease"] or release["tag_name"] != "v" + version:
        raise RuntimeError("Matching official stable release required")
    assets = [asset for asset in release["assets"] if asset["name"] == "cc-pocket-android.apk"]
    if len(assets) != 1 or not str(assets[0].get("digest", "")).startswith("sha256:"):
        raise RuntimeError("Matching APK with upstream SHA256 required")
    asset = assets[0]
    path = directory / f"cc-pocket-android-{version}-official.apk"
    if not path.exists():
        download(asset["browser_download_url"], path)
    if path.stat().st_size != asset["size"] or sha(path) != asset["digest"].split(":", 1)[1]:
        raise RuntimeError("Official APK size/checksum mismatch")
    verify_apk(path, version)
    return dict(file=path.name, version=version, sha256=sha(path), size=path.stat().st_size,
                url=asset["browser_download_url"], tag=release["tag_name"])


def verify_msi(directory, provenance, commit, version, run_id):
    if provenance.get("sourceCommit") != commit or provenance.get("version") != version:
        raise RuntimeError("MSI source/version mismatch")
    if str(provenance.get("runId")) != str(run_id):
        raise RuntimeError("MSI run mismatch")
    filename = provenance["file"]
    if Path(filename).name != filename or not filename.endswith(".msi"):
        raise RuntimeError("Unsafe MSI path")
    path = directory / filename
    if path.stat().st_size != provenance["size"] or sha(path) != provenance["sha256"]:
        raise RuntimeError("MSI size/checksum mismatch")
    return provenance


def actions_msi(run_id, commit, version, directory):
    run = api(f"/repos/{FORK}/actions/runs/{run_id}", True)
    if run["head_sha"] != commit or run["conclusion"] != "success" or run["path"] != ".github/workflows/build-windows.yml":
        raise RuntimeError("Matching successful Windows workflow required")
    artifacts = api(f"/repos/{FORK}/actions/runs/{run_id}/artifacts", True)["artifacts"]
    matching = [item for item in artifacts if item["name"] == f"cc-pocket-desktop-{version}-windows" and not item["expired"]]
    if len(matching) != 1:
        raise RuntimeError("Matching Windows artifact missing or ambiguous")
    archive = directory / "windows-actions.zip"
    download(matching[0]["archive_download_url"], archive, True)
    with zipfile.ZipFile(archive) as package:
        records = [name for name in package.namelist() if name == "provenance.json"]
        if len(records) != 1:
            raise RuntimeError("Windows provenance missing")
        provenance = json.loads(package.read(records[0]).decode("utf-8-sig"))
        filename = provenance["file"]
        if Path(filename).name != filename or not filename.endswith(".msi"):
            raise RuntimeError("Unsafe artifact path")
        with package.open(filename) as stream, (directory / filename).open("wb") as output:
            shutil.copyfileobj(stream, output)
    return verify_msi(directory, provenance, commit, version, run_id)


def remote(arguments):
    key = Path.home() / "pem/nas.pem"
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-i", str(key),
                             NAS, arguments], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError("NAS command failed; credentials/output suppressed")
    return result.stdout.strip()


def publish(path, expected):
    if sha(path) != expected["sha256"] or path.stat().st_size != expected["size"]:
        raise RuntimeError("Local artifact changed before delivery")
    final = REMOTE + "/" + path.name
    temporary = REMOTE + "/." + path.name + ".part-" + uuid.uuid4().hex
    remote("test -d " + shlex.quote(REMOTE))
    key = Path.home() / "pem/nas.pem"
    result = subprocess.run(["scp", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-i", str(key),
                             str(path), NAS + ":" + shlex.quote(temporary)], capture_output=True, timeout=600)
    if result.returncode:
        raise RuntimeError("NAS upload failed; partial file retained")
    quoted = shlex.quote(temporary)
    checksum = expected["sha256"]
    size = str(expected["size"])
    check = f'test "$(sha256sum {quoted} | cut -d " " -f 1)" = {checksum} && test "$(stat -c %s {quoted})" = {size}'
    remote(check + " && mv -n " + quoted + " " + shlex.quote(final))
    final_quoted = shlex.quote(final)
    remote(f'test "$(sha256sum {final_quoted} | cut -d " " -f 1)" = {checksum} && test "$(stat -c %s {final_quoted})" = {size}')
    remote("rm -f " + quoted)
    return dict(path=final, sha256=checksum, size=expected["size"], verified=True)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--commit")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = dict(schema=1, version=args.version, android=official_apk(args.version, args.directory))
    if args.run_id:
        if not args.commit or not re.fullmatch(r"[0-9a-f]{40}", args.commit):
            raise ValueError("Windows collection requires an exact source commit")
        record["windows"] = actions_msi(args.run_id, args.commit, args.version, args.directory)
    if args.publish:
        for platform in ("android", "windows"):
            if platform in record:
                item = record[platform]
                item["delivery"] = publish(args.directory / item["file"], item)
    save(args.directory / "clients.json", record)
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
