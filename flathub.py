#!/usr/bin/env python3

import base64
import binascii
import gzip
import hashlib
import json
import lzma
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

from gi.repository import GLib  # type: ignore[import-untyped]

from stats_store import atomic_json_write


def fetch_metadata(url):
    """Bound network stalls and retry transient failures, not missing objects."""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                data = response.read()
                if not data:
                    raise OSError(f"Empty metadata response: {url}")
                return data
        except OSError as error:
            if isinstance(error, urllib.error.HTTPError) and error.code not in (
                408,
                429,
                500,
                502,
                503,
                504,
            ):
                raise
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise AssertionError("unreachable")


def load_cache(path):
    commit_map = {}
    try:
        print(f"Loading cache from {path}")
        with open(path) as f:
            commit_map = json.loads(f.read())
    except FileNotFoundError:
        print("Starting a new commit cache")

    return CommitCache(commit_map)


class CommitCache:
    def __init__(self, commit_map):
        # flatpak_is_valid_arch()
        arch_re = re.compile(r"^[A-Za-z0-9_]+$")

        self.valid_arches: set[str] = {"x86_64", "aarch64", "i386", "arm"}
        self.commit_map: dict[str, list[str | None]] = {}
        self.dirtree_map: dict[str, set[str]] = {}
        self.failed_commits: set[str] = set()
        self.resolution_failures = 0
        self.modified = False

        try:
            url = "https://dl.flathub.org/repo/summary.idx"
            summary_idx = fetch_metadata(url)

            if summary_idx:
                idx_gvar = GLib.Variant.new_from_bytes(
                    GLib.VariantType.new("(a{s(ayaaya{sv})}a{sv})"),
                    GLib.Bytes.new(summary_idx),
                    False,
                )
                sub_sum_arr = idx_gvar.get_child_value(0)
                for sub_sum_name in sub_sum_arr.keys():
                    if arch_re.fullmatch(sub_sum_name):
                        self.valid_arches.add(sub_sum_name)
        except (OSError, GLib.Error) as err:
            print(f"Failed to load summary.idx: {err}")

        # Keep legacy ref-only entries as hints rather than treating them as
        # complete metadata or permanently caching failed resolutions.
        for commit, cached_data in commit_map.items():
            pair = cached_data if isinstance(cached_data, list) else [cached_data, None]
            self.commit_map[commit] = pair
            ref, dirtree = pair
            if ref and len(ref.split("/")) == 4:
                self.valid_arches.add(ref.split("/")[2])
            if dirtree:
                self.dirtree_map.setdefault(dirtree, set()).add(commit)

        # Recover historical roots even when the log supplies only a dirtree
        # and no ref header. This also preserves the old ref-only cache migration.
        for commit, (ref, dirtree) in list(self.commit_map.items()):
            if ref and not dirtree and should_keep_ref(ref, self.valid_arches):
                self.update_for_commit(commit, ref)

        self.summary_map = {}
        url = "https://dl.flathub.org/repo/summary"
        try:
            summaryv = fetch_metadata(url)
            if summaryv:
                v = GLib.Variant.new_from_bytes(
                    GLib.VariantType.new("(a(s(taya{sv}))a{sv})"),
                    GLib.Bytes.new(summaryv),
                    False,
                )
                for m in v[0]:
                    self.summary_map[m[0]] = binascii.hexlify(
                        bytearray(m[1][1])
                    ).decode("utf-8")
        except (OSError, GLib.Error):
            print("Failed to load summary: ")
            print(sys.exc_info())
            pass

    def update_from_summary(self, branch: str):
        commit = self.summary_map.get(branch, None)
        if commit and not self.has_commit(commit):
            self.update_for_commit(commit, branch)

    def update_for_commit(self, commit: str, known_branch: str | None = None):
        if self.has_commit(commit) or commit in self.failed_commits:
            return
        ref = known_branch or self.lookup_ref(commit)
        root_dirtree = None
        url = f"https://dl.flathub.org/repo/objects/{commit[0:2]}/{commit[2:]}.commit"
        print(f"Resolving {commit}", end=" ")
        try:
            commitv = fetch_metadata(url)
            if commitv:
                v = GLib.Variant.new_from_bytes(
                    GLib.VariantType.new("(a{sv}aya(say)sstayay)"),
                    GLib.Bytes.new(commitv),
                    False,
                )
                if "xa.ref" in v[0]:
                    ref = v[0]["xa.ref"]
                elif "ostree.ref-binding" in v[0]:
                    ref = v[0]["ostree.ref-binding"][0]
                root_dirtree = binascii.hexlify(bytearray(v[6])).decode("utf-8")
                if not re.fullmatch(r"[a-f0-9]{64}", root_dirtree):
                    raise ValueError("Invalid root dirtree checksum")
        except (OSError, GLib.Error, ValueError, IndexError):
            print("Failed to resolve commit")
            self.failed_commits.add(commit)
            self.resolution_failures += 1
            return
        print(f"-> {ref}, {root_dirtree}")
        self.modified = True
        self.commit_map[commit] = [ref, root_dirtree]
        if root_dirtree:
            self.dirtree_map.setdefault(root_dirtree, set()).add(commit)

    def has_commit(self, commit):
        pair = self.commit_map.get(commit)
        return bool(pair and pair[0] and pair[1])

    def lookup_ref(self, commit):
        pair = self.commit_map.get(commit, None)
        if pair:
            return pair[0]

    def lookup_by_dirtree(self, dirtree, ref=None) -> str | None:
        commits = self.dirtree_map.get(dirtree, set())
        if ref:
            commits = {commit for commit in commits if self.lookup_ref(commit) == ref}
        # Multiple commits of the same ref can share a root. Different refs
        # require a request header to disambiguate attribution.
        if commits and len({self.lookup_ref(commit) for commit in commits}) == 1:
            return min(commits)
        return None

    def save(self, path):
        if self.modified:
            atomic_json_write(path, self.commit_map)
            self.modified = False


