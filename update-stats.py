#!/usr/bin/env python3

import argparse
import json
import os.path
from contextlib import closing
from pathlib import Path

import flathub
from stats_io import file_lock
from stats_store import StatsStore, input_digest, snapshot_input

refs_cache = None


def ref_to_id(ref: str) -> str | None:
    parts = ref.split("/")
    if parts[0] == "app":
        return parts[1]
    if parts[0] == "runtime" and not (
        parts[1].endswith(".Debug")
        or parts[1].endswith(".Locale")
        or parts[1].endswith(".Sources")
    ):
        return f"{parts[1]}/{parts[3]}"
    return None


class RefInfo:
    def __init__(self):
        pass

    def add(self, ref: str, is_update: bool):
        parts = ref.split("/")
        try:
            arch = parts[2]
        except IndexError:
            arch = "x86_64"
        old = vars(self).get(arch, (0, 0))
        downloads = old[0] + 1
        updates = old[1]
        if is_update:
            updates = updates + 1
        vars(self)[arch] = (downloads, updates)

    def from_dict(self, dct):
        for i in dct:
            vars(self)[i] = dct[i]


class RefCountryInfo:
    def __init__(self):
        pass

    def add(self, is_update: bool, country: str):
        old_country = vars(self).get(country, (0, 0))
        downloads_country = old_country[0] + 1
        updates_country = old_country[1]
        if is_update:
            updates_country = updates_country + 1

        vars(self)[country] = (downloads_country, updates_country)

    def from_dict(self, dct):
        for i in dct:
            vars(self)[i] = dct[i]


class RefOsVersionInfo:
    def __init__(self):
        pass

    def add(self, is_update: bool, os_version: str):
        old_os_version = vars(self).get(os_version, (0, 0))
        downloads_os_version = old_os_version[0] + 1
        updates_os_version = old_os_version[1]
        if is_update:
            updates_os_version = updates_os_version + 1

        vars(self)[os_version] = (downloads_os_version, updates_os_version)

    def from_dict(self, dct):
        for i in dct:
            vars(self)[i] = dct[i]


class DayInfo:
    def __init__(self, date):
        self.date = date
        self.downloads = 0
        self.updates = 0
        self.delta_downloads = 0
        self.ostree_versions = {}
        self.flatpak_versions = {}
        self.refs = {}
        self.countries = {}
        self.ref_by_country = {}
        self.os_versions = {}
        self.ref_by_os_version = {}
        self.os_flatpak_versions = {}

    def from_dict(self, dct):
        self.countries = dct.get("countries", {})
        self.downloads = dct["downloads"]
        self.updates = dct["updates"]
        self.delta_downloads = dct["delta_downloads"]
        self.ostree_versions = dct["ostree_versions"]
        self.flatpak_versions = dct["flatpak_versions"]
        self.os_versions = dct.get("os_versions", {})
        refs = dct["refs"]
        for id in refs:
            ri = self.get_ref_info(id)
            ri.from_dict(refs[id])
        ref_by_country = dct.get("ref_by_country", {})
        for id in ref_by_country:
            ri = self.get_ref_country_info(id)
            ri.from_dict(ref_by_country[id])
        ref_by_os_version = dct.get("ref_by_os_version", {})
        for id in ref_by_os_version:
            ri = self.get_ref_os_version_info(id)
            ri.from_dict(ref_by_os_version[id])
        self.os_flatpak_versions = dct.get("os_flatpak_versions", {})

    def get_ref_info(self, id):
        if id not in self.refs:
            self.refs[id] = RefInfo()
        return self.refs[id]

    def get_ref_country_info(self, id):
        if id not in self.ref_by_country:
            self.ref_by_country[id] = RefCountryInfo()
        return self.ref_by_country[id]

    def get_ref_os_version_info(self, id):
        if id not in self.ref_by_os_version:
            self.ref_by_os_version[id] = RefOsVersionInfo()
        return self.ref_by_os_version[id]

    def add(self, download):
        download[flathub.CHECKSUM]
        ref = download[flathub.REF]

        if not ref:
            return

        id = ref_to_id(ref)
        if not id:
            return

        ri = self.get_ref_info(id)
        ri.add(ref, download[flathub.IS_UPDATE])

        self.downloads = self.downloads + 1
        if download[flathub.IS_DELTA]:
            self.delta_downloads = self.delta_downloads + 1
        if download[flathub.IS_UPDATE]:
            self.updates = self.updates + 1

        ostree_version = download[flathub.OSTREE_VERSION]
        self.ostree_versions[ostree_version] = (
            self.ostree_versions.get(ostree_version, 0) + 1
        )

        flatpak_version = download[flathub.FLATPAK_VERSION]
        if flatpak_version:
            self.flatpak_versions[flatpak_version] = (
                self.flatpak_versions.get(flatpak_version, 0) + 1
            )

        country = download[flathub.COUNTRY]
        if country:
            ri = self.get_ref_country_info(id)
            ri.add(download[flathub.IS_UPDATE], country)
            self.countries[country] = self.countries.get(country, 0) + 1

        os_version = download[flathub.OS_VERSION]
        if os_version:
            self.os_versions[os_version] = self.os_versions.get(os_version, 0) + 1
            ri = self.get_ref_os_version_info(id)
            ri.add(download[flathub.IS_UPDATE], os_version)

        if os_version and flatpak_version:
            if os_version not in self.os_flatpak_versions:
                self.os_flatpak_versions[os_version] = {}
            self.os_flatpak_versions[os_version][flatpak_version] = (
                self.os_flatpak_versions[os_version].get(flatpak_version, 0) + 1
            )


