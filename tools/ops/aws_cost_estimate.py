#!/usr/bin/env python3
"""
Seven-day cost worksheet for the demo stack, from AWS's PUBLIC price list.

    python tools/ops/aws_cost_estimate.py --region ap-south-1 --days 7 \
        --instance t3.micro --db-class db.t4g.micro --out logs/aws_cost

Reads https://pricing.us-east-1.amazonaws.com (no AWS account or credentials
needed; files are cached under --cache). The result is ON-DEMAND LIST PRICE
before any credits, Free Tier or tax. It cannot see this account's plan, credit
balance, expiry or eligible services — check those in Billing and Cost
Management. Every line names the price-list SKU it came from.

Resources priced (matching deploy/aws/shadowtrace-demo.yaml):
  EC2 instance (Linux, shared tenancy) · root + data EBS gp3 · EBS snapshots
  (data volume) · one public IPv4 address (Elastic IP, in use) · RDS
  PostgreSQL Single-AZ instance · RDS gp3 storage · RDS backup storage beyond
  the free allowance (assumed 0 for a 7-day demo) · S3 Standard backup storage
  + PUT requests · Secrets Manager (RDS-managed master secret).
Not included (not in the template): NAT Gateway, load balancer, CloudWatch
Logs ingestion, Route 53 hosted zone, data transfer out beyond the free tier,
Groq and Deepgram usage (billed by those providers, not AWS).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import time
import urllib.request
from pathlib import Path

BASE = "https://pricing.us-east-1.amazonaws.com"
LOCATIONS = {
    "ap-south-1": "Asia Pacific (Mumbai)",
    "us-east-1": "US East (N. Virginia)",
    "eu-west-1": "EU (Ireland)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
}


def offer_url(service: str, region: str, fmt: str) -> str:
    idx = json.load(urllib.request.urlopen(f"{BASE}/offers/v1.0/aws/{service}/current/region_index.json", timeout=60))
    url = BASE + idx["regions"][region]["currentVersionUrl"]
    return url[: -len("json")] + fmt if fmt == "csv" else url


def fetch(url: str, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / url.replace(BASE + "/offers/v1.0/aws/", "").replace("/", "_")
    if not path.exists():
        tmp = path.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=600) as resp, tmp.open("wb") as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
        tmp.replace(path)
    return path


def csv_rows(path: Path):
    with path.open(encoding="utf-8", newline="") as handle:
        for _ in range(5):  # price-list CSV preamble
            handle.readline()
        yield from csv.DictReader(handle)


def ec2_prices(path: Path, instance: str, location: str) -> dict:
    found: dict = {}
    for r in csv_rows(path):
        if r.get("TermType") != "OnDemand" or r.get("Location") != location:
            continue
        fam, unit = r.get("Product Family"), r.get("Unit")
        if (fam == "Compute Instance" and r.get("Instance Type") == instance and r.get("Operating System") == "Linux"
                and r.get("Tenancy") == "Shared" and r.get("Pre Installed S/W") == "NA"
                and (r.get("usageType") or "").endswith(f"BoxUsage:{instance}") and r.get("operation") == "RunInstances"):
            found["instance_hour"] = (float(r["PricePerUnit"]), r["SKU"])
        elif fam == "Storage" and r.get("Volume API Name") == "gp3" and unit == "GB-Mo":
            found["gp3_gb_month"] = (float(r["PricePerUnit"]), r["SKU"])
        elif fam == "Storage Snapshot" and "EBS:SnapshotUsage" in (r.get("usageType") or "") and unit == "GB-Mo" \
                and "Archive" not in (r.get("usageType") or "") and float(r["PricePerUnit"]) > 0:
            found.setdefault("snapshot_gb_month", (float(r["PricePerUnit"]), r["SKU"]))
    return found


def vpc_ipv4(path: Path) -> tuple[float, str] | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for sku, product in data["products"].items():
        usage = product["attributes"].get("usagetype", "")
        if "PublicIPv4:InUseAddress" in usage:
            for term in data["terms"]["OnDemand"].get(sku, {}).values():
                for dim in term["priceDimensions"].values():
                    price = float(dim["pricePerUnit"]["USD"])
                    if price > 0:
                        return price, sku
    return None


def rds_prices(path: Path, db_class: str) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    found: dict = {}
    for sku, product in data["products"].items():
        a = product["attributes"]
        fam = product.get("productFamily")
        target = None
        if (fam == "Database Instance" and a.get("instanceType") == db_class and a.get("databaseEngine") == "PostgreSQL"
                and a.get("deploymentOption") == "Single-AZ"):
            target = "instance_hour"
        elif fam == "Database Storage" and a.get("databaseEngine") == "PostgreSQL" and a.get("deploymentOption") == "Single-AZ" \
                and a.get("volumeType") in ("General Purpose-GP3", "General Purpose (SSD)") and "gp3" in a.get("usagetype", "").lower():
            target = "gp3_gb_month"
        elif fam == "Storage Snapshot" and a.get("databaseEngine") in ("PostgreSQL", "Any") and "BackupUsage" in a.get("usagetype", ""):
            target = "backup_gb_month"
        if not target:
            continue
        for term in data["terms"]["OnDemand"].get(sku, {}).values():
            for dim in term["priceDimensions"].values():
                price = float(dim["pricePerUnit"]["USD"])
                if price > 0:
                    found.setdefault(target, (price, sku))
    return found


def simple_price(path: Path, predicate) -> tuple[float, str] | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for sku, product in data["products"].items():
        if predicate(product):
            for term in data["terms"]["OnDemand"].get(sku, {}).values():
                for dim in sorted(term["priceDimensions"].values(), key=lambda d: float(d.get("beginRange", 0) or 0)):
                    price = float(dim["pricePerUnit"]["USD"])
                    if price > 0:
                        return price, sku
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--region", default="ap-south-1")
    p.add_argument("--days", type=float, default=7)
    p.add_argument("--instance", default="t3.micro")
    p.add_argument("--db-class", default="db.t4g.micro")
    p.add_argument("--root-gb", type=float, default=16)
    p.add_argument("--data-gb", type=float, default=10)
    p.add_argument("--db-gb", type=float, default=20)
    p.add_argument("--snapshot-gb", type=float, default=10, help="data-volume snapshot kept at teardown")
    p.add_argument("--s3-gb", type=float, default=1)
    p.add_argument("--s3-puts", type=int, default=200)
    p.add_argument("--cache", type=Path, default=Path("logs/pricing-cache"))
    p.add_argument("--out", type=Path, default=Path("logs/aws_cost"))
    a = p.parse_args()
    location = LOCATIONS.get(a.region)
    if not location:
        p.error(f"add the price-list location name for {a.region} to LOCATIONS")
    hours, months = a.days * 24, a.days / 30.4375

    ec2 = ec2_prices(fetch(offer_url("AmazonEC2", a.region, "csv"), a.cache), a.instance, location)
    ipv4 = vpc_ipv4(fetch(offer_url("AmazonVPC", a.region, "json"), a.cache))
    rds = rds_prices(fetch(offer_url("AmazonRDS", a.region, "json"), a.cache), a.db_class)
    s3_store = simple_price(fetch(offer_url("AmazonS3", a.region, "json"), a.cache),
                            lambda pr: pr.get("productFamily") == "Storage" and pr["attributes"].get("volumeType") == "Standard")
    s3_put = simple_price(fetch(offer_url("AmazonS3", a.region, "json"), a.cache),
                          lambda pr: pr["attributes"].get("group") == "S3-API-Tier1")
    secret = simple_price(fetch(offer_url("AWSSecretsManager", a.region, "json"), a.cache),
                          lambda pr: "Secret" in pr["attributes"].get("usagetype", "") and "API" not in pr["attributes"].get("usagetype", ""))

    lines = []

    def line(item, qty, unit, price_sku):
        if price_sku is None:
            lines.append({"item": item, "quantity": qty, "unit": unit, "unit_price_usd": None, "cost_usd": None, "sku": "NOT FOUND — check manually"})
            return
        price, sku = price_sku
        lines.append({"item": item, "quantity": round(qty, 4), "unit": unit, "unit_price_usd": price, "cost_usd": round(qty * price, 4), "sku": sku})

    line(f"EC2 {a.instance} Linux on-demand", hours, "hours", ec2.get("instance_hour"))
    line(f"EBS gp3 root ({a.root_gb:g} GB)", a.root_gb * months, "GB-month", ec2.get("gp3_gb_month"))
    line(f"EBS gp3 data ({a.data_gb:g} GB, encrypted)", a.data_gb * months, "GB-month", ec2.get("gp3_gb_month"))
    line("Public IPv4 (Elastic IP, in use)", hours, "hours", ipv4)
    line(f"RDS PostgreSQL {a.db_class} Single-AZ", hours, "hours", rds.get("instance_hour"))
    line(f"RDS gp3 storage ({a.db_gb:g} GB)", a.db_gb * months, "GB-month", rds.get("gp3_gb_month"))
    lines.append({"item": "RDS automated backups (up to allocated storage is not charged)", "quantity": 0, "unit": "GB-month",
                  "unit_price_usd": (rds.get("backup_gb_month") or (None,))[0], "cost_usd": 0.0, "sku": (rds.get("backup_gb_month") or (None, ""))[1]})
    line(f"S3 Standard backups (~{a.s3_gb:g} GB)", a.s3_gb * months, "GB-month", s3_store)
    line(f"S3 PUT requests (~{a.s3_puts})", a.s3_puts / 1000, "1,000 requests", (s3_put[0] * 1000, s3_put[1]) if s3_put else None)
    line("Secrets Manager (RDS-managed master secret)", months, "secret-month", secret)
    known = [l["cost_usd"] for l in lines if l["cost_usd"] is not None]
    after = {"item": f"After teardown: retained data-volume snapshot ({a.snapshot_gb:g} GB) and RDS final snapshot",
             "per_month_usd": round(a.snapshot_gb * (ec2.get("snapshot_gb_month") or (0,))[0], 4),
             "note": "RDS final snapshot billed at the backup-storage rate once the instance is deleted; delete snapshots to stop charges."}
    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "region": a.region, "days": a.days,
        "basis": "AWS public on-demand list price, USD, before credits, Free Tier and tax",
        "lines": lines,
        "total_usd_known_lines": round(sum(known), 2),
        "missing_prices": [l["item"] for l in lines if l["cost_usd"] is None],
        "after_teardown": after,
        "excluded": ["NAT Gateway", "load balancer", "CloudWatch Logs", "Route 53", "data transfer beyond free tier", "Groq", "Deepgram"],
    }
    a.out.mkdir(parents=True, exist_ok=True)
    stem = a.out / f"cost_{a.region}_{a.instance}_{a.db_class}_{int(a.days)}d".replace(".", "-")
    stem.with_suffix(".json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    md = [f"# {a.days:g}-day list-price estimate — {a.region}", "", result["basis"] + ".", "",
          "| Item | Quantity | Unit | Unit price (USD) | Cost (USD) | Price-list SKU |", "|---|---|---|---|---|---|"]
    for l in lines:
        md.append(f"| {l['item']} | {l['quantity']} | {l['unit']} | {l['unit_price_usd']} | {l['cost_usd']} | {l['sku']} |")
    md += ["", f"**Total (lines with a price): ${result['total_usd_known_lines']}**", "",
           f"After teardown, per month: ${after['per_month_usd']} for the retained data snapshot. {after['note']}"]
    stem.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
