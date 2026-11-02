#!/usr/bin/env python3
"""Refresh player photo URLs in output/images.csv with each player's most recent Leaguepedia image."""

import argparse
import csv
import os
import time

from mwrogue.esports_client import AuthCredentials, EsportsClient

RATE_LIMIT_SLEEP = 60
PLAYER_BATCH = 40
TITLE_BATCH = 50
PAGE_SIZE = 500
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def with_retry(call):
    """Run a wiki request, waiting out rate limits like the other fetch scripts."""
    while True:
        try:
            return call()
        except Exception as error:
            message = str(error).lower()
            if "ratelimit" not in message and "rate limit" not in message:
                raise
            print(f"Rate limited; retrying in {RATE_LIMIT_SLEEP} seconds.")
            time.sleep(RATE_LIMIT_SLEEP)


def sql_quote(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def is_profile(value):
    return str(value).strip().lower() in ("1", "yes", "true")


def fetch_latest_files(site, player_names):
    """Return {player name: image file name}, preferring IsProfileImage rows, then the newest tournament date."""
    latest = {}
    for start in range(0, len(player_names), PLAYER_BATCH):
        batch = player_names[start:start + PLAYER_BATCH]
        where = "PI.Link IN (" + ", ".join(sql_quote(n) for n in batch) + ")"
        offset = 0
        while True:
            page = with_retry(lambda: site.cargo_client.query(
                tables="PlayerImages=PI,Tournaments=T",
                join_on="PI.Tournament=T.OverviewPage",
                fields=(
                    "PI.FileName=FileName,PI.Link=Link,PI.IsProfileImage=IsProfileImage,"
                    "T.DateStart=DateStart,T.Date=Date"
                ),
                where=where,
                order_by="PI.Link, PI.FileName",
                limit=PAGE_SIZE,
                offset=offset,
            ))
            for row in page:
                file_name = (row.get("FileName") or "").strip()
                link = row.get("Link")
                if not file_name or not file_name.lower().endswith(IMAGE_EXTENSIONS):
                    continue
                # PlayerImages has no usable date; the tournament the photo was taken at does.
                taken = row.get("DateStart") or row.get("Date") or ""
                rank = (is_profile(row.get("IsProfileImage")), taken)
                best = latest.get(link)
                if best is None or rank > best[0]:
                    latest[link] = (rank, file_name)
            if len(page) < PAGE_SIZE:
                break
            offset += len(page)
        print(f"Looked up images for {min(start + PLAYER_BATCH, len(player_names))}/{len(player_names)} players.")
    return {link: file_name for link, (_, file_name) in latest.items()}


def resolve_urls(site, file_names):
    """Return {file name: direct image URL} through the imageinfo API."""
    urls = {}
    names = sorted(set(file_names))
    for start in range(0, len(names), TITLE_BATCH):
        batch = names[start:start + TITLE_BATCH]
        result = with_retry(lambda: site.client.api(
            "query",
            titles="|".join(f"File:{name}" for name in batch),
            prop="imageinfo",
            iiprop="url",
        ))
        pages = result.get("query", {}).get("pages", {})
        for page in pages.values():
            info = (page.get("imageinfo") or [None])[0]
            if info and info.get("url"):
                urls[page["title"].removeprefix("File:").replace(" ", "_")] = info["url"]
        print(f"Resolved {min(start + TITLE_BATCH, len(names))}/{len(names)} image URLs.")
    return urls


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        return list(csv.DictReader(csv_file))


def main():
    parser = argparse.ArgumentParser(description="Update player photo URLs in images.csv to the latest photo.")
    parser.add_argument("--players", default=os.path.join("output", "players.csv"))
    parser.add_argument("--images", default=os.path.join("output", "images.csv"))
    args = parser.parse_args()

    players = read_csv(args.players)
    images = read_csv(args.images) if os.path.exists(args.images) else []
    id_by_name = {row["IngameName"].strip().casefold(): row["Id"] for row in players if row.get("IngameName")}

    site = EsportsClient("lol", credentials=AuthCredentials(user_file="me"))
    names = [row["IngameName"] for row in players if row.get("IngameName")]
    files = fetch_latest_files(site, names)
    urls = resolve_urls(site, files.values())

    updated = 0
    new_urls = {}
    for name, file_name in files.items():
        # The wiki matches names case-insensitively, so "1SSUE" can come back for "1ssue".
        player_id = id_by_name.get(name.strip().casefold())
        url = urls.get(file_name.replace(" ", "_"))
        if player_id and url:
            new_urls[player_id] = url

    known_ids = set()
    for row in images:
        known_ids.add(row["Id"])
        url = new_urls.get(row["Id"])
        if url and url != row["Url"]:
            row["Url"] = url
            updated += 1
    for player_id, url in new_urls.items():
        if player_id not in known_ids:
            images.append({"Id": player_id, "Url": url})
            updated += 1

    with open(args.images, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=("Id", "Url"), quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(images)
    print(f"Updated {updated} image rows; {len(new_urls)}/{len(id_by_name)} players have a photo.")


if __name__ == "__main__":
    main()