def load_dayinfo(dest, date) -> DayInfo:
    day = DayInfo(date)
    path = os.path.join(dest, date + ".json")
    if os.path.exists(path):
        with open(path) as day_f:
            dct = json.loads(day_f.read())
            day = DayInfo(dct["date"])
            day.from_dict(dct)
    return day


parser = argparse.ArgumentParser()
parser.add_argument("--dest", type=str, help="path to destination dir", default="stats")
parser.add_argument(
    "--ref-cache",
    type=str,
    dest="ref_cache_path",
    metavar="REFCACHE",
    default="ref-cache.json",
    help="path to ref-cache.json",
)
parser.add_argument(
    "--ignore-deltas", action="store_true", help="ignore deltas in the log"
)
parser.add_argument(
    "--reprocess",
    action="store_true",
    help="replace previously recorded contributions for these inputs",
)
parser.add_argument(
    "logfiles", metavar="LOGFILE", type=str, help="path to log file", nargs="+"
)
args = parser.parse_args()

with (
    file_lock(args.ref_cache_path + ".lock"),
    file_lock(Path(args.dest) / ".ingestion.lock"),
    closing(StatsStore(args.dest)) as store,
):
    store.publish()  # Repair interrupted publication before accepting new input.
    pending = {}
    for logname in args.logfiles:
        digest = input_digest(logname)
        if store.already_processed(digest, args.ignore_deltas, args.reprocess):
            print(f"Skipping already processed log {logname}")
        else:
            pending.setdefault(digest, logname)

    if pending:
        refs_cache = flathub.load_cache(args.ref_cache_path)
        with store.db:
            touched = set()
            for digest, logname in pending.items():
                days = {}
                with snapshot_input(logname) as (snapshot, copied_digest):
                    if copied_digest != digest:
                        raise ValueError(f"Log changed during processing: {logname}")
                    for event in flathub.parse_log(
                        snapshot, refs_cache, args.ignore_deltas
                    ):
                        date = event[flathub.DATE]
                        if date not in days:
                            days[date] = DayInfo(date)
                        days[date].add(event)
                if input_digest(logname) != digest:
                    raise ValueError(f"Log changed during processing: {logname}")
                touched.update(
                    store.replace_input(
                        digest,
                        logname,
                        args.ignore_deltas,
                        {
                            date: json.loads(json.dumps(day, default=vars))
                            for date, day in days.items()
                        },
                    )
                )
            store.rebuild_days(
                touched,
                lambda date: json.loads(json.dumps(DayInfo(date), default=vars)),
            )
            refs_cache.save(args.ref_cache_path)
        store.publish()