# Indexes for log lines
CHECKSUM = 0
DATE = 1
REF = 2
OSTREE_VERSION = 3
FLATPAK_VERSION = 4
IS_DELTA = 5
IS_UPDATE = 6
COUNTRY = 7
OS_ID = 8
OS_VERSION = 9

# 151.100.102.134 "-" "-" [05/Jun/2018:10:01:16 +0000] "GET /repo/objects/ca/717a9f713291670035f228520523cdea82811eb34521b58b7eea6d5f9e4085.filez HTTP/1.1" 200 822627 "" "libostree/2018.5 flatpak/0.11.7" "runtime/org.freedesktop.Sdk/x86_64/1.6" "" IT
fastly_log_pat = (
    r""
    " ?"  # allow leading space in syslog message per RFC3164
    "([\\da-f.:]+)"  # source
    '\\s"-"\\s"-"\\s'
    "\\[([^\\]]+)\\]\\s"  # datetime
    '"(\\w+)\\s([^\\s"]+)\\s([^"]+)"\\s'  # path
    "(\\d+)\\s"  # status
    "([^\\s]+)\\s"  # size
    '"([^"]*)"\\s'  # referrer
    '"([^"]*)"\\s'  # user agent
    '"([^"]*)"\\s'  # ref
    '"([^"]*)"\\s'  # update_from
    "(\\w+)"  # country
    '(?:\\s"([^"]*)")?'  # os_info (optional)
)
fastly_log_re = re.compile(fastly_log_pat)


def deltaid_to_commit(deltaid: str) -> str | None:
    try:
        if re.fullmatch(r"[A-Za-z0-9+_]{43}", deltaid):
            decoded = base64.b64decode(deltaid.replace("_", "/") + "=", validate=True)
            if len(decoded) == 32:
                return decoded.hex()
    except (binascii.Error, ValueError):
        pass

    return None


def should_keep_ref(ref: str, valid_arches: set[str]) -> bool:
    parts = ref.split("/")

    if len(parts) != 4 or not all(parts):
        return False

    ref_kind, ref_id, ref_arch, _ = parts[0], parts[1], parts[2], parts[3]

    if ref_arch not in valid_arches:
        return False

    if ref_kind == "app":
        return True
    return bool(
        ref_kind == "runtime" and not ref_id.endswith((".Debug", ".Locale", ".Sources"))
    )


def open_log(logname):
    """Read bytes so one invalid UTF-8 line cannot discard adjacent valid lines."""
    name = str(logname)
    if name.endswith(".gz"):
        return gzip.open(name, "rb")
    if name.endswith(".xz"):
        return lzma.open(name, "rb")
    return open(name, "rb")


def log_digest(logname):
    digest = hashlib.sha256()
    with open_log(logname) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def log_matches(logname, quality=None, digest=None):
    if quality is None:
        quality = Counter()
    with open_log(logname) as stream:
        for line in stream:
            if digest is not None:
                digest.update(line)
            quality["lines"] += 1
            try:
                match = fastly_log_re.match(line.decode("utf-8"))
            except UnicodeDecodeError:
                match = None
            if not match:
                quality["malformed"] += 1
                continue
            yield match


