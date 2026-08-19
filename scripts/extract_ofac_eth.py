"""Extract EVM (0x) wallets + SDN metadata from OFAC SDN_ENHANCED.XML.

Streaming parse so the ~100MB file is not loaded as a full DOM.
Keeps any 0x + 40 hex digital-currency address (ETH, USDC, USDT-on-ETH, etc.).
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
DC_PREFIX = "Digital Currency Address - "
DEFAULT_XML = Path("sdn_enhanced.xml")
DEFAULT_OUT = Path("data/ofac_sanctions.json")
MAX_ALIASES = 8
MAX_RELATIONSHIPS = 8
MAX_OTHER_CRYPTO = 20


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _find(el: ET.Element, name: str) -> ET.Element | None:
    return el.find(f"{{*}}{name}")


def _findall(el: ET.Element, name: str) -> list[ET.Element]:
    return el.findall(f"{{*}}{name}")


def _extend_unique(dest: list, items: list) -> None:
    for item in items:
        if item and item not in dest:
            dest.append(item)


def _primary_name(entity: ET.Element) -> str:
    names = _find(entity, "names")
    if names is None:
        return ""
    fallback = ""
    for name in _findall(names, "name"):
        translations = _find(name, "translations")
        if translations is None:
            continue
        for translation in _findall(translations, "translation"):
            full = _text(_find(translation, "formattedFullName"))
            if not full:
                continue
            if fallback == "":
                fallback = full
            if _text(_find(name, "isPrimary")).lower() == "true":
                return full
    return fallback


def _aliases(entity: ET.Element, primary: str) -> list[str]:
    names = _find(entity, "names")
    if names is None:
        return []
    primary_l = primary.casefold()
    seen: list[str] = []
    for name in _findall(names, "name"):
        if _text(_find(name, "isPrimary")).lower() == "true":
            continue
        translations = _find(name, "translations")
        if translations is None:
            continue
        for translation in _findall(translations, "translation"):
            full = _text(_find(translation, "formattedFullName"))
            if not full or full.casefold() == primary_l or full in seen:
                continue
            seen.append(full)
            if len(seen) >= MAX_ALIASES:
                return seen
    return seen


def _list_text(entity: ET.Element, parent: str, child: str) -> list[str]:
    block = _find(entity, parent)
    if block is None:
        return []
    seen: list[str] = []
    for item in _findall(block, child):
        value = _text(item)
        if value and value not in seen:
            seen.append(value)
    return seen


def _list_dates(entity: ET.Element) -> list[str]:
    block = _find(entity, "sanctionsLists")
    if block is None:
        return []
    seen: list[str] = []
    for item in _findall(block, "sanctionsList"):
        date = (item.get("datePublished") or "").strip()
        if date and date not in seen:
            seen.append(date)
    return seen


def _relationships(entity: ET.Element) -> list[dict[str, str]]:
    block = _find(entity, "relationships")
    if block is None:
        return []
    out: list[dict[str, str]] = []
    for rel in _findall(block, "relationship"):
        related = _find(rel, "relatedEntity")
        entry = {
            "type": _text(_find(rel, "type")),
            "related_name": _text(related),
            "related_entity_id": related.get("entityId", "") if related is not None else "",
        }
        if entry["type"] or entry["related_name"]:
            out.append(entry)
        if len(out) >= MAX_RELATIONSHIPS:
            break
    return out


def _currency(type_text: str) -> str:
    if type_text.startswith(DC_PREFIX):
        return type_text[len(DC_PREFIX) :].strip()
    return ""


def _feature_bundle(entity: ET.Element) -> tuple[list[dict], list[dict], list[str], list[str]]:
    """Return (evm_wallets, other_crypto, secondary_sanctions, countries)."""
    features = _find(entity, "features")
    evm: list[dict] = []
    other: list[dict] = []
    secondary: list[str] = []
    countries: list[str] = []
    if features is None:
        return evm, other, secondary, countries

    for feature in _findall(features, "feature"):
        type_el = _find(feature, "type")
        type_text = _text(type_el)
        value = _text(_find(feature, "value"))
        lowered = type_text.lower()

        if type_text.startswith(DC_PREFIX):
            currency = _currency(type_text)
            if EVM_ADDRESS_RE.fullmatch(value):
                evm.append(
                    {
                        "address": value.lower(),
                        "currency": currency,
                        "reliability": _text(_find(feature, "reliability")) or "",
                    }
                )
            elif value:
                other.append({"currency": currency, "value": value})
            continue

        if "secondary sanctions" in lowered or lowered.startswith("additional sanctions information"):
            blob = " ".join(part for part in (type_text.rstrip(":"), value) if part).strip()
            if blob and blob not in secondary:
                secondary.append(blob)
            continue

        if type_text in {"Nationality Country", "Citizenship Country"} and value:
            if value not in countries:
                countries.append(value)

    return evm, other[:MAX_OTHER_CRYPTO], secondary, countries


def _entity_meta(entity: ET.Element) -> dict:
    info = _find(entity, "generalInfo")
    name = _primary_name(entity)
    dates = _list_dates(entity)
    evm, other_crypto, secondary, countries = _feature_bundle(entity)
    return {
        "name": name,
        "aliases": _aliases(entity, name),
        "entity_type": _text(_find(info, "entityType")) if info is not None else "",
        "entity_id": entity.get("id", ""),
        "identity_id": _text(_find(info, "identityId")) if info is not None else "",
        "programs": _list_text(entity, "sanctionsPrograms", "sanctionsProgram"),
        "legal_authorities": _list_text(entity, "legalAuthorities", "legalAuthority"),
        "sanctions_types": _list_text(entity, "sanctionsTypes", "sanctionsType"),
        "sanctions_lists": _list_text(entity, "sanctionsLists", "sanctionsList"),
        "list_date": dates[0] if dates else "",
        "secondary_sanctions": secondary,
        "countries": countries,
        "relationships": _relationships(entity),
        "other_crypto": other_crypto,
        "evm": evm,
    }


def _merge_wallet(existing: dict, meta: dict, wallet: dict) -> None:
    _extend_unique(existing["aliases"], meta["aliases"])
    _extend_unique(existing["programs"], meta["programs"])
    _extend_unique(existing["legal_authorities"], meta["legal_authorities"])
    _extend_unique(existing["sanctions_types"], meta["sanctions_types"])
    _extend_unique(existing["sanctions_lists"], meta["sanctions_lists"])
    _extend_unique(existing["secondary_sanctions"], meta["secondary_sanctions"])
    _extend_unique(existing["countries"], meta["countries"])
    _extend_unique(existing["currency_types"], [wallet["currency"]] if wallet.get("currency") else [])
    if wallet.get("reliability") and not existing.get("reliability"):
        existing["reliability"] = wallet["reliability"]
    elif wallet.get("reliability") == "Confirmed":
        existing["reliability"] = "Confirmed"
    if meta["list_date"] and (
        not existing.get("list_date") or meta["list_date"] < existing["list_date"]
    ):
        existing["list_date"] = meta["list_date"]
    if meta["relationships"] and not existing.get("relationships"):
        existing["relationships"] = meta["relationships"]
    if meta["other_crypto"] and not existing.get("other_crypto"):
        existing["other_crypto"] = meta["other_crypto"]


def extract(xml_path: Path) -> dict:
    wallets: dict[str, dict] = {}
    data_as_of = ""

    for _event, elem in ET.iterparse(xml_path, events=("end",)):
        tag = _local(elem.tag)
        if tag == "dataAsOf" and not data_as_of:
            data_as_of = _text(elem)
            elem.clear()
            continue
        if tag != "entity":
            continue

        meta = _entity_meta(elem)
        for wallet in meta["evm"]:
            address = wallet["address"]
            if address not in wallets:
                wallets[address] = {
                    "name": meta["name"],
                    "aliases": list(meta["aliases"]),
                    "entity_type": meta["entity_type"],
                    "entity_id": meta["entity_id"],
                    "identity_id": meta["identity_id"],
                    "programs": list(meta["programs"]),
                    "legal_authorities": list(meta["legal_authorities"]),
                    "sanctions_types": list(meta["sanctions_types"]),
                    "sanctions_lists": list(meta["sanctions_lists"]),
                    "list_date": meta["list_date"],
                    "secondary_sanctions": list(meta["secondary_sanctions"]),
                    "countries": list(meta["countries"]),
                    "relationships": list(meta["relationships"]),
                    "other_crypto": list(meta["other_crypto"]),
                    "currency_types": [wallet["currency"]] if wallet.get("currency") else [],
                    "reliability": wallet.get("reliability") or "",
                }
            else:
                _merge_wallet(wallets[address], meta, wallet)

        elem.clear()

    return {
        "comment": "EVM 0x wallets extracted from OFAC SDN_ENHANCED.XML. Refresh with: uv run python scripts/extract_ofac_eth.py",
        "source": str(xml_path),
        "data_as_of": data_as_of,
        "wallets": dict(sorted(wallets.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", type=Path, default=DEFAULT_XML)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if not args.xml.exists():
        raise SystemExit(f"XML not found: {args.xml}")

    payload = extract(args.xml)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(payload['wallets'])} EVM wallets to {args.out}")
    if payload["data_as_of"]:
        print(f"OFAC data as of {payload['data_as_of']}")


if __name__ == "__main__":
    main()