def request_object(path):
    """Return (kind, checksum, incremental), distinguishing malformed candidates."""
    if path.startswith("/repo/deltas/") and path.endswith("/superblock"):
        delta = path[len("/repo/deltas/") : -len("/superblock")].replace("/", "")
        source, separator, target = delta.partition("-")
        if separator:
            commit = deltaid_to_commit(target) if deltaid_to_commit(source) else None
        else:
            commit = deltaid_to_commit(source)
        return "delta", commit, bool(separator)
    for kind in ("dirtree", "commit"):
        if path.startswith("/repo/objects/") and path.endswith(f".{kind}"):
            checksum = path[len("/repo/objects/") : -len(kind) - 1].replace("/", "")
            return (
                kind,
                checksum if re.fullmatch(r"[a-f0-9]{64}", checksum) else None,
                False,
            )
    return None, None, False


def prepare_logs(lognames, cache: CommitCache):
    """Resolve metadata across the entire batch before counting any dirtrees."""
    for logname in lognames:
        for match in log_matches(logname):
            if match.group(3) != "GET" or match.group(6) != "200":
                continue
            ref = match.group(10) or None
            if ref and not should_keep_ref(ref, cache.valid_arches):
                continue
            kind, checksum, _ = request_object(match.group(4))
            if kind is None or checksum is None:
                continue
            if ref:
                cache.update_from_summary(ref)
            # Commit requests provide historical mappings even without a delta.
            if kind in ("delta", "commit") and not cache.has_commit(checksum):
                cache.update_for_commit(checksum, ref)


def parse_log(
    logname,
    cache: CommitCache,
    ignore_deltas=False,
    *,
    quality=None,
    prepared=False,
    digest=None,
):
    """Yield events; prepare a whole batch first to remove file-order dependence."""
    if not prepared:
        prepare_logs([logname], cache)
    if quality is None:
        quality = Counter()
    for match in log_matches(logname, quality, digest):
        if match.group(3) != "GET" or match.group(6) != "200":
            quality["filtered"] += 1
            continue
        target_ref = match.group(10) or None
        if target_ref and not should_keep_ref(target_ref, cache.valid_arches):
            quality["filtered"] += 1
            continue
        kind, checksum, is_delta = request_object(match.group(4))
        if kind not in ("delta", "dirtree") or (kind == "delta" and ignore_deltas):
            quality["filtered"] += 1
            continue
        if checksum is None:
            quality["malformed"] += 1
            continue
        if kind == "dirtree":
            commit = cache.lookup_by_dirtree(checksum, target_ref)
            if not commit:
                quality["unresolved_dirtrees"] += 1
                continue
        else:
            commit = checksum
        if not target_ref:
            target_ref = cache.lookup_ref(commit)
        if not target_ref:
            quality["unresolved_refs"] += 1
            continue
        if not should_keep_ref(target_ref, cache.valid_arches):
            quality["filtered"] += 1
            continue
        date_str = match.group(2)
        try:
            if not date_str.endswith(" +0000"):
                raise ValueError("Non-UTC timestamp")
            date_struct = time.strptime(date_str[:-6], "%d/%b/%Y:%H:%M:%S")
        except ValueError:
            quality["malformed"] += 1
            continue
        date = f"{date_struct.tm_year:04d}/{date_struct.tm_mon:02d}/{date_struct.tm_mday:02d}"

        ostree_version = "2017.15"  # Last version that didn't list its version.
        flatpak_version = None
        for ua in match.group(9).split():
            if ua.startswith("libostree/"):
                ostree_version = ua[10:]
            if ua.startswith("flatpak/"):
                flatpak_version = ua[8:]
        os_id = None
        os_version = None
        parts = (match.group(13) or "").split(";")
        if len(parts) >= 2 and parts[0] and parts[1]:
            os_id = parts[0]
            os_version = f"{parts[0]};{parts[1]}"

        quality["counted"] += 1
        if not os_version:
            quality["missing_os"] += 1
        if not flatpak_version:
            quality["missing_flatpak_version"] += 1
        yield (
            commit,
            date,
            target_ref,
            ostree_version,
            flatpak_version,
            is_delta,
            bool(is_delta or match.group(11)),
            match.group(12),
            os_id,
            os_version,
        )


if __name__ == "__main__":
    cache = CommitCache({})
    prepare_logs(sys.argv[1:], cache)
    for logname in sys.argv[1:]:
        for log in parse_log(logname, cache, prepared=True):
            print(log)
